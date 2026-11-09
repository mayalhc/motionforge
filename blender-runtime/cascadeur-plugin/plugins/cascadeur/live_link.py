# Copyright (c) 2026 Chamiseul. All rights reserved.
"""Real-time motion from Blender into an open Cascadeur scene.

Runs inside Cascadeur, not Blender - so nothing in here imports bpy, and
Blender never imports it. Nothing is installed either: Blender posts this file's source
to the script server Cascadeur starts from its own commands menu, and
it lives in memory until Cascadeur closes. The Kimodo plugin in
`third_party` is a separate thing and is still installed the usual
way; this does not replace it and does not change it.

This is the fast half of the bridge. `ardy_stream.py` already streamed, but it
collected its chunks from `scene_idle`, and scene_idle is a **fixed three
second timer** - measured on 2026.2.1, focused and unfocused alike, gaps of
3001/3000/2999/2997/2998 ms. Three seconds is a delivery, not a live link.

Cascadeur ships PySide6, so a `QTimer` can be created from Python and runs on
Cascadeur's own Qt loop. Asked for 33 ms it comes back at about 21 Hz, and -
the part that decides whether any of this works - a scene write from inside
that tick succeeds: 40 of 40 `modify_update_with_session` calls returned True
at 0.26 ms each. So the tick can drive the scene, and the link is real-time.

Nothing here is installed. Blender posts this file's source to Cascadeur's own
MCP script server, which execs it once; from then on the QTimer and a small
HTTP receiver live inside Cascadeur until they are stopped. That removes the
Administrator copy into Program Files that the older receiver needed.

Two threads, and the line between them is the whole design. The HTTP thread
only ever appends to a queue or hands back something the timer left for it.
The QTimer, on Cascadeur's main thread, is the ONLY thing that may touch the
scene - reading included.

That rule has been broken once, by an endpoint that listed the scene's objects
straight from the HTTP thread with a comment saying reading names was safe. It
was not. Cascadeur died. If something needs to know about the scene, the timer
looks and leaves the answer behind.
"""

import json
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import csc
import pycsc
from PySide6 import QtCore

HOST = "127.0.0.1"
PORT = 9566
VERSION = "1.7.0"

# Centimetres, as everything else on the Cascadeur side is.
SCALE = 100.0

# How often the scene is written. Cascadeur will not actually hit 30 Hz - a
# 33 ms request measures out at about 48 ms - but asking for less would cap it
# below what it can do.
INTERVAL_MS = 33

# Frames waiting to be written. Bounded: if the writer ever falls behind the
# sender, the right thing is to drop the oldest and stay live rather than
# build a lag that never clears.
QUEUE_LIMIT = 240

# The animation is lengthened a few seconds at a time. Growing is a modify
# session of its own, so doing it per frame would double the sessions.
GROW_BLOCK = 300

_state = None


