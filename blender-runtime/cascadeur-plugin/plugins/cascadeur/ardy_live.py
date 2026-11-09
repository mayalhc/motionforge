# Copyright (c) 2026 Chamiseul. All rights reserved.
"""ARDY Live - motion generation for Cascadeur, built up as a list of takes.

Build a list of takes ("walk" for 120 frames, then "run" for 80), press
Generate, and get the whole thing as one continuous motion in a scene of its
own, skeleton and body mesh included.

The takes are chained inside the ARDY service, not here - each take starts
from the previous take's own last frames as history, which is what an
autoregressive model is for, and what makes "walk" flow into "run" instead of
cutting between two separate clips. That placement is deliberate. Joining
motion on the Cascadeur side was tried first, through Cascadeur's own
View.Retargeting_Copy/Paste actions, and it crashed Cascadeur every time
(access violation at one fixed address in presenter_lib.dll; a settle delay
between every step changed nothing, ruling out a timing race). Retargeting is
built for Cascadeur's Rig system, and a bare imported ARDY skeleton has no rig
at all. Stitching in torch instead sidesteps that entirely: one chunk arrives
already whole.

The result lands on the ARDY.casc sample character (Cascadeur's samples
folder, same place Kimodo.casc lives for the Kimodo roundtrip) rather than a
freshly imported skeleton. ARDY.casc already carries a Cascadeur rig built for
ARDY's skeleton, so each Generate loads that sample scene fresh via
ProjectLoader.load_from() and then writes the motion onto it directly - see
ardy_apply.py.

There is no FBX anywhere in that path any more, and no Blender. Every chunk
used to be baked to FBX by a headless Blender launched per chunk, then handed
to import_animation() to be parsed back into the numbers it started as. It was
the slowest step in the loop and the most fragile: a drift between the FBX's
root name and the rig's was reported only as "No object with id cskel27 was
found in import data" in the event log, while the call itself returned
normally.

The ARDY service is started separately, with start_ardy.bat, never from here.
An early version launched it with subprocess.Popen inside a dialog callback,
blocking for several seconds while the model loaded, and Cascadeur died on
that call every time (STATUS_STACK_BUFFER_OVERRUN in ucrtbase.dll, two crash
dumps). Everything this file sends the service is a plain HTTP request to
something already running.
"""

import json
import os
import time
import urllib.error
import urllib.request

import csc

try:
    # Normal case: Cascadeur runs this as a real file, so __file__ is set.
    _MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # __file__ is only bound when Python runs a script from disk. Pasting
    # this file's text into Cascadeur's Python console instead exec()'s it
    # from a string, which never defines __file__. Without this fallback,
    # hitting the "ARDY not running" message below would raise its own
    # NameError instead of the intended RuntimeError.
    _MODULE_DIR = os.getcwd()

ARDY_BACKEND_PORT = 9553
ARDY_BACKEND_URL = f"http://127.0.0.1:{ARDY_BACKEND_PORT}"

# The rigged sample character each Generate loads before importing motion
# onto it - the ARDY counterpart to Kimodo.casc.
ARDY_SCENE_PATH = r"C:\Program Files\Cascadeur\samples\ARDY.casc"
KIMODO_SCENE_PATH = r"C:\Program Files\Cascadeur\samples\Kimodo.casc"

# Session-lifetime state: Cascadeur's Python process outlives any one script
# call, so the next click can still find the scene the last Generate made.
LIVE_STATE = {
    "live_view": None,
    "frames": 0,
    "segments": [],
}


def name():
    return "Animation Scripts.ARDY Live"


def description():
    return "ARDY: queue up takes and generate them as one continuous motion"


