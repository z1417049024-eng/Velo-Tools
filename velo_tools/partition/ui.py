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
        header.operator("velo.partition_passthrough_add", text="集合规则", icon="OUTLINER_COLLECTION")
        passthrough.operator(
            "velo.partition_add_native_parts",
            text="加入原生部件（自动识别 Cx）",
            icon="IMPORT",
        )
        join = passthrough.row(align=True)
        join.prop(settings, "partition_passthrough_component", text="放入 C")
        join.operator(
            "velo.partition_passthrough_join_selected",
            text=f"加入选中物体到 C{settings.partition_passthrough_component}",
            icon="ADD",
        )
        for index, item in enumerate(settings.partition_passthrough_items):
            row = passthrough.row(align=True)
            if item.source_kind == "COLLECTION":
                row.label(text="集合", icon="OUTLINER_COLLECTION")
                row.prop(item, "source_collection", text="")
                row.label(text="", icon="FORWARD")
                row.prop(item, "target_component", text="放入 C")
            else:
                name = item.source_object.name if item.source_object is not None else "对象已删除"
                row.label(text=name, icon="MESH_DATA")
                row.label(text="", icon="FORWARD")
                row.label(text=f"C{item.target_component}")
            remove = row.operator("velo.partition_passthrough_remove", text="", icon="X")
            remove.item_index = index

        actions = layout.column(align=True)
        actions.enabled = is_merged and settings.partition_master_object is not None
        preview = actions.row(align=True)
        preview.prop(settings, "partition_preview_mode", expand=True)
        actions.prop(settings, "partition_auto_link_separated")
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