class _Link:

    def __init__(self):
        self.queue = deque()
        self.lock = threading.Lock()
        self.server = None
        self.thread = None
        self.timer = None
        self.channels = None
        self.rests = None
        self.names = None
        self.frame = 0
        self.written = 0
        self.received = 0
        self.dropped = 0
        self.errors = []
        self.last_write_ms = 0.0
        self.started = time.time()
        self.stopping = False
        self.length = 0
        self.last_at = 0
        self.follow = False
        # The other direction. Sampling happens on the timer, like every other
        # scene access; the HTTP thread only ever hands out the snapshot it
        # left behind.
        self.reading = False
        self.latest = None
        self.read_ms = 0.0
        self.names_seen = []
        self.names_at = 0.0
        self.wanted_frame = None

    # -- the sending side, on the HTTP thread ---------------------------
    def offer(self, payload):
        frames = payload.get("frames") or []
        with self.lock:
            for frame in frames:
                if len(self.queue) >= QUEUE_LIMIT:
                    self.queue.popleft()
                    self.dropped += 1
                self.queue.append(frame)
                self.received += 1
            depth = len(self.queue)
        return {"ok": True, "queued": depth, "written": self.written,
                "dropped": self.dropped}

    # -- the writing side, on Cascadeur's main thread -------------------
    def _bind(self, scene):
        """Work out which joints of the open scene this stream can drive.

        The rests are read here and folded into every frame. What arrives is
        how far each joint has TURNED from its rest, not where it points in
        the world - `cascadeur_send.sample` explains why at length, and the
        short version is that Blender lays a bone's Y down its length while a
        Cascadeur joint rests however the character was built, so a character
        sent as world orientations arrives crumpled even with every name
        matching.
        """
        viewer = scene.model_viewer()
        behaviours = viewer.behaviour_viewer()
        objects = {viewer.get_object_name(o): o for o in viewer.get_objects()}

        channels = {}
        for name in self.names:
            object_id = objects.get(name)
            if object_id is None:
                continue
            transform = behaviours.get_behaviour_by_name(object_id, "Transform")
            if transform.is_null():
                continue
            channels[name] = (
                behaviours.get_behaviour_data(transform, "global_rotation"),
                behaviours.get_behaviour_data(transform, "global_position"))
        self.channels = channels
        # The recorded reference first, the bind matrices only for joints
        # it does not cover. Reading is the exact inverse of writing, so both
        # sides of this link use whatever this table says.
        bind = _rest_rotations(scene, self.names)
        recorded = _recorded_reference(self.names)
        rests = {}
        for name in self.names:
            rows = recorded.get(name)
            if rows is not None:
                try:
                    rests[name] = _orthonormal(
                        [[float(v) for v in row] for row in rows])
                    continue
                except (TypeError, ValueError):
                    pass
            if name in bind:
                rests[name] = bind[name]
        self.rests = rests
        self.rests_from = "reference" if recorded else "bind matrices"
        return channels

    def _tick(self):
        # A Qt timer swallows whatever its callback raises, so anything that
        # goes wrong in here has to be caught and kept or it is invisible -
        # which is how a wrong matrix type looked like "bound 18, written 0".
        try:
            self._tick_inner()
        except Exception:
            self._note_trace()

    def _tick_inner(self):
        # Asked to stop from the HTTP thread. A QTimer belongs to the thread
        # that made it and only that thread may touch it, so the request is
        # left as a flag and honoured here. Calling timer.stop() from the
        # request thread instead took Cascadeur down with it.
        if self.stopping:
            self._shutdown_timer()
            return
        # Importing a character creates and discards scenes, and reading
        # current_scene() in the middle of that is - in the pose receiver's
        # own words - one of the two ways this application has been made to
        # crash outright. It did, twice. So while an import is in flight this
        # tick does nothing at all, not even look.
        if _import_in_flight():
            return
        scene = _current_scene()
        if scene is None:
            return

        # Somebody asked for a frame and nothing else. Cheap, and the only
        # thread allowed to do it.
        if self.wanted_frame is not None:
            frame, self.wanted_frame = self.wanted_frame, None
            try:
                scene.set_current_frame(int(frame))
                self.last_at = int(frame)
            except Exception:
                pass

        # What the scene is made of, refreshed here because here is the only
        # thread allowed to ask. Every second is often enough: characters do
        # not appear while someone is posing.
        if time.time() - self.names_at > 1.0:
            try:
                self._refresh_names(scene)
            except Exception:
                pass

        # Cascadeur -> Blender. Done first and unconditionally: reading is
        # what the other direction needs, and it must not wait on there being
        # something to write.
        if self.reading:
            self._read(scene)

        with self.lock:
            if not self.queue:
                return
            # Everything waiting goes in one session. A session costs far more
            # than the values in it, so draining is cheaper than pacing.
            batch = list(self.queue)
            self.queue.clear()

        if not self.channels:
            # `not self.channels` rather than `is None`: a bind that matched
            # nothing left an empty dict behind, and every later tick then
            # thought it was bound and wrote nothing while counting frames.
            self.names = list(batch[0].get("names") or [])
            if not self._bind(scene):
                self._note("none of the streamed joints are on this "
                           "character - it was sent %d name(s) such as %s"
                           % (len(self.names), ", ".join(self.names[:3])))
                return

        channels = self.channels
        prepared = []
        wanted_at = []
        for frame in batch:
            rotations = frame.get("rotations") or []
            positions = frame.get("positions") or []
            # Which joints get a position at all. On Cascadeur's own rigged
            # character the rig derives them and writing this rig's lengths
            # would drag its bones about, so only the root goes. A character
            # sent over from Blender has no rig to derive anything: rotations
            # alone leave every joint exactly where it was - measured, the
            # take came out frozen with correct proportions - so all of them
            # have to be written.
            place_all = frame.get("positions_for") == "all"
            root = frame.get("root")
            # Where this frame goes. A take leaves it out and takes the next
            # slot; mirroring names the frame, so moving a controller in
            # Blender rewrites the frame you are looking at rather than
            # appending another one after it.
            wanted_at.append(frame.get("at"))
            entries = []
            for index, name in enumerate(self.names):
                ids = channels.get(name)
                if ids is None or index >= len(rotations):
                    continue
                # turned-from-rest, composed onto this character's own rest.
                # `from_rotation_matrix` takes a plain 3x3 of numbers - there
                # is no csc.math.Mat3 or Vec3, which is what the first attempt
                # assumed and what made the timer throw silently.
                world = _multiply(rotations[index], self.rests.get(name))
                rotation = csc.math.Rotation.from_rotation_matrix(world)
                place = None
                if (place_all or (root is not None and index == 0)) and positions:
                    point = positions[index]
                    place = np.array([point[0] * SCALE, point[1] * SCALE,
                                      point[2] * SCALE], dtype=np.float64)
                entries.append((ids, rotation, place))
            prepared.append(entries)

        # Nothing may be written past the end of the animation, and a freshly
        # loaded character is one frame long: the first attempt failed with
        # "cannot set value on frame 1, last frame in animation is 0" for
        # every frame of the take. Grown in blocks rather than per frame,
        # because growing is a modify session of its own.
        # The furthest frame this batch touches, whether it named one or not.
        named = [a for a in wanted_at if a is not None]
        highest = max(named) + 1 if named else 0
        needed = max(self.frame + len(prepared), highest)
        if needed > self.length:
            block = max(needed, self.length + GROW_BLOCK)
            if self._grow(scene, block):
                self.length = block

        started = time.perf_counter()
        try:
            self._write(scene, prepared, wanted_at)
        except Exception:
            self._note(traceback.format_exc().strip().splitlines()[-1])
            return
        self.last_write_ms = (time.perf_counter() - started) * 1000.0
        if self.follow:
            try:
                scene.set_current_frame(int(self.last_at))
            except Exception:
                self.follow = False

    def _read(self, scene):
        """Snapshot the bound joints at the frame Cascadeur is showing.

        Returned rest-relative, `world @ rest.T`, which is the exact inverse
        of what a write composes - `cascadeur_receive` documents the pairing
        and refuses a mismatch, because getting it wrong puts every bone out
        by a fixed angle and looks like a plausible pose rather than an error.
        """
        if not self.channels:
            # Reading has to be able to bind on its own. Binding used to
            # happen only when a frame arrived to be written, so turning the
            # other direction on and never writing anything left it looking
            # at nothing for ever.
            if not self.names:
                self.names = self.joint_names()
            if not self.names or not self._bind(scene):
                self._note("nothing to read: no joints bound")
                return
        started = time.perf_counter()
        viewer = scene.model_viewer()
        behaviours = viewer.behaviour_viewer()
        data = scene.data_viewer()
        frame = scene.get_current_frame()

        rotations, positions, names = [], [], []
        for name in self.names:
            ids = self.channels.get(name)
            if ids is None:
                continue
            rotation_id, position_id = ids
            try:
                turn = data.get_data_value(rotation_id, frame)
                where = data.get_data_value(position_id, frame)
            except Exception:
                continue
            world = _rows(turn.to_rotation_matrix()
                          if hasattr(turn, "to_rotation_matrix") else turn)
            rest = self.rests.get(name)
            relative = world if rest is None else _multiply(world,
                                                            _transpose(rest))
            names.append(name)
            rotations.append(relative)
            positions.append([float(where[0]) / SCALE,
                              float(where[1]) / SCALE,
                              float(where[2]) / SCALE])
        self.latest = {"frame": int(frame), "names": names,
                       "rotations": rotations, "positions": positions}
        self.read_ms = (time.perf_counter() - started) * 1000.0

    def _grow(self, scene, frames):
        """Make room for `frames` frames on every layer."""
        layers = scene.layers_viewer()
        py_scene = pycsc.wrap(scene)

        def grow(model, update, updater):
            editor = model.layers_editor()
            for layer_id in layers.all_layer_ids():
                if not layer_id.is_null():
                    editor.set_section(_fixed_section(), frames, layer_id)
            model.fit_animation_size_by_layers()
            updater.generate_update()

        ok = py_scene.modify_update("MotionForge live: make room", grow)
        if ok is False:
            self._note("could not make room for frame %d" % frames)
        return ok is not False

    def _write(self, scene, prepared, wanted_at=None):
        py_scene = pycsc.wrap(scene)
        layers = scene.layers_viewer()
        all_layers = [l for l in layers.all_layer_ids() if not l.is_null()]
        start = self.frame
        wanted_at = wanted_at or [None] * len(prepared)

        def write(model, update, updater):
            editor = model.data_editor()
            layer_editor = model.layers_editor()
            for offset, entries in enumerate(prepared):
                named = wanted_at[offset] if offset < len(wanted_at) else None
                at = start + offset if named is None else int(named)
                touched = set()
                for ids, rotation, place in entries:
                    rotation_id, position_id = ids
                    if not rotation_id.is_null():
                        editor.set_data_value(rotation_id, at, rotation)
                        touched.add(rotation_id)
                    if place is not None and not position_id.is_null():
                        editor.set_data_value(position_id, at, place)
                        touched.add(position_id)
                for layer_id in all_layers:
                    layer_editor.set_fixed_interpolation_or_key_if_need(
                        layer_id, at, True)
                model.set_fixed_interpolation_if_need(touched, at)
                updater.run_update(touched, at)

        ok = py_scene.modify_update("MotionForge live", write)
        if ok is False:
            self._note("the scene refused the write")
            return
        if not [a for a in (wanted_at or []) if a is not None]:
            self.frame += len(prepared)
        self.written += len(prepared)
        self.last_at = (int(wanted_at[-1]) if wanted_at and
                        wanted_at[-1] is not None else self.frame - 1)

    def _note_trace(self):
        """The last line is not enough to find anything. Keep the frame too."""
        lines = traceback.format_exc().strip().splitlines()
        where = [l.strip() for l in lines if l.strip().startswith("File ")]
        self._note("%s | %s" % (lines[-1] if lines else "?",
                                where[-1] if where else "?"))

    def _note(self, text):
        if text and (not self.errors or self.errors[-1] != text):
            self.errors.append(text)
            del self.errors[:-5]

    # -- lifecycle ------------------------------------------------------
    def start(self):
        self.server = ThreadingHTTPServer((HOST, PORT), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="MotionForge live link")
        self.thread.daemon = True
        self.thread.start()
        self.timer = QtCore.QTimer()
        self.timer.setInterval(INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _shutdown_timer(self):
        """Main thread only. Called from the tick itself."""
        if self.timer is not None:
            self.timer.stop()
            self.timer.timeout.disconnect()
            self.timer = None

    def stop(self):
        """Ask to stop. Safe from any thread.

        The timer is not touched here - it takes itself down on its next tick.
        The HTTP server is a plain socket server and has no such affinity, but
        `shutdown()` blocks until the serve loop notices, so it may not be
        called from one of its own request threads.
        """
        self.stopping = True
        server, self.server = self.server, None
        if server is not None:
            threading.Thread(target=_close_server, args=(server,),
                             daemon=True).start()

    def joint_names(self):
        """The scene's object names, as the timer last saw them.

        NEVER read from the scene here. This is called from the HTTP thread,
        and touching the scene off the main thread is one of the two ways
        this application has been made to crash outright - the receiver says
        so in its own notes, and I did it anyway, with a comment claiming it
        was safe. It crashed Cascadeur twice more before that comment was
        read back. The list is refreshed on the timer instead.
        """
        return list(self.names_seen)

    def _refresh_names(self, scene):
        """Main thread only, from the tick."""
        viewer = scene.model_viewer()
        self.names_seen = sorted(viewer.get_object_name(o)
                                 for o in viewer.get_objects())
        self.names_at = time.time()

    def report(self):
        with self.lock:
            depth = len(self.queue)
        return {"ok": True, "version": VERSION,
                "stamp": globals().get("STAMP"), "queued": depth,
                "received": self.received, "written": self.written,
                "dropped": self.dropped, "frame": self.frame,
                "bound": len(self.channels or {}),
                "write_ms": round(self.last_write_ms, 2),
                "importing": _import_in_flight(),
                "follow": self.follow,
                "at": self.last_at,
                "reading": self.reading,
                "read_ms": round(self.read_ms, 2),
                "errors": list(self.errors),
                "uptime": round(time.time() - self.started, 1)}


def _transpose(m):
    return [[m[c][r] for c in range(3)] for r in range(3)]


def _rows(matrix):
    """A csc rotation matrix as plain rows, whatever shape it arrives in."""
    try:
        return [[float(matrix[r][c]) for c in range(3)] for r in range(3)]
    except TypeError:
        return [[float(matrix[r * 3 + c]) for c in range(3)] for r in range(3)]


def _multiply(a, b):
    """3x3 by 3x3, with `b` missing meaning identity."""
    if b is None:
        return a
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
            for r in range(3)]


