"""The SOMA-30 control skeleton, carried by the add-on because kimodo.dll
does not expose it.

The C API returns local rotations and root translations but no joint names,
parents or rest positions. The upstream port's own BVH exporter
(`scripts/export_bvh.py` in kimodo.cpp) ships the exact table the SOMA
checkpoints predict, so it is copied here rather than guessed: 30 joints,
Y-up, metres.

`kimodo.py`'s Motion.fk() reads `parents` and `neutral` the same way it reads
MotionBricks' model-provided skeleton, which is why the rest of the add-on
sees no difference between the two engines.
"""

SKELETON_TAG = "soma30"

NAMES = [
    "Hips", "Spine1", "Spine2", "Chest", "Neck1", "Neck2", "Head", "Jaw",
    "LeftEye", "RightEye", "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
    "LeftHandThumbEnd", "LeftHandMiddleEnd", "RightShoulder", "RightArm",
    "RightForeArm", "RightHand", "RightHandThumbEnd", "RightHandMiddleEnd",
    "LeftLeg", "LeftShin", "LeftFoot", "LeftToeBase",
    "RightLeg", "RightShin", "RightFoot", "RightToeBase",
]

PARENTS = [-1, 0, 1, 2, 3, 4, 5, 6, 6, 6, 3, 10, 11, 12, 13, 13,
           3, 16, 17, 18, 19, 19, 0, 22, 23, 24, 0, 26, 27, 28]

# Rest offsets from the parent joint, in metres, model frame (Y up, Z forward).
OFFSETS = [
    [0.0, 0.0, 0.0], [-0.00013727, 0.0500376256, -0.00053726669],
    [-1.86574103e-09, 0.0712530139, -0.000298248546],
    [-5.75188398e-09, 0.0755006305, -0.00815970992],
    [-0.00181676517, 0.263112953, -0.00553348292],
    [-2.85102231e-08, 0.0770939664, 0.0230258546],
    [-4.5975437e-08, 0.0612891595, 0.0195370861],
    [2.63687901e-05, 0.0047559225, 0.0309494062],
    [0.0320638079, 0.0538020513, 0.0758688308],
    [-0.0322244017, 0.05361869, 0.0755823359],
    [0.0162165175, 0.232371641, 0.0511341324],
    [0.149198457, 2.19397873e-08, -0.0550232576],
    [0.287393078, 2.50268389e-09, -2.58787737e-05],
    [0.270939812, -7.06625108e-09, 2.60897248e-05],
    [0.122686267, -0.0322017573, 0.0483306876],
    [0.190119595, -0.00312878387, -0.000339570373],
    [-0.0138011824, 0.231803086, 0.0521415786],
    [-0.150371962, 1.17387901e-07, -0.0554560437],
    [-0.287366393, 1.87628082e-08, -2.59709359e-05],
    [-0.271336198, -1.16767401e-09, 2.61269368e-05],
    [-0.122642483, -0.0321145448, 0.0480403904],
    [-0.190005945, -0.00306615542, -0.0003157343],
    [0.10043214, -0.0843452671, 0.0259565473],
    [-1e-08, -0.432217537, -0.00802912805],
    [1e-08, -0.421550959, -0.0348152298],
    [0.0, -0.0505947206, 0.132315294],
    [-0.10047278, -0.0829525995, 0.0262031695],
    [1e-08, -0.433622059, -0.00805555828],
    [2e-08, -0.421173943, -0.0347839785],
    [-3.42907669e-09, -0.0507960932, 0.132841956],
]


class Skeleton:
    """Names, parents and absolute rest positions, in the shape the fk() and
    `rig.build_armature` want."""

    def __init__(self):
        self.names = list(NAMES)
        self.parents = list(PARENTS)
        # Offsets are parent-relative; neutral positions are absolute.
        self.neutral = []
        for i, offset in enumerate(OFFSETS):
            parent = self.parents[i]
            if parent < 0:
                self.neutral.append(list(offset))
            else:
                base = self.neutral[parent]
                self.neutral.append([base[k] + offset[k] for k in range(3)])

    def __len__(self):
        return len(self.names)

    def bones(self):
        """(name, parent name or None) pairs, which is what `rig` builds from."""
        return [(name,
                 self.names[self.parents[i]] if self.parents[i] >= 0 else None)
                for i, name in enumerate(self.names)]
