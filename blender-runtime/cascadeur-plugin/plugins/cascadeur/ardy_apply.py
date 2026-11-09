# Copyright (c) 2026 Chamiseul. All rights reserved.
"""Write an ARDY motion chunk straight onto the ARDY.casc rig.

This replaces the old route, which baked every chunk to FBX in a headless
Blender and imported that. The chunk is already numbers - joint rotations and
positions - and Cascadeur's rig takes numbers, so the detour bought nothing
and cost a Blender launch per chunk.

Four things have to be right, and each one of them fails quietly on its own:

1. The animation has to be long enough BEFORE anything is written, or every
   set_data_value is refused with "last frame in animation is 0".

2. ARDY's global_rot_mats are NOT world orientations. They are rotations
   against a rest pose where every joint's orientation is identity, so the
   world orientation is `R_ardy @ R_rest`. R_rest comes from the rig's own
   inverse bind matrices - measured, not guessed. Skip it and the hands come
   out rotated by exactly 180 degrees, which is what the wrists looked like.

3. Values go through the joint's Transform behaviour, not through
   root_group().node_deep("Position"). Writing the nodes directly pins the
   joints and Cascadeur then hides the AutoPosing controllers - the animation
   plays, but the character can no longer be posed. The Transform behaviour is
   the channel Cascadeur's own BVH importer uses.

4. Keyframes have to be seeded on EVERY layer, not just the layers the joints
   belong to. ARDY.casc spreads its rig over thirteen; the ten that hold the
   AutoPosing controllers would otherwise be left empty.

global_position is in centimetres and in the same frame as everything else.
(local_position is metres in a frame rotated -90 degrees about X - correct,
but there is no reason to work in it.)
"""

import numpy as np

import csc
import pycsc

# ARDY works in metres, Cascadeur's global channel in centimetres.
SCALE = 100.0

ARDY_SCENE_PATH = r"C:\Program Files\Cascadeur\samples\ARDY.casc"


def use_autoposing_view(scene_view):
    """Put this scene's viewport into AutoPosing mode.

    The mode is per viewport, not per character, and a scene created by
    create_application_scene() starts on whatever Cascadeur's default is - so
    a generated take opened in a tab of its own showed no AutoPosing
    controllers even though the rig has all 47 of its links. Nothing is wrong
    with the animation in that case; the tab is just looking at the character
    a different way.

    Best effort. A failure here is a viewport that needs one click, not a
    reason to lose the take.
    """
    try:
        viewport = scene_view.active_viewport().domain_viewport()
    except Exception:
        return False
    for setter in ("set_mode_visualizers", "set_mode"):
        method = getattr(viewport, setter, None)
        if method is None:
            continue
        try:
            method(csc.view.ViewportMode.AutoPosing)
            return True
        except Exception:
            continue
    return False


def open_scene(path=ARDY_SCENE_PATH):
    """Open the character the way File > Open does. Returns the scene, or None.

    DataSourceManager.load_scene is what the menu item calls.
    ProjectLoader.load_from, which this used before, only merges a file's data
    into a scene that already exists - the character arrives, and the setup
    that opening a file normally does never runs. Opened by hand the rig came
    up with its AutoPosing controllers; loaded this way it did not, and every
    other difference between the two had already been ruled out.
    """
    application = csc.app.get_application()
    manager = application.get_data_source_manager()
    try:
        opened = manager.load_scene(path)
    except Exception:
        opened = False
    if not opened:
        return None
    scene_view = application.get_scene_manager().current_scene()
    use_autoposing_view(scene_view)
    return scene_view


def load_scene(scene_manager, scene_view, path=ARDY_SCENE_PATH):
    """Merge the character into an existing scene.

    The older, lower-level route. Kept for callers that must land in a scene
    they already hold; open_scene() is the one to prefer.
    """
    scene_manager.set_current_scene(scene_view)
    csc.app.ProjectLoader.load_from(path, scene_view.domain_scene())
    scene_manager.set_current_scene(scene_view)
    use_autoposing_view(scene_view)