def _orthonormal(matrix):
    """The rotation part alone, with the bind matrix's scale divided out.

    An inverse bind matrix carries the metre-to-centimetre scale, so inverting
    one gives a rotation multiplied by a hundred.
    """
    out = [[0.0] * 3 for _ in range(3)]
    for column in range(3):
        length = sum(matrix[row][column] ** 2 for row in range(3)) ** 0.5
        for row in range(3):
            out[row][column] = (matrix[row][column] / length if length
                                else matrix[row][column])
    return out


def _invert(matrix):
    """The inverse of a 3x3, by cofactors. Only ever given bind matrices."""
    m = matrix
    a = m[1][1] * m[2][2] - m[1][2] * m[2][1]
    b = m[1][2] * m[2][0] - m[1][0] * m[2][2]
    c = m[1][0] * m[2][1] - m[1][1] * m[2][0]
    det = m[0][0] * a + m[0][1] * b + m[0][2] * c
    if abs(det) < 1e-12:
        return None
    adj = [
        [a, m[0][2] * m[2][1] - m[0][1] * m[2][2],
         m[0][1] * m[1][2] - m[0][2] * m[1][1]],
        [b, m[0][0] * m[2][2] - m[0][2] * m[2][0],
         m[0][2] * m[1][0] - m[0][0] * m[1][2]],
        [c, m[0][1] * m[2][0] - m[0][0] * m[2][1],
         m[0][0] * m[1][1] - m[0][1] * m[1][0]],
    ]
    return [[adj[r][c] / det for c in range(3)] for r in range(3)]


