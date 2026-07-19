"""EFMI Merged Component partition workflow."""

from . import operators, ui


def register():
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