def read_chunk(source):
    """A chunk as plain arrays, from an .npz path or an already-loaded dict.

    `space` says what the rotations mean. ARDY writes them against a rest pose
    where every joint is identity, so they need the rig's own rest folded back
    in; anything sending world orientations - Blender, say - marks itself
    "world" and is written as it stands.
    """
    data = np.load(source, allow_pickle=True) if isinstance(source, str) else source
    keys = set(data.keys()) if hasattr(data, "keys") else set()
    space = "rest"
    if "space" in keys:
        space = str(np.asarray(data["space"]).item()
                    if hasattr(np.asarray(data["space"]), "item")
                    else data["space"])
    positions_for = "all"
    if "positions_for" in keys:
        positions_for = str(np.asarray(data["positions_for"]).item()
                            if hasattr(np.asarray(data["positions_for"]), "item")
                            else data["positions_for"])
    return {
        "bone_names": [str(n) for n in data["bone_names"]],
        "bone_parents": [str(n) for n in data["bone_parents"]],
        "rotations": np.asarray(data["global_rot_mats"]),
        "positions": np.asarray(data["posed_joints"]),
        "fps": float(np.asarray(data["fps"])),
        "space": space,
        "positions_for": positions_for,
    }


def rest_rotations(scene, bone_names):
    """Each joint's rest orientation, taken from the rig's bind matrices.

    Joints the skin does not bind - the hand tips, the thumbs - have no bind
    matrix. Identity is right for them: they inherit their parent's correction
    through the hierarchy.
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
            found[name] = np.linalg.inv(np.array(inverse[index]))[:3, :3]

    missing = [n for n in bone_names if n not in found]
    return [_orthonormal(found[n]) if n in found else np.eye(3)
            for n in bone_names], missing


def _orthonormal(matrix):
    """The rotation part alone, with the bind matrix's scale divided out.

    The inverse bind matrices carry the metre-to-centimetre scale, so
    inverting one gives a rotation multiplied by a hundred. Composing that
    with a pose still came out right because Rotation.from_rotation_matrix
    normalises what it is handed - but only at the very end, and anything
    that reads these values on the way past sees a matrix whose determinant
    is a million. Normalising here means a rest rotation is a rest rotation.
    """
    columns = []
    for index in range(3):
        column = matrix[:, index]
        length = np.linalg.norm(column)
        columns.append(column / length if length else column)
    return np.stack(columns, axis=1)


def transform_data(scene, bone_names):
    """The global rotation/position data ids for every joint, by name.

    global_position is centimetres in the same frame as everything else, which
    is what the chunk already holds. The local channel was tried instead - it
    is what Kimodo's writer uses - and the pose came out wrong by up to 75 cm,
    because the root's local frame is not the identity on this rig. Global is
    the one that is verified correct here.
    """
    viewer = scene.model_viewer()
    behaviours = viewer.behaviour_viewer()
    names = {viewer.get_object_name(o): o for o in viewer.get_objects()}

    out, absent = {}, []
    for name in bone_names:
        object_id = names.get(name)
        if object_id is None:
            absent.append(name)
            continue
        transform = behaviours.get_behaviour_by_name(object_id, "Transform")
        if transform.is_null():
            absent.append(name)
            continue
        out[name] = (behaviours.get_behaviour_data(transform, "global_rotation"),
                     behaviours.get_behaviour_data(transform, "global_position"))
    return out, absent


def fixed_section():
    """A section whose keys interpolate the way Cascadeur's own takes do.

    csc.layers.layer.Section() comes up on STEP - measured - and STEP is a
    held pose. A take written into STEP sections plays correctly and cannot be
    worked on: AutoPosing has nothing to solve, so its controllers never draw.
    Cascadeur's own animated samples sit on FIXED.

    It has to be right when the section is created. Changing it afterwards
    does not take: change_section reports success and leaves the value alone,
    and re-issuing set_section over an existing range only extends it.
    """
    section = csc.layers.layer.Section()
    section.interval.interpolation = csc.layers.layer.Interpolation.FIXED
    return section


def ensure_length(scene, frames):
    """Make room for `frames` frames on every layer.

    Nothing can be written past the end of the animation, and a freshly loaded
    character is one frame long.
    """
    layers = scene.layers_viewer()
    py_scene = pycsc.wrap(scene)

    def grow(model, update, updater):
        editor = model.layers_editor()
        for layer_id in layers.all_layer_ids():
            if not layer_id.is_null():
                editor.set_section(fixed_section(), frames, layer_id)
        model.fit_animation_size_by_layers()
        updater.generate_update()

    return py_scene.modify_update("ARDY: make room", grow)


def apply_chunk(scene, chunk, frame_start=0, rests=None, channels=None):
    """Key one chunk onto the rig. Returns (ok, frames_written, notes)."""
    names = chunk["bone_names"]
    rotations = chunk["rotations"]
    positions = chunk["positions"]
    count = rotations.shape[0]
    notes = []

    if rests is None:
        if chunk.get("space") == "world":
            # Already world orientations; folding the rig's rest in on top
            # would turn the hands through the very 180 degrees that
            # correction exists to undo.
            rests = [np.eye(3)] * len(names)
        else:
            rests, missing = rest_rotations(scene, names)
            if missing:
                notes.append(f"no bind matrix for {len(missing)}: "
                             f"{', '.join(missing[:5])}")
    if channels is None:
        channels, absent = transform_data(scene, names)
        if absent:
            notes.append(f"not on the rig: {', '.join(absent[:5])}")
    if not channels:
        return False, 0, notes + ["none of the chunk's joints are on this rig"]

    layers = scene.layers_viewer()
    all_layers = [l for l in layers.all_layer_ids() if not l.is_null()]
    py_scene = pycsc.wrap(scene)

    # Which joints get a position at all. "all" is right when the numbers came
    # from this very skeleton - ARDY's own chunks, or a character sent from
    # Blender and keyed straight back. It is wrong when the sender's skeleton
    # is a different shape: writing its joint positions drags this character's
    # bones to the sender's lengths, and the mesh, still skinned to the bones
    # it was bound to, breaks at the elbows and knees. Measured on an SMPL rig
    # sent onto cskel27 - the upper arm came out 27 cm, the sender's length,
    # not the character's. Rotations alone carry the pose; only the root needs
    # to say where the body is.
    place = set(names)
    if chunk.get("positions_for") == "root":
        parents = set(chunk.get("bone_parents") or [])
        place = {n for n in names if n not in parents} & set(names[:1] or [])
        if not place and names:
            place = {names[0]}

    # Built outside the callback: a modify session is not the place to be
    # doing matrix algebra, and a slow callback is what stalls Cascadeur.
    prepared = []
    for f in range(count):
        frame = []
        for j, name in enumerate(names):
            if name not in channels:
                continue
            world = rotations[f, j] @ rests[j]
            frame.append((name,
                          csc.math.Rotation.from_rotation_matrix(world),
                          (positions[f, j] * SCALE).astype(np.float64)
                          if name in place else None))
        prepared.append(frame)

    def write(model, update, updater):
        editor = model.data_editor()
        layer_editor = model.layers_editor()
        for offset, frame in enumerate(prepared):
            at = frame_start + offset
            touched = set()
            for name, rotation, position in frame:
                rotation_id, position_id = channels[name]
                if not rotation_id.is_null():
                    editor.set_data_value(rotation_id, at, rotation)
                    touched.add(rotation_id)
                if position is not None and not position_id.is_null():
                    editor.set_data_value(position_id, at, position)
                    touched.add(position_id)
            for layer_id in all_layers:
                layer_editor.set_fixed_interpolation_or_key_if_need(layer_id, at, True)
            model.set_fixed_interpolation_if_need(touched, at)
            updater.run_update(touched, at)

        layer_editor.normalize_sections(scene)

    ok = py_scene.modify_update("ARDY: apply motion", write)
    return bool(ok), count, notes


def apply_file(scene, npz_path, frame_start=0, grow=True, rests=None):
    """Read a chunk file and put it on the rig. Returns a report dict.

    `rests` overrides the orientations a rest-relative chunk is composed onto.
    The caller may know them better than the bind matrices do - a character
    imported from Blender has bones with no bind matrix at all.
    """
    chunk = read_chunk(npz_path)
    frames = chunk["rotations"].shape[0]
    if grow:
        ensure_length(scene, frame_start + frames + 1)
    ok, written, notes = apply_chunk(scene, chunk, frame_start=frame_start,
                                     rests=rests)
    return {
        "ok": ok,
        "frames": written,
        "frame_start": frame_start,
        "fps": chunk["fps"],
        "notes": notes,
    }