def _recorded_reference(names):
    """What the pose receiver wrote down when the character arrived.

    The orientations to compose onto, and NOT the bind matrices this used to
    read. `cascadeur_receiver._reference_rests` spells out why, and it is not
    a preference - the bind matrices are wrong twice over. A MetaHuman binds
    its skin to the twist joints, so upperarm_l, lowerarm_l, thigh_l, calf_l
    and neck_01 have no bind matrix at all and silently became identity; and
    Blender's FBX exporter re-orients every bone on the way out, so even
    where a bind matrix does exist it is not the frame Cascadeur poses in.
    Switching a MetaHuman to Live threw exactly those limbs, and nothing
    else, into a broken pose the moment it was turned on.

    Resolved per joint by the caller, for the same reason the receiver does
    it that way: one name the sender has and this character does not must not
    throw the whole table back on the bind matrices.
    """
    import importlib
    import sys as _sys

    # Cascadeur loads the plugin's scripts under `commands.animation_scripts`,
    # not under the package name they are written in - looking only for
    # `KimodoCascadeurPlugin.cascadeur_receiver` found nothing while the
    # receiver was running and holding 353 recorded joints.
    for where in ("commands.animation_scripts.cascadeur_receiver",
                  "KimodoCascadeurPlugin.cascadeur_receiver",
                  "cascadeur_receiver"):
        module = _sys.modules.get(where)
        if module is None:
            try:
                module = importlib.import_module(where)
            except Exception:
                continue
        state = getattr(module, "_state", None) or {}
        found = state.get("reference") or {}
        if found and any(name in found for name in names):
            return found
    return {}


