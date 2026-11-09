# Copyright (c) 2026 Chamiseul. All rights reserved.
"""Named joint mappings for quadruped rigs, so a family is matched once.

The hierarchy matcher gets a rig it has never seen mostly right, and that is
the point of it - but "mostly" is the problem when it is wrong, because the
correction is thrown away the moment the scene closes. Every Fortnite animal
is boned the same way; so is every Mixamo one. A preset is that family's
answer, written down: which bone name fills which slot.

    {"name": ..., "bones": {"femur_l": "thigh_bk_l", ...}}

Applying one is a name lookup, so it costs nothing and cannot be wrong in the
way a guess can - a bone that is not in the character simply leaves its slot
empty, and the panel says how many of them landed. That number is also what
picks a preset automatically: the one that fits the open character best.

Built-in presets live beside this file; the ones you save go under Documents,
where they survive reinstalling the plugin.
"""

import json
import os

BUILT_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "quadruped_presets")
USER = os.path.join(os.path.expanduser("~"), "Documents",
                    "Cascadeur quadruped presets")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    bones = data.get("bones") or {}
    if not isinstance(bones, dict) or not bones:
        raise ValueError("no bones in %s" % os.path.basename(path))
    return {
        "name": data.get("name") or os.path.splitext(os.path.basename(path))[0],
        "note": data.get("note", ""),
        "bones": {str(k): str(v) for k, v in bones.items()},
        "path": path,
        "built_in": os.path.dirname(path) == BUILT_IN,
    }


def load_all():
    """Every preset, built-in first. Unreadable ones are skipped, not fatal."""
    out, seen = [], set()
    for folder in (BUILT_IN, USER):
        if not os.path.isdir(folder):
            continue
        for entry in sorted(os.listdir(folder)):
            if not entry.lower().endswith(".json"):
                continue
            try:
                preset = _read(os.path.join(folder, entry))
            except Exception as error:
                print("skipping preset %s: %s" % (entry, error))
                continue
            # A saved preset of the same name replaces the built-in, so a
            # correction to one of ours is not permanently shouted down by it.
            if preset["name"] in seen:
                out = [p for p in out if p["name"] != preset["name"]]
            seen.add(preset["name"])
            out.append(preset)
    return out


def save(name, bones, note=""):
    """Write a preset under Documents. Returns its path."""
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("a preset needs a name")
    if not bones:
        raise ValueError("nothing to save - match some joints first")
    os.makedirs(USER, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in " -_") else "_" for c in name)
    path = os.path.join(USER, safe.strip() + ".json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"name": name, "note": note, "bones": dict(bones)},
                  handle, indent=4, ensure_ascii=False)
    return path


def delete(preset):
    """Remove a saved preset. Built-in ones are left alone."""
    if preset.get("built_in"):
        raise ValueError("%s ships with the plugin and cannot be deleted"
                         % preset["name"])
    os.remove(preset["path"])


def resolve(preset, joint_names):
    """{slot: bone} for the bones this character actually has, plus the misses."""
    have = set(joint_names)
    landed = {slot: bone for slot, bone in preset["bones"].items()
              if bone in have}
    missing = sorted(slot for slot in preset["bones"] if slot not in landed)
    return landed, missing


def fit(preset, joint_names):
    """How much of this preset the character can take, 0.0 to 1.0."""
    landed, _ = resolve(preset, joint_names)
    return len(landed) / float(len(preset["bones"]) or 1)


def best(joint_names, presets=None, threshold=0.9):
    """The preset that fits this character, or None to fall back to matching.

    The threshold is high on purpose. A preset that half fits is worse than
    no preset: the slots it does fill are right, but the ones it misses look
    like the character has no such joint, and the shape matcher would have
    found them.
    """
    ranked = sorted(((fit(p, joint_names), p) for p in (presets or load_all())),
                    key=lambda pair: pair[0], reverse=True)
    if ranked and ranked[0][0] >= threshold:
        return ranked[0][1]
    return None
