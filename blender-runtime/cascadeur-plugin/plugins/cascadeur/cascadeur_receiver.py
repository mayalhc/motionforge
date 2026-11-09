# Copyright (c) 2026 Chamiseul. All rights reserved.
"""Take poses from outside Cascadeur - Blender, or anything else - and key them.

The same shape as the ARDY stream, and for the same reason: a write costs about
the same whether it carries one frame or a thousand, because the cost is in the
modify session rather than the values. Measured, a single-pose write settles at
roughly 2 Hz no matter how fast it is asked for, while forty frames of
twenty-seven joints land in 0.09 s in one call. So this takes ranges, not
frames, and there is no attempt at mirroring a rig pose-by-pose.

    GET  /health
    POST /pose   {"path": "<npz>"}          a chunk already on disk
    POST /pose   {"bone_names": [...], ...} the arrays inline as JSON

The file form is the fast one and what Blender uses; the JSON form is there so
the link can be tried from a terminal.

Chunks are queued and written from `scene_idle`, never from the HTTP thread:
the scene is not thread-safe. Start it with the Receive Poses command.
"""

import json
import os
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import csc

from events.scene_idle.scene_idle_manager import scene_idle_manager

try:
    from . import ardy_apply, ardy_live
except ImportError:
    import ardy_apply
    import ardy_live

DEFAULT_PORT = 9564

# Reload re-runs this file in the module's existing namespace, so the old
# value is still visible here - and has to be kept. Rebinding it to a fresh
# dict during development orphaned a running server: the socket stayed bound
# with nothing referring to it, and every later start failed with WinError
# 10048 until Cascadeur was restarted.
_state = globals().get("_state") or {
    "server": None,
    "thread": None,
    "subscription": None,
    "queue": [],
    "lock": threading.Lock(),
    "scene_view": None,
    "imported_view": None,
    "reference": None,
    "reference_positions": None,
    "reference_frame": None,
    "frames": 0,
    "applied": 0,
    "error": None,
    "append": True,
}


def name():
    return "Animation Scripts.Receive Poses (Blender)"


def description():
    return ("Listen for poses sent from Blender and key them onto the "
            "character in this scene")


# -- the queue, drained on Cascadeur's own thread -------------------------

class _Ask:
    """A read to run on Cascadeur's thread while the caller waits.

    Reading the scene straight from the HTTP thread works, and that is how
    /animation did it - there is no scene surgery to defer, so answering
    inline looked safe. It is not free, though: measured on a MetaHuman, 342
    joints over 12 frames took 24.69 s from the HTTP thread and 0.31 s from
    Cascadeur's own. About 6 ms of cross-thread cost on each of the 4,104
    reads, which is the entire wait Blender was seeing.

    So hand the work to the idle queue that already exists for writes, and
    block here until it comes back. Same answer, same synchronous reply.
    """

    def __init__(self, work):
        self.work = work
        self.done = threading.Event()
        self.value = None
        self.error = None

    def run(self):
        try:
            self.value = self.work()
        except BaseException as error:      # carried to the caller, not lost
            self.error = error
        finally:
            self.done.set()

    def wait(self, timeout=600):
        with _state["lock"]:
            _state["queue"].append(self)
        if not self.done.wait(timeout):
            raise RuntimeError(
                "Cascadeur did not answer within %ds. Is it busy with a "
                "dialog, or is 'Receive Poses (Blender)' no longer running?"
                % timeout)
        if self.error is not None:
            raise self.error
        return self.value


def _answer_reads():
    """Run any waiting reads, off a timer rather than off scene idle.

    The idle hook fires when Cascadeur decides the scene has settled, and
    measured on a real character that is about 2.2 s after the request lands -
    a fixed wait, the same for one joint as for six hundred. Writes can afford
    it; a caller blocked on a reply cannot.

    Reads change nothing, so they do not need the scene to be quiet. A short
    timer on the main thread costs a lock check per tick and takes the wait
    down to that tick.
    """
    while True:
        with _state["lock"]:
            job = None
            for index, queued in enumerate(_state["queue"]):
                if isinstance(queued, _Ask):
                    job = _state["queue"].pop(index)
                    break
        if job is None:
            return
        job.run()


def _start_pump():
    from PySide6 import QtCore
    pump = _state.get("pump")
    if pump is not None:
        try:
            pump.stop()
        except Exception:
            pass
    # Looked up through the module rather than bound, so a reload during
    # development replaces the body without leaving a stale one running.
    pump = QtCore.QTimer()
    pump.setInterval(10)
    pump.timeout.connect(lambda: _answer_reads())
    pump.start()
    _state["pump"] = pump


