"""MotionBricks SOMA as an engine the MotionBricks panel can drive.

The G1 engine is a DLL the panel talks to through `motionbrick.py`: an engine
with a skeleton and styles, and one agent per character that plans a chunk of
motion towards a heading or a target and is then told how much of it was kept.
This is the same shape for the SOMA human, so Generate, Bake, Retarget and the
rest of the panel run on it unchanged.

The SOMA model has no steering inputs. NVIDIA's runtime steers it by handing it
target poses: four consecutive poses cut from a clip of the style being asked
for, moved ahead of the character along the direction it should go and turned
to face it. A clip is therefore the style, and `styles/<name>.npz` holds one:
global rotations [F,30,3,3] and positions [F,30,3] at 30 fps.
"""

import math
import os

import numpy as np

from . import mb_soma, soma30

FPS = 30
TOKENS = 14
CONTEXT = mb_soma.POSES_PER_TOKEN
IDLE = "idle"
REACHED = 0.08
# The model's last frames bend towards the target pose and twitch on a character
# that is meant to be still, so a chunk stops short of them.
TAIL = 4
# Turns past TURN_FREE radians cost up to TURN_EASE of a stride's distance,
# reached at a quarter turn.
TURN_FREE = 0.4
TURN_EASE = 0.25


class SomaError(RuntimeError):
    pass


def styles_dir(prefs):
    root = mb_soma.folder(prefs)
    return os.path.join(root, "styles") if root else ""


def _heading(pos):
    return mb_soma._heading(pos[mb_soma._RIGHT], pos[mb_soma._LEFT])


# `move` for "keep going the way you face", whatever way that is by then.
AHEAD = "ahead"


class Clip:
    """One style: a clip of the human doing it, and what it says about itself."""

    def __init__(self, name, rotations, positions):
        self.name = name
        self.rotations = rotations
        self.positions = positions
        self.frames = len(rotations)
        hips = positions[:, 0]
        travel = np.linalg.norm(np.diff(hips[:, [0, 2]], axis=0), axis=1)
        self.speed = float(travel.sum() / max(1, self.frames - 1) * FPS)
        self.headings = np.array([_heading(p) for p in positions])
        # Each frame as the character itself would describe it: joints about
        # the hips ground point, in the frame the pelvis faces.
        self.features = np.array([self._feature(i) for i in range(self.frames)])

    def _feature(self, index):
        return local_pose(self.positions[index], self.headings[index],
                          self.positions[min(index + 1, self.frames - 1)])

    def match(self, feature, last):
        """The frame whose pose and motion look most like `feature`."""
        scores = np.abs(self.features[:last] - feature).sum(axis=1)
        return int(np.argmin(scores))


def local_pose(positions, heading, following):
    """Joint positions and hips velocity in the frame the pelvis faces."""
    ground = np.array([positions[0][0], 0.0, positions[0][2]])
    turn = mb_soma._ry(heading).T
    joints = ((positions - ground) @ turn.T)[1:]
    step = (following[0] - positions[0]) @ turn.T
    return np.concatenate([joints.ravel(), step[[0, 2]] * FPS * 2.0])


class Motion:
    """One planned chunk, in the shape `Motion.fk()` gives the panel."""

    def __init__(self, rotations, positions, contacts, wide=False):
        self.frames = len(rotations)
        self._rotations = rotations
        self._positions = positions
        self.contacts = contacts
        self._wide = wide

    def __len__(self):
        return self.frames

    def fk(self):
        """Global rotations and positions per frame; 77 joints for a rig with fingers."""
        if self._wide:
            return widen(self._rotations, self._positions[:, 0])
        return self._rotations.tolist(), self._positions.tolist()


def widen(rotations, hips):
    """The 30 predicted joints as the 77 of a rig with fingers.

    The other 47 keep the relaxed-hand rest, as a Kimodo take does, so the
    same expansion is used: local rotations in, the 77-joint FK out.
    """
    from mathutils import Matrix

    from . import kimodo, soma77

    skeleton = soma30.Skeleton()
    parents = skeleton.parents
    quats = []
    for frame in rotations:
        for joint in range(len(parents)):
            local = frame[joint] if parents[joint] < 0 else frame[parents[joint]].T @ frame[joint]
            q = Matrix(local.tolist()).to_quaternion()
            quats += [q.x, q.y, q.z, q.w]
    roots = [float(v) for v in np.asarray(hips).ravel()]
    narrow = kimodo.Motion(len(rotations), len(parents), roots, quats, skeleton)
    return soma77.expand(narrow).fk()


