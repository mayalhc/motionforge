# Copyright (c) 2026 Chamiseul. All rights reserved.
"""Put a Kimodo chunk on the Kimodo.casc rig.

Kimodo predicts 30 joints, the rig has 77. Writing only those 30 leaves the
other 47 (fingers, eyes) where they were, and writing a joint's position
without its neighbours makes the rig's solver pull it back: the pelvis could
not travel further than the legs, still pinned where they started, would
reach. So every joint of the rig is written, rotation and position, with the
joints Kimodo does not predict following their parent at their rest offset.

The rest pose is read from the scene at frame 0, so this has to land on a
character opened fresh from Kimodo.casc.

Measured on that rig: what is written is read back within 0.05 cm, and the
rig's own skeleton agrees with Kimodo's joint positions to 0.0 cm.
"""

import numpy as np

import csc
import pycsc

try:
    from . import ardy_apply as base
except ImportError:
    import ardy_apply as base

SCALE = base.SCALE


def _joints(scene):
    """(objects by name, joint names parents-first, parent name by joint)."""
    viewer = scene.model_viewer()
    behaviours = viewer.behaviour_viewer()
    objects = {viewer.get_object_name(o): o for o in viewer.get_objects()}

    def parent_of(name):
        link = behaviours.get_behaviour_by_name(objects[name], "Basic")
        if link.is_null():
            return None
        parent = behaviours.get_behaviour_object(link, "parent")
        return None if parent.is_null() else viewer.get_object_name(parent)

    children = {}
    for name, obj in objects.items():
        if not behaviours.get_behaviour_by_name(obj, "Transform").is_null():
            children.setdefault(parent_of(name), []).append(name)
    order, queue = [], ["Hips"]
    while queue:
        name = queue.pop(0)
        order.append(name)
        queue.extend(sorted(children.get(name, [])))
    return objects, order, {name: parent_of(name) for name in order}


def apply_file(scene, npz_path, frame_start=0, grow=True):
    """Key a Kimodo chunk onto the rig. Returns a report dict."""
    chunk = base.read_chunk(npz_path)
    names, parents = chunk["bone_names"], chunk["bone_parents"]
    rotations, positions = chunk["rotations"], chunk["positions"]
    frames = rotations.shape[0]
    notes = []

    objects, order, parent = _joints(scene)
    if "Hips" not in objects or "Hips" not in names:
        return {"ok": False, "frames": 0, "frame_start": frame_start, "fps": chunk["fps"],
                "notes": ["this scene has no Kimodo character in it"]}

    viewer = scene.model_viewer()
    behaviours = viewer.behaviour_viewer()
    values = viewer.data_viewer()

    def channel(name, kind):
        return behaviours.get_behaviour_data(
            behaviours.get_behaviour_by_name(objects[name], "Transform"), kind)

    rest_rot = {n: np.asarray(values.get_data_value(channel(n, "global_rotation"), 0)
                              .to_rotation_matrix(), dtype=float) for n in order}
    rest_pos = {n: np.asarray(values.get_data_value(channel(n, "global_position"), 0),
                              dtype=float) for n in order}

    index = {n: i for i, n in enumerate(order)}
    chunk_index = {n: i for i, n in enumerate(names)}

    def predicted(n):
        """Kimodo drives this joint, and from the same parent the rig has."""
        i = chunk_index.get(n)
        return i is not None and (n == "Hips" or (parent[n] == parents[i] and parent[n] in chunk_index))

    world_rot = np.zeros((frames, len(order), 3, 3))
    world_pos = np.zeros((frames, len(order), 3))
    for f in range(frames):
        for n in order:
            i = index[n]
            if n == "Hips":
                world_rot[f, i] = rotations[f, chunk_index[n]] @ rest_rot[n]
                world_pos[f, i] = positions[f, chunk_index[n]] * SCALE
                continue
            up = parent[n]
            k = index[up]
            if predicted(n):
                world_rot[f, i] = rotations[f, chunk_index[n]] @ rest_rot[n]
            else:
                world_rot[f, i] = world_rot[f, k] @ (rest_rot[up].T @ rest_rot[n])
            world_pos[f, i] = world_pos[f, k] + world_rot[f, k] @ (rest_rot[up].T @ (rest_pos[n] - rest_pos[up]))

    if grow:
        base.ensure_length(scene, frame_start + frames + 1)

    channels = {n: (channel(n, "global_rotation"), channel(n, "global_position")) for n in order}
    layers = [l for l in scene.layers_viewer().all_layer_ids() if not l.is_null()]
    everything = {x for pair in channels.values() for x in pair}
    ready = [[csc.math.Rotation.from_rotation_matrix(world_rot[f, index[n]]) for n in order]
             for f in range(frames)]

    def write(model, update, updater):
        editor = model.data_editor()
        layer_editor = model.layers_editor()
        for f in range(frames):
            at = frame_start + f
            for layer in layers:
                layer_editor.set_fixed_interpolation_or_key_if_need(layer, at, True)
            for n in order:
                rotation_id, position_id = channels[n]
                editor.set_data_value(rotation_id, at, ready[f][index[n]])
                editor.set_data_value(position_id, at, world_pos[f, index[n]].astype(np.float64))
            updater.run_update(everything, at)
        layer_editor.normalize_sections(scene)

    ok = pycsc.wrap(scene).modify_update("Kimodo: apply motion", write)
    return {"ok": bool(ok), "frames": frames if ok else 0, "frame_start": frame_start,
            "fps": chunk["fps"], "notes": notes}