def _stop_pump():
    pump = _state.get("pump")
    if pump is not None:
        try:
            pump.stop()
        except Exception:
            pass
    _state["pump"] = None


def _on_idle(scene):
    with _state["lock"]:
        job = _state["queue"].pop(0) if _state["queue"] else None
    if job is None:
        return

    if isinstance(job, _Ask):
        # Normally the pump has it first; this is here so a read is still
        # answered if the timer could not be created.
        job.run()
        return

    if isinstance(job, tuple) and job[0] == "import":
        _state["applying"] = True
        _schedule_import(job[1])
        return

    _state["applying"] = True
    try:
        names = _bone_names(job)
        view = _find_scene_for(names)
        _state["scene_view"] = view
        if view is None:
            _state["error"] = ("No open scene has any of these joints: "
                               + ", ".join(names[:5]))
            return
        start = _state["frames"] if _state["append"] else 0
        scene = view.domain_scene()
        # Only when the sender says its turns are measured from the pose the
        # character arrived in. The two sides have to agree: composing onto
        # the recorded orientations turns from the bone's own rest put every
        # joint exactly 90 degrees out - the bone-axis change Blender's FBX
        # exporter makes - and the limbs came out bent with the skeleton
        # otherwise perfectly rigid.
        rests = None
        if _chunk_base(job) == "reference":
            rests = _reference_rests(scene, names)
        report = ardy_apply.apply_file(scene, job, frame_start=start,
                                       rests=rests)
        if not report["ok"]:
            # Carry the notes across. Reporting only "it could not be written"
            # hid the actual reason, which was that the chosen scene held none
            # of the joints.
            _state["error"] = "; ".join(report["notes"]) or "the write was refused"
            return
        _state["frames"] = start + report["frames"]
        _state["applied"] += 1
        _extend_boundary(view, _state["frames"])
        _state["error"] = None
    except Exception as error:
        _state["error"] = f"{type(error).__name__}: {error}"
    finally:
        _state["applying"] = False


def _extend_boundary(scene_view, last_frame):
    try:
        boundary = scene_view.animation_boundary()
    except Exception:
        return
    for attribute in ("last_frame", "last_visible_frame"):
        try:
            if getattr(boundary, attribute) < last_frame:
                setattr(boundary, attribute, last_frame)
        except Exception:
            continue


def _schedule_import(fbx_path):
    """Do the import from the event loop, not from inside the idle callback.

    Creating a scene and importing a character straight from the scene_idle
    subscription killed Cascadeur outright - no Python traceback, no dump,
    just "the previous program termination was abnormal" in its log. It is the
    same access violation that showed up while ARDY Live was being built, and
    it happens when a scene is created and populated re-entrantly. ardy_live
    does this work from a command instead, which is why it never crashed.

    A zero-delay QTimer still runs on the main thread - the scene is not
    thread-safe, so it has to - but it runs after the idle callback has
    returned rather than nested inside it. The idle hook is dropped for the
    duration as well, so no second job can start on top of an import that is
    still running.
    """
    try:
        from PySide6 import QtCore
    except ImportError:
        _import_scene(fbx_path)
        return

    subscription = _state.get("subscription")
    if subscription is not None:
        try:
            scene_idle_manager.unsubscribe(subscription)
        except Exception:
            pass
        _state["subscription"] = None

    def run():
        try:
            _import_scene(fbx_path)
        finally:
            _state["applying"] = False
            if _state["server"] is not None and _state["subscription"] is None:
                _state["subscription"] = scene_idle_manager.subscribe(_on_idle)

    QtCore.QTimer.singleShot(0, run)


def _scene_is_open(manager, scene_view):
    """Is this handle still a live scene?

    Being in scenes() is NOT enough. The Python wrapper keeps passing that
    identity check after the native scene behind it is gone - which is what a
    tab the user closed by hand leaves behind - and using it then dies inside
    set_current_scene with "Wrong scene pointer", taking Cascadeur with it.
    Touching domain_scene() first turns that into a plain "no".
    kimodo_roundtrip carries the same check for the same reason.
    """
    if scene_view is None:
        return False
    try:
        if not any(existing is scene_view for existing in manager.scenes()):
            return False
        scene_view.domain_scene()
        return True
    except Exception:
        return False


