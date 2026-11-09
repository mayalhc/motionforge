# Copyright (c) 2026 Chamiseul. All rights reserved.
"""Fill Cascadeur's Quick Rigging Tool slots for a quadruped.

Rig mode registers a biped's joints for you because Cascadeur knows what a
human skeleton is called. A quadruped is named however its author felt, so
nothing matches and all 161 slots have to be dragged in by hand.

Names were the obvious way in and they are not enough. Two real rigs:

    Sabertooth   Pelvis  Femur_L  Tibia_L  Carpals_L  Fore_paw_L
    Fortnite     pelvis  thigh_bk_l  knee_bk_l  wrist_fr_l  ball_fr_l

The second names the JOINT (knee, ankle, wrist) where Cascadeur names the
BONE (tibia, tarsals, carpals), marks the end of the animal "fr"/"bk", and
hangs IK targets and a hundred face bones off the same skeleton. Matching
words picked `root` over `pelvis`, gave a front toe to a hind slot and left
the whole front leg empty.

The shape does not vary like that. Every quadruped is a spine from the hips
to the head, four chains leaving it - the back two where the spine starts,
the front two where it ends - a tail off the hips and ears off the head. So
this walks the hierarchy for the structure and uses names only for what the
hierarchy cannot say: which side a limb is on, and where the head is.

A wrong slot is worse than an empty one, so nothing is written until you
have read the report.

    report(scene)     # what would be filled, changes nothing

Open the Quick Rigging Tool and set it to a four-legged character BEFORE
running the command. Left on a humanoid it accepts a quadruped template in
silence and keeps its own - Cascadeur's own Quadruped.qrigcasc gets the same
treatment - so there is nothing on screen and nothing to go on.
"""

import re

import csc

# Words that mean the same joint, mapped onto the word Cascadeur uses. Only
# used for the leftovers now - the limbs come out of the hierarchy.
SYNONYMS = {
    "hip": "pelvis", "hips": "pelvis", "cog": "pelvis",
    "skull": "head", "cranium": "head",
}

LEFT = {"l", "left", "lf"}
RIGHT = {"r", "right", "rt"}

SCAPULA = {"scapula", "clavicle", "shoulderblade"}

# Joints that only exist to bend or twist between two real ones. They are not
# slots, and letting one match steals the slot from the joint that should
# have it - the sabertooth has Spine_1_p1, Spine_1_p2 between Spine_1 and
# Spine_2.
HELPER = re.compile(r"(^|_)p\d+($|_)|twist|roll|bend|helper|_add($|_)", re.I)

# Not part of the animal: IK targets, attachment points, accessories, the
# secondary motion rig. The Fortnite skeleton carries all four, and its IK
# foot targets are named so much like feet that they won the paw slots.
NOISE = re.compile(
    r"^ik[_\W]|[_\W]ik$|^attach|^acc[_\W]|^accessory|^dyn[_\W]|"
    r"^prop|^weapon|^camera|^cam[_\W]|^socket|^slot[_\W]", re.I)


def words(name):
    """A bone name broken into comparable words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]


def side_of(name):
    """'l', 'r' or None, from the name."""
    for word in words(name):
        if word in LEFT:
            return "l"
        if word in RIGHT:
            return "r"
    return None


def joints(scene):
    """Every joint in the scene, by name."""
    viewer = scene.model_viewer()
    behaviours = viewer.behaviour_viewer()
    out = {}
    for object_id in viewer.get_objects():
        if behaviours.get_behaviour_by_name(object_id, "Joint").is_null():
            continue
        out[viewer.get_object_name(object_id)] = object_id
    return out


def parents(scene):
    """{object_id: parent object_id} for every joint."""
    viewer = scene.model_viewer()
    behaviours = viewer.behaviour_viewer()
    out = {}
    for object_id in viewer.get_objects():
        basic = behaviours.get_behaviour_by_name(object_id, "Basic")
        if basic.is_null():
            continue
        parent = behaviours.get_behaviour_object(basic, "parent")
        out[object_id] = parent if (parent is not None
                                    and not parent.is_null()) else None
    return out


def slot_names():
    """Cascadeur's own quadruped slot names, in its own order."""
    import sys
    root = r"C:\Program Files\Cascadeur\resources\scripts\python"
    if root not in sys.path:
        sys.path.insert(0, root)
    from prototypes.rigs.qrt.quadruped.constants import NodeName
    return [str(n) for n in NodeName]


