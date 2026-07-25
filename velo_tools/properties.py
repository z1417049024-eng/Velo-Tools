"""Properties and scene data."""

import json
import re

import bpy
from bpy.props import (
    StringProperty,
    FloatProperty,
    FloatVectorProperty,
    BoolProperty,
    IntProperty,
    EnumProperty,
    PointerProperty,
    CollectionProperty,
)


_suspend_updates = False
_suspend_history = False
_suspend_base_reload = False
_SUFFIX_RE = re.compile(r"\.\d{3}$")
_PARTITION_COMPONENT_RE = re.compile(r"component[_ -]*(\d+)", re.IGNORECASE)
_PARTITION_PART_RE = re.compile(r"^part\.(\d+)$", re.IGNORECASE)
_partition_part_items_cache = []


# ============================================================
# Per-object mapping-table persistence (since v0.3.0_R3fix)
#
# Stores a JSON blob on base_object as the custom property "velo_local_rename_map":
#   {
#     "rows": [
#       {"src_orig": str, "current": str, "tgt_idx": int, "distance": float}
#     ],
#     "target_object": str | "",
#   }
# When base_object is switched / eyedropper-picked, s.mappings is automatically
# rebuilt from that object; on rename / revert it is written back in sync.
# ============================================================

PER_OBJ_KEY = "velo_local_rename_map"


def load_per_object_map(obj):
    if obj is None:
        return None
    raw = obj.get(PER_OBJ_KEY)
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return None


def save_per_object_map(obj, data: dict):
    if obj is None:
        return
    try:
        obj[PER_OBJ_KEY] = json.dumps(data or {}, ensure_ascii=False)
    except Exception:
        pass


def clear_per_object_map(obj):
    if obj is None:
        return
    if PER_OBJ_KEY in obj.keys():
        try:
            del obj[PER_OBJ_KEY]
        except Exception:
            pass


def _refresh_available_base_vgs(settings):
    coll = getattr(settings, "available_base_vgs", None)
    if coll is None:
        return
    coll.clear()
    names = set()
    obj = getattr(settings, "base_object", None)
    if obj is not None and obj.type == 'MESH':
        try:
            names.update(vg.name for vg in obj.vertex_groups)
        except Exception:
            pass
    for row in getattr(settings, "mappings", ()):
        for name in (
            (getattr(row, "original_name", "") or "").strip(),
            (getattr(row, "current_name", "") or "").strip(),
        ):
            if name:
                names.add(name)
    for name in sorted(names):
        item = coll.add()
        item.name = name


def _serialize_mapping_row(it) -> dict:
    return {
        "src_orig": it.original_name,
        "current_src": it.current_name,
        "target_name": it.target_name,
        "matched": bool(it.matched),
        "enabled": bool(getattr(it, "enabled", True)),
        "tgt_idx": int(it.target_vg_index),
        "distance": float(it.distance),
        # Cache centroids to avoid recomputing on object switch (performance-critical)
        "bc": list(it.base_centroid_local) if it.has_base_centroid else None,
        "tc": list(it.target_centroid_local) if it.has_target_centroid else None,
    }


def _legacy_target_name_from_row(row: dict, target):
    target_name = (row.get("target_name") or "").strip()
    if target_name:
        return target_name

    legacy_current = (row.get("current") or "").strip()
    tgt_idx = int(row.get("tgt_idx", -1))
    if target is not None and target.type == 'MESH':
        if legacy_current and target.vertex_groups.get(legacy_current) is not None:
            return legacy_current
        if 0 <= tgt_idx < len(target.vertex_groups):
            try:
                return target.vertex_groups[tgt_idx].name
            except Exception:
                pass
    return ""


def serialize_mappings_to_dict(settings) -> dict:
    rows = []
    for it in settings.mappings:
        rows.append(_serialize_mapping_row(it))
    target = settings.target_object
    base = settings.base_object
    # state: defaults to RENAMED (renamed state right after matching); set to ORIGINAL on revert
    existing = load_per_object_map(base)
    state = (existing or {}).get("state") or "RENAMED"
    return {
        "rows": rows,
        "target_object": target.name if target else "",
        "state": state,
    }


def sync_mappings_to_base_object(settings):
    """Write the current s.mappings back to the custom property on base_object."""
    base = settings.base_object
    if base is None:
        return
    save_per_object_map(base, serialize_mappings_to_dict(settings))


def set_per_object_state(obj, state: str):
    if obj is None:
        return
    data = load_per_object_map(obj) or {}
    data["state"] = state
    save_per_object_map(obj, data)


def get_per_object_state(obj):
    data = load_per_object_map(obj)
    if not data:
        return None
    return data.get("state")