def _discard_previous(manager):
    """Close the tab a previous send opened, if it is still there.

    Only ever the tab this receiver made itself - anything the user opened is
    left alone. Removing the active scene is not safe, so switch away first.
    """
    previous = _state.get("imported_view")
    _state["imported_view"] = None
    if not _scene_is_open(manager, previous):
        return
    try:
        moved = False
        for other in manager.scenes():
            if other is not previous and _scene_is_open(manager, other):
                manager.set_current_scene(other)
                moved = True
                break
        if not moved:
            return          # it is the only live tab; keep it
        ardy_live._settle(0.3)
        manager.remove_application_scene(previous)
        ardy_live._settle(0.3)
    except Exception:
        pass


def _import_scene(fbx_path):
    """Open a whole character sent from Blender, in a tab of its own.

    The chunk route can only key joints the open character already has. When
    the two skeletons have nothing in common there is nothing to key, so the
    character comes across as FBX and Cascadeur opens it with the same tool
    its File > Import uses.

    The _settle() pauses are not optional. Creating a scene and populating it
    in the same call crashed Cascadeur's renderer twice at the same address
    while ARDY Live was being built; ardy_live carries the same pauses for the
    same reason.
    """
    try:
        application = csc.app.get_application()
        manager = application.get_scene_manager()
        # Close the tab the last send landed in. A MetaHuman is about twelve
        # hundred objects, and Cascadeur was already past 4 GB after three
        # sends - left to pile up, the tabs run it out of memory, which looks
        # like exactly the same crash.
        _discard_previous(manager)
        scene_view = manager.create_application_scene()
        ardy_live._settle(0.5)

        # The scene is filled BEFORE it is shown. Making it current first left
        # the viewport rendering a scene while twelve hundred MetaHuman
        # objects streamed into it, and Cascadeur died in the renderer with no
        # traceback. Kimodo's own importer keeps its import scene out of the
        # viewport for the same reason.
        tools = application.get_tools_manager()
        loader = tools.get_tool("FbxSceneLoader").get_fbx_loader(scene_view)
        loader.import_scene(fbx_path.replace("\\", "/"))
        ardy_live._settle(1.0)

        manager.set_current_scene(scene_view)
        ardy_live._settle(0.5)

        _capture_reference(scene_view)

        ardy_apply.use_autoposing_view(scene_view)
        _state["imported_view"] = scene_view
        _state["scene_view"] = scene_view
        _state["frames"] = 0
        _state["applied"] += 1
        _state["error"] = None
    except Exception as error:
        _state["error"] = f"{type(error).__name__}: {error}"


def _reference_rests(scene, names):
    """The orientations to compose incoming turns onto, or None.

    The character's own, as it arrived - not the bind matrices ardy_apply
    falls back to. Two things go wrong with the bind matrices here: a
    MetaHuman binds its skin to the twist joints, so upperarm_l, calf_l and
    friends have none at all and silently became identity; and Blender's FBX
    exporter re-orients every bone on the way out, so even where a bind matrix
    exists it is not the frame Cascadeur poses in. What the joint actually
    reads when the character lands is neither guess - it is the answer.

    Resolved PER JOINT. Requiring every name to be in the recording meant one
    bone the sender has and this character does not threw the whole lot back
    on the bind matrices - and the arms came out bent again, on a character
    that had been sent from Blender minutes earlier.
    """
    reference = _state.get("reference")
    if not reference:
        return None
    if not any(name in reference for name in names):
        return None

    fallback, _missing = ardy_apply.rest_rotations(scene, names)
    out = []
    for index, name in enumerate(names):
        recorded = reference.get(name)
        if recorded is None:
            out.append(fallback[index])
        else:
            out.append(np.array(recorded, dtype=np.float64))
    return out