def _http_json_request(url, method="GET", payload=None, timeout=60):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read()
            if not raw_body:
                return {}
            return json.loads(raw_body.decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw_body = error.read()
        message = f"HTTP {error.code}"
        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
                message = payload.get("error") or payload.get("message") or message
            except Exception:
                try:
                    message = raw_body.decode("utf-8", errors="replace")
                except Exception:
                    pass
        raise RuntimeError(message) from error
    except urllib.error.URLError as error:
        raise RuntimeError(str(error.reason)) from error


def _status_reporter(scene):
    class Reporter:
        def update(self, text):
            try:
                scene.info(text)
            except Exception:
                pass

        def progress(self, text):
            self.update(text)

        def close(self):
            return

    return Reporter()


def _ardy_running():
    try:
        snapshot = _http_json_request(f"{ARDY_BACKEND_URL}/health", timeout=3)
    except Exception:
        return None
    return snapshot


def _require_ardy_running():
    """No subprocess call here on purpose - see the module docstring for why.
    Run start_ardy.bat first; this only ever does a quick /health check."""
    snapshot = _ardy_running()
    if not snapshot:
        raise RuntimeError(
            "The ARDY service is not running. Run start_ardy.bat first (in "
            f"{os.path.dirname(_MODULE_DIR) or '...'}), wait for "
            "'ARDY live service is ready.', then try again.")
    if snapshot.get("warming_up"):
        raise RuntimeError("ARDY is still loading. Wait for start_ardy.bat to "
                          "print 'ARDY live service is ready.'")
    if not snapshot.get("loaded"):
        raise RuntimeError(snapshot.get("load_error") or "ARDY failed to load.")
    return snapshot


def _discard_previous_live_scene(scene_manager):
    """Close the tab the last take landed in, if it is still open.

    Each Generate imports into a scene of its own, so without this every
    run left another ARDY tab behind and they piled up. Removing the current
    scene while it is the active one is not safe, so switch to some other
    open scene first.
    """
    previous = LIVE_STATE.get("live_view")
    LIVE_STATE["live_view"] = None
    if not _scene_is_open(scene_manager, previous):
        return
    try:
        for other in scene_manager.scenes():
            if other is not previous:
                scene_manager.set_current_scene(other)
                break
        scene_manager.remove_application_scene(previous)
    except Exception:
        # A tab left behind is untidy; failing the take over it is worse.
        pass


def _scene_is_open(scene_manager, scene_view):
    """Touches the scene rather than trusting Python identity alone: the
    wrapper can outlive the native scene behind it, and reusing one that
    has gone raises deep inside Cascadeur with no clean way to recover."""
    if scene_view is None:
        return False
    try:
        if not any(existing is scene_view for existing in scene_manager.scenes()):
            return False
    except Exception:
        return False
    try:
        scene_view.domain_scene()
        return True
    except Exception:
        return False


def _settle(seconds=0.3):
    """Cascadeur exposes no "wait for scene ready" hook, and every scene here
    is created and populated in the same call - the pattern the crashes below
    happened on.

    Two access-violation crashes in presenter_lib.dll (Cascadeur's renderer),
    same address both times, happened exactly at that pattern: create/populate
    a scene, then immediately select objects and fire an action on it. A
    plain time.sleep is a blunt tool, but there is no better one exposed here,
    and it is a small price for not crashing the whole application.
    """
    time.sleep(seconds)


def _load_ardy_sample_scene(scene_manager, scene_view):
    """Loads the rigged ARDY.casc sample character into an empty application
    scene - the ARDY counterpart of _load_scene_into_view() in
    kimodo_roundtrip.py for Kimodo.casc.

    ProjectLoader.load_from() is for .casc project files, which is exactly
    what this is: a saved character, rig included, not a raw FBX chunk.
    """
    scene_manager.set_current_scene(scene_view)
    _settle()
    try:
        csc.app.ProjectLoader.load_from(ARDY_SCENE_PATH, scene_view.domain_scene())
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load the ARDY sample scene ({ARDY_SCENE_PATH}) into "
            f"Cascadeur: {exc}") from exc
    scene_manager.set_current_scene(scene_view)
    _settle()


def _dump_scene_object_names(scene_view, limit=40):
    """Names of every object already in scene_view - diagnostic only, for
    comparing against the incoming FBX's own naming when import_animation
    can't match them up. Uses the same model_viewer().get_objects() +
    get_object_name() pattern kimodo_roundtrip.py's _object_list_summary()
    already relies on, rather than guessing at an API."""
    try:
        scene = scene_view.domain_scene()
        mv = scene.model_viewer()
        object_ids = list(mv.get_objects())
        names = []
        for obj_id in object_ids[:limit]:
            try:
                names.append(mv.get_object_name(obj_id))
            except Exception as exc:
                names.append(f"<name lookup failed: {exc}>")
        if len(object_ids) > limit:
            names.append(f"... ({len(object_ids)} total)")
        return names
    except Exception as exc:
        return [f"<dump failed: {exc}>"]