def rebuild_mappings_from_per_object(settings, base):
    """Restore the s.mappings display from base's custom property (no algorithm rerun, no vertex scan)."""
    settings.mappings.clear()
    settings.unmatched_sources.clear()
    settings.unmatched_targets.clear()
    settings.rename_history.clear()
    settings.source_baselines.clear()
    settings.baseline_object_name = base.name if base else ""

    data = load_per_object_map(base) if base else None
    if not data or not isinstance(data, dict):
        return 0

    rows = data.get("rows") or []
    if not isinstance(rows, list):
        return 0

    target = settings.target_object

    global _suspend_updates
    for src_idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        orig = row.get("src_orig") or ""
        curr = (row.get("current_src") or row.get("current") or orig)
        target_name = _legacy_target_name_from_row(row, target)
        enabled = bool(row.get("enabled", True))
        tgt_idx = int(row.get("tgt_idx", -1))
        matched = bool(row.get("matched", tgt_idx >= 0))
        dist = float(row.get("distance", -1.0))
        bc = row.get("bc")
        tc = row.get("tc")
        if not orig:
            continue

        bl = settings.source_baselines.add()
        bl.source_index = src_idx
        bl.baseline_name = orig

        it = settings.mappings.add()
        it.enabled = enabled
        it.source_index = src_idx
        it.original_name = orig
        it.current_name = curr
        it.distance = dist
        it.matched = matched
        it.target_vg_index = tgt_idx
        _suspend_updates = True
        try:
            it.target_name = target_name
        finally:
            _suspend_updates = False

        if bc and len(bc) == 3:
            it.base_centroid_local = bc
            it.has_base_centroid = True
        if tc and len(tc) == 3:
            it.target_centroid_local = tc
            it.has_target_centroid = True

    return len(settings.mappings)


def _on_base_object_update(self, context):
    """Automatically rebuild the match-result panel when the source object changes."""
    global _suspend_base_reload
    if _suspend_base_reload:
        return
    s = context.scene.velo_tools
    base = s.base_object

    # Before switching away, save the current unsaved edits to the previous base
    prev_name = s.baseline_object_name
    if prev_name and (base is None or base.name != prev_name):
        prev = bpy.data.objects.get(prev_name)
        if prev and len(s.mappings) > 0:
            try:
                # Temporarily point base back to serialize
                rows = []
                for it in s.mappings:
                    rows.append(_serialize_mapping_row(it))
                old = load_per_object_map(prev) or {}
                state = old.get("state") or "RENAMED"
                save_per_object_map(prev, {"rows": rows, "state": state})
            except Exception:
                pass

    if base is None:
        s.mappings.clear()
        s.unmatched_sources.clear()
        s.unmatched_targets.clear()
        s.baseline_object_name = ""
        return
    rebuild_mappings_from_per_object(s, base)
    # Task 4: switch the mapping-table Text based on object binding (each part keeps its own mapping after material separate/join)
    try:
        bound = base.get("velo_general_text", "")
        if bound:
            tb = bpy.data.texts.get(bound)
            if tb is not None and s.active_general_text != tb:
                s.active_general_text = tb  # the update callback rebuilds mappings
    except Exception:
        pass
    try:
        from . import overlay as _ov
        _ov.invalidate_cache()
    except Exception:
        pass
    try:
        _refresh_available_base_vgs(s)
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_target_object_update(self, context):
    """On target-object switch, only trigger a redraw; do not clear s.mappings (keep the user's unsaved edits)."""
    try:
        from . import overlay as _ov
        _ov.invalidate_cache()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_match_show_overlay_update(self, context):
    """The old general-overlay hook is disabled; the general section has moved to general_mapping.props."""
    return


