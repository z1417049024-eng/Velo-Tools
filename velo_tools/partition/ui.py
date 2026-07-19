"""N-panel UI for EFMI Component partitioning."""

import textwrap

import bpy


def _is_partition_tab(context):
    settings = getattr(context.scene, "velo_tools", None)
    return settings is not None and settings.active_tab == "PARTITION"


class VELO_PT_partition(bpy.types.Panel):
    bl_label = "EFMI Merged 分割操作"
    bl_idname = "VELO_PT_partition"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Velo Tools"
    bl_parent_id = "VELO_PT_main"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return _is_partition_tab(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.velo_tools
        cfg = getattr(context.scene, "VTEF_settings", None)

        column = layout.column(align=True)
        if cfg is not None:
            column.prop(cfg, "component_collection", text="组件集合")
            column.prop(cfg, "object_source_folder", text="对象源目录")
        column.prop(settings, "partition_legacy_object", text="旧手工合并体")
        column.operator("velo.partition_create_reference", icon="AUTOMERGE_ON")

        layout.separator()
        column = layout.column(align=True)
        column.prop(settings, "partition_reference_object", text="分区参考体")
        column.prop(settings, "partition_master_object", text="完整 Master")
        column.operator("velo.partition_project_split", icon="MOD_EXPLODE")

        row = layout.row(align=True)
        row.operator("velo.partition_select_ambiguous", icon="RESTRICT_SELECT_OFF")
        row.operator("velo.partition_restore_sources", icon="LOOP_BACK")

        if settings.partition_status:
            box = layout.box()
            for index, line in enumerate(textwrap.wrap(settings.partition_status, width=34)):
                box.label(text=line, icon="INFO" if index == 0 else "BLANK1")


_CLASSES = (VELO_PT_partition,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