def read_animation(names, start=0, end=None):
    """The character's rotations over a frame range, rest-relative.

    The exact inverse of what a send does. Applying composes
    `world = incoming @ rest`, so reading back has to be
    `outgoing = world @ rest.T` - against the SAME rests the send used, which
    is why the recorded reference is preferred over the bind matrices here
    too. Get that pairing wrong and the round trip comes back rotated by a
    fixed amount per bone, which is the failure this bridge already spent a
    day on in the other direction.

    Returns a dict ready to serialise, or raises with a reason.
    """
    view = _find_scene_for(names) or _state.get("scene_view")
    if view is None:
        raise ValueError("No open scene has any of these joints: "
                         + ", ".join(names[:5]))
    scene = view.domain_scene()

    channels, absent = ardy_apply.transform_data(scene, names)
    if not channels:
        raise ValueError("None of those joints are on the character.")

    rests = _reference_rests(scene, names)
    base = "reference"
    if rests is None:
        rests, _missing = ardy_apply.rest_rotations(scene, names)
        base = "rest"

    viewer = scene.model_viewer().data_viewer()
    try:
        available = int(viewer.get_animation_size())
    except Exception:
        available = 0
    last = available - 1 if available else 0
    start = max(0, int(start))
    end = last if end is None else min(int(end), last)
    if end < start:
        raise ValueError("This character holds %d frame(s); asked for %d-%d."
                         % (available, start, end))

    frames = list(range(start, end + 1))
    rotations, positions = [], []
    for frame in frames:
        turn, place = [], []
        for index, name in enumerate(names):
            pair = channels.get(name)
            if pair is None:
                turn.append(np.eye(3).tolist())
                place.append(None)
                continue
            rotation_id, position_id = pair
            if rotation_id.is_null():
                # An object Cascadeur lists as a joint but holds no rotation
                # channel for. Asking anyway threw "data(0000...) not found"
                # and lost the whole read over one such name.
                turn.append(np.eye(3).tolist())
                place.append(None)
                continue
            world = viewer.get_data_value(rotation_id,
                                          frame).to_rotation_matrix()
            world = np.array([[float(v) for v in row] for row in world])
            turn.append((world @ np.asarray(rests[index]).T).tolist())
            if position_id.is_null():
                place.append(None)
            else:
                place.append([float(v) / ardy_apply.SCALE
                              for v in viewer.get_data_value(position_id,
                                                             frame)])
        rotations.append(turn)
        positions.append(place)

    return {
        "ok": True,
        "names": list(names),
        "missing": absent,
        "first": start,
        "last": end,
        "frames": len(frames),
        # Which orientations the caller has to compose back onto. The two
        # sides have to agree, exactly as they do on the way out.
        "base": base,
        "rotations": rotations,
        "positions": positions,
    }


def register_rig_mode(fields, fingers=None, twist=None):
    """Fill the Quick Rigging Tool from a mapping the sender worked out.

    Rig Mode auto-registers by name, and a character whose bones are called
    anything else gets nothing. A TF2 character arrives with 701 joints and
    filled three fields - chest, neck and head, the only three named what
    Cascadeur expected. Blender already knows which bone is the pelvis; this
    is that answer being handed over rather than guessed at again.

    `fields` is {panel field: joint name}, e.g. {"pelvis": "bip_pelvis"}. The
    joints must be the ones the mesh is skinned to - naming a control here
    registers a bone that moves nothing.
    """
    app = csc.app.get_application()
    scene_view = _state.get("scene_view") or app.get_scene_manager().current_scene()
    if scene_view is None:
        raise ValueError("Cascadeur has no scene to register.")
    scene = scene_view.domain_scene()

    viewer = scene.model_viewer()
    by_name = {viewer.get_object_name(o): o for o in viewer.get_objects()}
    name_of = {o: n for n, o in by_name.items()}
    behaviours = scene.behaviour_viewer()

    def parent_of(object_id):
        try:
            basic = behaviours.get_behaviour_by_name(object_id, "Basic")
            parent = behaviours.get_behaviour_object(basic, "parent")
        except Exception:
            return None
        return None if parent is None or parent.is_null() else parent

    def joint_path(bone):
        """Every ancestor, root first, helpers included - as the panel wants."""
        out, node = [], parent_of(by_name[bone])
        while node is not None and node in name_of:
            out.append(name_of[node])
            node = parent_of(node)
        out.reverse()
        return out

    filled, missing = 0, []

    def build(layout, source):
        """Sections for one document, skipping fields with nothing to put in."""
        nonlocal filled
        out = []
        for title, slots in layout:
            names = []
            for field in slots:
                bone = source.get(field)
                if not bone:
                    continue
                if bone not in by_name:
                    missing.append(bone)
                    continue
                names.append({"Bone name": field, "Joint name": bone,
                              "Joint path": joint_path(bone)})
            if names:
                out.append({"Section": title, "Names": names})
                filled += len(names)
        return out

    body = build(_PANEL_SECTIONS, fields)
    if not body:
        raise ValueError("None of those joints are in the Cascadeur scene.")

    documents = [{"Title": "Body", "Sections": body}]
    # The hands and the twist segments are separate documents in Cascadeur's
    # own templates. Registering only the Body one leaves the fingers out of
    # the rig, so the hand moves and the finger mesh stays behind.
    for title, layout in (("Left hand", _HAND_SECTIONS("l")),
                          ("Right hand", _HAND_SECTIONS("r"))):
        sections = build(layout, fingers or {})
        if sections:
            documents.append({"Title": title, "Sections": sections})
    twist_sections = build(_TWIST_SECTIONS, twist or {})
    if twist_sections:
        documents.append({"Title": "Twist bones", "Sections": twist_sections})

    document = {"Document": documents,
                "Settings": {"Is align pelvis": True,
                             "Is create layers": True}}
    editor = app.get_tools_manager().get_tool(
        "RiggingToolWindowTool").editor(app.current_scene())
    editor.load_template_by_content(json.dumps(document, indent=4))

    # It reports nothing either way, and a template the panel declines is
    # accepted in silence, so the only honest answer is what it now holds.
    back = json.loads(editor.get_template_from_qrt() or "{}")
    landed = sum(1 for d in back.get("Document", [])
                 for sec in d.get("Sections", [])
                 for n in sec.get("Names", []) if n.get("Joint name"))
    return {"sent": filled, "registered": landed, "missing": sorted(set(missing)),
            "title": next((d.get("Title") for d in back.get("Document", [])), None)}