def _redraw_view3d(context=None):
    """Redraw the VIEW_3D area (including the UI region) of all windows.

    Iterate via window_manager rather than context.screen -- in property update
    callbacks context.screen is sometimes None, which makes the redraw not happen
    at all. Returns the number of redrawn areas, for diagnostics.
    """
    n = 0
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return 0
    for win in wm.windows:
        screen = getattr(win, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
                n += 1
                for region in area.regions:
                    if region.type == 'UI':
                        region.tag_redraw()
    return n


def _on_active_game_update(self, context):
    """Redraw the N panel when the current game (Endfield/Wuthering) is switched.

    Issue A fix (A2, 2026-06-01, confirmed by real Blender 4.4 GUI testing): the poll of both
    game root panels was changed to only check active_tab=='GAME', so they are "always
    instantiated" when entering the GAME tab; switching active_game just swaps content on the
    already-instantiated panels per active_game (root.draw early-return + child-panel poll gating,
    see games/_a2_panels.py). So switching games only needs a plain redraw to take effect
    immediately, no longer needing a 0-delay timer to re-register the root panels -- 1.1.1's timer
    re-registration was shown to be ineffective for this issue (re-registering when the root panel
    was never instantiated still produces no instance), so it was removed along with
    _rebuild_active_game_panels."""
    _redraw_view3d()


def _on_active_tab_update(self, context):
    if getattr(self, "active_tab", None) != 'WEIGHT':
        return
    try:
        from .weights import runtime as _weight_runtime
        _weight_runtime.reset_overlay_pick_runtime(context)
    except Exception:
        pass


def strip_dup_suffix(name: str) -> str:
    if not name:
        return name
    return _SUFFIX_RE.sub("", name)


def lookup_vgroup(obj, name):
    """Try an exact match first; on failure, match once more using the suffix-stripped name."""
    if not (obj and obj.type == 'MESH' and name):
        return None
    vg = obj.vertex_groups.get(name)
    if vg:
        return vg
    base = strip_dup_suffix(name)
    if base != name:
        return obj.vertex_groups.get(base)
    return None


def _drop_by_name(coll, *names):
    """Remove all items with the given name from the collection."""
    targets = {n for n in names if n}
    for i in range(len(coll) - 1, -1, -1):
        if coll[i].name in targets:
            coll.remove(i)


def _add_unmatched_unique(coll, name, reason, obj):
    """If name is not in coll and the vertex group exists on obj, add it (and cache the centroid)."""
    if not name or coll is None:
        return
    base_name = strip_dup_suffix(name)
    for it in coll:
        if it.name == name or strip_dup_suffix(it.name) == base_name:
            return
    if not (obj and obj.type == 'MESH'):
        return
    vg = obj.vertex_groups.get(name) or obj.vertex_groups.get(base_name)
    if not vg:
        return
    # Deferred import to avoid a cycle
    from . import operators as _ops
    c = _ops.vgroup_centroid_local(obj, vg.index)
    item = coll.add()
    item.name = vg.name
    item.reason = reason
    if c is not None:
        item.centroid_local = c
        item.has_centroid = True


def _push_rename_history(settings, source_index, original_name, old_name, new_name):
    """Record the local rename history, used by "undo rename only"."""
    if not original_name or not old_name or not new_name:
        return
    if old_name == new_name:
        return
    item = settings.rename_history.add()
    item.source_index = source_index
    item.original_name = original_name
    item.old_name = old_name
    item.new_name = new_name
    # Prevent the history from growing without bound
    if len(settings.rename_history) > 300:
        settings.rename_history.remove(0)


def _on_target_name_update(self, context):
    """When the target name is edited a second time:
    - rename the vertex group and bone in sync
    - refresh the cached centroid (suffix fallback)
    - if the target centroid is located successfully -> mark as matched and clear the corresponding unmatched item
    """
    global _suspend_updates, _suspend_history
    if _suspend_updates:
        return

    new_name = (self.target_name or "").strip()

    s = context.scene.velo_tools
    base = s.base_object
    target = s.target_object
    arm = s.armature_object
    old = self.current_name or self.original_name

    from . import operators as _ops
    from . import overlay as _ov
    from .core.mapping import algorithms as _algo
    from mathutils import Vector

    if not new_name:
        old_target_vg_index = self.target_vg_index
        old_target_vg_name = ""
        if (
            old_target_vg_index >= 0
            and target
            and target.type == 'MESH'
            and old_target_vg_index < len(target.vertex_groups)
        ):
            old_target_vg_name = target.vertex_groups[old_target_vg_index].name

        if base and base.type == 'MESH' and _algo.has_vg_snapshot(base):
            final_name, _bone_n = _ops._sync_general_source_row_incremental(s, self, "")
            base_lookup_name = (final_name or self.current_name or self.original_name or "").strip()
            bg = None
            if base_lookup_name:
                bg = (base.vertex_groups.get(base_lookup_name)
                      or base.vertex_groups.get(self.current_name)
                      or base.vertex_groups.get(self.original_name))
            if bg:
                c = _ops.vgroup_centroid_local(base, bg.index)
                if c is not None:
                    self.base_centroid_local = c
                    self.has_base_centroid = True
        self.has_target_centroid = False
        self.target_vg_index = -1
        self.matched = False
        if self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动断开映射",
                base,
            )
        if old_target_vg_index >= 0 and old_target_vg_name:
            still_claimed = False
            for it in s.mappings:
                if it == self:
                    continue
                if it.matched and it.target_vg_index == old_target_vg_index:
                    still_claimed = True
                    break
            if not still_claimed:
                _add_unmatched_unique(
                    s.unmatched_targets,
                    old_target_vg_name,
                    "原匹配被断开",
                    target,
                )
        _ops._general_autosync_to_text(s)
        _ov.invalidate_cache()
        _refresh_available_base_vgs(s)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return

    # If the source object is currently in unified-numbering state, subsequent mapping edits must:
    # only update the actual name corresponding to this single row; duplicate unified names (e.g. 36 / 36.001)
    # can be handled by non-merging suffix assignment, with no need to replay the whole table.
    if base and base.type == 'MESH' and _algo.has_vg_snapshot(base):
        final_name, _bone_n = _ops._sync_general_source_row_incremental(s, self, new_name)
        base_lookup_name = (final_name or self.current_name or self.original_name or new_name).strip()
        bg = None
        if base_lookup_name:
            bg = (base.vertex_groups.get(base_lookup_name)
                  or base.vertex_groups.get(self.current_name)
                  or base.vertex_groups.get(self.original_name)
                  or base.vertex_groups.get(new_name))
        if bg:
            c = _ops.vgroup_centroid_local(base, bg.index)
            if c is not None:
                self.base_centroid_local = c
                self.has_base_centroid = True

        found_target = False
        new_target_vg_index = -1
        new_target_vg_name = ""
        if target and target.type == 'MESH':
            tg = lookup_vgroup(target, new_name)
            if tg:
                c = _ops.vgroup_centroid_local(target, tg.index)
                if c is not None:
                    self.target_centroid_local = c
                    self.has_target_centroid = True
                    found_target = True
                    new_target_vg_index = tg.index
                    new_target_vg_name = tg.name
            if not found_target:
                self.has_target_centroid = False

        if self.has_base_centroid and self.has_target_centroid and base and target:
            bw = base.matrix_world @ Vector(self.base_centroid_local)
            tw = target.matrix_world @ Vector(self.target_centroid_local)
            self.distance = (bw - tw).length

        old_target_vg_index = self.target_vg_index
        old_target_vg_name = ""
        if (
            old_target_vg_index >= 0
            and target
            and target.type == 'MESH'
            and old_target_vg_index < len(target.vertex_groups)
        ):
            old_target_vg_name = target.vertex_groups[old_target_vg_index].name

        self.target_vg_index = new_target_vg_index
        self.matched = bool(found_target)
        if found_target:
            _drop_by_name(s.unmatched_sources, self.original_name)
            _drop_by_name(
                s.unmatched_targets,
                new_target_vg_name,
                strip_dup_suffix(new_target_vg_name),
            )
        elif self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动改名后失配",
                base,
            )

        if (
            old_target_vg_index >= 0
            and old_target_vg_index != new_target_vg_index
            and old_target_vg_name
        ):
            still_claimed = False
            for it in s.mappings:
                if it == self:
                    continue
                if it.matched and it.target_vg_index == old_target_vg_index:
                    still_claimed = True
                    break
            if not still_claimed:
                _add_unmatched_unique(
                    s.unmatched_targets,
                    old_target_vg_name,
                    "原匹配被改走",
                    target,
                )

        _ops._general_autosync_to_text(s)
        _ov.invalidate_cache()
        _refresh_available_base_vgs(s)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return

    if new_name != old:
        old_name_for_history = old
        final_name = new_name
        if arm and arm.type == 'ARMATURE':
            bone = arm.data.bones.get(old)
            if bone:
                bone.name = new_name
                final_name = bone.name

        if base and base.type == 'MESH':
            vg = base.vertex_groups.get(old) or base.vertex_groups.get(final_name)
            if vg and vg.name != final_name:
                vg.name = final_name
                final_name = vg.name

        _suspend_updates = True
        try:
            self.current_name = final_name
            if self.target_name != final_name:
                self.target_name = final_name
        finally:
            _suspend_updates = False

        if (not _suspend_history) and final_name != old_name_for_history:
            _push_rename_history(
                s,
                self.source_index,
                self.original_name,
                old_name_for_history,
                final_name,
            )
    else:
        final_name = old

    # Base-object centroid
    if base and base.type == 'MESH':
        bg = base.vertex_groups.get(final_name)
        if bg:
            c = _ops.vgroup_centroid_local(base, bg.index)
            if c is not None:
                self.base_centroid_local = c
                self.has_base_centroid = True

    # Target centroid (suffix fallback)
    found_target = False
    new_target_vg_index = -1
    new_target_vg_name = ""
    if target and target.type == 'MESH':
        tg = lookup_vgroup(target, final_name)
        if tg:
            c = _ops.vgroup_centroid_local(target, tg.index)
            if c is not None:
                self.target_centroid_local = c
                self.has_target_centroid = True
                found_target = True
                new_target_vg_index = tg.index
                new_target_vg_name = tg.name
        if not found_target:
            self.has_target_centroid = False

    # Distance
    if self.has_base_centroid and self.has_target_centroid and base and target:
        bw = base.matrix_world @ Vector(self.base_centroid_local)
        tw = target.matrix_world @ Vector(self.target_centroid_local)
        self.distance = (bw - tw).length

    # Old target vgroup index (the real target before editing), used to detect whether it becomes orphaned
    old_target_vg_index = self.target_vg_index
    old_target_vg_name = ""
    if (
        old_target_vg_index >= 0
        and target
        and target.type == 'MESH'
        and old_target_vg_index < len(target.vertex_groups)
    ):
        old_target_vg_name = target.vertex_groups[old_target_vg_index].name

    # Write back the new target index
    self.target_vg_index = new_target_vg_index

    # Mark match state + sync the unmatched collections
    if found_target:
        self.matched = True
        _drop_by_name(s.unmatched_sources, self.original_name)
        _drop_by_name(
            s.unmatched_targets,
            new_target_vg_name,
            strip_dup_suffix(new_target_vg_name),
        )
    else:
        self.matched = False
        if self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动改名后失配",
                base,
            )

    # If the old target becomes orphaned (no other matched row's target_vg_index equals it), add it back to unmatched targets
    if (
        old_target_vg_index >= 0
        and old_target_vg_index != new_target_vg_index
        and old_target_vg_name
    ):
        still_claimed = False
        for it in s.mappings:
            if it == self:
                continue
            if it.matched and it.target_vg_index == old_target_vg_index:
                still_claimed = True
                break
        if not still_claimed:
            _add_unmatched_unique(
                s.unmatched_targets,
                old_target_vg_name,
                "原匹配被改走",
                target,
            )

    _ov.invalidate_cache()
    _refresh_available_base_vgs(s)
    # Note: per-object persistence is synced only when matching finishes / on explicit revert / on object switch,
    # not full-serialized on every keystroke rename (performance-critical, avoids 200 rows x JSON per keystroke)
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class VELO_CandidateItem(bpy.types.PropertyGroup):
    """A single match candidate (for the UI dropdown switch)."""
    target_vg_index: IntProperty(default=-1)
    target_name: StringProperty()
    score: FloatProperty(default=-1.0)


