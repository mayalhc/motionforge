# Copyright (c) 2026 Chamiseul. All rights reserved.
"""MetaArKit - ARKit facial capture onto a character's blendshapes.

Takes the per-frame blendshape table an ARKit-style capture produces - Live
Link Face and the apps built on it all export this shape - and keys the
matching blendshapes on the selected mesh, smoothing each curve on the way in.

    timestamp, browDown_L, browDown_R, eyeBlink_L, ...
    0.000,     0.012,      0.008,      0.94, ...

This replaces load_shapekeys_csv.py, which never ran. It imported scipy, which
Cascadeur's Python does not have, so the module failed to load and the command
was simply absent; and it declared `command_name()` where Cascadeur calls
`name()`, so it would have had no menu entry even with scipy present. It was
also never added to the plugin installer, so it had never been copied into
Cascadeur in the first place.

Three things beyond making it load:

- The blendshape lookup was done per frame per column - name by name, through
  the behaviour API, with fit_animation_size_by_layers() called each time.
  For 500 frames of a 52-shape capture that is 26,000 lookups. It is resolved
  once now, into a dict.
- The match was a substring test, so "eyeBlink_L" could land on
  "eyeBlinkSquint_L" depending on order. Exact match first, then a match that
  normalises case, separators and the spelling of the side - and nothing
  looser than that, because on a real MetaHuman head the substring test put
  mouthUpperUp_L on mouth_up.
- The importer ran `modify_update` immediately, before the file dialog had
  chosen anything, and again from the callback. The first call did nothing
  because there was no filename yet.

    report(scene, path)    what would be keyed, changes nothing
    load(scene, path, ...) do it
"""

import csv
import os
import re
import sys

import csc

def _load_savgol():
    """The smoothing filter, however this module is being loaded.

    Three different loaders reach this file: Cascadeur importing it as a
    command, the panel loading it by path, and a test importing it plainly.
    Only the first has the folder on sys.path, so `import savgol` worked in
    Cascadeur's menu and failed the moment the panel tried to use it - the
    command appeared and then did nothing at all. Reading the file beside
    this one works in all three.
    """
    try:
        from . import savgol as module
        return module
    except (ImportError, ValueError):
        pass
    try:
        import savgol as module
        return module
    except ImportError:
        pass

    import importlib.util
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "savgol.py")
    if not os.path.isfile(beside):
        raise ImportError("savgol.py is not installed beside %s"
                          % os.path.basename(__file__))
    loaded = sys.modules.get("_metaarkit_savgol")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location("_metaarkit_savgol",
                                                  beside)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


savgol = _load_savgol()


# Columns that are not blendshapes. A Live Link capture leads with a
# timestamp and often carries head pose and eye angles as well; keying those
# onto a blendshape by name match would be wrong, not merely useless.
NOT_SHAPES = re.compile(
    r"^(timecode|timestamp|time|frame|blendshapecount|"
    r"head(yaw|pitch|roll)|left ?eye(yaw|pitch|roll)|"
    r"right ?eye(yaw|pitch|roll))$", re.I)


SIDE_WORDS = {"l": "l", "left": "l", "lf": "l",
              "r": "r", "right": "r", "rt": "r"}


def normalise(name):
    """A blendshape name reduced to what two spellings of it share."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def split_side(name):
    """(stem, side) - 'mouthDimple_L' and 'mouth_dimple_left' agree here.

    The side has to come off before the rest is compared, or the two
    spellings of it stop the stems from matching and a looser test has to be
    used to recover them. That looser test is where the errors come from: on
    a real MetaHuman head a plain substring match put mouthUpperUp_L on
    mouth_up, which is a different shape entirely.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+",
                                 re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name))
             if w]
    side = ""
    if words and words[-1].lower() in SIDE_WORDS:
        side = SIDE_WORDS[words[-1].lower()]
        words = words[:-1]
    return normalise("".join(words)), side


def side_key(name):
    stem, side = split_side(name)
    return stem + "|" + side