def _rest_rotations(scene, names):
    """Each joint's rest orientation, from the character's bind matrices.

    Joints the skin does not bind - hand tips, thumbs - have none. Leaving
    them out is right: they inherit their parent's correction through the
    hierarchy.
    """
    py_scene = pycsc.wrap(scene)
    viewer = scene.model_viewer()
    behaviours = viewer.behaviour_viewer()
    assets = scene.assets_manager()

    found = {}
    for mesh_obj in py_scene.get_scene_objects(with_behaviour="MeshObject"):
        behaviour = mesh_obj.get_behaviour("MeshObject")
        dependency = behaviours.get_behaviour_asset(behaviour.handle,
                                                    "mesh_dependency")
        if dependency.is_null():
            continue
        inverse = assets.at(dependency).inverse_bind_matrices()
        for index, joint in enumerate(behaviour.linked_objects.get()):
            if index >= len(inverse):
                break
            name = viewer.get_object_name(joint.unwrap())
            rows = [[float(inverse[index][r][c]) for c in range(3)]
                    for r in range(3)]
            back = _invert(rows)
            if back is not None:
                found[name] = _orthonormal(back)
    return {name: found[name] for name in names if name in found}


def _fixed_section():
    """A section whose keys interpolate the way Cascadeur's own takes do.

    `Section()` comes up on STEP, and STEP is a held pose: a take written into
    STEP sections plays but cannot be worked on, because AutoPosing has
    nothing to solve and never draws its controllers.
    """
    section = csc.layers.layer.Section()
    section.interval.interpolation = csc.layers.layer.Interpolation.FIXED
    return section


