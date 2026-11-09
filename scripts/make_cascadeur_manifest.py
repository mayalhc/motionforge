"""Stage blender-runtime/cascadeur-plugin/ from an Engine Folder and write its MANIFEST.json.

Install Engines in the Blender add-on downloads this tree into the person's
Engine Folder; the person then runs install_cascadeur_plugin.bat, which copies
the plug-in into Cascadeur. Paths in the manifest are relative to the Engine
Folder.

    python scripts/make_cascadeur_manifest.py [ENGINE_FOLDER]      (default D:\\Kimodo)
"""

import hashlib
import json
import os
import shutil
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
source = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else r"D:\Kimodo")
target = os.path.join(root, "blender-runtime", "cascadeur-plugin")

ROOT_FILES = ["start_ardy.bat", "stop_ardy.bat", "install_cascadeur_plugin.bat"]
SUFFIXES = {".py", ".bat", ".qml", ".json", ".ini", ".casc", ".md", ".txt"}


def wanted(name):
    return (os.path.splitext(name)[1] in SUFFIXES and not name.endswith("_test.py")
            and "__pycache__" not in name)


paths = list(ROOT_FILES)
plugin = os.path.join(source, "plugins", "cascadeur")
for folder, dirs, names in os.walk(plugin):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for name in names:
        if wanted(name):
            paths.append(os.path.relpath(os.path.join(folder, name), source).replace("\\", "/"))
paths = sorted(set(paths), key=lambda p: (p.count("/"), p))

for entry in os.listdir(target) if os.path.isdir(target) else []:
    if entry != "MANIFEST.json":
        full = os.path.join(target, entry)
        shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
os.makedirs(target, exist_ok=True)

files = []
for path in paths:
    origin = os.path.join(source, *path.split("/"))
    if not os.path.isfile(origin):
        raise SystemExit("%s is missing from %s" % (path, source))
    destination = os.path.join(target, *path.split("/"))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(origin, destination)
    with open(destination, "rb") as handle:
        data = handle.read()
    files.append({"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})

# Git must publish these bytes as they are: the manifest hashes them, and a batch
# file with its line endings rewritten no longer matches (or no longer runs).
with open(os.path.join(target, ".gitattributes"), "w", encoding="ascii", newline="\n") as handle:
    handle.write("* -text\n")

with open(os.path.join(target, "MANIFEST.json"), "w", encoding="utf-8", newline="\n") as handle:
    json.dump({"format": "cascadeur-plugin-manifest-v1",
               "plugin": "MotionForge Cascadeur plug-in and engine service, relative to the Engine Folder",
               "files": files}, handle, indent=2)
    handle.write("\n")
print("%d files staged in %s" % (len(files), target))