class VELO_AvailableVGName(bpy.types.PropertyGroup):
    pass


_suspend_shapekey_update = False


def _is_real_mesh_simple(obj):
    """Lightweight check equivalent to mesh_ops.is_real_mesh (avoids circular import)."""
    if obj is None or obj.type != 'MESH':
        return False
    if ".placeholder" in (obj.name or "").lower():
        return False
    if obj.data is not None and ".placeholder" in (obj.data.name or "").lower():
        return False
    return True


def _on_target_collection_update(self, context):
    """Scan once immediately when the target collection is set/switched (no manual refresh needed)."""
    self.shapekey_rename_unlock_from = -1
    self.shapekey_rename_unlock_end = -1
    # Deferred import to avoid a cycle
    try:
        from .mesh import shapekey_ops as _sk
    except Exception:
        return
    try:
        _sk.refresh_shapekey_list(context, force=True)
    except Exception:
        pass


def _on_shapekey_agg_name_update(self, context):
    """On rename in the aggregate panel, rename all same-named shape keys under the target collection together."""
    global _suspend_shapekey_update
    if _suspend_shapekey_update:
        return
    new_name = (self.name or "").strip()
    old_name = self.original_name
    if not new_name or not old_name or new_name == old_name:
        return

    s = context.scene.velo_tools
    coll = s.target_collection
    if coll is None:
        return

    from .mesh.shapekey_model import deform_number

    old_number = deform_number(old_name)
    new_number = deform_number(new_name)
    opened_repair_boundary = old_number is not None and new_number != old_number
    if opened_repair_boundary:
        existing_numbers = [
            number
            for item in s.shapekey_items
            if (number := deform_number(item.original_name or item.name)) is not None
        ]
        self.deform_rename_order = old_number
        current_boundary = int(s.shapekey_rename_unlock_from)
        s.shapekey_rename_unlock_from = (
            old_number
            if current_boundary < 0
            else min(current_boundary, old_number)
        )
        s.shapekey_rename_unlock_end = max(
            existing_numbers + [old_number, int(s.shapekey_rename_unlock_end)]
        )

    renamed = 0
    for obj in coll.all_objects:
        if not _is_real_mesh_simple(obj):
            continue
        sk = obj.data.shape_keys
        if not sk:
            continue
        kb = sk.key_blocks.get(old_name)
        if kb is None:
            continue
        kb.name = new_name
        renamed += 1

    _suspend_shapekey_update = True
    try:
        # When Blender auto-suffixes, sync the displayed real name (use the result of the last rename)
        self.original_name = new_name
        if self.name != new_name:
            self.name = new_name
    finally:
        _suspend_shapekey_update = False

    if opened_repair_boundary:
        from .mesh import shapekey_ops as _sk

        _sk.update_shapekey_number_locks(s)


