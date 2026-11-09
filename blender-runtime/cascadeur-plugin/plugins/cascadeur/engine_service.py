# Copyright (c) 2026 Chamiseul. All rights reserved.
"""ARDY and Kimodo for Cascadeur, on the same engine MotionForge uses.

Runs <engine>/ardy/bin/ardy-server.exe and <engine>/kimodo/bin/kmd-generate.exe
and answers the HTTP calls the Cascadeur panels make. Python standard library
only, so any interpreter will do.

    POST /kimodo/generate {"takes": [{"prompt", "frames"}], "seed", "diffusion_steps",
                           "transition_frames"}  -> {"chunk_path", "frames", "fps", "segments"}

    GET  /health
    POST /stream/start   {"prompt", "seed", "diffusion_steps", "num_frames", "history_frames"}
    POST /stream/step    {"stream_id"}            -> {"chunk_path", "frame_start", "frames"}
    POST /stream/stop    {"stream_id"}
    POST /takes/generate {"takes": [{"prompt", "frames"}], "seed", "diffusion_steps",
                          "history_frames", "transition_frames"}
    POST /shutdown

A chunk is an .npz holding bone_names, global_rot_mats, posed_joints and fps,
which is what ardy_apply.py puts on the rig.
"""

import argparse
import ast
import json
import os
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from array import array
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MODEL_NAME = "ardy-core-rp-20fps-h40-f32.gguf"
SERVER_NAME = "ardy-server.exe"
PROBE_NAME = "ardy-generate.exe"
FALLBACK = "cpu-fallback"
STALE_AFTER = 24 * 3600


def log(text):
    try:
        print(text, flush=True)
    except Exception:
        pass


# -- npz, without numpy --------------------------------------------------