# The slots each chain fills, from the body outwards.
TRUNK = ("stomach", "spine_1", "spine_2", "spine_3")
NECK = tuple("neck_%d" % i for i in range(1, 9))
TAIL = tuple("tail_%d" % i for i in range(1, 9))
FORE = ("scapula", "humerus", "radius", "carpals", "fore_paw", "fore_toes")
HIND = ("femur", "tibia", "tarsals", "hind_paw", "hind_toes")
EAR = ("ear_%s_1", "ear_%s_2", "ear_%s_3", "ear_%s_4")


class Skeleton:
    """The joints worth rigging, and how they hang together."""

    def __init__(self, scene):
        by_name = joints(scene)
        self.name_of = {i: n for n, i in by_name.items()}
        self.id_of = dict(by_name)

        parent_of = parents(scene)
        self.parent = {}
        for object_id, parent in parent_of.items():
            if object_id in self.name_of:
                self.parent[object_id] = (parent if parent in self.name_of
                                          else None)

        self.skipped = {i for i, n in self.name_of.items()
                        if NOISE.search(n) or HELPER.search(n)}
        # A skipped joint's children still belong to the animal - the bendy
        # joints sit IN the chain, not beside it.
        self.children = {}
        for object_id in self.name_of:
            parent = self._real_parent(object_id)
            if parent is not None:
                self.children.setdefault(parent, []).append(object_id)

        self._depth = {}

    def _real_parent(self, object_id):
        """The nearest ancestor that is a joint we keep."""
        if object_id in self.skipped:
            return None
        parent = self.parent.get(object_id)
        while parent is not None and parent in self.skipped:
            if NOISE.search(self.name_of[parent]):
                return None      # under an IK target: not the animal at all
            parent = self.parent.get(parent)
        return parent

    def kids(self, object_id):
        return self.children.get(object_id, [])

    def depth(self, object_id):
        """Length of the longest chain below this joint."""
        if object_id not in self._depth:
            self._depth[object_id] = (
                1 + max([self.depth(c) for c in self.kids(object_id)] or [0]))
        return self._depth[object_id]

    def roots(self):
        return [i for i in self.name_of
                if i not in self.skipped and self._real_parent(i) is None]

    def ancestry(self, object_id):
        """Root-first path down to this joint."""
        path, node = [], object_id
        while node is not None:
            path.append(node)
            node = self._real_parent(node)
        path.reverse()
        return path

    def ancestry_down(self, start):
        """Every joint at or below this one, nearest first."""
        out, queue = [], [start]
        while queue:
            node = queue.pop(0)
            out.append(node)
            queue.extend(self.kids(node))
        return out

    def chain(self, start):
        """Walk out from a joint, following the longest way down.

        Stops at a real fork. A limb forks once at the paw into five equal
        toes, and an ear tip forks into two equal flaps - those are ends. A
        limb also "forks" where the scapula hangs as a leaf off the top of
        the arm, and that is not an end, so the deepest branch wins whenever
        it is unambiguously the deepest.
        """
        out, node = [start], start
        while True:
            kids = sorted(self.kids(node), key=self.depth, reverse=True)
            if not kids:
                break
            if len(kids) > 1 and self.depth(kids[0]) == self.depth(kids[1]):
                nxt = self._next_in_sequence(node, kids)
                if nxt is None:
                    break
                node = nxt
            else:
                node = kids[0]
            out.append(node)
        return out

    def _next_in_sequence(self, node, kids):
        """The child that is plainly the next segment of the same chain.

        Where a tie is really an end - a paw splitting into five toes - the
        children are named nothing like the parent. Where it is not, they
        are named exactly like it with the number stepped on: this rig hangs
        ear_03_l beside the two flaps ear_in_l and ear_ot_l, all leaves.
        """
        stem, index = self._numbered(self.name_of[node])
        if index is None:
            return None
        following = [k for k in kids
                     if self._numbered(self.name_of[k]) == (stem, index + 1)]
        return following[0] if len(following) == 1 else None

    @staticmethod
    def _numbered(name):
        """(the name without its number, the number) or (name, None)."""
        found = [w for w in words(name) if w.isdigit()]
        if len(found) != 1:
            return name.lower(), None
        stem = tuple(w for w in words(name) if not w.isdigit())
        return stem, int(found[0])