def _on_shapekey_agg_value_update(self, context):
    """On value change in the aggregate panel, set the value of all same-named shape keys under the target collection together."""
    global _suspend_shapekey_update
    if _suspend_shapekey_update:
        return
    s = context.scene.velo_tools
    coll = s.target_collection
    if coll is None:
        return
    name = self.original_name or self.name
    if not name:
        return
    v = float(self.value)
    for obj in coll.all_objects:
        if not _is_real_mesh_simple(obj):
            continue
        sk = obj.data.shape_keys
        if not sk:
            continue
        kb = sk.key_blocks.get(name)
        if kb is None:
            continue
        if kb.value != v:
            kb.value = v


class VELO_ShapeKeyAggItem(bpy.types.PropertyGroup):
    """An aggregate entry for shape keys that appear in the target collection (only mesh.shape_keys, i.e. MMD vertex morphs)."""
    name: StringProperty(
        name="形态键名",
        description="编辑名称将同步重命名集合里所有同名形态键",
        update=_on_shapekey_agg_name_update,
    )
    original_name: StringProperty()
    selected: BoolProperty(
        name="选择自动重命名",
        description="仅勾选且当前已解锁的形态键会参与自动重命名",
        default=False,
    )
    deform_rename_order: IntProperty(default=-1, options={'HIDDEN'})
    count: IntProperty(default=0, description="该名称在多少个网格上出现")
    contributor_names: StringProperty(
        name="贡献对象",
        description="包含该形态键的全部网格对象名称",
    )
    is_deform_numbered: BoolProperty(
        name="已有 Deform 编号",
        description="连续编号锁定前缀中的形态键受保护，不参与自动重命名",
        default=False,
    )
    value: FloatProperty(
        name="值",
        default=0.0,
        min=-10.0,
        max=10.0,
        soft_min=0.0,
        soft_max=1.0,
        precision=3,
        description="同步设置集合里所有同名形态键的 value",
        update=_on_shapekey_agg_value_update,
    )


class VELO_MatchMappingItem(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="启用", default=True)
    source_index: IntProperty(name="源索引", default=-1)
    target_vg_index: IntProperty(name="目标顶点组索引", default=-1)
    original_name: StringProperty(name="原始名称")
    current_name: StringProperty(name="当前名称")
    target_name: StringProperty(
        name="目标名称",
        description="目标顶点组名; 直接编辑可二次修正, 同步顶点组与骨骼",
        update=_on_target_name_update,
    )
    distance: FloatProperty(name="距离", default=-1.0)
    matched: BoolProperty(default=True)

    base_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    target_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    has_base_centroid: BoolProperty(default=False)
    has_target_centroid: BoolProperty(default=False)

    # Full candidate ranking (ascending by score, including the current selection); the UI dropdown filters out the current one in real time and takes the top N
    candidates: CollectionProperty(type=VELO_CandidateItem)


class VELO_UnmatchedItem(bpy.types.PropertyGroup):
    name: StringProperty()
    reason: StringProperty()
    centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    has_centroid: BoolProperty(default=False)


class VELO_RenameHistoryItem(bpy.types.PropertyGroup):
    source_index: IntProperty(default=-1)
    original_name: StringProperty()
    old_name: StringProperty()
    new_name: StringProperty()


class VELO_SourceBaselineItem(bpy.types.PropertyGroup):
    source_index: IntProperty(default=-1)
    baseline_name: StringProperty()


def _partition_component_id(obj):
    if obj is None:
        return None
    raw = obj.get("velo_component_id") if hasattr(obj, "get") else None
    try:
        if raw is not None:
            return int(raw)
    except (TypeError, ValueError):
        pass
    for collection in getattr(obj, "users_collection", ()):
        raw = collection.get("velo_component_id") if hasattr(collection, "get") else None
        try:
            if raw is not None:
                return int(raw)
        except (TypeError, ValueError):
            pass
    match = _PARTITION_COMPONENT_RE.search(getattr(obj, "name", ""))
    return int(match.group(1)) if match else None