def _npy(descr, shape, payload):
    header = "{'descr': '%s', 'fortran_order': False, 'shape': %s, }" % (
        descr, "(%s)" % "".join("%d, " % n for n in shape) if shape else "()")
    header += " " * (63 - (10 + len(header)) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin1") + payload


def _write_npz(path, arrays):
    """arrays: name -> ("f4" | "f8" | "U", shape, values)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as bundle:
        for name, (kind, shape, values) in arrays.items():
            if kind == "U":
                width = max(1, max(len(v) for v in values))
                data = b"".join(v.ljust(width, "\0").encode("utf-32-le") for v in values)
                blob = _npy("<U%d" % width, shape, data)
            elif kind == "f8":
                blob = _npy("<f8", shape, struct.pack("<%dd" % len(values), *values))
            else:
                values = values if isinstance(values, array) else array("f", values)
                if sys.byteorder == "big":
                    values = array("f", values)
                    values.byteswap()
                blob = _npy("<f4", shape, values.tobytes())
            bundle.writestr(name + ".npy", blob)


def _read_npz(path):
    out = {}
    with zipfile.ZipFile(path) as bundle:
        for entry in bundle.namelist():
            raw = bundle.read(entry)
            length = struct.unpack("<H", raw[8:10])[0]
            head = ast.literal_eval(raw[10:10 + length].decode("latin1"))
            body = raw[10 + length:]
            shape = tuple(head["shape"])
            if head["descr"] == "<f4":
                values = array("f")
                values.frombytes(body)
            elif head["descr"] == "<f8":
                values = array("d")
                values.frombytes(body)
            else:
                continue
            out[entry[:-4]] = (shape, values)
    return out


def _need_numpy(what):
    try:
        import numpy  # noqa: F401
    except ImportError:
        raise RuntimeError("%s needs numpy, and this Python (%s) has none. Run: \"%s\" -m pip install numpy"
                           % (what, sys.version.split()[0], sys.executable))


def _quat_matrix(x, y, z, w):
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [[1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)]]


def _soma_fk(rotations, roots, frames):
    """SOMA-30 local rotations (frames x 30 x xyzw) to global matrices and joints."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import soma30_skeleton as soma
    parents, offsets = soma.PARENTS, soma.OFFSETS
    count = len(parents)
    mats, joints = array("f"), array("f")
    for frame in range(frames):
        base = frame * count * 4
        grot, gpos = [None] * count, [None] * count
        for j in range(count):
            q = base + j * 4
            local = _quat_matrix(rotations[q], rotations[q + 1], rotations[q + 2], rotations[q + 3])
            parent = parents[j]
            if parent < 0:
                grot[j] = local
                gpos[j] = [roots[frame * 3], roots[frame * 3 + 1], roots[frame * 3 + 2]]
            else:
                p = grot[parent]
                grot[j] = [[p[r][0] * local[0][c] + p[r][1] * local[1][c] + p[r][2] * local[2][c]
                            for c in range(3)] for r in range(3)]
                o = offsets[j]
                gpos[j] = [gpos[parent][k] + p[k][0] * o[0] + p[k][1] * o[1] + p[k][2] * o[2]
                           for k in range(3)]
            for row in grot[j]:
                mats.extend(row)
            joints.extend(gpos[j])
    return mats, joints


def _flat(nested, into):
    for item in nested:
        if isinstance(item, list):
            _flat(item, into)
        else:
            into.append(item)
    return into


# -- the ardy-server process ---------------------------------------------

class Engine:
    def __init__(self, root):
        self.root = root
        self.folder = os.path.join(root, "ardy")
        self.proc = None
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.next_id = 1
        self.ready = False
        self.loading = False
        self.error = ""
        self.backend = ""
        self.info = {}
        self.bones = []

    def exe(self):
        bin_dir = os.path.join(self.folder, "bin")
        main = os.path.join(bin_dir, SERVER_NAME)
        twin = os.path.join(bin_dir, FALLBACK, SERVER_NAME)
        probe = os.path.join(bin_dir, PROBE_NAME)
        label = ""
        if os.path.isfile(probe):
            try:
                run = subprocess.run([probe, "--probe-backend"], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=60,
                                     creationflags=NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                run = None
            if run is not None and run.returncode == 0:
                for line in run.stdout.splitlines():
                    if line.startswith("backend="):
                        label = line[len("backend="):].strip()
            elif os.path.isfile(twin):
                self.backend = "cpu"
                return twin
        self.backend = label
        if os.path.isfile(main):
            return main
        self.backend = "cpu"
        return twin

    def paths(self):
        model = os.path.join(self.folder, "models", MODEL_NAME)
        bundle = os.path.join(self.root, "kimodo", "generated", "llm2vec-text-bundle")
        skin = os.path.join(self.folder, "assets", "cskel27", "skin_standard.npz")
        missing = [p for p in (model, bundle) if not os.path.exists(p)]
        if missing:
            raise RuntimeError("The engine folder is missing " + ", ".join(missing)
                               + ". Press Install Engines in MotionForge's preferences.")
        return model, bundle, skin

    def start(self):
        self.loading = True
        try:
            exe = self.exe()
            model, bundle, skin = self.paths()
            cmd = [exe, "--model", model, "--text-bundle", bundle]
            if os.path.isfile(skin):
                cmd += ["--skin", skin]
            log("Starting %s (%s)" % (os.path.basename(exe), self.backend or "backend unknown"))
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW)
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()
            while True:
                try:
                    event = self.events.get(timeout=1.0)
                except queue.Empty:
                    if self.proc.poll() is not None:
                        raise RuntimeError(self.error or "ardy-server exited while loading.")
                    continue
                kind = event.get("event")
                if kind == "progress":
                    log(event.get("message", ""))
                elif kind == "ready":
                    self.info = {k: v for k, v in event.items() if k != "event"}
                    break
                elif kind == "error" and event.get("fatal"):
                    raise RuntimeError(event.get("message", "ardy-server failed to start."))
            self.ready = True
            self.bones = self.request("skeleton", 60.0)["bones"]
            log("ARDY live service is ready.")
        except Exception as error:
            self.error = str(error)
            log("Load failed: " + self.error)
        finally:
            self.loading = False

    def _pump(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.events.put(json.loads(line))
            except ValueError:
                log(line)

    def _drain(self, proc):
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                log(line)
                if "rror" in line:
                    self.error = line

    def request(self, cmd, timeout=300.0, **payload):
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError("ardy-server is not running.")
        if not self.ready:
            raise RuntimeError("ARDY is still loading.")
        with self.lock:
            req_id = self.next_id
            self.next_id += 1
            message = {"cmd": cmd, "id": req_id}
            message.update(payload)
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Timed out waiting for %r." % cmd)
                try:
                    event = self.events.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    if self.proc.poll() is not None:
                        raise RuntimeError("ardy-server exited.")
                    continue
                kind = event.get("event")
                if kind == "result" and event.get("id") == req_id:
                    return event
                if kind == "error" and event.get("id") == req_id:
                    raise RuntimeError(event.get("message", "unknown error"))

    def stop(self):
        proc, self.proc = self.proc, None
        self.ready = False
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
            proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


class Kimodo:
    """kmd-generate.exe in --server mode: the model loads once, takes follow."""

    MODEL = "kimodo-soma-rp-v1.1-f32.gguf"
    FPS = 30.0

    def __init__(self, root, work):
        self.root = root
        self.work = work
        self.folder = os.path.join(root, "kimodo")
        self.proc = None
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.backend = ""
        self.error = ""

    def exe(self):
        bin_dir = os.path.join(self.folder, "bin")
        main = os.path.join(bin_dir, "kmd-generate.exe")
        twin = os.path.join(bin_dir, FALLBACK, "kmd-generate.exe")
        label = ""
        if os.path.isfile(main):
            try:
                run = subprocess.run([main, "--probe-backend"], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=60,
                                     creationflags=NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                run = None
            if run is not None and run.returncode == 0:
                for line in run.stdout.splitlines():
                    if line.startswith("backend="):
                        label = line[len("backend="):].strip()
                self.backend = label
                return main
        if os.path.isfile(twin):
            self.backend = "cpu"
            return twin
        raise RuntimeError("Kimodo is not installed in %s. Press Install Engines in "
                           "MotionForge's preferences." % self.folder)

    def paths(self):
        models = os.path.join(self.folder, "models")
        model = os.path.join(models, self.MODEL)
        if not os.path.isfile(model) and os.path.isdir(models):
            found = sorted(n for n in os.listdir(models) if n.endswith(".gguf"))
            model = os.path.join(models, found[0]) if found else model
        bundle = os.path.join(self.folder, "generated", "llm2vec-text-bundle")
        missing = [p for p in (model, bundle) if not os.path.exists(p)]
        if missing:
            raise RuntimeError("The engine folder is missing " + ", ".join(missing))
        return model, bundle

    def _ensure(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        exe = self.exe()
        model, bundle = self.paths()
        log("Starting %s (%s)" % (os.path.basename(exe), self.backend or "backend unknown"))
        self.events = queue.Queue()
        self.proc = subprocess.Popen(
            [exe, "--server", model, bundle], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, args=(self.proc, self.events), daemon=True).start()
        threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()

    def _pump(self, proc, events):
        for line in proc.stdout:
            events.put(line.rstrip("\r\n"))

    def _drain(self, proc):
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                log(line)

    def generate(self, segments, transition, steps, seed):
        """segments: [(frames, prompt)]. Returns (frames, roots, rotations) as flat floats."""
        with self.lock:
            self._ensure()
            job = os.path.join(self.work, "kimodo_%s" % uuid.uuid4().hex[:12])
            os.makedirs(job)
            fields = [str(transition), str(steps), str(seed), os.path.join(job, "out")]
            for index, (frames, prompt) in enumerate(segments):
                path = os.path.join(job, "prompt_%d.txt" % index)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(prompt)
                fields += [str(frames), path]
            self.proc.stdin.write("\t".join(fields) + "\n")
            self.proc.stdin.flush()
            deadline = time.monotonic() + 1800
            while True:
                try:
                    line = self.events.get(timeout=1.0)
                except queue.Empty:
                    if self.proc.poll() is not None:
                        self.proc = None
                        raise RuntimeError("kmd-generate exited.")
                    if time.monotonic() > deadline:
                        raise RuntimeError("Timed out waiting for Kimodo.")
                    continue
                parts = line.split("\t")
                if parts[0] == "ERR":
                    raise RuntimeError(parts[1] if len(parts) > 1 else "Kimodo failed.")
                if parts[0] == "OK":
                    frames, joints = int(parts[1]), int(parts[2])
                    break
            if joints != 30:
                raise RuntimeError("Kimodo produced %d joints; this service reads the SOMA-30 model." % joints)
            roots, rotations = array("f"), array("f")
            with open(os.path.join(job, "out", "root_positions.f32"), "rb") as handle:
                roots.frombytes(handle.read())
            with open(os.path.join(job, "out", "local_rotations_xyzw.f32"), "rb") as handle:
                rotations.frombytes(handle.read())
            import shutil
            shutil.rmtree(job, ignore_errors=True)
            return frames, roots, rotations

    def constrained(self, prompt, frames, steps, seed, text_cfg, constraint_cfg, poses):
        """poses: [(frame, positions[30*3], rotations[30*9])]. Returns (frames, roots, rotations)."""
        with self.lock:
            self._ensure()
            job = os.path.join(self.work, "kimodo_%s" % uuid.uuid4().hex[:12])
            os.makedirs(job)
            prompt_path = os.path.join(job, "prompt.txt")
            with open(prompt_path, "w", encoding="utf-8") as handle:
                handle.write(prompt)
            poses_path = os.path.join(job, "poses.bin")
            with open(poses_path, "wb") as handle:
                handle.write(struct.pack("<ii", len(poses), 30))
                for frame, positions, rotations in poses:
                    handle.write(struct.pack("<i", int(frame)))
                    handle.write(array("f", positions).tobytes())
                    handle.write(array("f", rotations).tobytes())
            fields = ["C", str(steps), str(seed), os.path.join(job, "out"), str(frames), prompt_path,
                      "%g" % text_cfg, "%g" % constraint_cfg, poses_path]
            self.proc.stdin.write("\t".join(fields) + "\n")
            self.proc.stdin.flush()
            deadline = time.monotonic() + 1800
            while True:
                try:
                    line = self.events.get(timeout=1.0)
                except queue.Empty:
                    if self.proc.poll() is not None:
                        self.proc = None
                        raise RuntimeError("kmd-generate exited.")
                    if time.monotonic() > deadline:
                        raise RuntimeError("Timed out waiting for Kimodo.")
                    continue
                parts = line.split("\t")
                if parts[0] == "ERR":
                    raise RuntimeError(parts[1] if len(parts) > 1 else "Kimodo failed.")
                if parts[0] == "OK":
                    made = int(parts[1])
                    break
            roots, rotations = array("f"), array("f")
            with open(os.path.join(job, "out", "root_positions.f32"), "rb") as handle:
                roots.frombytes(handle.read())
            with open(os.path.join(job, "out", "local_rotations_xyzw.f32"), "rb") as handle:
                rotations.frombytes(handle.read())
            import shutil
            shutil.rmtree(job, ignore_errors=True)
            return made, roots, rotations

    def stop(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()


class MotionBricks:
    """MotionBricks' human model (SOMA-30), through the add-on's own planner.

    The planner needs numpy, so it loads on the first request and the rest of
    the service stays standard library only. Its code lives in mbcore/, copied
    from MotionForge; the ONNX model and the styles are the engine folder's.
    """

    FPS = 30.0
    PLAN_TAIL = 2

    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.core = None
        self.engine = None

    class _Prefs:
        def __init__(self, root):
            self.root = root

    def _load(self):
        if self.core is not None:
            return
        _need_numpy("MotionBricks")
        import importlib
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        soma = importlib.import_module("mbcore.mb_soma")
        soma._python = lambda: sys.executable
        planner = importlib.import_module("mbcore.mb_soma_engine")
        prefs = self._Prefs(self.root)
        problem = soma.problem(prefs)
        if problem:
            raise RuntimeError(problem)
        self.core = (soma, planner)
        self.engine = planner.Engine(prefs)

    def styles(self):
        with self.lock:
            self._load()
            return [{"name": n, "speed": float(self.engine.style_speed(n))}
                    for n in self.engine.style_names()]

    def generate(self, style, frames, heading, speed, seed):
        import numpy as np
        with self.lock:
            self._load()
            soma, planner = self.core
            if style not in self.engine.style_names():
                raise ValueError("No MotionBricks style called %r." % style)
            agent = self.engine.agent(style)
            if heading is None:
                move = planner.AHEAD
            else:
                angle = float(heading) * 3.141592653589793 / 180.0
                move = (float(np.sin(angle)), 0.0, float(np.cos(angle)))
            speed = float(speed) if speed else None

            def aim():
                return seed + agent.played + 30

            # A fresh agent lurches out of its standing pose: one chunk is
            # planned and thrown away, and the character put back where it stood.
            stood = np.array(agent.positions[-1][0])
            warm = agent.plan(move=move, speed=speed, seed=aim())
            if warm.frames:
                agent.advance(max(1, warm.frames - self.PLAN_TAIL))
            gone = np.array(agent.positions[-1][0]) - stood
            gone[1] = 0.0
            agent.positions = agent.positions - gone

            rotations, positions = [], []
            skip = False
            while len(rotations) < frames:
                motion = agent.plan(move=move, speed=speed, seed=aim())
                if not motion.frames:
                    break
                frame_rotations, frame_positions = motion.fk()
                take = min(max(1, motion.frames - self.PLAN_TAIL), frames - len(rotations) + (1 if skip else 0))
                start = 1 if skip and take > 1 and getattr(agent, "overlap", True) else 0
                rotations.extend(frame_rotations[start:take])
                positions.extend(frame_positions[start:take])
                agent.advance(take)
                skip = True
            if not rotations:
                raise RuntimeError("MotionBricks planned nothing.")
            return (np.asarray(rotations[:frames], np.float32),
                    np.asarray(positions[:frames], np.float32))

    def stop(self):
        if self.core is not None:
            try:
                self.core[0].release()
            except Exception:
                pass
            self.core = None


# -- the service ---------------------------------------------------------

class Service:
    def __init__(self, engine, work):
        self.engine = engine
        self.work = work
        self.kimodo = Kimodo(engine.root, work)
        self.motionbricks = MotionBricks(engine.root)
        self.jobs = {}
        self.job_counter = 0
        self.streams = {}
        self.guard = threading.Lock()

    def soma_chunk(self, mats, joints, fps):
        """A SOMA-30 chunk file from [T,30,3,3] rotations and [T,30,3] positions."""
        import soma30_skeleton as soma
        names = soma.NAMES
        parents = [soma.NAMES[p] if p >= 0 else "" for p in soma.PARENTS]
        frames = len(mats) // (len(names) * 9)
        path = os.path.join(self.work, "soma_%s.npz" % uuid.uuid4().hex[:12])
        _write_npz(path, {
            "bone_names": ("U", (len(names),), names),
            "bone_parents": ("U", (len(parents),), parents),
            "global_rot_mats": ("f4", (frames, len(names), 3, 3), mats),
            "posed_joints": ("f4", (frames, len(names), 3), joints),
            "fps": ("f8", (), [fps]),
            "positions_for": ("U", (), ["root"]),
        })
        return path, frames

    def roundtrip_start(self, body):
        """Kimodo Roundtrip's job: poses from Cascadeur in, a motion through them out."""
        self.job_counter += 1
        job = {"id": self.job_counter, "status": "queued", "progress": 0, "done": False, "error": ""}
        self.jobs[job["id"]] = job
        threading.Thread(target=self._roundtrip, args=(job, body), daemon=True).start()
        return {"ok": True, "job_id": job["id"]}

    def roundtrip_job(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "No such job."}
        return {"ok": True, "job": {k: job[k] for k in ("status", "progress", "done", "error")}}

    def _roundtrip(self, job, body):
        import soma30_skeleton as soma30
        import soma77_skeleton as soma77
        try:
            _need_numpy("Kimodo Roundtrip")
            import numpy as np
            job["status"] = "Reading the poses..."
            with np.load(body["constraints"], allow_pickle=False) as data:
                frames_at = [int(f) for f in data["frame_indices"]]
                positions = np.asarray(data["global_positions"], np.float32)
                rotations = np.asarray(data["global_rot_mats"], np.float32)
                offset = np.asarray(data["position_offset"], np.float32)
            if positions.shape[1] == 77:
                pick = [soma77.NAMES.index(n) for n in soma30.NAMES]
                positions, rotations = positions[:, pick], rotations[:, pick]
            elif positions.shape[1] != 30:
                raise ValueError("The poses have %d joints; Kimodo reads 30 or 77." % positions.shape[1])
            total = int(body["num_frames"])
            poses = [(f, positions[i].ravel().tolist(), rotations[i].reshape(-1).tolist())
                     for i, f in enumerate(frames_at)]
            seed = body.get("seed")
            seed = int(seed) if seed is not None and int(seed) >= 0 else int.from_bytes(os.urandom(4), "little")
            cfg = bool(body.get("cfg_enabled", True))
            text_w = float(body.get("text_weight", 2.0)) if cfg else 1.0
            constraint_w = float(body.get("constraint_weight", 2.0)) if cfg else 1.0
            job["status"] = "Generating %d frames through %d pose(s)..." % (total, len(poses))
            job["progress"] = 10
            made, roots, rot = self.kimodo.constrained(
                str(body.get("prompt") or ""), total, int(body.get("diffusion_steps") or 20), seed,
                text_w, constraint_w, poses)
            job["status"] = "Building the motion..."
            job["progress"] = 90
            mats, joints = _soma_fk(rot, roots, made)
            shifted = array("f", joints)
            for i in range(0, len(shifted), 3):
                shifted[i] += float(offset[0]); shifted[i + 1] += float(offset[1]); shifted[i + 2] += float(offset[2])
            names = soma30.NAMES
            parents = [soma30.NAMES[p] if p >= 0 else "" for p in soma30.PARENTS]
            out = str(body.get("output") or "").strip()
            if not out:
                out = os.path.join(self.work, "roundtrip_%s.npz" % uuid.uuid4().hex[:12])
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            _write_npz(out, {
                "bone_names": ("U", (len(names),), names),
                "bone_parents": ("U", (len(parents),), parents),
                "global_rot_mats": ("f4", (made, len(names), 3, 3), mats),
                "posed_joints": ("f4", (made, len(names), 3), shifted),
                "fps": ("f8", (), [Kimodo.FPS]),
                "positions_for": ("U", (), ["root"]),
            })
            job["status"] = "Wrote the generated motion."
            job["progress"] = 100
        except Exception as error:
            import traceback
            log(traceback.format_exc())
            job["error"] = "%s: %s" % (type(error).__name__, error)
            job["status"] = job["error"]
        finally:
            job["done"] = True

    def motionbricks_styles(self, body):
        return {"styles": self.motionbricks.styles()}

    def motionbricks_generate(self, body):
        style = str(body.get("style") or "walk")
        frames = int(body.get("frames") or 150)
        if frames < 1:
            raise ValueError("No frames asked for.")
        seed = body.get("seed")
        seed = int(seed) if seed is not None and int(seed) >= 0 else 0
        heading = body.get("heading")
        heading = None if heading is None or heading == "ahead" else float(heading)
        rotations, positions = self.motionbricks.generate(style, frames, heading, body.get("speed"), seed)
        path, count = self.soma_chunk(array("f", rotations.ravel().tolist()),
                                      array("f", positions.ravel().tolist()), MotionBricks.FPS)
        return {"chunk_path": path, "fbx_path": "", "frames": count, "fps": MotionBricks.FPS, "segments": []}

    def kimodo_takes(self, body):
        takes = body.get("takes") or []
        if not takes:
            raise ValueError("No takes to generate.")
        segments, plan = [], []
        for index, take in enumerate(takes, start=1):
            prompt = str(take.get("prompt", "")).strip()
            if not prompt:
                raise ValueError("Take %d has an empty prompt." % index)
            frames = int(take.get("frames") or 90)
            if frames < 1:
                raise ValueError("Take %d has no frames." % index)
            segments.append((frames, prompt))
            plan.append({"index": index, "prompt": prompt})
        seed = body.get("seed")
        seed = int(seed) if seed is not None and int(seed) >= 0 else int.from_bytes(os.urandom(4), "little")
        steps = int(body.get("diffusion_steps") or 20)
        transition = int(body.get("transition_frames") or 8)
        log("kimodo: %d take(s), %d steps, seed %d" % (len(segments), steps, seed))
        frames, roots, rotations = self.kimodo.generate(segments, transition, steps, seed)
        mats, joints = _soma_fk(rotations, roots, frames)
        import soma30_skeleton as soma
        names = soma.NAMES
        parents = [soma.NAMES[p] if p >= 0 else "" for p in soma.PARENTS]
        path = os.path.join(self.work, "kimodo_%s.npz" % uuid.uuid4().hex[:12])
        _write_npz(path, {
            "bone_names": ("U", (len(names),), names),
            "bone_parents": ("U", (len(parents),), parents),
            "global_rot_mats": ("f4", (frames, len(names), 3, 3), mats),
            "posed_joints": ("f4", (frames, len(names), 3), joints),
            "fps": ("f8", (), [Kimodo.FPS]),
            "positions_for": ("U", (), ["root"]),
        })
        cursor, spans = 0, []
        for (count, _prompt), item in zip(segments, plan):
            item.update({"frame_start": cursor, "frames": count})
            spans.append(item)
            cursor += count
        return {"chunk_path": path, "fbx_path": "", "frames": frames, "fps": Kimodo.FPS, "segments": spans}

    def names(self):
        return [b[0] for b in self.engine.bones]

    def chunk(self, rotations, joints, fps):
        """Write one chunk file; rotations and joints are (shape, values)."""
        path = os.path.join(self.work, "ardy_%s.npz" % uuid.uuid4().hex[:12])
        names = self.names()
        parents = [b[1] or "" for b in self.engine.bones]
        _write_npz(path, {
            "bone_names": ("U", (len(names),), names),
            "bone_parents": ("U", (len(parents),), parents),
            "global_rot_mats": ("f4",) + rotations,
            "posed_joints": ("f4",) + joints,
            "fps": ("f8", (), [float(fps)]),
        })
        return path

    def start_stream(self, body):
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("The prompt is empty.")
        seed = body.get("seed")
        payload = {"prompt": prompt, "stream": uuid.uuid4().hex[:12]}
        if seed is not None and int(seed) >= 0:
            payload["seed"] = int(seed)
        if body.get("diffusion_steps"):
            payload["diffusion_steps"] = int(body["diffusion_steps"])
        if body.get("history_frames"):
            payload["history_frames"] = int(body["history_frames"])
        info = self.engine.request("stream_start", 300.0, **payload)
        horizon = int(info["horizon"])
        chunk = int(body.get("num_frames") or horizon)
        if chunk <= 0:
            chunk = horizon
        with self.guard:
            self.streams[payload["stream"]] = {"chunk": chunk, "frames": 0}
        fps = float(info["fps"])
        return {"stream_id": payload["stream"], "fps": fps, "window_seconds": chunk / fps,
                "chunk_frames": chunk, "history_frames": int(info["history_frames"])}

    def step(self, body):
        name = body.get("stream_id")
        with self.guard:
            state = self.streams.get(name)
        if state is None:
            raise RuntimeError("No stream %r. Call /stream/start first." % name)
        want = state["chunk"]
        rotations, joints = array("f"), array("f")
        made = 0
        while made < want:
            reply = self.engine.request("stream_step", 300.0, stream=name)
            frames = int(reply["frames"])
            keep = min(frames, want - made)
            joint_count = len(self.engine.bones)
            rot = _flat(reply["global_rot_mats"], [])[:keep * joint_count * 9]
            pos = _flat(reply["posed_joints"], [])[:keep * joint_count * 3]
            rotations.extend(rot)
            joints.extend(pos)
            made += keep
        start = state["frames"]
        state["frames"] += made
        count = len(self.engine.bones)
        fps = float(self.engine.info.get("fps", 20))
        path = self.chunk(((made, count, 3, 3), rotations), ((made, count, 3), joints), fps)
        return {"chunk_path": path, "fbx_path": "", "frame_start": start, "frames": made}

    def stop_stream(self, body):
        with self.guard:
            self.streams.pop(body.get("stream_id"), None)
        try:
            self.engine.request("stream_stop", 30.0, stream=body.get("stream_id"))
        except RuntimeError:
            pass
        return {"ok": True}

    def takes(self, body):
        takes = body.get("takes") or []
        if not takes:
            raise ValueError("No takes to generate.")
        fps = float(self.engine.info.get("fps", 20))
        horizon = int(self.engine.info.get("horizon", 40))
        seed = body.get("seed")
        handover = int(body.get("transition_frames") or 4)
        rotations, joints = array("f"), array("f")
        segments, previous, cursor = [], None, 0
        for index, take in enumerate(takes, start=1):
            prompt = str(take.get("prompt", "")).strip()
            if not prompt:
                raise ValueError("Take %d has an empty prompt." % index)
            frames = int(take.get("frames") or horizon)
            log("take %d/%d: %r (%d frames)" % (index, len(takes), prompt, frames))
            payload = {"prompt": prompt, "duration": frames / fps,
                       "out_path": os.path.join(self.work, "take_%s" % uuid.uuid4().hex[:12])}
            if seed is not None and int(seed) >= 0:
                payload["seed"] = int(seed) + index - 1
            if body.get("diffusion_steps"):
                payload["diffusion_steps"] = int(body["diffusion_steps"])
            if body.get("history_frames"):
                payload["history_frames"] = int(body["history_frames"])
            if previous:
                payload["continue_from"] = previous
                payload["continue_frames"] = handover
            reply = self.engine.request("generate", 900.0, **payload)
            previous = reply["path"]
            data = _read_npz(previous)
            made = data["global_rot_mats"][0][0]
            rotations.extend(data["global_rot_mats"][1])
            joints.extend(data["posed_joints"][1])
            segments.append({"index": index, "prompt": prompt, "frame_start": cursor, "frames": made})
            cursor += made
        count = len(self.engine.bones)
        path = self.chunk(((cursor, count, 3, 3), rotations), ((cursor, count, 3), joints), fps)
        return {"chunk_path": path, "fbx_path": "", "frames": cursor, "fps": fps, "segments": segments}

    def health(self):
        return {"ok": True, "loaded": self.engine.ready, "warming_up": self.engine.loading,
                "load_error": self.engine.error if not self.engine.ready else "",
                "device": self.engine.backend, "streams": list(self.streams)}


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def _send(self, status, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self._send(HTTPStatus.OK, service.health())
            elif self.path.startswith("/jobs/"):
                try:
                    self._send(HTTPStatus.OK, service.roundtrip_job(int(self.path.rsplit("/", 1)[1])))
                except ValueError:
                    self._send(HTTPStatus.NOT_FOUND, {"error": "unknown job"})
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "unknown path"})

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(size) or b"{}")
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "the body is not JSON"})
                return
            routes = {"/stream/start": service.start_stream, "/stream/step": service.step,
                      "/stream/stop": service.stop_stream, "/takes/generate": service.takes,
                      "/kimodo/generate": service.kimodo_takes,
                      "/generate": service.roundtrip_start,
                      "/motionbricks/styles": service.motionbricks_styles,
                      "/motionbricks/generate": service.motionbricks_generate}
            if self.path == "/shutdown":
                try:
                    self._send(HTTPStatus.OK, {"ok": True})
                finally:
                    threading.Thread(target=self._stop, daemon=True).start()
                return
            call = routes.get(self.path)
            if call is None:
                self._send(HTTPStatus.NOT_FOUND, {"error": "unknown path"})
                return
            if not self.path.startswith(("/kimodo/", "/motionbricks/", "/generate")) and not service.engine.ready:
                self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                           {"error": service.engine.error or "ARDY is still loading."})
                return
            try:
                self._send(HTTPStatus.OK, call(body))
            except (ValueError, RuntimeError) as error:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except Exception as error:
                import traceback
                log(traceback.format_exc())
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "%s: %s" % (type(error).__name__, error)})

        def _stop(self):
            service.engine.stop()
            service.kimodo.stop()
            service.motionbricks.stop()
            self.server.shutdown()

    return Handler


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default=os.path.abspath(os.path.join(here, "..", "..")))
    parser.add_argument("--port", type=int, default=9553)
    args = parser.parse_args()

    work = os.path.join(tempfile.gettempdir(), "KimodoEngine", "ardy-chunks")
    os.makedirs(work, exist_ok=True)
    for name in os.listdir(work):
        path = os.path.join(work, name)
        try:
            if time.time() - os.path.getmtime(path) > STALE_AFTER:
                os.remove(path)
        except OSError:
            pass

    engine = Engine(os.path.abspath(args.engine))
    service = Service(engine, work)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(service))
    except OSError as error:
        log("Port %d is taken (%s). Is the ARDY service already running?" % (args.port, error))
        return 1
    threading.Thread(target=engine.start, daemon=True).start()
    log("Listening on 127.0.0.1:%d" % args.port)
    try:
        server.serve_forever()
    finally:
        engine.stop()
        service.kimodo.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
