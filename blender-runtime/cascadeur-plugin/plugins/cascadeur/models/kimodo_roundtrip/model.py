"""Kimodo Roundtrip as one panel instead of a chain of dialogs.

The command it replaces asked for the nine generation settings through a modal
inputs dialog, behind a second modal dialog that held Start / Stop / Generate.
Every value was a string typed from memory, nothing said what any of them
cost, and the backend had to be started from one dialog before the settings
could be seen in another.

Here everything is on screen together, each number says what it does, and the
backend buttons sit beside Generate.

Generation runs on a worker thread. It has to: a roundtrip is one blocking
call that can last minutes, and doing it in the slot froze the window and the
progress bar with it. The scene is only ever touched from the main thread -
Cascadeur's scene is not thread-safe - so the roundtrip itself is run from a
timer tick rather than from the worker; the worker only waits on the backend.

Built on the ARDY Live panel, which is where the QGuiApplication lesson came
from: constructing a QWidget under 2026.2 takes the whole process down. QML is
what the application actually is.
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

DATASETS = ("RP", "SEED")
ENCODERS = (("llama", "LLAMA"),
            ("llama-10gb", "LLAMA (10GB VRAM)"),
            ("fallback", "LLAMA off"))


def _kimodo():
    """The command module, imported late.

    Cascadeur loads models and commands separately and in no fixed order, so
    reaching for it at import time is a coin flip.
    """
    try:
        from commands.animation_scripts import kimodo_roundtrip
    except ImportError:
        import kimodo_roundtrip
    return kimodo_roundtrip


def _summary(snapshot):
    """Just the parts of a health reply the panel draws.

    Compared between polls so an unchanged backend does not re-evaluate every
    binding on the panel six times a minute.
    """
    if not snapshot:
        return None
    job = snapshot.get("current_job") or {}
    return (snapshot.get("ok"), snapshot.get("busy"), snapshot.get("warming_up"),
            snapshot.get("text_encoder_profile"),
            tuple(snapshot.get("loaded_datasets") or ()),
            snapshot.get("warmup_dataset"), snapshot.get("warmup_error"),
            job.get("status"), job.get("progress"))


def _scene_view():
    return csc.app.get_application().get_scene_manager().current_scene()


def _scene():
    """The scene the command would have been handed.

    A command's run(scene) is given csc.domain.Scene, which is what every
    helper in kimodo_roundtrip expects - including scene.info() for its status
    line. A model is created without one, so the scene in front is used, the
    same one the menu command would have run on.
    """
    return _scene_view().domain_scene()


def _scene_name(view):
    try:
        return view.name()
    except Exception:
        return "the scene in front"


def _rig_count(scene):
    try:
        viewer = scene.model_viewer().behaviour_viewer()
        return len(list(viewer.get_behaviours("RigInfo")))
    except Exception:
        return 0


def _scenes_with_rigs():
    """(name, rig count) for every open scene that has a rigged character."""
    out = []
    try:
        manager = csc.app.get_application().get_scene_manager()
        for view in manager.scenes():
            try:
                count = _rig_count(view.domain_scene())
            except Exception:
                continue
            if count:
                out.append((_scene_name(view), count))
    except Exception:
        pass
    return out


class KimodoRoundtripModel(QtCore.QObject):
    prompt_changed = QtCore.Signal()
    samples_changed = QtCore.Signal()
    sample_index_changed = QtCore.Signal()
    seed_changed = QtCore.Signal()
    steps_changed = QtCore.Signal()
    cfg_changed = QtCore.Signal()
    text_weight_changed = QtCore.Signal()
    constraint_weight_changed = QtCore.Signal()
    dataset_changed = QtCore.Signal()
    encoder_changed = QtCore.Signal()
    status_changed = QtCore.Signal()
    busy_changed = QtCore.Signal()
    progress_changed = QtCore.Signal()
    backend_changed = QtCore.Signal()
    target_changed = QtCore.Signal()

    # The weights are floats the dialog took as text. Sliders are integers, so
    # they are held as hundredths and divided on the way out.
    WEIGHT_SCALE = 100.0

    def __init__(self, parent=None):
        super().__init__(parent)
        defaults = {}
        try:
            defaults = _kimodo()._ensure_config()["defaults"]
        except Exception:
            pass

        def number(key, fallback, cast=int):
            try:
                return cast(str(defaults.get(key, fallback)).strip())
            except (TypeError, ValueError):
                return cast(fallback)

        self.m_prompt = str(defaults.get("prompt", "") or "")
        self.m_samples = max(1, number("samples_num", 1))
        self.m_sample_index = max(0, number("sample_index", 0))
        self.m_seed = number("seed", -1)
        self.m_steps = max(1, number("denoising_steps", 100))
        self.m_cfg = str(defaults.get("cfg_enabled", "True")).strip().lower() \
            not in ("false", "0", "no", "off")
        self.m_text_weight = int(round(number("text_weight", 2.0, float)
                                       * self.WEIGHT_SCALE))
        self.m_constraint_weight = int(round(number("constraint_weight", 2.0, float)
                                             * self.WEIGHT_SCALE))
        dataset = str(defaults.get("dataset", "RP")).strip().upper()
        self.m_dataset = DATASETS.index(dataset) if dataset in DATASETS else 0
        self.m_encoder = 0

        self.m_status = ""
        self.m_busy = False
        self.m_progress = 0.0
        self._snapshot = None
        self._cached_config = None
        self._job = None

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)

        # The backend reports its own progress while a job runs, so the panel
        # asks it rather than guessing from a stopwatch the way ARDY Live has
        # to. It is asked from a WORKER THREAD, and never from here: measured,
        # one _backend_snapshot() blocks for 2036 ms whether the backend
        # answers or not. On a 1.5s timer on the main thread that left
        # Cascadeur blocked more often than not, and the whole application
        # stuttered while the panel was open.
        self._probe = None
        self._probed = None
        self._probe_at = 0.0
        self._target = None
        self._watch = QtCore.QTimer(self)
        self._watch.setInterval(300)
        self._watch.timeout.connect(self._tick_backend)
        self._watch.start()
        self._start_probe()

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

    @QtCore.Property(int, notify=samples_changed, final=True)
    def samples(self):
        return self.m_samples

    @samples.setter
    def samples(self, value):
        self._int_setter("m_samples", value, self.samples_changed, 1, 16)
        if self.m_sample_index >= self.m_samples:
            self.m_sample_index = self.m_samples - 1
            self.sample_index_changed.emit()

    @QtCore.Property(int, notify=sample_index_changed, final=True)
    def sample_index(self):
        return self.m_sample_index

    @sample_index.setter
    def sample_index(self, value):
        self._int_setter("m_sample_index", value, self.sample_index_changed,
                         0, max(0, self.m_samples - 1))

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
        self._int_setter("m_steps", value, self.steps_changed, 1, 1000)

    @QtCore.Property(bool, notify=cfg_changed, final=True)
    def cfg(self):
        return self.m_cfg

    @cfg.setter
    def cfg(self, value):
        value = bool(value)
        if self.m_cfg != value:
            self.m_cfg = value
            self.cfg_changed.emit()
            self.text_weight_changed.emit()

    @QtCore.Property(int, notify=text_weight_changed, final=True)
    def text_weight(self):
        return self.m_text_weight

    @text_weight.setter
    def text_weight(self, value):
        self._int_setter("m_text_weight", value, self.text_weight_changed,
                         0, 1000)

    @QtCore.Property(int, notify=constraint_weight_changed, final=True)
    def constraint_weight(self):
        return self.m_constraint_weight

    @constraint_weight.setter
    def constraint_weight(self, value):
        self._int_setter("m_constraint_weight", value,
                         self.constraint_weight_changed, 0, 1000)

    @QtCore.Property(int, notify=dataset_changed, final=True)
    def dataset(self):
        return self.m_dataset

    @dataset.setter
    def dataset(self, value):
        self._int_setter("m_dataset", value, self.dataset_changed,
                         0, len(DATASETS) - 1)

    @QtCore.Property(int, notify=encoder_changed, final=True)
    def encoder(self):
        return self.m_encoder

    @encoder.setter
    def encoder(self, value):
        self._int_setter("m_encoder", value, self.encoder_changed,
                         0, len(ENCODERS) - 1)

    @QtCore.Property("QStringList", constant=True)
    def dataset_names(self):
        return list(DATASETS)

    @QtCore.Property("QStringList", constant=True)
    def encoder_names(self):
        return [label for _, label in ENCODERS]

    # -- what each value costs, said in the terms that matter ------------

    @QtCore.Property(str, notify=samples_changed, final=True)
    def samples_hint(self):
        if self.m_samples == 1:
            return ("One motion for the prompt. More gives alternatives to "
                    "choose between, at close to the same cost each.")
        return (f"{self.m_samples} motions for the same prompt, generated "
                f"together. Pick which one lands with Sample index.")

    @QtCore.Property(str, notify=sample_index_changed, final=True)
    def sample_index_hint(self):
        return (f"Which of the {self.m_samples} goes onto the character - "
                f"number {self.m_sample_index}, counting from 0.")

    @QtCore.Property(str, notify=seed_changed, final=True)
    def seed_hint(self):
        if self.m_seed < 0:
            return "Random: the same prompt gives a different motion each time."
        return (f"Fixed at {self.m_seed}: the same prompt and settings will "
                f"reproduce this motion exactly. Set -1 for random.")

    @QtCore.Property(str, notify=steps_changed, final=True)
    def steps_hint(self):
        return (f"{self.m_steps} denoising steps. More is cleaner and slower; "
                f"100 is the tested default. Below about 30 the motion starts "
                f"to look unfinished.")

    @QtCore.Property(str, notify=text_weight_changed, final=True)
    def text_weight_hint(self):
        if not self.m_cfg:
            return ("Classifier free guidance is off, so this is ignored. "
                    "Turn it on to steer with the prompt.")
        return (f"How hard the prompt pulls: {self.m_text_weight / self.WEIGHT_SCALE:.2f}. "
                f"Higher follows the words more closely and can stiffen the "
                f"motion; 2.0 is the tested default.")

    @QtCore.Property(str, notify=constraint_weight_changed, final=True)
    def constraint_weight_hint(self):
        return (f"How hard the character's own pose pulls: "
                f"{self.m_constraint_weight / self.WEIGHT_SCALE:.2f}. Higher "
                f"keeps closer to where the rig already is; 2.0 is the tested "
                f"default.")

    @QtCore.Property(str, notify=dataset_changed, final=True)
    def dataset_hint(self):
        if DATASETS[self.m_dataset] == "SEED":
            return ("SEED. Each dataset is loaded on first use, so the first "
                    "generation after switching takes longer.")
        return ("RP, the default. Each dataset is loaded on first use, so the "
                "first generation after switching takes longer.")

    @QtCore.Property(str, notify=encoder_changed, final=True)
    def encoder_hint(self):
        mode = ENCODERS[self.m_encoder][0]
        if mode == "fallback":
            return ("Starts without the LLAMA text encoder. Frees the VRAM it "
                    "would hold, and the prompt is understood less well.")
        if mode == "llama-10gb":
            return ("LLAMA quantised to fit a 10GB card. Slower to load than "
                    "the full one, and it leaves room for the model.")
        return "The full LLAMA text encoder. Use it if the card has the room."

    # -- what the panel shows back ---------------------------------------

    def _config(self):
        """The parsed ini, kept. Read on every binding evaluation otherwise."""
        if self._cached_config is None:
            self._cached_config = _kimodo()._ensure_config()
        return self._cached_config

    @QtCore.Property(str, notify=backend_changed, final=True)
    def backend_text(self):
        try:
            config = self._config()
        except Exception:
            return "Kimodo is not configured - kimodo_roundtrip.ini is missing."
        try:
            return _kimodo()._backend_dialog_text(self._snapshot, config)
        except Exception as error:
            return f"{type(error).__name__}: {error}"

    @QtCore.Property(bool, notify=backend_changed, final=True)
    def backend_up(self):
        return bool(self._snapshot and self._snapshot.get("ok"))

    @QtCore.Property(str, notify=target_changed, final=True)
    def target_text(self):
        """Which scene Generate will work on, said before it is pressed.

        Kimodo generates onto a character that is already in the scene - it
        does not bring one. It works on whichever tab is in front, exactly as
        the menu command did, and "no rigged character" on its own gives no
        hint of that.
        """
        try:
            view = _scene_view()
            name = _scene_name(view)
            count = _rig_count(view.domain_scene())
        except Exception as error:
            return f"No scene open ({type(error).__name__})."
        if count:
            return (f"Generating onto: {name} - "
                    f"{count} rigged character(s).")
        elsewhere = _scenes_with_rigs()
        if elsewhere:
            names = ", ".join(n for n, _ in elsewhere[:3])
            return (f"{name} has no rigged character. Click the tab with "
                    f"yours first ({names}).")
        return (f"{name} has no rigged character. Open yours, or press Open "
                f"sample character - then drag out a frame range on the "
                f"timeline.")

    @QtCore.Property(bool, notify=backend_changed, final=True)
    def can_generate(self):
        snapshot = self._snapshot or {}
        return bool(snapshot.get("ok")
                    and not snapshot.get("busy")
                    and not snapshot.get("warming_up"))

    @QtCore.Property(str, notify=status_changed, final=True)
    def status(self):
        return self.m_status

    @QtCore.Property(bool, notify=busy_changed, final=True)
    def busy(self):
        return self.m_busy

    @QtCore.Property(float, notify=progress_changed, final=True)
    def progress(self):
        return self.m_progress

    def _set_status(self, text):
        text = str(text)
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
        if abs(self.m_progress - value) > 1e-4:
            self.m_progress = value
            self.progress_changed.emit()

    # -- the backend -----------------------------------------------------

    PROBE_IDLE = 6.0            # seconds between checks when nothing is running
    PROBE_BUSY = 2.0            # while a job is on, so its progress moves

    def _start_probe(self):
        """Ask the backend how it is, on a thread of its own.

        One at a time: the call takes about two seconds, so starting another
        before the last returns would just pile up threads.
        """
        if self._probe is not None and self._probe.is_alive():
            return
        kimodo = _kimodo()
        result = {}

        def work():
            try:
                result["snapshot"] = kimodo._backend_snapshot()
            except Exception:
                result["snapshot"] = None

        self._probed = result
        self._probe = threading.Thread(target=work, daemon=True)
        self._probe_at = time.perf_counter()
        self._probe.start()

    def _tick_backend(self):
        """Publish whatever the probe found, and start the next one when due.

        Main thread, but does no I/O - only reads what the worker left.
        """
        # Nothing at all while a roundtrip is running. It creates, loads and
        # removes scene tabs as it works, and reading one of those from here
        # while it does is how Cascadeur was taken down.
        if self.m_busy:
            return

        # The tab in front decides what Generate works on, and it changes
        # without telling anyone, so it is watched here. Cheap: one behaviour
        # lookup, no I/O.
        try:
            view = _scene_view()
            target = (_scene_name(view), _rig_count(view.domain_scene()))
        except Exception:
            target = None
        if target != self._target:
            self._target = target
            self.target_changed.emit()

        if self._probe is not None and self._probe.is_alive():
            return
        if self._probed is not None and "snapshot" in self._probed:
            self._apply_snapshot(self._probed["snapshot"])
            self._probed = None

        snapshot = self._snapshot or {}
        due = self.PROBE_BUSY if (snapshot.get("busy")
                                  or snapshot.get("warming_up")) else self.PROBE_IDLE
        if time.perf_counter() - self._probe_at >= due:
            self._start_probe()

    def _apply_snapshot(self, snapshot):
        before = self._snapshot
        self._snapshot = snapshot
        # Only when something actually changed: every emit re-evaluates the
        # panel's bindings, and the status box is rebuilt with it.
        if _summary(before) != _summary(snapshot):
            self.backend_changed.emit()

        snapshot = snapshot or {}
        if self.m_busy:
            return                          # our own job owns the status line
        job = snapshot.get("current_job") or {}
        if snapshot.get("busy") and job:
            percent = float(job.get("progress") or 0)
            self._set_progress(percent / 100.0)
            self._set_status(f"Kimodo is busy: {job.get('status', 'running')} "
                             f"({int(percent)}%).")
        elif snapshot.get("warming_up"):
            self._set_status("Kimodo is warming up - the dataset is loading.")

    @QtCore.Slot()
    def refresh(self):
        """The Refresh button: ask again now rather than waiting for the tick."""
        self._probe_at = 0.0
        self._start_probe()

    @QtCore.Slot()
    def start_backend(self):
        if self.m_busy:
            return
        mode = ENCODERS[self.m_encoder][0]
        self._set_busy(True)
        self._set_status(f"Starting Kimodo with {ENCODERS[self.m_encoder][1]}...")
        try:
            _kimodo().run_start_backend(_scene(), text_encoder_mode=mode)
            self._set_status("Kimodo started.")
        except Exception as error:
            self._set_status(f"{type(error).__name__}: {error}")
        finally:
            self._set_busy(False)
            self.refresh()

    @QtCore.Slot()
    def stop_backend(self):
        if self.m_busy:
            return
        self._set_busy(True)
        self._set_status("Stopping Kimodo...")
        try:
            _kimodo().run_stop_backend(_scene())
            self._set_status("Kimodo stopped.")
        except Exception as error:
            self._set_status(f"{type(error).__name__}: {error}")
        finally:
            self._set_busy(False)
            self.refresh()

    @QtCore.Slot()
    def open_sample(self):
        """Open the Kimodo character that ships with the plugin.

        Kimodo generates onto a character already in the scene and does not
        bring one, so with an empty scene there is nothing to press Generate
        on and the error says only "no rigged character". ARDY Live opens its
        own character; this does the same for anyone without a rig to hand.

        load_scene is what File > Open calls. ProjectLoader.load_from only
        merges a file's data into an existing scene, and a character arriving
        that way comes up without its AutoPosing controllers.
        """
        try:
            path = _kimodo()._expand_win_path(
                self._config().get("paths", "kimodo_scene"))
        except Exception as error:
            self._set_status(f"{type(error).__name__}: {error}")
            return
        if not os.path.isfile(path):
            self._set_status(f"The sample character is not installed: {path}. "
                             f"Rerun install_cascadeur_plugin.bat.")
            return
        try:
            application = csc.app.get_application()
            if not application.get_data_source_manager().load_scene(path):
                self._set_status(f"Cascadeur would not open {path}.")
                return
        except Exception as error:
            self._set_status(f"{type(error).__name__}: {error}")
            return
        self._target = None                 # re-read on the next tick
        self._set_status("Opened the Kimodo character. Drag out a frame range "
                         "on the timeline, then press Generate.")

    @QtCore.Slot()
    def remove_frames(self):
        if self.m_busy:
            return
        try:
            _kimodo()._remove_kimodo_generated_frames(_scene())
            self._set_status("Removed the frames Kimodo generated.")
        except Exception as error:
            self._set_status(f"{type(error).__name__}: {error}")

    @QtCore.Slot()
    def randomise_seed(self):
        self.seed = -1

    # -- generating ------------------------------------------------------

    def settings(self):
        """The dict _run_roundtrip_for_selected_characters expects.

        Built directly rather than through _parse_settings: that one exists to
        turn nine strings typed into a dialog back into values, and there are
        no strings here.
        """
        samples = max(1, self.m_samples)
        return {
            "prompt": self.m_prompt.strip(),
            "samples_num": samples,
            "sample_index": min(max(0, self.m_sample_index), samples - 1),
            "seed": None if self.m_seed < 0 else self.m_seed,
            "denoising_steps": max(1, self.m_steps),
            "cfg_enabled": bool(self.m_cfg),
            "text_weight": self.m_text_weight / self.WEIGHT_SCALE,
            "constraint_weight": self.m_constraint_weight / self.WEIGHT_SCALE,
            "dataset": DATASETS[self.m_dataset],
            "keep_debug_scenes": False,
            "inspect_in_gui": False,
        }

    @QtCore.Slot()
    def generate(self):
        if self.m_busy:
            return
        if not self.m_prompt.strip():
            self._set_status("Nothing to generate - write a prompt first.")
            return

        # The last poll, not a fresh call: asking again costs two seconds of
        # frozen window, and the roundtrip refuses cleanly by itself if the
        # backend turned busy in the meantime.
        snapshot = self._snapshot
        if not snapshot or not snapshot.get("ok"):
            self._set_status("Kimodo is not started. Press Start Kimodo first.")
            self.refresh()
            return
        if snapshot.get("warming_up"):
            self._set_status("Kimodo is still warming up. Wait for the dataset "
                             "to finish loading.")
            return
        if snapshot.get("busy"):
            self._set_status("Kimodo is busy. Wait for the current job.")
            return

        # Said here rather than let the roundtrip fail three steps in with
        # "Could not find a rigged character", which does not mention that it
        # means the tab in front.
        try:
            view = _scene_view()
            if not _rig_count(view.domain_scene()):
                elsewhere = _scenes_with_rigs()
                where = (" It is open in: "
                         + ", ".join(n for n, _ in elsewhere[:3])) if elsewhere else ""
                self._set_status(f"{_scene_name(view)} has no rigged character "
                                 f"- Kimodo generates onto one already in the "
                                 f"scene. Open yours, or press Open sample "
                                 f"character.{where}")
                return
        except Exception as error:
            self._set_status(f"{type(error).__name__}: {error}")
            return

        self._job = {"started": time.perf_counter(), "step": "start"}
        self._set_busy(True)
        self._set_progress(0.0)
        self._set_status("Generating...")
        # One tick, so the button repaints as disabled before the roundtrip
        # blocks the thread. The roundtrip itself has to run here rather than
        # on a worker: it writes to the scene, and the scene is main-thread
        # only.
        self._timer.start()

    def _poll(self):
        job = self._job
        if job is None:
            self._timer.stop()
            return
        if job["step"] != "start":
            return
        job["step"] = "running"
        self._timer.stop()
        # Belt as well as braces: the watchdog already returns early while
        # busy, but it is stopped outright so nothing can fire into the
        # roundtrip's scene work at all.
        self._watch.stop()

        kimodo = _kimodo()
        reporter = _Reporter(self)
        try:
            scene = _scene()
            kimodo._run_roundtrip_for_selected_characters(
                scene, self._config(), self.settings(), reporter)
            elapsed = time.perf_counter() - job["started"]
            self._set_progress(1.0)
            self._set_status(f"Done in {elapsed:.0f}s.")
        except Exception as error:
            self._set_progress(0.0)
            self._set_status(f"{type(error).__name__}: {error}")
        finally:
            self._job = None
            self._set_busy(False)
            self._target = None             # the tabs moved under us
            self._watch.start()
            self.refresh()


class _Reporter:
    """What kimodo_roundtrip reports progress through, routed to the panel."""

    def __init__(self, owner):
        self.owner = owner

    def update(self, text):
        # Status only. NO processEvents here.
        #
        # It was pumping the event loop at every reported step so the panel
        # would repaint while the roundtrip ran. What that actually did was
        # re-enter the loop in the middle of scene surgery - the roundtrip
        # creates and loads and removes scene tabs as it goes - and let timers
        # run inside it, including this panel's own watchdog reading the scene
        # being torn down. Cascadeur died there. A window that does not
        # repaint for the length of a generation is what the dialog it
        # replaced did too, and it is not worth a crash.
        self.owner._set_status(text)

    def progress(self, percent, text=""):
        try:
            self.owner._set_progress(float(percent) / 100.0)
        except (TypeError, ValueError):
            pass
        if text:
            self.update(text)

    def close(self):
        return


def make_resizable(title="Kimodo Roundtrip", width=860, height=800,
                   minimum=(700, 480)):
    """Make the dialog usable: resizable, and not modal.

    The width is not a guess. Cascadeur opens these small, and the row along
    the bottom - Start, Stop, Refresh, Generate, Close - ran off the right
    edge, so the two buttons that matter most could not be reached at all on
    first open. The minimum width keeps them reachable however far it is
    dragged in.

    Cascadeur builds these fixed-size and application-modal. Modal here does
    not merely grey the viewport - it stops Cascadeur's idle loop dead. Both
    live on the QWindow rather than in the QML, so both can be undone once the
    window exists. Same reasoning, and the same code, as the ARDY Live panel.
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

    setModality on its own does nothing once a window is on screen; Qt only
    reads it when the platform window is created, so it is hidden and shown
    again. If any of it fails the window goes back up regardless - a modal
    panel is a nuisance, an invisible one is a lost tool.
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
    model = KimodoRoundtripModel()
    pointer = shiboken6.getCppPointer(model)
    if isinstance(pointer, tuple):
        pointer = pointer[0]
    return model, pointer


def show_qml_view(model, name):
    csc.view.PythonModelsManager.instance().show_dialog("Kimodo Roundtrip", name)
    # After the dialog exists, not before: show_dialog returns before the
    # window is mapped, so this is tried again shortly after.
    try:
        QtCore.QTimer.singleShot(0, make_resizable)
        QtCore.QTimer.singleShot(150, make_resizable)
    except Exception:
        make_resizable()