def _apply_generated_animation(scene_manager, scene_view, reply, reporter=None,
                               frame_start=0, engine="ardy"):
    """Put a generated chunk on the ARDY.casc rig already in scene_view.

    Straight from the numbers the model produced. The FBX route this replaces
    launched a headless Blender per chunk to write a file Cascadeur then parsed
    back into the same numbers - and it broke silently whenever the FBX's root
    name drifted from what the rig expected ("No object with id cskel27 was
    found in import data", logged and otherwise invisible).
    """
    chunk_path = reply.get("chunk_path")
    if not chunk_path:
        raise RuntimeError(
            "The ARDY service did not return a chunk path. It is probably an "
            "older build - restart it so /stream/step returns 'chunk_path'.")

    try:
        from . import ardy_apply
    except ImportError:
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import ardy_apply

    scene_manager.set_current_scene(scene_view)
    if engine == "kimodo":
        try:
            from . import soma_apply as applier
        except ImportError:
            import soma_apply as applier
    else:
        applier = ardy_apply
    report = applier.apply_file(scene_view.domain_scene(), chunk_path,
                                frame_start=frame_start)
    # A take lands in a tab of its own, and a new tab starts on Cascadeur's
    # default viewport mode rather than the one the last scene was using.
    if not ardy_apply.use_autoposing_view(scene_view) and reporter is not None:
        reporter.update("ARDY Live: switch the viewport to AutoPosing to see "
                        "the controllers.")
    if reporter is not None:
        for note in report["notes"]:
            reporter.update(f"ARDY Live: {note}")
    if not report["ok"]:
        raise RuntimeError(
            f"Could not put {os.path.basename(chunk_path)} on the rig. See "
            f"Cascadeur's event log for the failure inside the modify call.")
    return report


# The take list and the knobs behind it, for this Cascadeur session. Module
# level on purpose: Cascadeur's Python process outlives any one command call,
# so a list built up over several clicks has to live somewhere that does too.
TAKES = []

SETTINGS = {
    # None means the service picks one, so two runs of the same prompt differ.
    "seed": None,
    "diffusion_steps": None,
    # How many frames of the previous take the next one conditions on, and how
    # many it spends crossing over. None leaves the service's own defaults -
    # the numbers quoted in the panel are those defaults, not settings.
    "history_frames": None,
    "transition_frames": None,
}


def _total_take_frames():
    return sum(int(take.get("frames") or 0) for take in TAKES)


def _takes_summary():
    if not TAKES:
        return ["  (none yet - press Add take)"]
    return [f"  {index}. {take['prompt']}  ({take['frames']} frames)"
            for index, take in enumerate(TAKES, 1)]


def run_add_take(scene):
    """Ask for one take's prompt and length, and queue it."""
    def callback(prompt, frames):
        if prompt is None:
            return
        prompt = str(prompt).strip()
        if not prompt:
            scene.warning("A take needs a prompt.")
            return
        try:
            count = int(frames)
        except (TypeError, ValueError):
            scene.warning(f"{frames!r} is not a frame count.")
            return
        if count <= 0:
            scene.warning("A take needs at least one frame.")
            return
        TAKES.append({"prompt": prompt, "frames": count})
        scene.info(f"Take {len(TAKES)} added: {prompt} ({count} frames)")

    csc.view.DialogManager.instance().show_inputs_dialog(
        "ARDY Live - add take",
        ["Prompt", "Frames"],
        ["a person walks forward", "120"],
        2,
        callback,
    )


def run_clear_takes(scene):
    count = len(TAKES)
    TAKES.clear()
    scene.info(f"Cleared {count} take(s).")


def run_settings(scene):
    """Seed and the two take-joining knobs. Blank means "let ARDY decide"."""
    def callback(seed, history, transition):
        def number(value, label):
            text = str(value).strip() if value is not None else ""
            if not text:
                return None
            try:
                return int(text)
            except ValueError:
                scene.warning(f"{label} {text!r} is not a number - left as it was.")
                return SETTINGS[label]

        SETTINGS["seed"] = number(seed, "seed")
        SETTINGS["history_frames"] = number(history, "history_frames")
        SETTINGS["transition_frames"] = number(transition, "transition_frames")
        scene.info(f"Seed {SETTINGS['seed'] if SETTINGS['seed'] is not None else 'random'}, "
                   f"history {SETTINGS['history_frames'] or 40}, "
                   f"take change {SETTINGS['transition_frames'] or 4}.")

    def text(key):
        value = SETTINGS.get(key)
        return "" if value is None else str(value)

    csc.view.DialogManager.instance().show_inputs_dialog(
        "ARDY Live - settings",
        ["Seed (blank = random)", "History frames (blank = 40)",
         "Take change frames (blank = 4)"],
        [text("seed"), text("history_frames"), text("transition_frames")],
        3,
        callback,
    )


