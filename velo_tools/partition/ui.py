"""N-panel UI for the EFMI Merged whole-mesh workflow."""

import textwrap

import bpy

from .constants import EXPORT_ZONE_KEY


def _is_partition_tab(context):
    settings = getattr(context.scene, "velo_tools", None)
    return settings is not None and settings.active_tab == "PARTITION"


class VELO_PT_partition(bpy.types.Panel):
    bl_label = "EFMI 整体区 / 分割区"
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
        if cfg is None:
            layout.label(text="未启用 Endfield EFMI 工作流", icon="ERROR")
            return

        setup = layout.box()
        setup.label(text="基础设置", icon="OUTLINER_COLLECTION")
        row = setup.row()
        row.label(text="游戏")
        row.label(text="终末地（测试版本）")
        row = setup.row()
        row.label(text="骨架模式")
        row.label(text="Merged（固定）", icon="LOCKED")
        is_merged = getattr(cfg, "mod_skeleton_type", None) == "MERGED"
        if not is_merged:
            setup.label(text="请先在游戏页把骨架模式设为 MERGED", icon="ERROR")

        authoring = settings.partition_authoring_collection
        if authoring is None and cfg.component_collection is not None:
            if not cfg.component_collection.get(EXPORT_ZONE_KEY):
                authoring = cfg.component_collection
        root_row = setup.row()
        root_row.enabled = False
        root_row.prop(settings, "partition_authoring_collection", text="原始整体区")
        if settings.partition_authoring_collection is None and authoring is not None:
            root_row.label(text=authoring.name)
        setup.prop(cfg, "object_source_folder", text="对象源目录")
        setup.prop(settings, "partition_legacy_object", text="原部件手工合并体")

        whole = layout.box()
        whole.enabled = is_merged
        whole.label(text="整体模型", icon="MOD_DATA_TRANSFER")
        active = context.active_object
        whole.label(
            text=f"活动 Mesh：{active.name if active is not None and active.type == 'MESH' else '未选择'}"
        )
        whole.prop(settings, "partition_register_component", text="整体区存放 C")
        buttons = whole.row(align=True)
        standard_label = "设为基准身体" if settings.partition_master_object else "创建基准身体"
        buttons.operator("velo.partition_create_standard_body", text=standard_label, icon="ARMATURE_DATA")
        buttons.operator("velo.partition_add_whole_meshes", text="加入整体模型", icon="ADD")
        split_label = "完成分割" if settings.partition_mesh_split_active else "分割完整模型"
        whole.operator(
            "velo.partition_split_whole_mesh",
            text=split_label,
            icon="CHECKMARK" if settings.partition_mesh_split_active else "MOD_BOOLEAN",
        )

        master_group_id = (
            str(settings.partition_master_object.get("velo_partition_whole_split_group", "") or "")
            if settings.partition_master_object is not None
            else ""
        )
        for item in settings.partition_whole_mesh_items:
            row = whole.row(align=True)
            item_group_id = (
                str(item.object.get("velo_partition_whole_split_group", "") or "")
                if item.object is not None
                else ""
            )
            is_standard = item.object is settings.partition_master_object or (
                master_group_id and item_group_id == master_group_id
            )
            icon = "SOLO_ON" if is_standard else "MESH_DATA"
            name = item.object.name if item.object is not None else "已删除"
            row.label(text=name, icon=icon)
            row.prop(item, "home_component", text="存放 C")

        rules = layout.box()
        rules.enabled = is_merged and settings.partition_master_object is not None
        header = rules.row(align=True)
        header.label(text="分块归并")
        header.operator("velo.partition_merge_add", text="添加", icon="ADD")
        for index, mapping in enumerate(settings.partition_merge_items):
            row = rules.row(align=True)
            row.prop(mapping, "source_object", text="")
            row.label(text="", icon="FORWARD")
            row.prop(mapping, "target_object", text="")
            remove = row.operator("velo.partition_merge_remove", text="", icon="X")
            remove.mapping_index = index

        passthrough = layout.box()
        passthrough.enabled = is_merged and settings.partition_master_object is not None
        header = passthrough.row(align=True)
        header.label(text="原样同步到 Component")
        header.operator("velo.partition_passthrough_add", text="添加", icon="ADD")
        for index, item in enumerate(settings.partition_passthrough_items):
            row = passthrough.row(align=True)
            row.prop(item, "source_kind", text="")
            if item.source_kind == "COLLECTION":
                row.prop(item, "source_collection", text="")
            else:
                row.prop(item, "source_object", text="")
            row.label(text="", icon="FORWARD")
            row.prop(item, "target_component", text="部件")
            remove = row.operator("velo.partition_passthrough_remove", text="", icon="X")
            remove.item_index = index

        actions = layout.column(align=True)
        actions.enabled = is_merged and settings.partition_master_object is not None
        preview = actions.row(align=True)
        preview.prop(settings, "partition_preview_mode", expand=True)
        actions.operator("velo.partition_sync_zones", text="同步到分割区", icon="FILE_REFRESH")
        actions.operator(
            "velo.partition_select_ambiguous",
            text="选择模糊面",
            icon="RESTRICT_SELECT_OFF",
        )

        if settings.partition_status:
            box = layout.box()
            for index, line in enumerate(textwrap.wrap(settings.partition_status, width=34)):
                icon = "CHECKMARK" if "可以导出" in settings.partition_status else "INFO"
                box.label(text=line, icon=icon if index == 0 else "BLANK1")


_CLASSES = (VELO_PT_partition,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