def _close_server(server):
    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass


def _import_in_flight():
    """Whether the pose receiver is busy swapping scenes about.

    Asked of the receiver rather than tracked here, because the import is
    started from Blender and this module never hears about it.
    """
    receiver = sys.modules.get("cascadeur_receiver")
    if receiver is None:
        return False
    try:
        return bool(receiver.busy())
    except Exception:
        return False


def _current_scene():
    view = csc.app.get_application().get_scene_manager().current_scene()
    return view.domain_scene() if view is not None else None


class _Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip("/")
        if path in ("/status", "/health"):
            self._reply(_state.report() if _state else {"ok": False})
        elif path == "/joints":
            # What the character over there is actually made of, asked now.
            # The reference recorded at import time is not the thing to trust:
            # it is captured as soon as any joint appears, so during an import
            # it can be a partial list - 157 of 707, in the run that sent
            # three bones and moved nothing.
            if _state is None:
                self._reply({"ok": False, "error": "not running"}, 404)
            else:
                self._reply({"ok": True, "names": _state.joint_names()})
        elif path == "/rests":
            # The bind orientations every returned rotation is measured off.
            # Blender divides by the pose it exported the character at, and
            # the two only agree joint by joint - where they did not, the
            # pose came back bent. Handing them over lets that side work out
            # the difference exactly instead of guessing at it.
            #
            # Read straight off `rests`, which the timer fills once at start
            # and never touches again. No scene call happens here: reading
            # the scene from this thread is what took Cascadeur down.
            if _state is None:
                self._reply({"ok": False, "error": "not running"}, 404)
            else:
                self._reply({"ok": True, "rests": _state.rests or {}})
        elif path == "/pose":
            if _state is None:
                self._reply({"ok": False, "error": "not running"}, 404)
            elif _state.latest is None:
                self._reply({"ok": False,
                             "error": "reading is off, or nothing bound yet"})
            else:
                found = dict(_state.latest)
                found["ok"] = True
                self._reply(found)
        else:
            self._reply({"ok": False, "error": "unknown path"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as error:
            self._reply({"ok": False, "error": str(error)}, 400)
            return
        path = self.path.rstrip("/")
        # /stop sets _state to None, and a request that raced it - or one
        # sent by a Blender that never heard the link had stopped - used to
        # take the handler down with AttributeError and answer nothing.
        if path == "/frames":
            if _state is None:
                self._reply({"ok": False, "error": "not running"}, 409)
            else:
                self._reply(_state.offer(payload))
        elif path == "/at":
            # Just move the playhead. During playback this is all that is
            # wanted: the take is already over there, and re-writing every
            # joint of it thirty times a second is what took Cascadeur down.
            state = _state
            if state is None:
                self._reply({"ok": False, "error": "not running"}, 409)
            else:
                state.wanted_frame = int(payload.get("frame") or 0)
                self._reply({"ok": True, "frame": state.wanted_frame})
        elif path == "/read":
            state = _state
            if state is None:
                self._reply({"ok": False, "error": "not running"}, 409)
            else:
                state.reading = bool(payload.get("on", True))
                if payload.get("names"):
                    state.names = list(payload["names"])
                    state.channels = None
                if not state.reading:
                    state.latest = None
                self._reply(state.report())
        elif path == "/reset":
            if _state is None:
                self._reply({"ok": False, "error": "not running"}, 409)
            else:
                _reset(payload)
                self._reply(_state.report())
        elif path == "/stop":
            self._reply({"ok": True})
            stop()
        else:
            self._reply({"ok": False, "error": "unknown path"}, 404)


def _reset(payload):
    """Start a new take: rebind to the character and rewind the frame."""
    with _state.lock:
        _state.queue.clear()
    _state.channels = None
    _state.names = list(payload.get("names") or []) or _state.names
    _state.frame = int(payload.get("frame_start") or 0)
    _state.length = 0
    _state.follow = bool(payload.get("follow"))
    _state.latest = None
    _state.written = 0
    _state.received = 0
    _state.dropped = 0
    del _state.errors[:]


def start():
    global _state
    if _state is not None:
        return _state.report()
    _state = _Link()
    _state.start()
    return _state.report()


def stop():
    global _state
    if _state is None:
        return {"ok": True, "running": False}
    _state.stop()
    _state = None
    return {"ok": True, "running": False}


def restart():
    """Stop and start again, for when the source has changed."""
    stop()
    return start()


def status():
    return _state.report() if _state is not None else {"ok": False,
                                                       "running": False}