class Agent:
    """One character's state: the last four poses, and what they were asked for."""

    # A chunk starts one step on from where the last one ended; the robot's
    # starts on the same frame, which is why the panel drops it there.
    overlap = False

    def __init__(self, engine, style, wide=False):
        self.engine = engine
        self.wide = wide
        self.style = style
        self.played = 0
        self._planned = None
        self._start(style)

    def reset(self, style):
        """Change style where the character stands."""
        self.style = style
        self._planned = None

    def _start(self, style):
        self.style = style
        clip = self.engine.clip(IDLE if IDLE in self.engine.names else style)
        heading = 0.0
        turn = mb_soma._ry(heading - clip.headings[0])
        pivot = clip.positions[0][0] * np.array([1.0, 0.0, 1.0])
        start = list(range(CONTEXT))
        self.rotations = np.array([self._turned(turn, clip.rotations[i]) for i in start])
        self.positions = np.array([(clip.positions[i] - pivot) @ turn.T for i in start])
        self.played = 0
        self._planned = None

    @staticmethod
    def _turned(turn, rotations):
        return np.einsum("ab,jbc->jac", turn, rotations)

    def close(self):
        self._planned = None

    def advance(self, frames):
        """Keep `frames` of the last plan: the character stands where they end."""
        if self._planned is None:
            return
        keep = min(frames, self._planned.frames)
        rotations, positions = self._planned._rotations, self._planned._positions
        joined_r = np.concatenate([self.rotations, rotations[:keep]])
        joined_p = np.concatenate([self.positions, positions[:keep]])
        self.rotations = joined_r[-CONTEXT:]
        self.positions = joined_p[-CONTEXT:]
        self.played += keep
        self._planned = None

    def plan(self, move=(0.0, 0.0, 1.0), target=None, speed=None, seed=0):
        clip_name = self.style
        here = self.positions[-1]
        face = _heading(here)
        ground = np.array([here[0][0], 0.0, here[0][2]])
        still = target is None and not any(move)
        clip = self.engine.clip(IDLE if still else clip_name)
        walk = speed if speed else clip.speed
        duration = (mb_soma.frames_for(TOKENS) + 1) / float(FPS)

        if target is not None:
            gx, gz = target[0], target[1]
            offset = np.array([gx, 0.0, gz]) - ground
            remaining = float(np.linalg.norm(offset))
            aim = math.atan2(offset[0], offset[2]) if remaining > REACHED else face
            arriving = remaining <= max(walk * duration, REACHED)
            if arriving and len(target) > 2 and target[2] is not None:
                aim = float(target[2])
            if arriving:
                clip = self.engine.clip(IDLE)
                distance, heading = remaining, aim
            else:
                distance, heading = walk * duration * self._easing(aim, face), aim
            direction = np.array([math.sin(aim), 0.0, math.cos(aim)])
            if arriving and remaining > REACHED:
                direction = offset / remaining
        elif move == AHEAD:
            distance, heading = walk * duration, face
            direction = np.array([math.sin(face), 0.0, math.cos(face)])
        elif still:
            distance, heading = 0.0, face
            direction = np.array([math.sin(face), 0.0, math.cos(face)])
        else:
            direction = np.array([move[0], 0.0, move[2]])
            norm = np.linalg.norm(direction)
            direction = direction / norm if norm > 1e-6 else np.array([math.sin(face), 0.0, math.cos(face)])
            heading = math.atan2(direction[0], direction[2])
            distance = walk * duration * self._easing(heading, face)

        feature = local_pose(here, face, self._ahead(here))
        horizon = mb_soma.frames_for(TOKENS) + 1
        last = max(2, clip.frames - horizon - CONTEXT)
        found = clip.match(feature, last)
        first = min(found + horizon, clip.frames - CONTEXT)
        window = range(first, first + CONTEXT)

        spin = mb_soma._ry(heading - clip.headings[first])
        pivot = clip.positions[first][0] * np.array([1.0, 0.0, 1.0])
        landing = ground + direction * distance
        target_pos = np.array([(clip.positions[i] - pivot) @ spin.T + landing for i in window])
        target_rot = np.array([self._turned(spin, clip.rotations[i]) for i in window])
        return self._toward(target_pos, target_rot, TOKENS, TAIL)

    @staticmethod
    def _easing(heading, face):
        """How much of a stride's distance a turn leaves. The goal sits ahead
        in the new direction while the character still moves the old way, so
        a full stride makes it sprint to catch up."""
        turn = abs((heading - face + math.pi) % (2.0 * math.pi) - math.pi)
        return 1.0 - TURN_EASE * min(1.0, max(0.0, turn - TURN_FREE) / (0.5 * math.pi - TURN_FREE))

    def plan_clip(self, clip_name, index, landing, heading, poses, tokens):
        """Generate towards a pose of a clip, placed at `landing` and turned to `heading`.

        `poses` of the clip's frames from `index` on are the target, the way the
        Inbetweener's Poses to Use works: one is somewhere to stop, more carry
        the clip's own speed through it. The model picks the length within
        `tokens`. Returns the whole motion; the caller says how much to keep.
        """
        clip = self.engine.clip(clip_name)
        index = int(max(0, min(index, clip.frames - poses)))
        window = range(index, index + poses)
        spin = mb_soma._ry(heading - clip.headings[index])
        pivot = clip.positions[index][0] * np.array([1.0, 0.0, 1.0])
        target_pos = np.array([(clip.positions[i] - pivot) @ spin.T + landing for i in window])
        target_rot = np.array([self._turned(spin, clip.rotations[i]) for i in window])
        return self._toward(target_pos, target_rot, tokens)

    def plan_pose(self, rotations, hips, tokens):
        """Generate towards poses already placed in this character's own frame.

        `rotations` [n,30,3,3] and `hips` [n,3] are one to four poses, the way
        a Smart Primitive's step gives them. Returns the whole motion; the
        caller says how much to keep.
        """
        return self._toward(mb_soma.joint_positions(hips, rotations), rotations, tokens)

    def _toward(self, target_pos, target_rot, tokens, trim=0):
        hips, rotations, contacts = mb_soma.generate(
            self.engine.prefs, self.positions, self.rotations, target_pos, target_rot, tokens)
        # The next frame as the last two imply it, so a chunk that starts a
        # few centimetres off - which reads as a twitch on a still character
        # - starts where the character was already going.
        last, before = self.rotations[-1], self.rotations[-2]
        ahead = np.einsum("jab,jcb,jcd->jad", last, before, last)
        rotations, hips = mb_soma.pin_ends(
            rotations, hips, (ahead, 2.0 * self.positions[-1][0] - self.positions[-2][0]), None, fade=16)
        if trim:
            rotations, hips, contacts = rotations[:-trim], hips[:-trim], contacts[:-trim]
        positions = mb_soma.joint_positions(hips, rotations)
        self._planned = Motion(rotations, positions, contacts, self.wide)
        return self._planned

    def _ahead(self, positions):
        """The next pose's hips, as the velocity the clip frames are described with."""
        before = self.positions[-2]
        step = positions[0] - before[0]
        stand_in = positions.copy()
        stand_in[0] = positions[0] + step
        return stand_in


class Engine:
    """The styles on disk and the model behind them."""

    def __init__(self, prefs):
        self.prefs = prefs
        self.skeleton = soma30.Skeleton()
        self._clips = {}
        self.names = []
        self.load_styles()

    def load_styles(self):
        folder = styles_dir(self.prefs)
        self._clips.clear()
        found = []
        if folder and os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".npz"):
                    found.append(os.path.splitext(name)[0])
        self.names = found

    def style_names(self):
        return list(self.names)

    def style_speed(self, name):
        return self.clip(name).speed

    def clip(self, name):
        if name not in self._clips:
            path = os.path.join(styles_dir(self.prefs), name + ".npz")
            if not os.path.isfile(path):
                raise SomaError("No SOMA style called %s." % name)
            with np.load(path) as data:
                self._clips[name] = Clip(name, data["rotations"].astype(np.float64),
                                         data["positions"].astype(np.float64))
        return self._clips[name]

    def agent(self, style, wide=False):
        return Agent(self, style, wide)

    def close(self):
        self._clips.clear()