def _on_partition_merge_source_update(self, _context):
    component_id = _partition_component_id(self.source_object)
    if component_id is not None:
        self.source_component = component_id


def _on_partition_merge_target_update(self, _context):
    component_id = _partition_component_id(self.target_object)
    if component_id is not None:
        self.target_component = component_id


def _partition_part_target_items(_self, context):
    global _partition_part_items_cache
    settings = getattr(context.scene, "velo_tools", None)
    cfg = getattr(context.scene, "VTEF_settings", None)
    root = getattr(cfg, "component_collection", None) if settings else None
    indices = set()
    if root is not None:
        pending = [root]
        collections = []
        while pending:
            collection = pending.pop()
            collections.append(collection)
            pending.extend(collection.children)
        for collection in collections:
            raw = collection.get("velo_partition_part_index")
            try:
                if raw is not None:
                    indices.add(int(raw))
                    continue
            except (TypeError, ValueError):
                pass
            match = _PARTITION_PART_RE.match(collection.name)
            if match:
                indices.add(int(match.group(1)))
        for obj in root.all_objects:
            try:
                index = int(obj.get("velo_partition_part_index", 0) or 0)
            except (TypeError, ValueError):
                index = 0
            if index > 0:
                indices.add(index)
    next_index = max(indices, default=0) + 1
    _partition_part_items_cache = [
        ("AUTO", f"自动（part.{next_index}）", "新批次自动使用下一个 part 编号")
    ]
    _partition_part_items_cache.extend(
        (str(index), f"part.{index}", f"将输出放入已有 part.{index}")
        for index in sorted(indices)
    )
    return _partition_part_items_cache


def _partition_merge_object_poll(_self, obj):
    return obj.type == "MESH" and obj.get("velo_partition_role") in {"output", "source"}


def _on_partition_whole_home_update(self, context):
    if self.object is None:
        return
    try:
        from .partition.sync import move_whole_mesh_to_home

        move_whole_mesh_to_home(context.scene, self.object, int(self.home_component))
    except Exception:
        pass


class VELO_PartitionWholeMeshItem(bpy.types.PropertyGroup):
    object: PointerProperty(
        name="整体模型",
        type=bpy.types.Object,
        poll=lambda _self, obj: obj.type == "MESH",
    )
    source_id: StringProperty(default="", options={'HIDDEN'})
    home_component: IntProperty(
        name="整体区存放 Component",
        description="只决定完整模型在原始整体区存放于哪个 Cx，不改变同步后的分割路由",
        default=0,
        min=0,
        max=15,
        update=_on_partition_whole_home_update,
    )


class VELO_PartitionMergeItem(bpy.types.PropertyGroup):
    source_object: PointerProperty(
        name="归并来源",
        description="左侧基准身体片段所属 Component 将并入右侧 Component",
        type=bpy.types.Object,
        poll=_partition_merge_object_poll,
        update=_on_partition_merge_source_update,
    )
    target_object: PointerProperty(
        name="归并目标",
        description="接收左侧分块的基准身体片段",
        type=bpy.types.Object,
        poll=_partition_merge_object_poll,
        update=_on_partition_merge_target_update,
    )
    source_component: IntProperty(default=-1, options={'HIDDEN'})
    target_component: IntProperty(default=-1, options={'HIDDEN'})


class VELO_PartitionNewPartItem(bpy.types.PropertyGroup):
    object: PointerProperty(
        name="新部件",
        description="使用完整 Master 的既有分区拆分这个新增网格",
        type=bpy.types.Object,
        poll=lambda _self, obj: obj.type == "MESH",
    )


class VELO_PartitionPassthroughItem(bpy.types.PropertyGroup):
    source_kind: EnumProperty(
        name="来源类型",
        items=[
            ('OBJECT', "对象", "原样同步一个 Mesh 对象"),
            ('COLLECTION', "集合", "递归原样同步集合中的全部 Mesh"),
        ],
        default='OBJECT',
    )
    source_object: PointerProperty(
        name="来源对象",
        type=bpy.types.Object,
        poll=lambda _self, obj: obj.type == "MESH",
    )
    source_collection: PointerProperty(
        name="来源集合",
        type=bpy.types.Collection,
    )
    target_component: IntProperty(
        name="目标 Component",
        default=0,
        min=0,
        max=15,
    )


def _on_partition_preview_mode_update(self, context):
    try:
        from .partition.sync import apply_preview_mode

        apply_preview_mode(context.scene)
    except Exception:
        pass


def _on_active_general_text_update(self, context):
    """When the mapping-table Text is switched: load that Text's content into mappings."""
    if _suspend_updates:
        return
    s = context.scene.velo_tools
    tb = s.active_general_text
    base = s.base_object
    if base is not None:
        try:
            base["velo_general_text"] = (tb.name if tb is not None else "")
        except Exception:
            pass
    if tb is None:
        return
    try:
        from . import operators as _ops
        _ops._load_general_text_into_mappings(s, tb.as_string())
        try:
            from .core.mapping import algorithms as _algo
            if base is not None and _algo.has_vg_snapshot(base):
                _ops._sync_general_source_from_table(s, save_backup=False)
        except Exception:
            pass
        _refresh_available_base_vgs(s)
    except Exception:
        pass


