"""Zenless Zone Zero DBMT/ZZMI integration driver for Velo Tools."""

from __future__ import annotations

import bpy

from . import _zzmi_core as _core
from ._zzmi_core.ui.panel_ui import PanelButtons
from .. import _a2_panels as _a2
from .. import registry as _registry


_GAME_VALUE = "ZENLESS"
_ROOT_ID = "VTZZ_PT_SIDEBAR"
_MISSING = object()
_patched_panels = []

_DESCRIPTOR = _registry.GameDescriptor(
    key="ZENLESS",
    game_value=_GAME_VALUE,
    display_name="绝区零",
    settings_attr="VTZZ_properties_generate_mod",
    adapter_key="ZZMI",
    export_op="vtzz.generate_mod_unity_vs",
    export_op_class="SSMTGenerateModUnityVS",
)


def enabled() -> bool:
    return True


def _record(cls, attr, values):
    values[attr] = cls.__dict__.get(attr, _MISSING)


def _patch_panels():
    for cls in _core.register_classes:
        if not isinstance(cls, type) or not issubclass(cls, bpy.types.Panel):
            continue
        values = {}
        for attr in ("bl_category", "bl_parent_id", "bl_label", "bl_options"):
            _record(cls, attr, values)
        cls.bl_category = "Velo Tools"
        if cls is PanelButtons:
            cls.bl_label = "绝区零 ZZMI / DBMT"
            cls.bl_parent_id = "VELO_PT_main"
            cls.bl_options = set(getattr(cls, "bl_options", set())) | {"DEFAULT_CLOSED"}
        else:
            cls.bl_parent_id = _ROOT_ID
        _patched_panels.append((cls, values))


def _restore_panels():
    for cls, values in reversed(_patched_panels):
        for attr, value in values.items():
            if value is _MISSING:
                if attr in cls.__dict__:
                    delattr(cls, attr)
            else:
                setattr(cls, attr, value)
    _patched_panels.clear()


def register():
    _patch_panels()
    _core.register()
    _DESCRIPTOR.header_label = "绝区零 ZZMI / DBMT"
    _DESCRIPTOR.draw_body = _a2.make_draw_body(PanelButtons)
    _registry.register_descriptor(_DESCRIPTOR)
    try:
        from ...core.export import hook as _hook
        _hook.install_export_hook()
    except Exception:
        import traceback
        traceback.print_exc()
    _a2.gate(_ROOT_ID, _GAME_VALUE, PanelButtons)


def unregister():
    try:
        _a2.ungate(_ROOT_ID)
    except Exception:
        pass
    _registry.unregister_descriptor(_GAME_VALUE)
    _DESCRIPTOR.header_label = None
    _DESCRIPTOR.draw_body = None
    try:
        _core.unregister()
    finally:
        _restore_panels()