def _has_ardy_rig(scene_view):
    """Is the ARDY character already in this scene?"""
    try:
        viewer = scene_view.domain_scene().model_viewer()
        names = {viewer.get_object_name(o) for o in viewer.get_objects()}
    except Exception:
        return False
    return "cskel27" in names and "Hips" in names


def _is_empty(scene_view):
    try:
        return not scene_view.domain_scene().model_viewer().get_objects()
    except Exception:
        return False


def _scene_for_take(scene_manager, reporter, new_scene=True, engine="ardy"):
    """Where the take should land. Returns (scene_view, we_created_it).

    `new_scene` opens the character fresh, which is what leaves a tab per
    take - useful for comparing them, and the default. Turning it off lands
    on the ARDY tab already in front instead, so the tabs stop piling up;
    the take replaces what is on that character.

    The scene in front, whenever it can be used. A take used to open in a tab
    of its own, and a fresh tab is not the same as the one being worked in:
    the character comes up without its AutoPosing controllers, so the motion
    plays and cannot be touched. Landing on the character already open keeps
    whatever state it is in.

    A scene with other work in it is left alone - that gets a tab of its own,
    as before.
    """
    try:
        from . import ardy_apply
    except ImportError:
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import ardy_apply

    # Reuse the tab in front, when asked to and when it is an ARDY tab. No
    # reload: opening the character again is what makes a new tab, and the one
    # already there was opened properly, so it still has its AutoPosing.
    current = scene_manager.current_scene()
    if engine == "kimodo":
        # The rest pose is read from the scene, so it is always a fresh one.
        reporter.update("Kimodo: opening the character.")
        opened = ardy_apply.open_scene(KIMODO_SCENE_PATH)
        if opened is None:
            raise RuntimeError(f"Could not open {KIMODO_SCENE_PATH}.")
        _settle()
        if current is not None and current is not opened and _is_empty(current):
            try:
                scene_manager.remove_application_scene(current)
            except Exception:
                pass
        return opened, False
    if not new_scene:
        if _has_ardy_rig(current):
            reporter.update("ARDY Live: replacing the motion on this character.")
            scene_manager.set_current_scene(current)
            return current, False
        reporter.update("ARDY Live: no character in this tab - opening one.")

    # Opened the way the File menu opens it. Merging the file into a scene
    # that already exists - which is what this did - brings the character in
    # without the setup that opening a file normally does, and the rig lands
    # with no AutoPosing controllers on it.
    previous = current
    reporter.update("ARDY Live: opening the character.")
    opened = ardy_apply.open_scene()
    if opened is not None:
        _settle()
        # The tab the last take opened, if it is still around and empty of
        # anything worth keeping; otherwise they pile up one per Generate.
        if previous is not None and previous is not opened \
                and _is_empty(previous):
            try:
                scene_manager.remove_application_scene(previous)
            except Exception:
                pass
        return opened, False

    reporter.update("ARDY Live: could not open ARDY.casc - falling back.")
    current = scene_manager.current_scene()
    if _has_ardy_rig(current) or _is_empty(current):
        _load_ardy_sample_scene(scene_manager, current)
        return current, False

    _discard_previous_live_scene(scene_manager)
    made = scene_manager.create_application_scene()
    _settle()
    _load_ardy_sample_scene(scene_manager, made)
    return made, True