class VELO_ToolsSettings(bpy.types.PropertyGroup):
    base_object: PointerProperty(
        name="源物体",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        update=lambda self, context: _on_base_object_update(self, context),
    )
    target_object: PointerProperty(
        name="目标物体",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        update=lambda self, context: _on_target_object_update(self, context),
    )
    armature_object: PointerProperty(
        name="骨架",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',
        description="可选; 选择后改名将同步到骨骼",
    )

    mappings: CollectionProperty(type=VELO_MatchMappingItem)
    active_mapping_index: IntProperty(default=0)
    available_base_vgs: CollectionProperty(type=VELO_AvailableVGName)

    unmatched_sources: CollectionProperty(type=VELO_UnmatchedItem)
    unmatched_targets: CollectionProperty(type=VELO_UnmatchedItem)
    active_unmatched_source_index: IntProperty(default=0)
    active_unmatched_target_index: IntProperty(default=0)

    rename_history: CollectionProperty(type=VELO_RenameHistoryItem)
    source_baselines: CollectionProperty(type=VELO_SourceBaselineItem)
    baseline_object_name: StringProperty(default="")

    # Task 4: current mapping-table Text selector (template_ID style, consistent with the MMD section)
    active_general_text: PointerProperty(
        name="映射表",
        type=bpy.types.Text,
        description="当前使用的映射表内置文本；点击三角小图标在多个映射表之间切换",
        update=_on_active_general_text_update,
    )

    # Match options
    match_method: EnumProperty(
        name="匹配算法",
        items=[
            ('SAMPLES', "Top-K 加权样本", "每个顶点组取权重最高 K 个顶点做加权点云距离 (V0.0.5 算法)"),
            ('CENTROID', "权重重心", "仅比较权重中心 (旧算法, 速度最快, 区分度低)"),
        ],
        default='SAMPLES',
    )
    sample_count: IntProperty(
        name="采样数 K",
        default=8,
        min=1,
        max=64,
        description="每个顶点组用权重最高的 K 个顶点做匹配",
    )
    use_max_distance: BoolProperty(
        name="启用最大距离过滤",
        default=False,
        description="得分(距离)超过阈值的匹配视为失败, 保留原名",
    )
    max_match_distance: FloatProperty(
        name="最大匹配距离",
        default=0.5,
        min=0.0,
        soft_max=10.0,
    )

    # Visualization
    show_overlay: BoolProperty(
        name="启用可视化校对",
        default=False,
        description="旧通用映射可视化开关；当前通用映射已迁移到独立面板，此项不再自动关闭任何其它可视化功能",
        update=lambda self, context: _on_match_show_overlay_update(self, context),
    )
    only_show_active: BoolProperty(
        name="只显示当前选中行",
        default=False,
        description="仅显示当前选中映射行的可视化连线和端点",
    )
    overlay_max_distance: FloatProperty(
        name="距离阈值(可视化)",
        default=0.1,
        min=0.0,
        soft_max=2.0,
        description="可视化中判断匹配距离是否正常的阈值；超过阈值会显示为异常颜色",
    )
    show_labels: BoolProperty(
        name="显示名称标签",
        default=True,
        description="在可视化端点旁显示顶点组名称标签",
    )
    show_unmatched_targets: BoolProperty(
        name="显示未匹配的目标顶点组",
        default=True,
        description="显示目标网格上尚未被映射表认领的可用顶点组端点",
    )
    show_unmatched_sources: BoolProperty(
        name="显示未匹配的源顶点组",
        default=False,
        description="显示源网格上尚未被映射表认领的可用顶点组端点",
    )

    # ============================================================
    # Mesh / shape-key features (separate N-panel tab "Velo 网格")
    # ============================================================
    target_collection: PointerProperty(
        name="目标集合",
        type=bpy.types.Collection,
        description="形态键聚合面板的扫描范围",
        update=lambda self, context: _on_target_collection_update(self, context),
    )
    shapekey_items: CollectionProperty(type=VELO_ShapeKeyAggItem)
    active_shapekey_index: IntProperty(default=0)
    shapekey_rename_unlock_from: IntProperty(default=-1, options={'HIDDEN'})
    shapekey_rename_unlock_end: IntProperty(default=-1, options={'HIDDEN'})

    # Split by material: shape-key "near-zero displacement" cleanup threshold
    # Unit = object local coordinates (meters). Baking bones into shape keys often leaves residual displacement on the order of 1e-4 ~ 1e-3;
    # Blender's built-in "clean" only deletes exact zeros, so this provides an adjustable threshold as a fallback.
    shapekey_cleanup_threshold: FloatProperty(
        name="形态键清理阈值",
        description=(
            "按材质拆分后, 若某形态键在该子网格上所有顶点的最大位移 ≤ 此阈值, "
            "则视为无效并删除 (单位: 米, 物体局部坐标)"
        ),
        default=1e-4,
        min=0.0,
        soft_max=1e-2,
        precision=6,
        step=0.01,
    )
    mesh_component_prefix_id: IntProperty(
        name="Component 编号",
        description="网格工具中为选中物体添加 Component 前缀时使用的编号",
        default=0,
        min=0,
        soft_max=999,
    )

    mesh_auto_material_on_rename: BoolProperty(
        name="改名时自动同步材质球",
        description=(
            "开启后，当前游戏 Export Mod 部件集合及其子集合中的单材质网格物体改名时，"
            "自动同步 mesh 与材质球名称；多材质槽物体不处理"
        ),
        default=False,
    )

    partition_reference_object: PointerProperty(
        name="分区参考体",
        description="包含面级 Component ID 的参考网格",
        type=bpy.types.Object,
    )
    partition_master_object: PointerProperty(
        name="完整 Master",
        description="需要保留完整版本并自动拆回各 Component 的目标网格",
        type=bpy.types.Object,
    )
    partition_legacy_object: PointerProperty(
        name="旧手工合并体",
        description="可选；旧的手工 Join 网格会被隐藏并排除导出",
        type=bpy.types.Object,
    )
    partition_authoring_collection: PointerProperty(
        name="原始整体区",
        description="原始导入的 EFMI Component 树；用户直接在这里编辑",
        type=bpy.types.Collection,
    )
    partition_export_collection: PointerProperty(
        name="工作分割区",
        description="插件生成并交给 EFMI 导出的只读分割镜像",
        type=bpy.types.Collection,
    )
    partition_previous_export_collection: PointerProperty(
        name="旧导出集合",
        description="初始化前使用的 EFMI 组件集合",
        type=bpy.types.Collection,
        options={'HIDDEN'},
    )
    partition_register_component: IntProperty(
        name="整体区存放 Component",
        description="把新登记的整体模型放入原始整体区的 Cx；这不是最终分割结果",
        default=0,
        min=0,
        max=15,
    )
    partition_whole_mesh_items: CollectionProperty(type=VELO_PartitionWholeMeshItem)
    partition_whole_mesh_index: IntProperty(default=0)
    partition_mesh_split_active: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    partition_mesh_split_object: PointerProperty(
        type=bpy.types.Object,
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    partition_registry_migrated: BoolProperty(default=False, options={'HIDDEN'})
    partition_imported_captured: BoolProperty(default=False, options={'HIDDEN'})
    partition_auto_link_separated: BoolProperty(
        name="自动识别分离并创建集合",
        description=(
            "同步时按内部来源 ID 识别复制或手工分离的整体模型，自动创建强关联集合；"
            "支持连续多次分离，改名不影响识别"
        ),
        default=True,
    )
    partition_output_mode: EnumProperty(
        name="生成方式",
        items=[
            ('REPLACE', "重新生成", "覆盖同一 Master 之前生成的全部 Component 输出"),
            ('APPEND', "额外生成", "保留之前的输出并额外生成一套 Component"),
            ('WEIGHTS', "仅更新权重", "不重新分面，只把完整 Master 当前权重同步到已有 Component 输出"),
        ],
        default='REPLACE',
    )
    partition_part_target: EnumProperty(
        name="输出合集",
        description="自动使用下一个 part 编号，或选择已有 part 合集",
        items=_partition_part_target_items,
    )
    partition_merge_items: CollectionProperty(type=VELO_PartitionMergeItem)
    partition_new_part_items: CollectionProperty(type=VELO_PartitionNewPartItem)
    partition_passthrough_items: CollectionProperty(type=VELO_PartitionPassthroughItem)
    partition_passthrough_component: IntProperty(
        name="不分割物体目标 Component",
        description=(
            "“加入选中物体”会立即把选中的 Mesh 移入整体区这个 Cx；"
            "之后同步时原样复制到分割区，不进行 Component 拆分"
        ),
        default=0,
        min=0,
        max=15,
    )
    partition_sync_manifest: StringProperty(default="", options={'HIDDEN'})
    partition_output_manifest: StringProperty(default="", options={'HIDDEN'})
    partition_preview_mode: EnumProperty(
        name="预览区域",
        items=[
            ('AUTHORING', "整体区", "显示整体区并隐藏分割区"),
            ('EXPORT', "分割区", "隐藏整体区并显示分割区"),
        ],
        default='AUTHORING',
        update=_on_partition_preview_mode_update,
    )
    partition_status: StringProperty(
        name="分割状态",
        default="",
        options={'HIDDEN'},
    )

    # Top-of-main-panel tab switch
    active_tab: EnumProperty(
        name="功能区",
        items=[
            ('MATCH', "顶点组工具", "顶点组名称匹配 / MMD 映射 / 顶点组操作"),
            ('MESH', "网格工具", "材质 / 拆分合并 / 形态键聚合 / 多物体雕刻"),
            ('WEIGHT', "权重工具", "权重传递 / 平滑 / 限制组数量"),
            ('PARTITION', "分割操作", "EFMI Merged 整体区到分割区同步"),
            ('GAME', "游戏", "游戏 MOD 工作流：终末地(EFMI) / 鸣潮(WWMI) / 绝区零(ZZMI)"),
        ],
        default='MATCH',
        update=_on_active_tab_update,
    )

    # Game selector (dropdown) inside the "游戏" tab: Endfield (EFMI) / Wuthering (WWMI)
    active_game: EnumProperty(
        name="游戏",
        items=[
            ('ENDFIELD', "终末地", "明日方舟：终末地 MOD 工作流（EFMITools）"),
            ('WUTHERING', "鸣潮", "鸣潮 MOD 工作流（WWMITools）"),
            ('ZENLESS', "绝区零", "绝区零 DBMT / ZZMI MOD 工作流"),
        ],
        default='ENDFIELD',
        update=_on_active_game_update,
    )


_classes = (
    VELO_CandidateItem,
    VELO_AvailableVGName,
    VELO_ShapeKeyAggItem,
    VELO_MatchMappingItem,
    VELO_UnmatchedItem,
    VELO_RenameHistoryItem,
    VELO_SourceBaselineItem,
    VELO_PartitionWholeMeshItem,
    VELO_PartitionMergeItem,
    VELO_PartitionNewPartItem,
    VELO_PartitionPassthroughItem,
    VELO_ToolsSettings,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.velo_tools = PointerProperty(type=VELO_ToolsSettings)


def unregister():
    if hasattr(bpy.types.Scene, "velo_tools"):
        del bpy.types.Scene.velo_tools
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