# The biped panel's own sections and field names, taken from the templates
# Cascadeur ships in resources/autorig_templates.
_PANEL_SECTIONS = (
    ("Body", ("pelvis", "stomach", "chest", "neck", "head")),
    ("Left arm", ("clavicle_l", "arm_l", "forearm_l", "hand_l")),
    ("Right arm", ("clavicle_r", "arm_r", "forearm_r", "hand_r")),
    ("Left leg", ("thigh_l", "calf_l", "foot_l", "toe_l")),
    ("Right leg", ("thigh_r", "calf_r", "foot_r", "toe_r")),
)

_FINGERS = (("Thumb", "thumb"), ("Index finger", "index_finger"),
            ("Middle finger", "middle_finger"), ("Ring finger", "ring_finger"),
            ("Pinky", "pinky"))


def _HAND_SECTIONS(side):
    return tuple((title, tuple("%s_%s_%d" % (word, side, i + 1)
                               for i in range(3)))
                 for title, word in _FINGERS)


# "arm_l" here is the limb the twist belongs to, not the Body field of the
# same name - the two documents each have their own namespace.
_TWIST_SECTIONS = (
    ("Left arm", ("arm_l", "forearm_l")),
    ("Right arm", ("arm_r", "forearm_r")),
    ("Left leg", ("thigh_l", "calf_l")),
    ("Right leg", ("thigh_r", "calf_r")),
)


def export_scene(names=None, path=None):
    """Write the character Cascadeur holds to an FBX. Returns the path.

    The other half of Mesh + Animation. Which scene goes is decided the same
    way a send decides where to write - by the joints it carries - so a
    Cascadeur with several tabs open does not export whichever happened to
    be in front.
    """
    view = None
    if names:
        view = _find_scene_for(list(names))
    if view is None:
        view = _state.get("scene_view")
    if view is None:
        view = csc.app.get_application().get_scene_manager().current_scene()
    if view is None:
        raise ValueError("Cascadeur has no scene to export.")

    if not path:
        handle, path = tempfile.mkstemp(prefix="cascadeur_back_", suffix=".fbx")
        os.close(handle)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)

    tool = csc.app.get_application().get_tools_manager() \
        .get_tool("FbxSceneLoader")
    if tool is None:
        raise RuntimeError("Cascadeur has no FBX exporter.")

    # Binary, explicitly. `export_fbx_scene` writes ASCII whatever
    # FbxSettings defaults say, and Blender's importer refuses ASCII FBX
    # outright - "ASCII FBX files are not supported". Going through the
    # loader lets the mode be set first.
    written = False
    try:
        loader = tool.get_fbx_loader(view)
        settings = csc.fbx.FbxSettings()
        settings.mode = csc.fbx.FbxSettingsMode.Binary
        settings.bake_animation = True
        loader.set_settings(settings)
        loader.export_all_objects(path)
        written = os.path.isfile(path)
    except Exception:
        written = False
    if not written:
        tool.export_fbx_scene(view, path)

    if not os.path.isfile(path):
        raise RuntimeError("The exporter wrote nothing to %s" % path)

    # Say which it is rather than let Blender find out: an ASCII file here is
    # a dead end at the far end, and the reason is worth naming.
    with open(path, "rb") as handle:
        head = handle.read(24)
    if not head.startswith(b"Kaydara FBX Binary"):
        raise RuntimeError(
            "Cascadeur wrote an ASCII FBX, which Blender cannot read. "
            "Its exporter ignored the binary setting.")
    return path