def _generate_core(reporter):
    """Everything Generate does, minus any dialog/scene-logging concerns.

    Shared by the dialog command (run_generate) and the QML panel, so
    the two UIs can never drift into different behavior. Raises on failure
    (callers decide how to surface that); returns the service's reply dict
    on success, after LIVE_STATE has already been updated.
    """
    if not TAKES:
        raise RuntimeError("No takes to generate. Add a take first.")
    _require_ardy_running()

    total = _total_take_frames()
    reporter.update(f"Generating {len(TAKES)} take(s), {total} frames...")
    reply = _http_json_request(
        f"{ARDY_BACKEND_URL}/takes/generate", method="POST",
        payload={"takes": TAKES,
                 "seed": SETTINGS["seed"],
                 "diffusion_steps": SETTINGS["diffusion_steps"],
                 "history_frames": SETTINGS["history_frames"],
                 "transition_frames": SETTINGS["transition_frames"]},
        # One model call per 40 frames, and the service replies only when
        # the whole list is done, so the wait scales with the take list
        # instead of being a fixed cost.
        timeout=180 + total * 3)

    _land_reply(reply, reporter)
    return reply


def _land_reply(reply, reporter, new_scene=True, engine="ardy"):
    """Open the character and put a generated reply on it.

    Split out from _generate_core so a caller that did its own HTTP - the
    panel, which does it on a worker thread to keep the window alive - can
    still land the result through exactly this path. Must run on the main
    thread: the scene is not thread-safe.
    """
    scene_manager = csc.app.get_application().get_scene_manager()
    live_view, made_it = _scene_for_take(scene_manager, reporter, new_scene, engine)
    try:
        _apply_generated_animation(scene_manager, live_view, reply, reporter=reporter,
                                   engine=engine)
    except Exception:
        if made_it:
            try:
                scene_manager.remove_application_scene(live_view)
            except Exception:
                pass
        raise

    LIVE_STATE.update({
        "live_view": live_view,
        "frames": int(reply.get("frames") or 0),
        "segments": reply.get("segments") or [],
    })
    return live_view


def run_generate(scene):
    """Dialog-command entry point for Generate - thin wrapper around
    _generate_core() that reports through this command's scene object."""
    reporter = _status_reporter(scene)
    try:
        _generate_core(reporter)
    except Exception as error:
        reporter.update("ARDY Live generate failed.")
        scene.error(f"{type(error).__name__}: {error}")
    finally:
        reporter.close()


# --- Entry point: Cascadeur imports this module once and calls run(scene) on
# every click of the command. ---
def run(scene):
    """The panel, with the old dialog chain behind it as a fallback.

    One menu entry either way. The panel is the better tool - every setting on
    screen at once instead of four modal prompts in a row - but it needs the
    QML model installed under Cascadeur's models folder, and an install that
    predates it should still work rather than dead-end.
    """
    try:
        csc.view.PythonModelsManager.instance().show_qml_view("ardy_live")
        return
    except Exception as error:
        scene.warning(f"ARDY Live panel unavailable ({type(error).__name__}: "
                      f"{error}); using dialogs. Rerun "
                      f"install_cascadeur_plugin.bat to install the panel.")
    _run_dialogs(scene)


def _run_dialogs(scene):
    snapshot = _ardy_running()
    lines = ["Takes run as one continuous motion - take 2 carries on from",
             "where take 1 ends.", "", "Takes:"]
    lines.extend(_takes_summary())
    if TAKES:
        lines.append(f"  Total: {_total_take_frames()} frames")
    lines.append("")
    lines.append(f"Seed: {'random' if SETTINGS['seed'] is None else SETTINGS['seed']}"
                 f"    History: {SETTINGS['history_frames'] or 40}"
                 f"    Take change: {SETTINGS['transition_frames'] or 4}")
    lines.append("ARDY: " + ("ready" if snapshot and snapshot.get("loaded")
                             else "not running - run start_ardy.bat first"))
    if LIVE_STATE.get("frames"):
        lines.append(f"Last result: {LIVE_STATE['frames']} frames "
                     "(the next Generate replaces that tab)")

    buttons = [csc.view.DialogButton("Add take", lambda: run_add_take(scene))]
    if TAKES:
        buttons.append(csc.view.DialogButton("Generate", lambda: run_generate(scene)))
        buttons.append(csc.view.DialogButton("Clear takes", lambda: run_clear_takes(scene)))
    buttons.append(csc.view.DialogButton("Settings", lambda: run_settings(scene)))
    buttons.append(csc.view.DialogButton(csc.view.StandardButton.Cancel))

    csc.view.DialogManager.instance().show_buttons_dialog(
        "ARDY Live", "\n".join(lines), buttons)
