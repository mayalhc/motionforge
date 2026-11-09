# Copyright (c) 2026 Chamiseul. All rights reserved.
"""Quadruped rigging as one panel: match, correct once, keep it as a preset.

The shape matcher reads a four-legged skeleton and works out which joint is
which, which is enough for a rig it has never seen. What it could not do is
remember. A rig it gets slightly wrong had to be corrected by hand every time
a character of that family was opened, and a rig it gets right was matched
again from scratch for every one of them.

So the mapping is a thing you can name and save. Presets are matched by bone
name, so they apply instantly and to every character of the family; the panel
says how much of the open character each one covers, and picks the one that
covers it if there is one.

Everything here is fast - a match is a hierarchy walk over a hundred joints -
so unlike the ARDY panel there is no worker thread. The one slow thing would
be Cascadeur itself, and that is on the main thread by necessity.
"""

import os
import sys

import csc
import shiboken6
from PySide6 import QtCore

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(os.path.dirname(_HERE))


def _sibling(module_name):
    """Load one of the plugin's scripts by path.

    By name would be ambiguous: this package is called quadruped_autorig and
    so is the script, and which one `import quadruped_autorig` finds depends
    on the order of two directories on sys.path. Installed they are
    commands/animation_scripts/quadruped_autorig.py and
    models/quadruped_autorig/, and the package - which is empty - can win.
    """
    import importlib.util

    places = [
        os.path.join(_PLUGIN_DIR, module_name + ".py"),          # a checkout
        os.path.join(_PLUGIN_DIR, "commands", "animation_scripts",
                     module_name + ".py"),                        # installed
    ]
    for path in places:
        if not os.path.isfile(path):
            continue
        loaded = sys.modules.get("_quad_panel_" + module_name)
        if loaded is not None:
            return loaded
        spec = importlib.util.spec_from_file_location(
            "_quad_panel_" + module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    raise ImportError("cannot find %s.py beside the panel (looked in %s)"
                      % (module_name, ", ".join(places)))


autorig = _sibling("quadruped_autorig")
presets = _sibling("quadruped_presets")

AUTO = "Auto - match by shape"


def _scene():
    return csc.app.get_application().get_scene_manager() \
        .current_scene().domain_scene()


class QuadrupedModel(QtCore.QObject):

    changed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._presets = []
        self._choice = 0
        self._mapping = {}
        self._status = "Open a four-legged character and press Match."
        self._save_name = ""
        self._joints = []
        self.refresh()

    # -- what is on screen ------------------------------------------------

    def _emit(self):
        self.changed.emit()

    @QtCore.Property(list, notify=changed)
    def choices(self):
        out = [AUTO]
        for preset in self._presets:
            fit = presets.fit(preset, self._joints) if self._joints else 0.0
            suffix = "  -  fits %d%%" % round(fit * 100) if self._joints else ""
            out.append(preset["name"] + suffix)
        return out

    @QtCore.Property(int, notify=changed)
    def choice(self):
        return self._choice

    @choice.setter
    def choice(self, value):
        self._choice = max(0, min(int(value), len(self._presets)))
        self._emit()

    @QtCore.Property(str, notify=changed)
    def status(self):
        return self._status

    @QtCore.Property(str, notify=changed)
    def note(self):
        preset = self._selected()
        if preset is None:
            return ("Reads the skeleton's shape - the spine from the hips to "
                    "the head, the four chains that leave it - and names the "
                    "joints from that. Use it on a rig with no preset yet.")
        return preset.get("note", "")

    @QtCore.Property(str, notify=changed)
    def mapping_text(self):
        if not self._mapping:
            return ""
        order = {slot: i for i, slot in enumerate(autorig.slot_names())}
        lines = ["%-20s  %s" % (slot, self._mapping[slot])
                 for slot in sorted(self._mapping,
                                    key=lambda s: order.get(s, 999))]
        return "\n".join(lines)

    @QtCore.Property(str, notify=changed)
    def summary(self):
        if not self._mapping:
            return "nothing matched yet"
        return "%d joints mapped" % len(self._mapping)

    @QtCore.Property(bool, notify=changed)
    def has_mapping(self):
        return bool(self._mapping)

    @QtCore.Property(bool, notify=changed)
    def can_delete(self):
        preset = self._selected()
        return preset is not None and not preset.get("built_in")

    @QtCore.Property(str, notify=changed)
    def save_name(self):
        return self._save_name

    @save_name.setter
    def save_name(self, value):
        self._save_name = str(value)
        self._emit()

    def _selected(self):
        if self._choice <= 0:
            return None
        return self._presets[self._choice - 1]

    # -- what it does -----------------------------------------------------

    @QtCore.Slot()
    def refresh(self):
        """Re-read the presets and the open character."""
        self._presets = presets.load_all()
        try:
            self._joints = sorted(autorig.joints(_scene()))
        except Exception:
            self._joints = []
        if self._choice > len(self._presets):
            self._choice = 0
        if not self._joints:
            self._status = "No character in this scene."
        self._emit()

    @QtCore.Slot()
    def match(self):
        """Fill the mapping from the chosen preset, or from the shape."""
        self.refresh()
        if not self._joints:
            return
        scene = _scene()
        preset = self._selected()
        if preset is None:
            picked = presets.best(self._joints, self._presets)
            if picked is not None:
                landed, missing = presets.resolve(picked, self._joints)
                self._mapping = landed
                self._status = ("%s fits this character - used it (%d joints)."
                                % (picked["name"], len(landed)))
                self._emit()
                return
            chosen, leftover, notes = autorig.match(scene)
            self._mapping = chosen
            self._status = ("Matched %d joints by shape. Read them before "
                            "generating." % len(chosen))
            if notes:
                self._status += "  " + "  ".join(notes)
        else:
            landed, missing = presets.resolve(preset, self._joints)
            self._mapping = landed
            self._status = "%s: %d joints" % (preset["name"], len(landed))
            if missing:
                self._status += ("  -  %d not in this character (%s)"
                                 % (len(missing), ", ".join(missing[:6])))
        self._emit()

    @QtCore.Slot()
    def register(self):
        """Hand the mapping to the Quick Rigging Tool."""
        if not self._mapping:
            self._status = "Nothing to register - press Match first."
            self._emit()
            return
        try:
            filled = autorig.register(_scene(), self._mapping)
        except Exception as error:
            self._status = "Could not register: %s" % error
            self._emit()
            return
        if not filled:
            self._status = ("The Quick Rigging Tool did not take it. Open it "
                            "and set it to a four-legged character - on a "
                            "humanoid it ignores this in silence.")
        else:
            self._status = ("Registered %d fields. The panel has no fields "
                            "for ears or toes, so those stay out." % filled)
        self._emit()

    @QtCore.Slot()
    def build(self):
        """Build the rig without going through the panel."""
        if not self._mapping:
            self._status = "Nothing to build - press Match first."
            self._emit()
            return
        try:
            autorig.apply(_scene())
            self._status = ("Built the rig. Check it, and undo if a joint "
                            "landed wrong.")
        except Exception as error:
            self._status = "Could not build: %s" % error
        self._emit()

    @QtCore.Slot()
    def save(self):
        """Keep the current mapping as a preset."""
        try:
            path = presets.save(self._save_name, self._mapping)
        except Exception as error:
            self._status = str(error)
            self._emit()
            return
        self._save_name = ""
        name = os.path.basename(path)
        self.refresh()
        self._status = "Saved %s. It will be offered for any character whose " \
                       "bones are named this way." % name
        self._emit()

    @QtCore.Slot()
    def remove(self):
        preset = self._selected()
        if preset is None:
            return
        try:
            presets.delete(preset)
        except Exception as error:
            self._status = str(error)
            self._emit()
            return
        self._choice = 0
        self.refresh()
        self._status = "Deleted %s." % preset["name"]
        self._emit()


def make_resizable(title="Rig Quadruped", width=620, height=720,
                   minimum=(520, 420)):
    """Undo the fixed-size, application-modal window Cascadeur gives us.

    Both live on the QWindow rather than in the QML, so both can only be
    undone once the window exists - see the ARDY panel for the full story.
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
    model = QuadrupedModel()
    pointer = shiboken6.getCppPointer(model)
    if isinstance(pointer, tuple):
        pointer = pointer[0]
    return model, pointer


def show_qml_view(model, name):
    csc.view.PythonModelsManager.instance().show_dialog("Rig Quadruped", name)
    try:
        QtCore.QTimer.singleShot(0, make_resizable)
        QtCore.QTimer.singleShot(150, make_resizable)
    except Exception:
        make_resizable()