def _capture_reference(scene_view):
    """Record every joint's orientation as the character arrived.

    Taken at frame 0, which is the first frame of whatever the sender
    exported. Paired with the sender's own pose at that same frame it gives
    the fixed per-bone offset Blender's FBX exporter introduces, so later
    poses can be corrected without anyone guessing at bone-axis conventions.
    """
    _state["reference"] = {}
    _state["reference_frame"] = 0
    try:
        scene = scene_view.domain_scene()
        names = sorted(_object_names(scene_view))
        channels, _ = ardy_apply.transform_data(scene, names)
        viewer = scene.model_viewer().data_viewer()
        out, places = {}, {}
        for name, (rotation_id, position_id) in channels.items():
            if not rotation_id.is_null():
                matrix = viewer.get_data_value(rotation_id, 0).to_rotation_matrix()
                out[name] = [[float(v) for v in row] for row in matrix]
            if not position_id.is_null():
                places[name] = [float(v) for v in
                                viewer.get_data_value(position_id, 0)]
        _state["reference"] = out
        _state["reference_positions"] = places
    except Exception:
        _state["reference"] = {}


def _bone_names(npz_path):
    with np.load(npz_path, allow_pickle=True) as data:
        return [str(n) for n in data["bone_names"]]


def _chunk_base(npz_path):
    """What the sender measured its turns from: "reference" or "rest".

    Chunks written before this existed say nothing, and "rest" is the right
    reading of those - it is what ARDY's own chunks have always meant.
    """
    try:
        with np.load(npz_path, allow_pickle=True) as data:
            if "base" not in data:
                return "rest"
            return str(np.asarray(data["base"]).item())
    except Exception:
        return "rest"


def _object_names(scene_view):
    try:
        viewer = scene_view.domain_scene().model_viewer()
        return {viewer.get_object_name(o) for o in viewer.get_objects()}
    except Exception:
        return set()


def _scene_object_lists():
    """Every open scene's object names, for GET /joints. Runs on
    Cascadeur's thread via _Ask, never on the HTTP one - see the
    module docstring for what reading the scene off-thread costs."""
    manager = csc.app.get_application().get_scene_manager()
    return [sorted(_object_names(view)) for view in manager.scenes()]


def _find_scene_for(bone_names):
    """The open scene that actually has these joints.

    Not "the scene in front", and not "any scene with objects in it" - the
    first version took whichever tab was active and a Cascy sample happened to
    be in front, so a chunk of ARDY joints was refused by a character that had
    none of them. Matching on the names is the only thing that means anything
    when several characters are open.

    Ties go to the scene in front, which is the one being worked in.
    """
    manager = csc.app.get_application().get_scene_manager()
    wanted = set(bone_names)
    if not wanted:
        return None

    current = manager.current_scene()
    best, best_score = None, 0
    for view in manager.scenes():
        score = len(wanted & _object_names(view))
        if score > best_score or (score == best_score and score > 0
                                  and view is current):
            best, best_score = view, score
    return best