def align(chain, slots, side=None):
    """Lay a chain of bones onto an ordered list of slots.

    Same length is the common case and then the order settles everything.
    Where the rig has fewer joints than Cascadeur has slots, some slot must
    go empty, and which one is a naming question again - so the names are
    allowed to break the tie, but only inside an order the hierarchy fixed.
    """
    n, m = len(chain), len(slots)
    if n == 0 or m == 0:
        return {}
    if n > m:
        chain = chain[:m]
        n = m

    def named(slot):
        return slot % side if side and "%s" in slot else slot

    def fit(bone, slot):
        common = set(words(bone)) & set(words(named(slot)))
        return 10 * len(common) + 1

    # best[i][j]: aligning chain[i:] onto slots[j:], in order.
    best = [[0] * (m + 1) for _ in range(n + 1)]
    take = [[False] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if m - j < n - i:
                best[i][j] = -1
                continue
            with_it = fit(chain[i], slots[j]) + best[i + 1][j + 1]
            without = best[i][j + 1] if m - j - 1 >= n - i else -1
            if with_it >= without:
                best[i][j], take[i][j] = with_it, True
            else:
                best[i][j] = without

    out, i, j = {}, 0, 0
    while i < n:
        if take[i][j]:
            out[named(slots[j])] = chain[i]
            i += 1
        j += 1
    return out


def match(scene):
    """{slot: bone name}, plus the joints nothing wanted."""
    bones = Skeleton(scene)
    name_of = bones.name_of
    chosen = {}
    notes = []

    # The head is the one thing names must settle: it is where the spine
    # ends, but so is the tip of the tail.
    heads = [i for i in name_of
             if i not in bones.skipped
             and any(SYNONYMS.get(w, w) == "head" for w in words(name_of[i]))]
    if not heads:
        return {}, sorted(name_of[i] for i in name_of
                          if i not in bones.skipped), ["no head joint found"]
    head = min(heads, key=lambda i: len(bones.ancestry(i)))
    spine = bones.ancestry(head)

    # Everything hanging off the spine, and how far along it hangs.
    branches = []
    for index, node in enumerate(spine[:-1]):
        for kid in bones.kids(node):
            if kid is spine[index + 1] or kid == spine[index + 1]:
                continue
            branches.append([index, kid])

    # A rig may gather the tail and both back legs under one extra joint
    # ("hips") instead of hanging them off the spine directly. Open that up,
    # or the three of them arrive as a single branch.
    opened = []
    for index, node in branches:
        deep = [k for k in bones.kids(node) if bones.depth(k) >= 3]
        if len(deep) >= 2:
            opened.extend([index, k] for k in bones.kids(node))
        else:
            opened.append([index, node])
    branches = opened

    limbs = [(i, n) for i, n in branches if bones.depth(n) >= 3
             and side_of(name_of[n]) is not None
             and "tail" not in words(name_of[n])]
    tails = [(i, n) for i, n in branches if "tail" in words(name_of[n])]

    if len(limbs) < 4:
        notes.append("found %d limbs, expected 4" % len(limbs))

    # The back legs leave the spine where it starts, the front legs where it
    # ends. Nothing in between is a leg.
    limbs.sort(key=lambda p: p[0])
    hind = [p for p in limbs if p[0] == limbs[0][0]][:2] if limbs else []
    fore = [p for p in limbs if p[0] == limbs[-1][0]][:2] if limbs else []
    if hind and fore and hind[0][0] == fore[0][0]:
        notes.append("front and back legs leave the spine at the same joint")
        fore = []

    # The pelvis is where the back legs join the spine.
    pelvis_index = hind[0][0] if hind else 0
    chosen["pelvis"] = name_of[spine[pelvis_index]]

    # Between the pelvis and the head: the trunk, then the neck. Named neck
    # joints are the reliable split; without them assume one neck segment.
    middle = spine[pelvis_index + 1:-1]
    neck = [n for n in middle if "neck" in words(name_of[n])]
    if not neck and len(middle) > 1:
        neck = middle[-1:]
    trunk = [n for n in middle if n not in neck]

    # Right-aligned: the last segment is the chest, whatever the count, and
    # Cascadeur calls the first one "stomach" where no rig does.
    chosen.update({s: name_of[b] for s, b in
                   zip(TRUNK[-len(trunk):] if trunk else (),
                       trunk[-len(TRUNK):])})
    chosen.update({s: name_of[b] for s, b in zip(NECK, neck)})
    chosen["head"] = name_of[head]

    for _, root in tails:
        chain = bones.chain(root)
        chosen.update({s: name_of[b] for s, b in zip(TAIL, chain)})
        if len(chain) < len(TAIL):
            chosen["tail_end"] = name_of[chain[-1]]
            del chosen["tail_%d" % len(chain)]

    for group, slots in ((hind, HIND), (fore, FORE)):
        for _, root in group:
            side = side_of(name_of[root])
            chain = bones.chain(root)
            usable = list(slots)

            # A rig that names the joint rather than the bone puts the
            # scapula beside the top of the arm instead of above it, and
            # then the chain is one short and every slot below it shifts.
            if slots is FORE:
                blade = [k for k in bones.kids(root) if not bones.kids(k)
                         and set(words(name_of[k])) & SCAPULA]
                if blade:
                    chosen["scapula_" + side] = name_of[blade[0]]
                    usable = usable[1:]

            laid = align([name_of[b] for b in chain],
                         [s + "_" + side for s in usable])
            chosen.update(laid)
            # A rig that ends its toes with a tip joint has one more than
            # Cascadeur has slots, and that last one is the tip slot.
            if len(chain) > len(usable):
                chosen["%s_%s_end" % (usable[-1], side)] = \
                    name_of[chain[len(usable)]]

    # Ears. Not always children of the head - the Fortnite rig hangs its
    # whole face, ears included, off a face_attach joint under the head.
    for slot_side in ("l", "r"):
        found = None
        for node in bones.ancestry_down(head):
            if "ear" not in words(name_of[node]):
                continue
            parent = bones._real_parent(node)
            if parent is not None and "ear" in words(name_of[parent]):
                continue        # already inside an ear
            if side_of(name_of[node]) != slot_side:
                continue
            found = node
            break
        if found is None:
            continue
        chain = bones.chain(found)
        chosen.update({s % slot_side: name_of[b] for s, b in zip(EAR, chain)})
        if len(chain) <= len(EAR):
            chosen["ear_%s_end" % slot_side] = name_of[chain[-1]]
            del chosen["ear_%s_%d" % (slot_side, len(chain))]

    tip = [k for k in bones.kids(head)
           if any(w in ("end", "tip") for w in words(name_of[k]))]
    if tip:
        chosen["head_end"] = name_of[tip[0]]

    known = set(slot_names())
    chosen = {s: b for s, b in chosen.items() if s in known and b}

    taken = set(chosen.values())
    leftover = sorted(name_of[i] for i in name_of
                      if i not in bones.skipped and name_of[i] not in taken)
    return chosen, leftover, notes


def report(scene=None):
    """Print what would be filled. Changes nothing."""
    if scene is None:
        scene = csc.app.get_application().get_scene_manager() \
            .current_scene().domain_scene()
    chosen, leftover, notes = match(scene)
    order = {s: i for i, s in enumerate(slot_names())}
    print("matched %d slots" % len(chosen))
    for slot in sorted(chosen, key=lambda s: order.get(s, 999)):
        print("   %-22s <- %s" % (slot, chosen[slot]))
    for note in notes:
        print("note: %s" % note)
    print("\n%d joints left over" % len(leftover))
    for name in leftover[:40]:
        print("   %s" % name)
    if len(leftover) > 40:
        print("   ... and %d more" % (len(leftover) - 40))
    return chosen, leftover


def _section_of(slot):
    """Which of the five QRT sections a slot belongs to.

    Cascadeur flattens all five and keys on the field name, so the grouping
    only has to be consistent - but the tool reads them section by section,
    so a limb slot in the trunk list would be looked for in the wrong place.
    """
    if slot.startswith("fore_") or slot.startswith("hind_"):
        side = "l" if "_l" in slot.replace("_left", "_l") else "r"
        end = "fore" if slot.startswith("fore_") else "hind"
        return "%s_%s_paw" % ("left" if side == "l" else "right", end)
    for stem, end in (("scapula", "fore"), ("humerus", "fore"),
                      ("radius", "fore"), ("carpals", "fore"),
                      ("femur", "hind"), ("tibia", "hind"),
                      ("tarsals", "hind")):
        if slot.startswith(stem + "_"):
            side = slot.rsplit("_", 1)[-1]
            side = "left" if side.startswith("l") else "right"
            return "%s_%s_paw" % (side, end)
    return "trunk"


def build(scene, chosen):
    """A csc.rig.QrtData carrying the matched joints."""
    by_name = joints(scene)
    handle = scene.model_viewer()

    sections = {"trunk": [], "left_fore_paw": [], "right_fore_paw": [],
                "left_hind_paw": [], "right_hind_paw": []}
    order = {s: i for i, s in enumerate(slot_names())}
    for slot in sorted(chosen, key=lambda s: order.get(s, 999)):
        object_id = by_name.get(chosen[slot])
        if object_id is None:
            continue
        prop = csc.rig.BoneProperty()
        prop.bone_name = slot
        prop.object_id = object_id
        prop.joint_path_name = csc.model.PathName.get_path_name(object_id,
                                                                handle)
        sections[_section_of(slot)].append(prop)

    quad = csc.rig.QuadrupedBoneProperty()
    quad.trunk = [sections["trunk"]]
    quad.left_fore_paw = [sections["left_fore_paw"]]
    quad.right_fore_paw = [sections["right_fore_paw"]]
    quad.left_hind_paw = [sections["left_hind_paw"]]
    quad.right_hind_paw = [sections["right_hind_paw"]]

    data = csc.rig.QrtData()
    data.quadruped = [quad]
    data.is_create_layers = True
    data.is_replace_existing = True
    data.is_align_pelvis = True
    return data


def apply(scene=None, chosen=None):
    """Build the quadruped rig from a mapping. Changes the scene."""
    app = csc.app.get_application()
    current = app.get_scene_manager().current_scene().domain_scene()
    if scene is None:
        scene = current
    elif scene is not current:
        # Cascadeur re-resolves every joint against whatever scene is in
        # front, not the one handed in, so building into a background scene
        # would look up this animal's joints in a different animal.
        print("that scene is not the one in front - open it first")
        return None

    if chosen is None:
        chosen, leftover, notes = match(scene)
    if "pelvis" not in chosen or len(chosen) < 20:
        print("only %d slots matched - not building a rig from that" %
              len(chosen))
        return None
    data = build(scene, chosen)

    from prototypes.qrt_prototypes import create
    defaults = csc.rig.AddElementData()
    create.create_quadruped_rig(scene, data,
                                defaults.offset_point_controller,
                                defaults.joint_size_without_child,
                                defaults.point_color, "")
    print("built a quadruped rig from %d joints" % len(chosen))
    return chosen


# The Quick Rigging Tool's own field names. They are the slot names except
# for the paws, which Cascadeur renames on the way in - see
# Quadruped._get_field_names_mapping(). Sending "fore_paw_l" to the panel
# leaves that field empty.
PANEL_NAME = {
    "fore_paw_l": "paw_f_l", "fore_paw_r": "paw_f_r",
    "hind_paw_l": "paw_h_l", "hind_paw_r": "paw_h_r",
    "fore_paw_l_end": "paw_f_end_l", "fore_paw_r_end": "paw_f_end_r",
    "hind_paw_l_end": "paw_h_end_l", "hind_paw_r_end": "paw_h_end_r",
}

# The panel's sections and fields.
#
# Sending it every slot the match had found looked harmless and was not - the
# panel then filled nothing at all, where before it had at least guessed a
# humanoid, so one unrecognised name costs the whole file. The shipped
# Quadruped.qrigcasc fills only 31 fields and is no guide to what exists, so
# these come from the field table in tools.dll. Two things it settles:
# `stomach`, the paw ends and the toes are real fields, and **the quadruped
# panel has no ears at all** - the ear entries were what broke the load. Note
# also that the panel stops at neck_2 and tail_4 where the slot list runs to
# neck_8 and tail_8.
PANEL_SECTIONS = (
    ("Trunk", ("pelvis", "stomach", "spine_1", "spine_2", "spine_3",
               "neck_1", "neck_2", "head", "head_end")),
    ("Left fore leg", ("scapula_l", "humerus_l", "radius_l", "carpals_l",
                       "fore_paw_l", "fore_paw_l_end",
                       "fore_toes_l", "fore_toes_l_end")),
    ("Right fore leg", ("scapula_r", "humerus_r", "radius_r", "carpals_r",
                        "fore_paw_r", "fore_paw_r_end",
                        "fore_toes_r", "fore_toes_r_end")),
    ("Left hind leg", ("femur_l", "tibia_l", "tarsals_l",
                       "hind_paw_l", "hind_paw_l_end",
                       "hind_toes_l", "hind_toes_l_end")),
    ("Right hind leg", ("femur_r", "tibia_r", "tarsals_r",
                        "hind_paw_r", "hind_paw_r_end",
                        "hind_toes_r", "hind_toes_r_end")),
    ("Tail", ("tail_1", "tail_2", "tail_3", "tail_4", "tail_end")),
)

PANEL_KNOWN = frozenset("""
pelvis stomach spine_1 spine_2 spine_3 neck_1 neck_2 head head_end
scapula_l humerus_l radius_l carpals_l paw_f_l paw_f_end_l
fore_toes_l fore_toes_l_end
scapula_r humerus_r radius_r carpals_r paw_f_r paw_f_end_r
fore_toes_r fore_toes_r_end
femur_l tibia_l tarsals_l paw_h_l paw_h_end_l
hind_toes_l hind_toes_l_end
femur_r tibia_r tarsals_r paw_h_r paw_h_end_r
hind_toes_r hind_toes_r_end
tail_1 tail_2 tail_3 tail_4 tail_end
""".split())


def template(scene, chosen=None, extras=False):
    """The match as the JSON the Quick Rigging Tool reads."""
    import json

    if chosen is None:
        chosen, _, _ = match(scene)

    by_name = joints(scene)
    parent_of = parents(scene)
    name_of = {i: n for n, i in by_name.items()}

    def joint_path(bone):
        """Every ancestor, root first, helpers included - as the panel wants."""
        out, node = [], parent_of.get(by_name[bone])
        while node is not None and node in name_of:
            out.append(name_of[node])
            node = parent_of.get(node)
        out.reverse()
        return out

    sections, skipped = [], []
    for title, slots in PANEL_SECTIONS:
        names = []
        for slot in slots:
            bone = chosen.get(slot)
            if bone is None:
                continue
            field = PANEL_NAME.get(slot, slot)
            if not extras and field not in PANEL_KNOWN:
                skipped.append(field)
                continue
            names.append({"Bone name": field,
                          "Joint name": bone,
                          "Joint path": joint_path(bone)})
        if names:
            sections.append({"Section": title, "Names": names})

    if skipped:
        print("left out %d fields the panel does not have: %s"
              % (len(skipped), ", ".join(skipped)))
    document = {"Document": [{"Title": "Trunk", "Sections": sections}],
                "Settings": {"Is align pelvis": True,
                             "Is create layers": True}}
    filled = sum(len(s["Names"]) for s in sections)
    return json.dumps(document, indent=4), filled


def register(scene=None, chosen=None):
    """Fill the Quick Rigging Tool's fields with the match.

    `RiggingToolWindowTool` is the panel, and its editor takes the template
    as a string. Two other routes were tried first and neither works: a
    .qrigcasc on disk (the panel's Import is the only thing that reads one,
    and it was ignoring the file for the reason below), and writing the
    `qrt_json` behaviour on the scene (nothing holds it until a rig exists).
    """
    import json

    app = csc.app.get_application()
    if scene is None:
        scene = app.get_scene_manager().current_scene().domain_scene()
    text, filled = template(scene, chosen)

    # The panel's own loader. Writing the string onto the scene's QrtInfo
    # only works once a rig exists to hold it; this goes straight into the
    # open tool.
    window = app.get_tools_manager().get_tool("RiggingToolWindowTool")
    editor = window.editor(app.current_scene())
    editor.load_template_by_content(text)

    # It reports nothing either way, so ask it what it now holds. Loading a
    # quadruped template while the tool is set to a humanoid character is
    # accepted in silence and changes nothing - Cascadeur's own
    # Quadruped.qrigcasc behaves exactly the same way - so a silent success
    # is not one.
    back = editor.get_template_from_qrt()
    titles = [d.get("Title") for d in json.loads(back or "{}").get("Document", [])]
    if "Trunk" not in titles:
        print("the Quick Rigging Tool did not take it - it is holding %s. "
              "Switch it to a four-legged character and run this again."
              % (", ".join(t for t in titles if t) or "nothing"))
        return 0
    print("registered %d fields with the Quick Rigging Tool" % filled)
    return filled


def write_template(scene, path, chosen=None, extras=False):
    """Save the match as a .qrigcasc the Quick Rigging Tool can load.

    Building the rig straight from Python works, but it goes round the panel,
    and the panel is where the joints are meant to show up - registered, so
    they can be looked over and corrected before anything is generated. The
    panel has no API. It does have a template format, the same one Cascadeur
    ships Metahuman.qrigcasc and Quadruped.qrigcasc in, so writing one of
    those is how the mapping gets in front of you.
    """
    import json

    if chosen is None:
        chosen, _, _ = match(scene)

    by_name = joints(scene)
    parent_of = parents(scene)
    name_of = {i: n for n, i in by_name.items()}

    def joint_path(bone):
        """Every ancestor, root first, helpers included - as the panel wants."""
        out, node = [], parent_of.get(by_name[bone])
        while node is not None and node in name_of:
            out.append(name_of[node])
            node = parent_of.get(node)
        out.reverse()
        return out

    sections, skipped = [], []
    for title, slots in PANEL_SECTIONS:
        names = []
        for slot in slots:
            bone = chosen.get(slot)
            if bone is None:
                continue
            field = PANEL_NAME.get(slot, slot)
            if not extras and field not in PANEL_KNOWN:
                skipped.append(field)
                continue
            names.append({"Bone name": field,
                          "Joint name": bone,
                          "Joint path": joint_path(bone)})
        if names:
            sections.append({"Section": title, "Names": names})

    document = {"Document": [{"Title": "Trunk", "Sections": sections}],
                "Settings": {"Is align pelvis": True,
                             "Is create layers": True}}
    with open(path, "w", encoding="utf-8") as out:
        json.dump(document, out, indent=4)
    if skipped:
        print("left out %d fields the panel is not known to have: %s"
              % (len(skipped), ", ".join(skipped)))
    return sum(len(s["Names"]) for s in sections)


def name():
    return "Animation Scripts.Rig Quadruped (auto)"


def description():
    return ("Work out which joint is which on a four-legged character and "
            "build its rig, instead of filling 161 fields by hand")


def run(scene):
    """The panel, with the one-shot match behind it as a fallback."""
    try:
        csc.view.PythonModelsManager.instance() \
            .show_qml_view("quadruped_autorig")
        return
    except Exception as error:
        scene.warning("Panel unavailable (%s) - matching in one go instead."
                      % error)
    run_once(scene)


def run_once(scene):
    """Match and register without the panel."""
    chosen, leftover, notes = match(scene)
    for note in notes:
        scene.warning(note)
    if "pelvis" not in chosen or len(chosen) < 20:
        scene.error("Only %d joints could be placed - this does not look "
                    "like a quadruped skeleton." % len(chosen))
        return

    report(scene)
    filled = register(scene, chosen)
    if not filled:
        scene.error("Matched %d joints but could not hand them to the Quick "
                    "Rigging Tool - see the console." % len(chosen))
        return
    scene.info("Registered %d joints with the Quick Rigging Tool. Close and "
               "reopen it to see them, and check the mapping in the console "
               "before generating." % filled)


if __name__ == "__main__":
    report()
