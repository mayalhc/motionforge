"""MotionBricks SOMA: fill the frames between two poses.

NVIDIA's SOMA model takes four poses that came before and up to four poses to
arrive at, and writes the motion in between. It ships as one ONNX graph, so
this runs it on ONNX Runtime's CPU provider in numpy - no Warp, no TensorRT,
no GPU. The input encoding and output decoding follow the published tensor
contract (NVlabs/motionbricks v0.2.0, Apache-2.0); the weights are NVIDIA's
and travel with their licence.

The model's joints are the 30 of `soma30`, but each joint's frame is aligned to
its bone, where Kimodo's is the standard T-pose frame. `soma_offsets.json`
holds the per-joint rotation between the two (Kimodo's
`standard_t_pose_global_offsets_rots`).
"""

import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import zipfile

import numpy as np

from . import soma30

SAMPLE_RATE = 30.0
POSES_PER_TOKEN = 4
BOUNDARY = POSES_PER_TOKEN - 1
SLOTS = 8
MIN_TOKENS, MAX_TOKENS = 6, 16
MAX_TARGETS = 4
UP = np.array([0.0, 1.0, 0.0])

MODEL_NAME = "mmm_v6_soma_fp16.onnx"
MODEL_URL = ("https://github.com/NVlabs/motionbricks/raw/"
             "54fc563aeaaf819a753d72cc9fdc6713989cdd43/"
             "motionbricks/assets/onnx/soma/" + MODEL_NAME)
MODEL_BYTES = 380327058
MODEL_SHA256 = "6c9f78e23decbda8e1cfd1b0cc96980ea4b24b3ca2c808d03b058f48f82fc9ef"

# ONNX Runtime's CPU wheel per CPython, from PyPI at a pinned version.
ORT_VERSION = "1.31.1"
ORT_WHEELS = {
    (3, 11): ("onnxruntime-1.31.1-cp311-cp311-win_amd64.whl",
              "c3d91b5de31e2fc82d71bd65b2b22bddee8b49057a3a36338e9a868e0ea90c05"),
    (3, 12): ("onnxruntime-1.31.1-cp312-cp312-win_amd64.whl",
              "ae8a9a351efda685cc59a66226cdefaa37a126c1d66872ca23a2de9425326130"),
    (3, 13): ("onnxruntime-1.31.1-cp313-cp313-win_amd64.whl",
              "0558f29d5f6ac09957390c9c5495931485ee8ae7f0ce781ccee86a0ae5c2348b"),
}

# The person's styles are published beside the user guide, with a manifest that
# says each file's size and SHA-256.
STYLES_URL = "https://raw.githubusercontent.com/mayalhc/motionforge/main/soma-styles"
STYLE_NAME = re.compile(r"^[A-Za-z0-9_-]+\.npz$")

USER_AGENT = "MotionForge"
BLOCK = 8 << 20


class SomaError(RuntimeError):
    pass


# --- where it lives --------------------------------------------------------

def folder(prefs):
    """The install folder, or "" when no Engine Folder is set."""
    from . import engines

    base = engines.folder(prefs, "motionbricks")
    return os.path.join(base, "soma") if base else ""


def _model_path(root):
    return os.path.join(root, MODEL_NAME)


def _ort_path(root, version=None):
    """One folder per CPython: a Blender 5.0 and a 5.2 can share the install."""
    major, minor = version or sys.version_info[:2]
    return os.path.join(root, "ort-" + ORT_VERSION, "py%d%d" % (major, minor))


_PROBLEMS = {}


def invalidate():
    _PROBLEMS.clear()


def problem(prefs):
    """One line for the UI, or "" when the model and the runtime are in place.

    The panel asks on every redraw, so the answer is kept until an install or
    a reload says the folder changed.
    """
    root = folder(prefs)
    if root not in _PROBLEMS:
        if not root:
            found = "Choose the Engine Folder in Preferences first."
        elif not os.path.isfile(_model_path(root)):
            found = "The SOMA in-between model is not installed."
        elif not os.path.isdir(os.path.join(_ort_path(root), "onnxruntime")):
            found = "ONNX Runtime is not installed."
        else:
            found = ""
        _PROBLEMS[root] = found
    return _PROBLEMS[root]