# -- HTTP ----------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "CascadeurReceiver/1.0"

    def log_message(self, fmt, *args):
        return

    def _reply(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            # The sender hanging up mid-reply is its business, not an error
            # worth unwinding the server for.
            pass

    def do_GET(self):
        if self.path == "/joints":
            # What the sender needs to decide which names to send under.
            # A MetaHuman imported from Blender is called pelvis/spine_01;
            # Cascadeur's own character is Hips/Spine. Guessing wrong means
            # the chunk matches no open scene and nothing happens at all.
            # Answered over there, on Cascadeur's thread, through _Ask:
            # listing the scene from this one is one of the two ways this
            # application has been made to crash.
            try:
                scenes = _Ask(_scene_object_lists).wait()
            except Exception as error:
                self._reply(HTTPStatus.OK,
                            {"ok": False,
                             "error": f"{type(error).__name__}: {error}"})
                return
            self._reply(HTTPStatus.OK, {"ok": True, "scenes": scenes})
            return
        if self.path == "/reference":
            # The orientations the imported character actually has, and the
            # frame they were taken at. The sender needs these because
            # Blender's FBX exporter re-orients every bone on the way out -
            # measured, each joint arrives exactly 90 degrees off its Blender
            # rest, about a different axis per bone. Comparing this against
            # the sender's own pose at the same frame gives that offset
            # exactly, with nobody having to model the convention.
            self._reply(HTTPStatus.OK, {
                "ok": True,
                "frame": _state.get("reference_frame"),
                "rotations": _state.get("reference") or {},
                "positions": _state.get("reference_positions") or {},
            })
            return
        if self.path == "/health":
            with _state["lock"]:
                queued = len(_state["queue"])
            self._reply(HTTPStatus.OK, {
                "ok": True,
                "queued": queued,
                "frames": _state["frames"],
                "applied": _state["applied"],
                "error": _state["error"],
            })
            return
        self._reply(HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": "GET /health, /joints or POST /pose"})

    # Every path do_POST answers. A branch further down is not enough - this
    # is checked first, and leaving a new endpoint out of it makes the branch
    # unreachable and the reply a bare 404, which reads as "the plugin is out
    # of date" rather than "the list was not updated".
    POST_PATHS = ("/pose", "/reset", "/import", "/animation", "/export",
                  "/reference", "/rigmode")

    def do_POST(self):
        if self.path not in self.POST_PATHS:
            self._reply(HTTPStatus.NOT_FOUND,
                        {"ok": False,
                         "error": "POST one of " + ", ".join(self.POST_PATHS)})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as error:
            self._reply(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)})
            return

        if self.path == "/reference":
            # Put back what a restart lost. The recording only lives in
            # memory, so restarting Cascadeur with the character still on
            # screen left the sender holding a base the reader no longer
            # knew - and every later read was refused, correctly, with
            # nothing to do about it but export the whole character again.
            #
            # The sender kept a copy of exactly these numbers, so it can
            # hand them straight back. Same values, no re-export.
            rotations = payload.get("rotations") or {}
            if not isinstance(rotations, dict) or not rotations:
                self._reply(HTTPStatus.BAD_REQUEST,
                            {"ok": False,
                             "error": "send the rotations that were recorded"})
                return
            _state["reference"] = rotations
            _state["reference_positions"] = payload.get("positions") or {}
            frame = payload.get("frame")
            _state["reference_frame"] = 0 if frame is None else int(frame)
            self._reply(HTTPStatus.OK,
                        {"ok": True, "restored": len(rotations)})
            return

        if self.path == "/animation":
            # The way back. Run on Cascadeur's thread, not this one - see
            # _Ask for what reading from here costs.
            names = payload.get("names") or []
            if not isinstance(names, list) or not names:
                self._reply(HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "send a list of names"})
                return
            start, end = payload.get("start", 0), payload.get("end")
            try:
                answer = _Ask(
                    lambda: read_animation(names, start, end)).wait()
            except Exception as error:
                self._reply(HTTPStatus.OK,
                            {"ok": False,
                             "error": f"{type(error).__name__}: {error}"})
                return
            self._reply(HTTPStatus.OK, answer)
            return

        if self.path == "/export":
            # Mesh + Animation, coming back. The FBX is written where the
            # caller asks, or to a temporary file whose path is returned.
            want, where = payload.get("names"), payload.get("path")
            try:
                written = _Ask(lambda: export_scene(want, where)).wait()
            except Exception as error:
                self._reply(HTTPStatus.OK,
                            {"ok": False,
                             "error": f"{type(error).__name__}: {error}"})
                return
            self._reply(HTTPStatus.OK,
                        {"ok": True, "path": written,
                         "bytes": os.path.getsize(written)})
            return

        if self.path == "/import":
            path = payload.get("path")
            if not path or not os.path.isfile(path):
                self._reply(HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": f"No such file: {path!r}"})
                return
            with _state["lock"]:
                _state["queue"].append(("import", path))
                queued = len(_state["queue"])
            self._reply(HTTPStatus.OK, {"ok": True, "queued": queued})
            return

        if self.path == "/rigmode":
            fields = payload.get("fields") or {}
            if not fields:
                self._reply(HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "No fields to register."})
                return
            # Through the idle queue: this drives the open panel, and the
            # HTTP thread is not the one that owns it.
            try:
                answer = _Ask(lambda: register_rig_mode(
                    fields, payload.get("fingers"),
                    payload.get("twist"))).wait()
            except Exception as error:
                self._reply(HTTPStatus.OK,
                            {"ok": False,
                             "error": f"{type(error).__name__}: {error}"})
                return
            answer["ok"] = True
            self._reply(HTTPStatus.OK, answer)
            return

        if self.path == "/reset":
            _state["frames"] = 0
            _state["applied"] = 0
            _state["error"] = None
            self._reply(HTTPStatus.OK, {"ok": True})
            return

        try:
            path = _chunk_path(payload)
        except Exception as error:
            self._reply(HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": f"{type(error).__name__}: {error}"})
            return

        _state["append"] = bool(payload.get("append", True))
        with _state["lock"]:
            _state["queue"].append(path)
            queued = len(_state["queue"])
        self._reply(HTTPStatus.OK, {"ok": True, "queued": queued,
                                    "frames_so_far": _state["frames"]})


def _chunk_path(payload):
    """The chunk as a file on disk, whichever way it arrived."""
    path = payload.get("path")
    if path:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        return path

    names = payload.get("bone_names")
    rotations = payload.get("global_rot_mats")
    positions = payload.get("posed_joints")
    if not names or rotations is None or positions is None:
        raise ValueError("Need either 'path', or bone_names + global_rot_mats "
                         "+ posed_joints.")
    handle, temp = tempfile.mkstemp(suffix=".npz", prefix="cascadeur_pose_")
    os.close(handle)
    np.savez(
        temp,
        bone_names=np.array([str(n) for n in names]),
        bone_parents=np.array([str(n) for n in payload.get("bone_parents")
                               or [""] * len(names)]),
        neutral_joints=np.zeros((len(names), 3), dtype=np.float32),
        global_rot_mats=np.asarray(rotations, dtype=np.float32),
        posed_joints=np.asarray(positions, dtype=np.float32),
        fps=np.array(float(payload.get("fps") or 20.0)),
        space=np.array(str(payload.get("space") or "world")),
    )
    return temp


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def start(port=DEFAULT_PORT):
    if _state["server"] is not None:
        return False
    server = _Server(("127.0.0.1", int(port)), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _state.update({"server": server, "thread": thread, "scene_view": None,
                   "frames": 0, "applied": 0, "error": None})
    _state["subscription"] = scene_idle_manager.subscribe(_on_idle)
    try:
        _start_pump()
    except Exception as error:
        # Not fatal: reads fall back to the idle hook, just slowly.
        print("[Kimodo] read pump unavailable (%s); reads will be slower"
              % error)
    return True


def stop():
    _stop_pump()
    if _state["subscription"] is not None:
        try:
            scene_idle_manager.unsubscribe(_state["subscription"])
        except Exception:
            pass
        _state["subscription"] = None
    server = _state["server"]
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    _state["server"] = None
    _state["thread"] = None
    with _state["lock"]:
        waiting = _state["queue"]
        _state["queue"] = []
    # Their HTTP threads block in wait() up to 600 s; without this, a stop
    # mid-receive froze Blender for the rest of that timeout.
    for ask in waiting:
        ask.error = RuntimeError("receiver stopped")
        ask.done.set()
    return True


def running():
    return _state["server"] is not None


def busy():
    """True while a send is being applied to the scene.

    Anything else in Cascadeur that polls the scene on a timer has to stop
    while this is true. Importing a character creates and discards scenes,
    and a watchdog reading `current_scene()` in the middle of that is one of
    the two ways this application has been made to crash outright.
    """
    if _state["server"] is None:
        return False
    with _state["lock"]:
        if _state["queue"]:
            return True
    return bool(_state.get("applying"))


# `applying` may be absent on a receiver that was hot-reloaded from an older
# copy of this file; treat that as not busy rather than raising in a timer.
_state.setdefault("applying", False)


def run(scene):
    """Toggle the receiver from the commands menu."""
    if running():
        stop()
        _live_link(False)
        scene.info("Pose receiver stopped.")
        return
    try:
        start()
    except OSError as error:
        scene.error(f"Could not listen on port {DEFAULT_PORT}: {error}")
        return
    live = _live_link(True)
    scene.info(f"Listening for poses on 127.0.0.1:{DEFAULT_PORT}. "
               f"Send them from Blender."
               + (" Live mirroring is up too." if live else ""))


def _live_link(on):
    """Bring the live mirror up or down with the receiver.

    It listens on its own port, but it is not a separate thing to
    the person using it: one command, and both directions work.

    Blender can also hand the link over the script server, which is
    how it was reached before this shipped. That server is a
    developer tool and nobody animating has any reason to turn it
    on, so the link installs with the plug-in and starts here.

    Never fatal. The pose receiver is the older and more important
    half; if the live link will not start, the receiver still must.
    """
    try:
        import live_link
    except Exception:
        try:
            from . import live_link
        except Exception:
            return False
    try:
        if on:
            live_link.start()
        else:
            live_link.stop()
        return True
    except Exception:
        return False
