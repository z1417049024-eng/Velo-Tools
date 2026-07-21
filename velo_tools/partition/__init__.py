"""Merged Component partition workflow."""

from . import operators, sync, ui


def register():
    operators.register()
    sync.register()
    ui.register()


def unregister():
    ui.unregister()
    sync.unregister()
    operators.unregister()