# --- install ---------------------------------------------------------------

def _digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(BLOCK), b""):
            value.update(block)
    return value.hexdigest()


def _fetch(url, destination, progress=None):
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temporary = destination + ".download"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(temporary, "wb") as output:
                while True:
                    block = response.read(BLOCK)
                    if not block:
                        break
                    output.write(block)
                    done += len(block)
                    if progress is not None:
                        progress(done, total)
        os.replace(temporary, destination)
    finally:
        if os.path.isfile(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def _wheel_url(filename):
    request = urllib.request.Request(
        "https://pypi.org/pypi/onnxruntime/%s/json" % ORT_VERSION,
        headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        release = json.load(response)
    for entry in release["urls"]:
        if entry["filename"] == filename:
            return entry["url"]
    raise SomaError("PyPI no longer lists %s." % filename)


def install_runtime(root, report):
    """Every pinned wheel, so each Blender that shares this folder finds its own."""
    if sys.platform != "win32":
        raise SomaError("The in-between install is Windows only for now.")
    wanted = [v for v in ORT_WHEELS
              if not os.path.isdir(os.path.join(_ort_path(root, v), "onnxruntime"))]
    if not wanted:
        report("ONNX Runtime already present.")
        return
    for version in wanted:
        filename, sha256 = ORT_WHEELS[version]
        path = os.path.join(root, filename)
        report("ONNX Runtime for Python %d.%d" % version, 0.0)
        _fetch(_wheel_url(filename), path)
        try:
            if _digest(path) != sha256:
                raise SomaError("%s failed its hash check." % filename)
            target = _ort_path(root, version)
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    # Only the package itself; its other dependency is
                    # Blender's own numpy.
                    if name.startswith("onnxruntime/") and ".." not in name:
                        archive.extract(name, target)
        finally:
            os.remove(path)


def install_model(root, report, cancelled=None):
    path = _model_path(root)
    if (os.path.isfile(path) and os.path.getsize(path) == MODEL_BYTES
            and _digest(path) == MODEL_SHA256):
        report("SOMA model already verified.")
        return

    def on_block(done, total):
        if cancelled is not None and cancelled():
            raise SomaError("Cancelled.")
        report("%s: %.0f of %.0f MB" % (MODEL_NAME, done / (1 << 20),
                                        (total or MODEL_BYTES) / (1 << 20)),
               done / (total or MODEL_BYTES))

    report("%s (%.0f MB)" % (MODEL_NAME, MODEL_BYTES / (1 << 20)), 0.0)
    _fetch(MODEL_URL, path, on_block)
    if os.path.getsize(path) != MODEL_BYTES or _digest(path) != MODEL_SHA256:
        os.remove(path)
        raise SomaError("%s failed its size or hash check." % MODEL_NAME)


PUBLISHED = ".published.json"


def _published(folder):
    try:
        with open(os.path.join(folder, PUBLISHED), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _style_manifest():
    request = urllib.request.Request(STYLES_URL + "/manifest.json", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        manifest = json.load(response)
    files = manifest.get("files")
    if manifest.get("format") != "motionforge-soma-styles-v1" or not isinstance(files, list):
        raise SomaError("The styles manifest is not one this add-on reads.")
    for entry in files:
        if (not STYLE_NAME.match(str(entry.get("path", ""))) or not isinstance(entry.get("bytes"), int)
                or not isinstance(entry.get("sha256"), str)):
            raise SomaError("unexpected record in the styles manifest.")
    return files


def _style_state(folder, entry, published):
    """missing, current, outdated (ours, unchanged, a newer one is published) or yours."""
    destination = os.path.join(folder, entry["path"])
    if not os.path.isfile(destination):
        return "missing"
    here = _digest(destination)
    if here == entry["sha256"]:
        return "current"
    # One we installed and nobody has edited is ours to update; anything else is the user's.
    return "outdated" if published.get(entry["path"]) == here else "yours"


def style_changes(root):
    """How many of the published styles are missing or have a newer version."""
    folder = os.path.join(root, "styles")
    published = _published(folder)
    return sum(1 for entry in _style_manifest()
               if _style_state(folder, entry, published) in ("missing", "outdated"))


def install_styles(root, report, cancelled=None):
    """The person's styles from the guide's repository. Returns (fetched, updated).

    Missing ones are fetched. One we installed earlier and nobody has changed is
    replaced when a newer one is published; a style of the same name that the
    user made or edited is never overwritten. Every file is checked against the
    manifest before it counts.
    """
    folder = os.path.join(root, "styles")
    os.makedirs(folder, exist_ok=True)
    files = _style_manifest()
    published = _published(folder)
    fetched = updated = 0
    for index, entry in enumerate(files, 1):
        name = entry["path"]
        state = _style_state(folder, entry, published)
        destination = os.path.join(folder, name)
        if state in ("current", "yours"):
            if state == "current":
                published[name] = entry["sha256"]
            continue
        if cancelled is not None and cancelled():
            raise SomaError("Cancelled.")
        report("style %s%s (%d of %d)" % ("update " if state == "outdated" else "", name[:-4], index, len(files)),
               index / float(len(files)))
        _fetch(STYLES_URL + "/" + urllib.parse.quote(name), destination)
        if os.path.getsize(destination) != entry["bytes"] or _digest(destination) != entry["sha256"]:
            os.remove(destination)
            raise SomaError("%s failed its size or hash check." % name)
        published[name] = entry["sha256"]
        fetched += 1
        updated += 1 if state == "outdated" else 0
    with open(os.path.join(folder, PUBLISHED), "w", encoding="utf-8") as handle:
        json.dump(published, handle, indent=1)
    return fetched, updated


def install(root, report, cancelled=None):
    """Runtime, model, styles and licences into `root`/soma."""
    from . import mb_install

    root = (root or "").strip()
    if not root:
        raise SomaError("Choose a folder to install into first.")
    os.makedirs(root, exist_ok=True)
    install_runtime(root, report)
    # The runtime used to live in a folder without its version in the name.
    shutil.rmtree(os.path.join(root, "ort"), ignore_errors=True)
    install_model(root, report, cancelled)
    fetched, updated = install_styles(root, report, cancelled)
    mb_install.copy_licenses(root)
    note = "SOMA in-between ready."
    if updated:
        note += " %d style(s) updated." % updated
    return note


def changes(root):
    """What an install would fetch or replace, without fetching: a list of short phrases."""
    found = []
    model = _model_path(root)
    if not os.path.isfile(model) or os.path.getsize(model) != MODEL_BYTES:
        found.append("the SOMA model")
    if not os.path.isdir(os.path.join(_ort_path(root), "onnxruntime")):
        found.append("ONNX Runtime")
    count = style_changes(root)
    if count:
        found.append("%d SOMA style(s)" % count)
    return found


# --- the model -------------------------------------------------------------

_OFFSETS = None
_WORKERS = {}


def offsets():
    """[30,3,3] model frame from Kimodo's: R_model = R_kimodo @ O."""
    global _OFFSETS
    if _OFFSETS is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "soma_offsets.json")
        with open(path, "r", encoding="utf-8") as handle:
            _OFFSETS = np.array(json.load(handle), np.float64).reshape(-1, 3, 3)
    return _OFFSETS


def _python():
    """Blender's bundled interpreter, which is not the process we are in."""
    return os.path.join(sys.prefix, "bin", "python.exe")


def _write(stream, arrays):
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    data = buffer.getvalue()
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def _read(stream):
    head = stream.read(4)
    if len(head) < 4:
        raise SomaError("The ONNX Runtime process stopped.")
    (size,) = struct.unpack("<I", head)
    data = stream.read(size)
    with np.load(io.BytesIO(data)) as loaded:
        return {key: loaded[key] for key in loaded.files}


def worker(prefs):
    """The ONNX Runtime process for the installed model, started once."""
    reason = problem(prefs)
    if reason:
        raise SomaError(reason)
    root = folder(prefs)
    process = _WORKERS.get(root)
    if process is None or process.poll() is not None:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "mb_soma_worker.py")
        process = subprocess.Popen(
            [_python(), "-I", script, _ort_path(root), _model_path(root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=0x08000000)
        _WORKERS[root] = process
        try:
            _read(process.stdout)
        except SomaError:
            _WORKERS.pop(root, None)
            raise SomaError("ONNX Runtime would not start.")
    return process


def run(prefs, inputs):
    process = worker(prefs)
    _write(process.stdin, inputs)
    return _read(process.stdout)


def release():
    for process in _WORKERS.values():
        try:
            process.stdin.close()
            process.terminate()
        except OSError:
            pass
    _WORKERS.clear()
    invalidate()


def _ry(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _heading(right, left):
    """The floor direction the pelvis faces, from the two hip joints."""
    across = np.array([right[0] - left[0], 0.0, right[2] - left[2]])
    length = np.linalg.norm(across)
    if length <= 1e-6:
        return 0.0
    across /= length
    forward = np.cross(UP, across)
    return math.atan2(forward[0], forward[2])


def _six_to_matrix(six):
    x = six[0:3] / np.linalg.norm(six[0:3])
    z = np.cross(x, six[3:6])
    z /= np.linalg.norm(z)
    y = np.cross(z, x)
    return np.stack([x, y, z], axis=1)


_RIGHT = soma30.NAMES.index("RightLeg")
_LEFT = soma30.NAMES.index("LeftLeg")
_COUNT = len(soma30.NAMES)


def encode(positions, rotations, targets):
    """Model inputs and the alignment to undo afterwards.

    positions [8,30,3] and rotations [8,30,3,3] are the four context poses and
    four target slots, Kimodo's frame; slots past `targets` repeat the last
    pose given. Alignment is (origin, angle) of the last context pose.
    """
    rotations = rotations @ offsets()
    hips = positions[POSES_PER_TOKEN - 1][0]
    current = _heading(positions[POSES_PER_TOKEN - 1][_RIGHT],
                       positions[POSES_PER_TOKEN - 1][_LEFT])
    angle = current - math.atan2(1.0, 0.0)
    origin = np.array([hips[0], 0.0, hips[2]])
    inverse = _ry(angle).T

    def place(p):
        return inverse @ (p - origin)

    g = np.zeros((1, SLOTS, 5), np.float32)
    l = np.zeros((1, SLOTS, 4), np.float32)
    poses = np.zeros((1, SLOTS, (_COUNT - 1) * 3 + _COUNT * 6), np.float32)
    for frame in range(SLOTS):
        nxt = frame if frame == POSES_PER_TOKEN - 1 else min(frame + 1, SLOTS - 1)
        cur_h = place(positions[frame][0])
        nxt_h = place(positions[nxt][0])
        cur_heading = _heading(place(positions[frame][_RIGHT]), place(positions[frame][_LEFT]))
        nxt_heading = _heading(place(positions[nxt][_RIGHT]), place(positions[nxt][_LEFT]))
        delta = math.atan2(math.sin(nxt_heading - cur_heading), math.cos(nxt_heading - cur_heading))
        g[0, frame, :3] = cur_h
        g[0, frame, 3] = math.cos(cur_heading)
        g[0, frame, 4] = math.sin(cur_heading)
        l[0, frame] = (delta * SAMPLE_RATE, (nxt_h[0] - cur_h[0]) * SAMPLE_RATE,
                       (nxt_h[2] - cur_h[2]) * SAMPLE_RATE, cur_h[1])
        ground = np.array([cur_h[0], 0.0, cur_h[2]])
        for joint in range(_COUNT):
            if joint > 0:
                poses[0, frame, (joint - 1) * 3:(joint - 1) * 3 + 3] = place(positions[frame][joint]) - ground
            rot = inverse @ rotations[frame][joint]
            at = (_COUNT - 1) * 3 + joint * 6
            poses[0, frame, at:at + 3] = rot[:, 0]
            poses[0, frame, at + 3:at + 6] = rot[:, 1]

    given = POSES_PER_TOKEN + targets
    has = np.array([[1 if i < given else 0 for i in range(SLOTS)]], np.int32)
    has_local_root = has.copy()
    has_local_root[0, POSES_PER_TOKEN - 1] = 0
    has_local_root[0, given - 1] = 0
    return {"global_root_values": g, "has_global_root_values": has,
            "local_root_values": l, "has_local_root_values": has_local_root,
            "local_poses": poses, "has_local_poses": has}, (origin, angle)


def allowed_bits(tokens):
    """The token counts the model may pick: one number, or a (smallest, largest) pair."""
    low, high = (tokens, tokens) if isinstance(tokens, (int, np.integer)) else tokens
    low, high = max(MIN_TOKENS, int(low)), min(MAX_TOKENS, int(high))
    bits = np.zeros((1, MAX_TOKENS - MIN_TOKENS + 1), np.int32)
    bits[0, low - MIN_TOKENS:high - MIN_TOKENS + 1] = 1
    return bits


def decode(output, alignment, index=0):
    """(hips [F,3], rotations [F,30,3,3], contacts [F,4]) in Kimodo's frame."""
    tokens = int(output["pred_num_tokens"][index])
    motion = output["pred_global_motions"][index]
    frames = max(0, tokens * POSES_PER_TOKEN - 2 * BOUNDARY)
    origin, angle = alignment
    rotate = _ry(angle)
    at = 5 + (_COUNT - 1) * 3
    hips = np.zeros((frames, 3))
    rotations = np.zeros((frames, _COUNT, 3, 3))
    contacts = np.zeros((frames, 4))
    inverse = np.transpose(offsets(), (0, 2, 1))
    for frame in range(frames):
        raw = motion[BOUNDARY + frame]
        hips[frame] = origin + rotate @ raw[0:3]
        for joint in range(_COUNT):
            six = raw[at + joint * 6:at + joint * 6 + 6]
            rotations[frame, joint] = rotate @ _six_to_matrix(six) @ inverse[joint]
        contacts[frame] = np.clip(raw[-4:], 0.0, 1.0)
    return hips, rotations, contacts


def frames_for(tokens):
    return tokens * POSES_PER_TOKEN - 2 * BOUNDARY


def tokens_for(frames):
    """The token count whose output is nearest `frames`, or None if out of reach."""
    tokens = int(round((frames + 2 * BOUNDARY) / POSES_PER_TOKEN))
    if tokens < MIN_TOKENS or tokens > MAX_TOKENS:
        return None
    return tokens


def _request(context_pos, context_rot, target_pos, target_rot, tokens):
    """The model inputs for one character, and the alignment to undo afterwards."""
    count = len(target_pos)
    if len(context_pos) != POSES_PER_TOKEN or not 1 <= count <= MAX_TARGETS:
        raise SomaError("Need four context poses and one to four targets.")
    pad = SLOTS - POSES_PER_TOKEN - count
    positions = np.concatenate([context_pos, target_pos] + [target_pos[-1:]] * pad)
    rotations = np.concatenate([context_rot, target_rot] + [target_rot[-1:]] * pad)
    inputs, alignment = encode(positions, rotations, count)
    inputs["allowed_pred_num_tokens"] = allowed_bits(tokens)
    return inputs, alignment


def generate_many(prefs, requests):
    """Several characters in one model call.

    `requests` are (context_pos, context_rot, target_pos, target_rot, tokens)
    tuples, each as `generate` takes them; every one keeps its own targets and
    token counts. Returns one (hips, rotations, contacts) per request.
    """
    built = [_request(*request) for request in requests]
    stacked = {key: np.concatenate([inputs[key] for inputs, _ in built]) for key in built[0][0]}
    output = run(prefs, stacked)
    return [decode(output, alignment, index) for index, (_, alignment) in enumerate(built)]


_LOCAL = threading.local()


class Batcher:
    """Lets code written for one character at a time share model calls.

    Each of `parties` threads plans one character and calls `generate` as if it
    were alone. A call waits until every thread still planning has asked for
    something, and then the asks go to the model together. A thread that is
    finished tells the batcher so nobody waits for it.
    """

    def __init__(self, prefs, parties):
        self.prefs = prefs
        self.parties = parties
        self._pending = []
        self._condition = threading.Condition()

    def ask(self, request):
        slot = {"request": request, "result": None, "error": None, "done": False}
        with self._condition:
            self._pending.append(slot)
            self._maybe_run()
            while not slot["done"]:
                self._condition.wait()
        if slot["error"] is not None:
            raise slot["error"]
        return slot["result"]

    def finished(self):
        with self._condition:
            self.parties -= 1
            self._maybe_run()

    def _maybe_run(self):
        """Run the batch when everyone still planning is waiting on it. Lock held."""
        if not self._pending or len(self._pending) < self.parties:
            return
        batch, self._pending = self._pending, []
        try:
            results = generate_many(self.prefs, [slot["request"] for slot in batch])
            for slot, result in zip(batch, results):
                slot["result"] = result
        except Exception as error:  # noqa: BLE001 - handed to every thread that asked
            for slot in batch:
                slot["error"] = error
        for slot in batch:
            slot["done"] = True
        self._condition.notify_all()


def use_batcher(batcher):
    """Route this thread's `generate` calls through a batcher, or None to stop."""
    _LOCAL.batcher = batcher


def generate(prefs, context_pos, context_rot, target_pos, target_rot, tokens):
    """One generation.

    context_* hold the four poses before the gap, target_* one to four poses
    to arrive at, all [n,30,...] in Kimodo's frame. Returns (hips, rotations,
    contacts) for frames_for(tokens) frames.
    """
    request = (context_pos, context_rot, target_pos, target_rot, tokens)
    batcher = getattr(_LOCAL, "batcher", None)
    if batcher is not None:
        return batcher.ask(request)
    return generate_many(prefs, [request])[0]


def joint_positions(hips, rotations):
    """World joint positions [F,30,3] from the root and global rotations."""
    skeleton = soma30.Skeleton()
    parents = skeleton.parents
    rest = np.array(skeleton.neutral)
    out = np.zeros((len(hips), _COUNT, 3))
    out[:, 0] = hips
    for joint in range(1, _COUNT):
        offset = rest[joint] - rest[parents[joint]]
        out[:, joint] = out[:, parents[joint]] + np.einsum(
            "fab,b->fa", rotations[:, parents[joint]], offset)
    return out


def _quaternion(matrix):
    """A rotation matrix as (w, x, y, z), w >= 0."""
    m = np.asarray(matrix, np.float64)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0.0:
        s = 2.0 * math.sqrt(trace + 1.0)
        q = np.array([0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = np.array([(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s])
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = np.array([(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s])
    else:
        s = 2.0 * math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = np.array([(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s])
    q /= np.linalg.norm(q)
    return -q if q[0] < 0.0 else q


def _part_turn(quaternion, weight):
    """The rotation matrix `weight` of the way from the identity to `quaternion`."""
    w = min(1.0, max(-1.0, quaternion[0]))
    theta = math.acos(w)
    sine = math.sin(theta)
    if sine < 1e-8:
        q = np.array([1.0, 0.0, 0.0, 0.0])
    else:
        q = (math.sin((1.0 - weight) * theta) * np.array([1.0, 0.0, 0.0, 0.0])
             + math.sin(weight * theta) * quaternion) / sine
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def pin_ends(rotations, hips, start=None, end=None, fade=12):
    """The model's frames, nudged to begin and end on given poses.

    `start` and `end` are (rotations [30,3,3], hips [3]) or None. The model
    lands within 2 cm of a pose it knows and 20 cm off one it does not; the
    offset at an end is faded out over the nearest dozen frames.
    """
    count = len(rotations)
    fade = max(1, min(fade, count // 2))
    rotations = rotations.copy()
    hips = hips.copy()
    for want, at, step in ((start, 0, 1), (end, count - 1, -1)):
        if want is None:
            continue
        want_rot, want_hips = want
        offsets = [_quaternion(want_rot[j] @ rotations[at, j].T)
                   for j in range(rotations.shape[1])]
        move = want_hips - hips[at]
        for k in range(fade):
            weight = 1.0 - k / float(fade)
            index = at + step * k
            hips[index] += move * weight
            for j, offset in enumerate(offsets):
                rotations[index, j] = _part_turn(offset, weight) @ rotations[index, j]
    return rotations, hips
