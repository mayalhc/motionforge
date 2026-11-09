# Copyright (c) 2026 Chamiseul. All rights reserved.
"""MetaArKit as a panel.

The old command was a chain of two modal dialogs - pick a file, then answer
two numbers about a filter you cannot see the effect of - and it told you
nothing until it had already keyed the take. Here the file, the mesh, the
smoothing and the frame it starts on are on screen together, and pressing
Check shows exactly which columns will land on which blendshapes before
anything is written.

Reading and smoothing a capture is fast enough to do on the main thread; the
writing is the slow half, and that has to be on the main thread anyway
because Cascadeur's scene is not thread-safe.
"""

import os
import sys

import csc
import shiboken6
from PySide6 import QtCore

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(os.path.dirname(_HERE))


def _sibling(module_name):
    """Load one of the plugin's scripts by path, not by name.

    Same reason as the quadruped panel: this package and the script it needs
    can share a name, and which one `import` finds then depends on sys.path
    order.
    """
    import importlib.util

    places = [
        os.path.join(_PLUGIN_DIR, module_name + ".py"),
        os.path.join(_PLUGIN_DIR, "commands", "animation_scripts",
                     module_name + ".py"),
    ]
    for path in places:
        if not os.path.isfile(path):
            continue
        loaded = sys.modules.get("_metaarkit_panel_" + module_name)
        if loaded is not None:
            return loaded
        spec = importlib.util.spec_from_file_location(
            "_metaarkit_panel_" + module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    raise ImportError("cannot find %s.py beside the panel (looked in %s)"
                      % (module_name, ", ".join(places)))


mocap = _sibling("metaarkit")


def _scene():
    return csc.app.get_application().get_scene_manager() \
        .current_scene().domain_scene()


def _send_in_progress():
    """True while the Blender pose receiver is putting a character in.

    Asked of the receiver rather than guessed at: it knows when it has work
    queued and when it is mid-import. If it is not installed, or is an older
    copy without `busy()`, nothing is landing that this panel would disturb.
    """
    try:
        receiver = _sibling("cascadeur_receiver")
    except Exception:
        return False
    checker = getattr(receiver, "busy", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except Exception:
        return False


class FacialMocapModel(QtCore.QObject):

    changed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._path = ""
        # Off by default. A CSV exported from a rig is already exact, and
        # smoothing it only rounds off what it got right; smoothing is for
        # raw capture straight off a phone.
        self._smooth = False
        self._window = 5
        self._order = 3
        self._scale = 100.0
        self._start = 0
        self._status = "Select the mesh with the blendshapes, then pick a CSV."
        self._preview = ""
        self._ready = False
        self._busy = False
        self._selection = ""
        self._counted_name = None
        self._counted = 0

    def _emit(self):
        self.changed.emit()

    # -- settings ---------------------------------------------------------

    @QtCore.Property(str, notify=changed)
    def path(self):
        return self._path

    @QtCore.Property(str, notify=changed)
    def file_label(self):
        return os.path.basename(self._path) if self._path else "no file chosen"

    @QtCore.Property(int, notify=changed)
    def window(self):
        return self._window

    @window.setter
    def window(self, value):
        self._window = max(3, int(value))
        self._ready = False
        self._emit()

    @QtCore.Property(int, notify=changed)
    def order(self):
        return self._order

    @order.setter
    def order(self, value):
        self._order = max(1, int(value))
        self._ready = False
        self._emit()

    @QtCore.Property(int, notify=changed)
    def start(self):
        return self._start

    @start.setter
    def start(self, value):
        self._start = max(0, int(value))
        self._emit()

    @QtCore.Property(float, notify=changed)
    def scale(self):
        return self._scale

    @scale.setter
    def scale(self, value):
        self._scale = float(value)
        self._emit()

    @QtCore.Property(bool, notify=changed)
    def smooth(self):
        return self._smooth

    @smooth.setter
    def smooth(self, value):
        self._smooth = bool(value)
        self._emit()

    @QtCore.Property(str, notify=changed)
    def smoothing_hint(self):
        if not self._smooth:
            return ("Values go in exactly as the file has them. Leave this "
                    "off for a CSV exported from Blender - it is already the "
                    "rig's own output, and filtering it only rounds it off.")
        complaint = mocap.savgol.valid_window(self._window, self._order)
        if complaint:
            return complaint
        return ("Fits a degree-%d polynomial over %d frames. For raw phone "
                "capture. It overshoots fast changes and the ends of a take."
                % (self._order, self._window))

    @QtCore.Property(bool, notify=changed)
    def valid(self):
        if not self._smooth:
            return True
        return not mocap.savgol.valid_window(self._window, self._order)

    @QtCore.Property(str, notify=changed)
    def status(self):
        return self._status

    @QtCore.Property(str, notify=changed)
    def preview(self):
        return self._preview

    @QtCore.Property(bool, notify=changed)
    def ready(self):
        return self._ready and not self._busy

    @QtCore.Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @QtCore.Property(str, notify=changed)
    def mesh_label(self):
        """What is selected in Cascadeur right now.

        The panel used to say nothing about this, and the Import button just
        sat greyed out: the CSV had been chosen, the mesh had been selected
        afterwards, and nothing told you the panel had not noticed. Showing
        the selection makes the missing half visible.
        """
        return self._selection or "nothing selected"

    @QtCore.Property(str, notify=changed)
    def blocked(self):
        """Why Import is unavailable, or "" when it is not."""
        if self._busy:
            return "Importing..."
        if self._smooth:
            complaint = mocap.savgol.valid_window(self._window, self._order)
            if complaint:
                return complaint
        if not self._selection:
            return "Select the mesh that carries the blendshapes."
        if not self._path:
            return "Choose a CSV."
        if not self._ready:
            return "Press Check."
        return ""

    def _look_at_selection(self):
        """(label, changed) - and it has to stay cheap.

        Counting the blendshapes every tick meant walking the Dynamic
        behaviour of a 737-shape head twice a second. The count only changes
        when the selected object does, so it is remembered per object.
        """
        try:
            scene = _scene()
            viewer = scene.model_viewer()
            selected = [s for s in scene.selector().selected().ids
                        if isinstance(s, csc.model.ObjectId)]
            if len(selected) == 1:
                mesh_id = selected[0]
                name = viewer.get_object_name(mesh_id)
                if name != self._counted_name:
                    self._counted_name = name
                    self._counted = len(mocap.blendshapes(scene, mesh_id))
                label = "%s - %d blendshape(s)" % (name, self._counted)
                if not self._counted:
                    label += "  (no blendshapes on it)"
            elif not selected:
                self._counted_name = None
                label = ""
            else:
                self._counted_name = None
                label = "%d objects selected - pick one" % len(selected)
        except Exception:
            label = ""
        changed = label != self._selection
        self._selection = label
        return label, changed

    @QtCore.Slot()
    def poll(self):
        """Watch the viewport selection, so the panel keeps up with it.

        Never while a send from Blender is landing. That work creates and
        discards scenes, and reading `current_scene()` in the middle of it
        crashes Cascadeur outright - three dumps in four minutes when this
        timer was added without the guard.
        """
        if self._busy or _send_in_progress():
            return
        label, changed = self._look_at_selection()
        if not changed:
            return
        # A CSV is already chosen and the selection just became a single
        # mesh: re-check rather than make them press it again.
        if self._path and label and "objects selected" not in label:
            self.check()
        else:
            self._ready = False
            self._emit()

    # -- actions ----------------------------------------------------------

    @QtCore.Slot()
    def choose(self):
        def picked(path):
            if not path:
                return
            self._path = path
            self._ready = False
            self._preview = ""
            self._status = "Press Check to see what it would key."
            self._emit()
            self.check()

        csc.app.get_application().get_file_dialog_manager() \
            .show_open_file_dialog("Choose the mocap CSV", "", ["*.csv"],
                                   picked)

    @QtCore.Slot()
    def check(self):
        """Read the file and say what would land where. Writes nothing."""
        if not self._path:
            self._status = "Pick a CSV first."
            self._emit()
            return
        try:
            found = mocap.report(_scene(), self._path)
        except Exception as error:
            self._ready = False
            self._preview = ""
            self._status = str(error)
            self._emit()
            return

        lines = ["%-28s  ->  %s" % (header, shape)
                 for header, shape in sorted(found["matched"].items())]
        if found["missing"]:
            lines.append("")
            lines.append("no blendshape for these %d column(s):"
                         % len(found["missing"]))
            lines.extend("   " + name for name in found["missing"])
        if found["unused"]:
            lines.append("")
            lines.append("%d blendshape(s) the file does not drive:"
                         % len(found["unused"]))
            lines.extend("   " + name for name in found["unused"][:20])
        self._preview = "\n".join(lines)
        self._ready = bool(found["matched"])
        self._status = ("%s: %d of %d columns match, %d frames."
                        % (found["mesh"], len(found["matched"]),
                           found["columns"], found["frames"]))
        if not found["matched"]:
            self._status += "  Nothing to import."
        self._emit()

    @QtCore.Slot()
    def load(self):
        if not self._ready or self._busy:
            return
        self._busy = True
        self._status = "Importing..."
        self._emit()

        def progress(done, total):
            self._status = "Importing... %d / %d frames" % (done, total)
            self._emit()

        try:
            result = mocap.load(_scene(), self._path,
                                window_length=self._window,
                                polyorder=self._order,
                                scale=self._scale,
                                start_frame=self._start,
                                smooth=self._smooth,
                                progress=progress)
        except Exception as error:
            self._status = "Import failed: %s" % error
            self._busy = False
            self._emit()
            return

        self._busy = False
        self._status = ("Keyed %d blendshapes over frames %d-%d on %s."
                        % (result["keyed"], result["first"], result["last"],
                           result["mesh"]))
        if result["missing"]:
            self._status += ("  %d column(s) matched nothing."
                             % len(result["missing"]))
        self._emit()


def make_resizable(title="MetaArKit", width=640, height=680,
                   minimum=(520, 420)):
    """Undo the fixed-size, application-modal window Cascadeur gives us."""
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
    model = FacialMocapModel()
    pointer = shiboken6.getCppPointer(model)
    if isinstance(pointer, tuple):
        pointer = pointer[0]
    return model, pointer


def show_qml_view(model, name):
    csc.view.PythonModelsManager.instance().show_dialog("MetaArKit",
                                                        name)
    try:
        QtCore.QTimer.singleShot(0, make_resizable)
        QtCore.QTimer.singleShot(150, make_resizable)
    except Exception:
        make_resizable()
