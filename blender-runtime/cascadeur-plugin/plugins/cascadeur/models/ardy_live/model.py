# Copyright (c) 2026 Chamiseul. All rights reserved.
"""ARDY Live as one panel instead of a chain of dialogs.

Everything a take needs is on screen at once - prompt, length, seed, sampling,
how takes join - so the settings can be read and changed together rather than
remembered across four modal prompts in a row.

Generation runs on a worker thread. It has to: a take is one blocking HTTP
call that can last tens of seconds, and doing that in the slot froze the whole
window, progress bar included. The thread only talks to the service; the reply
is applied to the scene from a timer on the main thread, because Cascadeur's
scene is not thread-safe.

The QtWidgets panel this replaces killed Cascadeur outright. 2026.2 runs a
QGuiApplication, and constructing a QWidget under one takes the process down.
QML is what the application actually is.
"""

import os
import sys
import threading
import time

import csc
import shiboken6
from PySide6 import QtCore

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(os.path.dirname(_HERE))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)


def _project_root():
    """Where start_ardy.bat lives.

    Installed, this file lands in Cascadeur's models folder and the ini is two
    directories across in commands/animation_scripts; from a checkout they are
    siblings. Look in both rather than guessing which layout.
    """
    # kimodo_engine.ini is what the plugin installer writes. The older
    # kimodo_roundtrip.ini holds the same key and is still read, so a
    # machine that has only that one keeps working.
    names = ("kimodo_engine.ini", "kimodo_roundtrip.ini")
    ini_paths = [os.path.join(_PLUGIN_DIR, n) for n in names]
    ini_paths += [os.path.join(_PLUGIN_DIR, "commands",
                               "animation_scripts", n) for n in names]
    candidates = []
    for ini_path in ini_paths:
        if not os.path.isfile(ini_path):
            continue
        import configparser
        parser = configparser.ConfigParser()
        try:
            parser.read(ini_path, encoding="utf-8")
            root = parser.get("paths", "kimodo_root", fallback="").strip()
            if root:
                candidates.append(os.path.expandvars(root))
        except Exception:
            pass
    candidates.append(os.path.join(_PLUGIN_DIR, "..", ".."))

    for candidate in candidates:
        root = os.path.abspath(candidate)
        if os.path.isfile(os.path.join(root, "start_ardy.bat")):
            return root
    return ""


def _ardy():
    """The command module, imported late.

    Cascadeur loads models and commands separately and in no fixed order, so
    reaching for it at import time is a coin flip.
    """
    try:
        from commands.animation_scripts import ardy_live
    except ImportError:
        import ardy_live
    return ardy_live


