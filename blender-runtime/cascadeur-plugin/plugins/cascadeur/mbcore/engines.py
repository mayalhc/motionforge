"""Stands in for the add-on's engines module: where an engine's files are."""
import os


def folder(prefs, name):
    return os.path.join(prefs.root, name)