def read_csv(path):
    """(headers, rows) with the non-blendshape columns dropped.

    Blank cells become None rather than 0.0: a gap in a capture is missing
    data, and writing zero into it would key the shape fully open-to-closed
    on every dropped frame.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            raw_headers = next(reader)
        except StopIteration:
            raise ValueError("that file is empty")
        keep = [i for i, name in enumerate(raw_headers)
                if not NOT_SHAPES.match(name.strip())]
        if not keep:
            raise ValueError("no blendshape columns in that file")
        headers = [raw_headers[i].strip() for i in keep]

        rows = []
        for line in reader:
            if not line:
                continue
            row = []
            for i in keep:
                cell = line[i].strip() if i < len(line) else ""
                try:
                    row.append(float(cell) if cell else None)
                except ValueError:
                    row.append(None)
            rows.append(row)
    if not rows:
        raise ValueError("that file has headers but no rows")
    return headers, rows


def _fill_gaps(column):
    """Carry the last known value across blanks, so a gap holds rather than
    snapping to zero. Leading blanks take the first real value."""
    out, last = [], None
    for value in column:
        if value is None:
            out.append(last)
        else:
            last = value
            out.append(value)
    first = next((v for v in out if v is not None), 0.0)
    return [first if v is None else v for v in out]


def blendshapes(scene, mesh_id):
    """{name: DataId} for every blendshape on this mesh."""
    viewer = scene.model_viewer()
    behaviours = viewer.behaviour_viewer()
    dynamic = behaviours.get_behaviour_by_name(mesh_id, "Dynamic")
    if dynamic.is_null():
        return {}
    values = behaviours.get_behaviour_data_range(dynamic, "datas")
    names = behaviours.get_behaviour_data_range(dynamic, "dataNames")
    data_viewer = scene.data_viewer()
    out = {}
    for name_id, value_id in zip(names, values):
        out[data_viewer.get_data(name_id).name] = value_id
    return out


def match_columns(headers, available):
    """(header -> blendshape name, headers that matched nothing).

    Names, not DataIds, so nothing here depends on a csc handle being
    hashable or comparable.

    Exact name, then the name with case, separators and the spelling of the
    side normalised away. Nothing looser: a plain substring test put
    eyeBlink_L on eyeBlinkSquint_L, and on a MetaHuman head it put
    mouthUpperUp_L on mouth_up. A column left unmatched is visible in the
    panel and can be dealt with; a column silently keyed onto the wrong
    shape is not.
    """
    by_normal, by_side = {}, {}
    for shape in available:
        by_normal.setdefault(normalise(shape), shape)
        by_side.setdefault(side_key(shape), shape)

    matched, missing, used = {}, [], set()
    for header in headers:
        shape = None
        if header in available and header not in used:
            shape = header
        if shape is None:
            candidate = by_normal.get(normalise(header))
            if candidate is not None and candidate not in used:
                shape = candidate
        if shape is None:
            candidate = by_side.get(side_key(header))
            if candidate is not None and candidate not in used:
                shape = candidate
        if shape is None:
            missing.append(header)
            continue
        matched[header] = shape
        used.add(shape)
    return matched, missing


def selected_mesh(scene):
    """The one selected object, or raise saying which way it went wrong."""
    selected = {sid for sid in scene.selector().selected().ids
                if isinstance(sid, csc.model.ObjectId)}
    if not selected:
        raise ValueError("Select the mesh that carries the blendshapes first.")
    if len(selected) > 1:
        raise ValueError("Select one mesh, not %d." % len(selected))
    return next(iter(selected))


def report(scene, path):
    """What would be keyed. Changes nothing."""
    mesh_id = selected_mesh(scene)
    headers, rows = read_csv(path)
    available = blendshapes(scene, mesh_id)
    if not available:
        raise ValueError("That object has no blendshapes.")
    matched, missing = match_columns(headers, available)
    return {
        "mesh": scene.model_viewer().get_object_name(mesh_id),
        "frames": len(rows),
        "columns": len(headers),
        "matched": matched,
        "missing": missing,
        "unused": sorted(s for s in available if s not in set(matched.values())),
        "available": available,
        "headers": headers,
        "rows": rows,
        "mesh_id": mesh_id,
    }


def load(scene, path, window_length=5, polyorder=3, scale=100.0,
         start_frame=0, smooth=False, progress=None):
    """Key the CSV onto the selected mesh. Returns a summary dict.

    Smoothing is off unless asked for. It belongs to raw capture, where the
    per-frame jitter of a phone camera is worth filtering; applied to a curve
    that is already the exact output of a rig, it only distorts it. Measured
    on a MetaHuman export: a mid-range value of 0.5020 came out at 0.4463,
    values that should have been exactly zero went negative, and the tail of
    the take overshot - which is precisely "it does not match what Blender
    showed".
    """
    if smooth:
        complaint = savgol.valid_window(window_length, polyorder)
        if complaint:
            raise ValueError(complaint)

    found = report(scene, path)
    matched, headers, rows = found["matched"], found["headers"], found["rows"]
    if not matched:
        raise ValueError("None of the %d columns match a blendshape on %s."
                         % (len(headers), found["mesh"]))

    # Smooth column by column, before touching the scene at all: a filter
    # that throws half way through would otherwise leave the take part keyed.
    index_of = {name: i for i, name in enumerate(headers)}
    available = found["available"]
    curves = []
    for header, shape in matched.items():
        column = _fill_gaps([row[index_of[header]] for row in rows])
        if smooth:
            column = savgol.smooth(column, window_length, polyorder)
        curves.append((available[shape],
                       [float(v) * scale for v in column]))

    layer_id = scene.layers_viewer().layer_id_by_obj_id(found["mesh_id"])
    frames = len(rows)
    last_frame = start_frame + frames - 1

    touched = {data_id for data_id, _ in curves}

    def write(model_editor, update_editor, scene_updater):
        data_editor = model_editor.data_editor()
        layers_editor = model_editor.layers_editor()
        # Once, not per frame: the animation only has to be long enough, and
        # fit_animation_size_by_layers() inside the loop was most of the cost.
        for frame in (start_frame, last_frame):
            layers_editor.set_fixed_interpolation_or_key_if_need(
                layer_id, frame, True)
        model_editor.fit_animation_size_by_layers()

        for offset in range(frames):
            frame = start_frame + offset
            layers_editor.set_fixed_interpolation_or_key_if_need(
                layer_id, frame, True)
            # A set, not a list: run_update's binding takes
            # set[csc.model.DataId] and rejects a list outright. DataId is
            # hashable, so building the set once outside the loop is free.
            for data_id, values in curves:
                data_editor.set_data_value(data_id, frame=frame,
                                           value=values[offset])
            scene_updater.run_update(touched, frame)
            if progress is not None and offset % 25 == 0:
                progress(offset, frames)

    if not scene.modify_update("MetaArKit import", write):
        raise RuntimeError("Cascadeur refused the change.")

    if progress is not None:
        progress(frames, frames)
    return {"mesh": found["mesh"], "frames": frames,
            "keyed": len(matched), "missing": found["missing"],
            "first": start_frame, "last": last_frame}


def name():
    return "Animation Scripts.MetaArKit - Facial Capture"


def description():
    return ("Key an ARKit-style blendshape CSV onto the selected mesh, "
            "smoothed")


def run(scene):
    """The panel, with a plain file dialog behind it as a fallback.

    The panel's model is imported first, deliberately. show_qml_view does not
    raise when the model behind it fails to import - it just shows nothing -
    and that is exactly what happened: the command sat in the menu and did
    nothing when clicked, with no message anywhere. Importing it here turns a
    silent no-op into either a working panel or a stated reason.
    """
    try:
        _check_panel()
    except Exception as error:
        scene.warning("Panel unavailable (%s) - using the file dialog instead."
                      % error)
        run_dialog(scene)
        return
    try:
        csc.view.PythonModelsManager.instance().show_qml_view("metaarkit")
    except Exception as error:
        scene.warning("Could not open the panel (%s) - using the file dialog."
                      % error)
        run_dialog(scene)


def _check_panel():
    """Import the panel's model, so a broken one is reported rather than mute."""
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    # Installed, the command sits in commands\animation_scripts and the panel
    # two directories up in models\; from a checkout they are siblings.
    places = [
        os.path.join(here, "models", "metaarkit", "model.py"),
        os.path.join(here, "..", "..", "models", "metaarkit", "model.py"),
    ]
    for path in places:
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location("_metaarkit_panel",
                                                      path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise ImportError("models\\metaarkit\\model.py is not installed")


def run_dialog(scene):
    """Pick a file and import it with the default smoothing."""
    try:
        selected_mesh(scene)
    except ValueError as error:
        scene.error(str(error))
        return

    def chosen(path):
        if not path:
            return
        try:
            done = load(scene, path)
        except Exception as error:
            scene.error(str(error))
            return
        scene.success("Keyed %d blendshapes over %d frames on %s."
                      % (done["keyed"], done["frames"], done["mesh"]))
        if done["missing"]:
            scene.warning("%d column(s) matched nothing: %s"
                          % (len(done["missing"]),
                             ", ".join(done["missing"][:8])))

    csc.app.get_application().get_file_dialog_manager().show_open_file_dialog(
        "Choose the mocap CSV", "", ["*.csv"], chosen)