class ArdyLiveModel(QtCore.QObject):
    prompt_changed = QtCore.Signal()
    frames_changed = QtCore.Signal()
    seed_changed = QtCore.Signal()
    steps_changed = QtCore.Signal()
    history_changed = QtCore.Signal()
    transition_changed = QtCore.Signal()
    takes_changed = QtCore.Signal()
    status_changed = QtCore.Signal()
    busy_changed = QtCore.Signal()
    progress_changed = QtCore.Signal()
    new_scene_changed = QtCore.Signal()
    engine_changed = QtCore.Signal()
    live_changed = QtCore.Signal()

    # Measured on an RTX 5080: one 40-frame chunk at 8 diffusion steps took
    # 0.49s (median of five, range 0.48-0.59), and applying it to the rig
    # another 0.09s. Generation scales close to linearly with the step count.
    # A chunk is 40 frames at 20 fps - two seconds of motion - so the ratio of
    # the two is what says whether generation can stay ahead of playback.
    SECONDS_PER_STEP = 0.49 / 8.0
    CHUNK_FRAMES = 40.0
    CHUNK_SECONDS = 2.0
    APPLY_SECONDS = 0.09

    def __init__(self, parent=None):
        super().__init__(parent)
        self.m_prompt = "a person walks forward"
        self.m_frames = 120
        # -1, not 0. Zero is a perfectly good seed, and as the default it made
        # every Generate return the same motion for the same prompt - which
        # reads as the prompt being ignored.
        self.m_seed = -1
        self.m_steps = 8
        self.m_history = 40
        self.m_transition = 4
        self.m_status = ""
        self.m_busy = False
        self.m_progress = 0.0
        self.m_service_up = False
        # A tab per take by default: takes are cheap to make and worth
        # comparing side by side, which is the whole reason Cascadeur has
        # tabs. Turn it off when they start piling up.
        self.m_new_scene = True
        # Which model the take list goes to: ARDY, Kimodo (text, 30 fps), or
        # MotionBricks (the prompt is a style name such as "walk").
        self.m_engine = 0
        self._takes = []
        self._job = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)
        # Slower: this one only reads counters a worker thread keeps.
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.setInterval(500)
        self._live_timer.timeout.connect(self._tick_live)
        self._sync_status()

    # -- values the panel edits ------------------------------------------

    @QtCore.Property(str, notify=prompt_changed, final=True)
    def prompt(self):
        return self.m_prompt

    @prompt.setter
    def prompt(self, value):
        value = str(value)
        if self.m_prompt != value:
            self.m_prompt = value
            self.prompt_changed.emit()

    def _int_setter(self, attribute, value, signal, low, high):
        try:
            value = int(round(float(value)))
        except (TypeError, ValueError):
            return
        value = max(low, min(high, value))
        if getattr(self, attribute) != value:
            setattr(self, attribute, value)
            signal.emit()

    @QtCore.Property(int, notify=frames_changed, final=True)
    def frames(self):
        return self.m_frames

    @frames.setter
    def frames(self, value):
        self._int_setter("m_frames", value, self.frames_changed, 20, 1200)

    @QtCore.Property(int, notify=seed_changed, final=True)
    def seed(self):
        return self.m_seed

    @seed.setter
    def seed(self, value):
        self._int_setter("m_seed", value, self.seed_changed, -1, 2 ** 31 - 1)

    @QtCore.Property(int, notify=steps_changed, final=True)
    def steps(self):
        return self.m_steps

    @steps.setter
    def steps(self, value):
        self._int_setter("m_steps", value, self.steps_changed, 1, 60)

    @QtCore.Property(int, notify=history_changed, final=True)
    def history(self):
        return self.m_history

    @history.setter
    def history(self, value):
        self._int_setter("m_history", value, self.history_changed, 0, 120)

    @QtCore.Property(int, notify=transition_changed, final=True)
    def transition(self):
        return self.m_transition

    @transition.setter
    def transition(self, value):
        self._int_setter("m_transition", value, self.transition_changed, 0, 40)

    # -- what each value costs, said in the terms that matter ------------

    @QtCore.Property(str, notify=frames_changed, final=True)
    def frames_hint(self):
        seconds = self.m_frames / 20.0
        chunks = max(1, int(round(self.m_frames / self.CHUNK_FRAMES)))
        return (f"{seconds:.1f} seconds of motion at 20 fps, generated as "
                f"{chunks} chunk(s) of 40 frames.")

    @QtCore.Property(str, notify=steps_changed, final=True)
    def steps_hint(self):
        cost = self.m_steps * self.SECONDS_PER_STEP + self.APPLY_SECONDS
        ratio = self.CHUNK_SECONDS / cost if cost else 0.0
        if ratio >= 2.0:
            verdict = "well ahead of playback"
        elif ratio >= 1.2:
            verdict = "ahead of playback, with less room"
        elif ratio >= 1.0:
            verdict = "only just keeping up"
        else:
            verdict = "slower than playback"
        return (f"Cleaner motion, slower to make. About {cost:.1f}s per 2s of "
                f"motion ({ratio:.1f}x realtime) - {verdict}. 8 is the tested "
                f"default; 30+ suits a final take rather than a live one.")

    @QtCore.Property(str, notify=seed_changed, final=True)
    def seed_hint(self):
        if self.m_seed < 0:
            return "Random: the same prompt gives a different take each time."
        return (f"Fixed at {self.m_seed}: the same prompt and settings will "
                f"reproduce this take exactly. Set -1 for random.")

    @QtCore.Property(str, notify=history_changed, final=True)
    def history_hint(self):
        return (f"How many frames of the previous take the next one looks back "
                f"at - {self.m_history / 20.0:.1f}s. More carries momentum "
                f"across the join; less lets the next prompt take over sooner.")

    @QtCore.Property(str, notify=transition_changed, final=True)
    def transition_hint(self):
        if self.m_transition == 0:
            return "Takes cut straight from one to the next."
        return (f"{self.m_transition} frames ({self.m_transition / 20.0:.2f}s) "
                f"of crossover where one take becomes the next.")

    # -- what the panel shows back ---------------------------------------

    @QtCore.Property(str, notify=takes_changed, final=True)
    def takes_text(self):
        if not self._takes:
            return ("No takes queued. Press Generate to run the prompt above "
                    "on its own, or Add take to build a sequence.")
        lines, total = [], 0
        for index, take in enumerate(self._takes, 1):
            total += take["frames"]
            lines.append(f"{index}.  {take['prompt']}"
                         f"   -  {take['frames']}f / {take['frames'] / 20.0:.1f}s")
        lines.append("")
        lines.append(f"{len(self._takes)} takes, {total} frames, "
                     f"{total / 20.0:.1f}s in one continuous motion.")
        return "\n".join(lines)

    @QtCore.Property(str, notify=status_changed, final=True)
    def status(self):
        return self.m_status

    @QtCore.Property(bool, notify=busy_changed, final=True)
    def busy(self):
        return self.m_busy

    @QtCore.Property(float, notify=progress_changed, final=True)
    def progress(self):
        return self.m_progress

    @QtCore.Property(bool, notify=status_changed, final=True)
    def service_up(self):
        return self.m_service_up

    @QtCore.Property(bool, notify=new_scene_changed, final=True)
    def new_scene(self):
        return self.m_new_scene

    @new_scene.setter
    def new_scene(self, value):
        value = bool(value)
        if self.m_new_scene != value:
            self.m_new_scene = value
            self.new_scene_changed.emit()

    ENGINES = ("ardy", "kimodo", "motionbricks")

    @QtCore.Property(int, notify=engine_changed, final=True)
    def engine_index(self):
        return self.m_engine

    @engine_index.setter
    def engine_index(self, value):
        value = max(0, min(int(value), len(self.ENGINES) - 1))
        if self.m_engine != value:
            self.m_engine = value
            # Kimodo wants more denoising steps than ARDY's default of 8.
            if value == 1 and self.m_steps == 8:
                self.m_steps = 20
                self.steps_changed.emit()
            elif value != 1 and self.m_steps == 20:
                self.m_steps = 8
                self.steps_changed.emit()
            if value == 2 and self.m_prompt == "a person walks forward":
                self.m_prompt = "walk"
                self.prompt_changed.emit()
            elif value != 2 and self.m_prompt == "walk":
                self.m_prompt = "a person walks forward"
                self.prompt_changed.emit()
            self.engine_changed.emit()

    @QtCore.Property(str, notify=engine_changed, final=True)
    def engine_hint(self):
        return ("ARDY: a prompt, 20 fps.", "Kimodo: a prompt, 30 fps.",
                "MotionBricks: the prompt is a style (walk, run, walk_zombie, ...), 30 fps, "
                "straight ahead.")[self.m_engine]

    @QtCore.Property(str, notify=new_scene_changed, final=True)
    def new_scene_hint(self):
        if self.m_new_scene:
            return "Each take opens its own tab, so they can be compared."
        return ("The take replaces the motion on the character in front. No "
                "new tabs; a shorter take leaves the longer one's tail behind.")

    def _set_status(self, text):
        if self.m_status != text:
            self.m_status = text
            self.status_changed.emit()

    def _set_busy(self, value):
        value = bool(value)
        if self.m_busy != value:
            self.m_busy = value
            self.busy_changed.emit()

    def _set_progress(self, value):
        value = max(0.0, min(1.0, float(value)))
        if abs(self.m_progress - value) > 0.001:
            self.m_progress = value
            self.progress_changed.emit()

    def _sync_status(self):
        try:
            snapshot = _ardy()._ardy_running()
        except Exception as error:
            self.m_service_up = False
            self._set_status(f"ARDY Live is not installed properly: {error}")
            return
        self.m_service_up = bool(snapshot)
        if not snapshot:
            self._set_status("ARDY service is not running - press Start server.")
        elif snapshot.get("warming_up"):
            self._set_status("ARDY is loading the model...")
        elif not snapshot.get("loaded"):
            self._set_status(snapshot.get("load_error") or "ARDY failed to load.")
        else:
            self._set_status("ARDY is ready.")
        self.status_changed.emit()

    # -- the server ------------------------------------------------------

    @QtCore.Slot()
    def refresh(self):
        self._sync_status()

    @QtCore.Slot()
    def start_server(self):
        """Launch start_ardy.bat and return immediately.

        Never waited on. An earlier version started the service with a
        blocking subprocess call inside a dialog callback and Cascadeur died
        on it every time - two crash dumps - because the model takes tens of
        seconds to load and the UI thread was held for all of it.
        """
        import subprocess

        root = _project_root()
        script = os.path.join(root, "start_ardy.bat") if root else ""
        if not script or not os.path.isfile(script):
            self._set_status("Could not find start_ardy.bat. Rerun "
                             "install_cascadeur_plugin.bat so it records where "
                             "the install lives, or start it by hand.")
            return
        try:
            subprocess.Popen(
                [script], cwd=root, close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except Exception as error:
            self._set_status(f"Could not start it: {type(error).__name__}: {error}")
            return
        self._set_status("Starting ARDY - watch its console, then press "
                         "Refresh. The model takes a while to load.")

    @QtCore.Slot()
    def stop_server(self):
        """Ask the service to shut itself down, over HTTP rather than by
        killing a process: it closes its own port and frees the GPU."""
        ardy_live = _ardy()
        try:
            ardy_live._http_json_request(
                f"{ardy_live.ARDY_BACKEND_URL}/shutdown", method="POST", timeout=10)
        except Exception as error:
            self._set_status(f"Stop: {error}")
        else:
            self._set_status("ARDY service stopped.")
        self._sync_status()

    # -- the take list ---------------------------------------------------

    @QtCore.Slot()
    def randomise_seed(self):
        self.seed = -1

    @QtCore.Slot()
    def add_take(self):
        prompt = self.m_prompt.strip()
        if not prompt:
            self._set_status("Give the take a prompt first.")
            return
        self._takes.append({"prompt": prompt, "frames": self.m_frames})
        self.takes_changed.emit()
        self._set_status(f"Added take {len(self._takes)}.")

    @QtCore.Slot()
    def remove_last_take(self):
        if not self._takes:
            return
        self._takes.pop()
        self.takes_changed.emit()
        self._set_status("Removed the last take.")

    @QtCore.Slot()
    def clear_takes(self):
        self._takes = []
        self.takes_changed.emit()
        self._set_status("Take list cleared.")

    # -- generating ------------------------------------------------------

    def _estimate(self, total_frames):
        chunks = max(1.0, total_frames / self.CHUNK_FRAMES)
        return chunks * (self.m_steps * self.SECONDS_PER_STEP) + self.APPLY_SECONDS

    # -- live streaming ---------------------------------------------------

    @QtCore.Property(bool, notify=live_changed, final=True)
    def live(self):
        return self._stream() is not None and self._stream().running

    def _stream(self):
        try:
            from commands.animation_scripts import ardy_stream
        except ImportError:
            import ardy_stream
        return ardy_stream.current()

    @QtCore.Slot()
    def toggle_live(self):
        """Start or stop continuous generation into the scene."""
        try:
            from commands.animation_scripts import ardy_stream
        except ImportError:
            import ardy_stream

        if ardy_stream.current() is not None:
            self._live_timer.stop()
            self._set_status("Writing the streamed motion onto the character...")
            written = ardy_stream.stop()
            if written:
                self._set_status(f"Live done - {written} frames "
                                 f"({written / 20.0:.1f}s) written.")
            else:
                self._set_status("Live stopped - nothing was generated.")
            self.live_changed.emit()
            return

        prompt = self.m_prompt.strip()
        if not prompt:
            self._set_status("Write a prompt to stream.")
            return

        ardy_live = _ardy()
        try:
            ardy_live._require_ardy_running()
        except Exception as error:
            self._set_status(str(error))
            self._sync_status()
            return

        # Live always needs a character to write onto, and it has to be one
        # opened properly or it arrives without its AutoPosing controllers.
        reporter = _Reporter(self)
        try:
            scene_manager = csc.app.get_application().get_scene_manager()
            scene_view, _made = ardy_live._scene_for_take(
                scene_manager, reporter, new_scene=self.m_new_scene)
            ardy_stream.start(
                prompt, scene_view,
                seed=None if self.m_seed < 0 else self.m_seed,
                steps=self.m_steps,
                history=self.m_history,
                on_event=self._set_status)
        except Exception as error:
            self._set_status(f"{type(error).__name__}: {error}")
            return

        self._live_timer.start()
        self.live_changed.emit()

    def _tick_live(self):
        stream = self._stream()
        if stream is None:
            self._live_timer.stop()
            self.live_changed.emit()
            return
        state = stream.snapshot()
        if state["error"]:
            self._set_status(f"Live stopped: {state['error']}")
            self._live_timer.stop()
            self.live_changed.emit()
            return
        self._set_status(
            f"Live: {state['frames']} frames ({state['seconds']:.1f}s) keyed, "
            f"{state['lead']:.1f}s generated ahead.")

    @QtCore.Slot()
    def generate(self):
        """Ask the service for the take list, on a thread.

        Typing a prompt and pressing Generate is the common case, so a queue
        of one is assumed rather than refused - having Generate do nothing
        because Add take was never pressed is a trap.
        """
        if self.m_busy:
            return
        takes = list(self._takes)
        if not takes:
            prompt = self.m_prompt.strip()
            if not prompt:
                self._set_status("Nothing to generate - write a prompt first.")
                return
            takes = [{"prompt": prompt, "frames": self.m_frames}]

        ardy_live = _ardy()
        try:
            ardy_live._require_ardy_running()
        except Exception as error:
            self._set_status(str(error))
            self._sync_status()
            return

        total = sum(t["frames"] for t in takes)
        job = {
            "takes": takes,
            "reply": None,
            "error": None,
            "started": time.perf_counter(),
            "expected": self._estimate(total),
            "total": total,
        }

        engine = self.ENGINES[self.m_engine]
        # Kimodo and MotionBricks both land on the Kimodo character.
        job["engine"] = "ardy" if engine == "ardy" else "kimodo"
        route = {"ardy": "/takes/generate", "kimodo": "/kimodo/generate",
                 "motionbricks": "/motionbricks/generate"}[engine]
        seed = None if self.m_seed < 0 else self.m_seed
        if engine == "motionbricks":
            payload = {"style": takes[0]["prompt"].strip().replace(" ", "_").lower(),
                       "frames": takes[0]["frames"], "seed": seed, "heading": "ahead"}
        else:
            payload = {
                "takes": takes,
                # None is what the service reads as "pick one yourself".
                "seed": seed,
                "diffusion_steps": self.m_steps,
                "history_frames": self.m_history,
                "transition_frames": self.m_transition,
            }

        def work():
            try:
                job["reply"] = ardy_live._http_json_request(
                    f"{ardy_live.ARDY_BACKEND_URL}{route}", method="POST",
                    payload=payload,
                    timeout=180 + total * 3)
            except Exception as error:
                job["error"] = f"{type(error).__name__}: {error}"

        job["thread"] = threading.Thread(target=work, daemon=True)
        self._job = job
        self._set_busy(True)
        self._set_progress(0.0)
        self._set_status(f"Generating {len(takes)} take(s), {total} frames "
                         f"- about {job['expected']:.0f}s...")
        job["thread"].start()
        self._timer.start()

    def _poll(self):
        """Tick the progress bar, and land the take when it arrives.

        Runs on the main thread, which is why the scene write happens here and
        not in the worker.
        """
        job = self._job
        if job is None:
            self._timer.stop()
            return

        elapsed = time.perf_counter() - job["started"]
        if job["thread"].is_alive():
            # Against the estimate, and never quite reaching the end: a bar
            # that sits at 100% while work continues is worse than one that
            # creeps.
            self._set_progress(min(0.97, elapsed / max(job["expected"], 0.1)))
            self._set_status(f"Generating {len(job['takes'])} take(s), "
                             f"{job['total']} frames - {elapsed:.0f}s of about "
                             f"{job['expected']:.0f}s...")
            return

        self._timer.stop()
        self._job = None
        try:
            if job["error"]:
                self._set_status(job["error"])
                return
            reply = job["reply"] or {}
            self._set_progress(0.98)
            self._set_status("Putting the motion on the character...")
            ardy_live = _ardy()
            reporter = _Reporter(self)
            ardy_live._land_reply(reply, reporter, new_scene=self.m_new_scene,
                                  engine=job.get("engine", "ardy"))
            frames = reply.get("frames", 0)
            self._set_progress(1.0)
            self._set_status(f"Done - {frames} frames ({frames / float(reply.get('fps') or 20.0):.1f}s) "
                             f"from {len(job['takes'])} take(s) in "
                             f"{elapsed:.0f}s.")
        except Exception as error:
            self._set_status(f"{type(error).__name__}: {error}")
        finally:
            self._set_busy(False)


class _Reporter:
    """What ardy_live reports progress through, routed to the status line."""

    def __init__(self, owner):
        self.owner = owner

    def update(self, text):
        self.owner._set_status(text)

    progress = update

    def close(self):
        return


def make_resizable(title="ARDY Live", width=680, height=760,
                   minimum=(460, 460)):
    """Make the dialog usable: resizable, and not modal.

    Cascadeur builds these at a fixed size and application-modal - measured,
    the window came up 315x354 with minimum == maximum == that, so nothing
    could be resized, and while it was open the viewport would not take a
    click, which meant the take could not even be played back. Both live on
    the QWindow rather than in the QML, so both can be undone once the window
    exists.
    """
    try:
        from PySide6 import QtCore as _QtCore, QtGui
    except ImportError:
        return False

    application = QtGui.QGuiApplication.instance()
    if application is None:
        return False
    for window in application.allWindows():
        try:
            if window.title() != title or not window.isVisible():
                continue
        except RuntimeError:
            continue
        window.setMaximumSize(_QtCore.QSize(16777215, 16777215))
        window.setMinimumSize(_QtCore.QSize(*minimum))
        if window.width() < width or window.height() < height:
            window.resize(width, height)
        _make_modeless(window, _QtCore)
        return True
    return False


def _make_modeless(window, qtcore):
    """Drop the dialog's modality, by rebuilding the native window.

    The panel is meant to be worked alongside - scrub the take, press play,
    come back and generate another - and Cascadeur shows it application-modal.
    Modal here does not merely grey the viewport: it stops Cascadeur's idle
    loop dead, so nothing driven by that runs at all while the panel is up.

    setModality on its own does nothing once a window is on screen; Qt only
    reads it when the platform window is created. Hiding it first and showing
    it again is what forces that to happen. If any of it fails the window is
    put back visible regardless - a modal panel is a nuisance, an invisible
    one is a lost tool.
    """
    if window.modality() == qtcore.Qt.NonModal:
        return True
    try:
        geometry = window.geometry()
        window.setVisible(False)
        window.setModality(qtcore.Qt.NonModal)
        window.setFlags(qtcore.Qt.Window
                        | qtcore.Qt.WindowTitleHint
                        | qtcore.Qt.WindowSystemMenuHint
                        | qtcore.Qt.WindowMinMaxButtonsHint
                        | qtcore.Qt.WindowCloseButtonHint)
        window.setVisible(True)
        window.setGeometry(geometry)
        return window.modality() == qtcore.Qt.NonModal
    except Exception:
        try:
            window.setVisible(True)
        except Exception:
            pass
        return False


# The three functions below are called from C++ by name. Do not rename them.

def qml_view_name():
    return "view"


def create_model():
    model = ArdyLiveModel()
    pointer = shiboken6.getCppPointer(model)
    if isinstance(pointer, tuple):
        pointer = pointer[0]
    return model, pointer


def show_qml_view(model, name):
    csc.view.PythonModelsManager.instance().show_dialog("ARDY Live", name)
    # After the dialog exists, not before: show_dialog returns before the
    # window is mapped, so this is tried again shortly after.
    try:
        QtCore.QTimer.singleShot(0, make_resizable)
        QtCore.QTimer.singleShot(150, make_resizable)
    except Exception:
        make_resizable()
