"""Smoke test for the EFMI authoring/export zone workflow."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import bpy


REPO_ROOT = Path(os.environ.get("VELO_TEST_ADDON_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO_ROOT))

import velo_tools  # noqa: E402
from velo_tools.partition.constants import (  # noqa: E402
    EXPORT_ZONE_KEY,
    PART_COLLECTION_KEY,
    ROLE_KEY,
    ROLE_IMPORTED,
    ROLE_MASTER,
    ROLE_OUTPUT,
    ROLE_SOURCE,
    SOURCE_ID_KEY,
    WHOLE_SPLIT_COLLECTION_KEY,
    WHOLE_SPLIT_GROUP_KEY,
)
from velo_tools.partition.sync import (  # noqa: E402
    build_output_manifest,
    output_names_for_source,
    validate_export_state,
)
from velo_tools.games.arknights_endfield.embedded.crossib.generator import (  # noqa: E402
    _source_name_aliases,
)


def mesh_object(name, vertices, faces, collection):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x, co.y)
    return obj


def weighted_grid(name, collection, z=0.0):
    obj = mesh_object(
        name,
        [
            (-1, -1, z), (0, -1, z), (1, -1, z),
            (-1, 1, z), (0, 1, z), (1, 1, z),
        ],
        [(0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4)],
        collection,
    )
    left = obj.vertex_groups.new(name="0")
    right = obj.vertex_groups.new(name="1")
    for vertex in obj.data.vertices:
        factor = max(0.0, min(1.0, (vertex.co.x + 1.0) / 2.0))
        left.add([vertex.index], 1.0 - factor, 'REPLACE')
        right.add([vertex.index], factor, 'REPLACE')
    return obj


def legacy_reference(collection):
    obj = weighted_grid("Component 2 old joined", collection)
    left = bpy.data.materials.new("Component 1 left")
    right = bpy.data.materials.new("Component 2 right")
    obj.data.materials.append(left)
    obj.data.materials.append(right)
    for polygon in obj.data.polygons:
        polygon.material_index = 0 if polygon.center.x < 0 else 1
    return obj


def main():
    velo_tools.register()
    scene = bpy.context.scene
    raw_root = bpy.data.collections.new("Synthetic Character")
    scene.collection.children.link(raw_root)
    components = {}
    for component_id in range(3):
        collection = bpy.data.collections.new(f"C{component_id}")
        collection["velo_component_id"] = component_id
        raw_root.children.link(collection)
        components[component_id] = collection

    source_left = mesh_object(
        "Component 1 left",
        [(-1, -1, 0), (0, -1, 0), (0, 1, 0), (-1, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
        components[1],
    )
    source_right = mesh_object(
        "Component 2 right",
        [(0, -1, 0), (1, -1, 0), (1, 1, 0), (0, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
        components[2],
    )
    body = weighted_grid("Component 2 Complete Body", components[2])
    body[ROLE_KEY] = ROLE_MASTER
    body["velo_partition_id"] = "legacy-partition"
    body["authoring_marker"] = "preserved"
    body.rotation_euler.z = 0.125
    parent = bpy.data.objects.new("Authoring Parent", None)
    raw_root.objects.link(parent)
    body.parent = parent
    constraint = body.constraints.new(type='LIMIT_LOCATION')
    constraint.name = "Authoring Limit"
    constraint.use_min_x = True
    constraint.min_x = -1.0
    material = bpy.data.materials.new("Body Material")
    body.data.materials.append(material)
    body.shape_key_add(name="Basis")
    raised = body.shape_key_add(name="Raised")
    for point in raised.data:
        point.co.z += 0.1
    raised.driver_add("value").driver.expression = "0.25"
    garment = weighted_grid("Component 2 Complete Garment", components[2], z=0.05)
    garment[ROLE_KEY] = ROLE_MASTER
    garment["velo_partition_id"] = "legacy-partition"
    legacy = legacy_reference(components[2])
    passthrough = mesh_object(
        "Component 0 Face",
        [(-0.2, -0.2, 0.2), (0.2, -0.2, 0.2), (0, 0.2, 0.2)],
        [(0, 1, 2)],
        components[0],
    )

    source_folder = Path(tempfile.mkdtemp(prefix="velo-zone-sync-"))
    payload = {
        "format_version": 1,
        "metadata_format_version": 3,
        "components": [
            {"mesh_name": "Component 0", "vg_map": {"0": 0, "1": 1}},
            {"mesh_name": "Component 1", "vg_map": {"0": 0, "1": 1}},
            {"mesh_name": "Component 2", "vg_map": {"0": 0, "1": 1}},
        ],
    }
    (source_folder / "VertexGroupMap.json").write_text(json.dumps(payload), encoding="utf-8")

    cfg = scene.VTEF_settings
    cfg.component_collection = raw_root
    cfg.object_source_folder = str(source_folder)
    cfg.mod_skeleton_type = "MERGED"
    settings = scene.velo_tools
    settings.partition_master_object = body
    settings.partition_legacy_object = legacy
    settings.partition_authoring_collection = raw_root

    assert bpy.ops.velo.partition_initialize_zones() == {'FINISHED'}
    authoring = settings.partition_authoring_collection
    export = settings.partition_export_collection
    assert authoring is not None and export is not None
    assert authoring == raw_root
    assert cfg.component_collection == export
    assert export.get(EXPORT_ZONE_KEY)
    assert body in set(authoring.all_objects) and garment in set(authoring.all_objects)
    assert len(settings.partition_whole_mesh_items) == 2
    assert not len(settings.partition_passthrough_items)
    assert passthrough.get(ROLE_KEY) == ROLE_IMPORTED
    assert source_left.get(ROLE_KEY) == ROLE_SOURCE
    assert source_right.get(ROLE_KEY) == ROLE_SOURCE
    assert len(export.children) == 16
    assert not [item for item in export.children if item.get(PART_COLLECTION_KEY)]

    outputs = [obj for obj in export.all_objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
    assert len(outputs) == 5
    assert len(output_names_for_source(body, cfg)) == 2
    assert len(output_names_for_source(garment, cfg)) == 2
    assert len(output_names_for_source(passthrough, cfg)) == 1
    assert all(obj.hide_get() for obj in outputs)
    body_outputs = [obj for obj in outputs if obj.get(SOURCE_ID_KEY) == body.get(SOURCE_ID_KEY)]
    assert all(obj.get("authoring_marker") == "preserved" for obj in body_outputs)
    assert all(obj.parent is parent for obj in body_outputs)
    assert all(obj.constraints.get("Authoring Limit") is not None for obj in body_outputs)
    assert all(obj.rotation_euler[:] == body.rotation_euler[:] for obj in body_outputs)
    assert all(obj.data.materials[0] is material for obj in body_outputs)
    assert all([key.name for key in obj.data.shape_keys.key_blocks] == ["Basis", "Raised"] for obj in body_outputs)
    assert all(obj.data.shape_keys.animation_data is not None for obj in body_outputs)
    initial_validation = validate_export_state(scene)
    if initial_validation:
        print("OUTPUT_MANIFESTS", settings.partition_output_manifest, build_output_manifest(export))
    assert initial_validation is None, initial_validation

    native_c9 = mesh_object(
        "Component 9 38faea2c.004",
        [(-0.2, -0.2, 0.4), (0.2, -0.2, 0.4), (0, 0.2, 0.4)],
        [(0, 1, 2)],
        components[2],
    )
    native_c1 = mesh_object(
        "Component 1 1f3a085f",
        [(-0.2, -0.2, 0.5), (0.2, -0.2, 0.5), (0, 0.2, 0.5)],
        [(0, 1, 2)],
        components[2],
    )
    native_c9[ROLE_KEY] = ROLE_SOURCE
    native_c1[ROLE_KEY] = ROLE_SOURCE
    source_right.hide_viewport = False
    source_right.hide_render = False
    source_right.hide_set(False)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in (native_c9, native_c1, source_right):
        obj.select_set(True)
    bpy.context.view_layer.objects.active = native_c9
    assert bpy.ops.velo.partition_add_native_parts() == {'FINISHED'}
    native_targets = {
        item.source_object: int(item.target_component)
        for item in settings.partition_passthrough_items
        if item.source_kind == 'OBJECT'
    }
    assert native_targets[native_c9] == 9
    assert native_targets[native_c1] == 1
    assert native_targets[source_right] == 2
    assert all(obj.get(ROLE_KEY) == ROLE_IMPORTED for obj in (native_c9, native_c1, source_right))
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert source_right.get(ROLE_KEY) == ROLE_IMPORTED
    assert len(output_names_for_source(native_c9, cfg)) == 1
    assert output_names_for_source(native_c9, cfg)[0].startswith("Component 9 ")
    assert len(output_names_for_source(native_c1, cfg)) == 1
    assert output_names_for_source(native_c1, cfg)[0].startswith("Component 1 ")
    assert len(output_names_for_source(source_right, cfg)) == 1
    for obj in (native_c9, native_c1, source_right):
        obj.hide_set(True)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert not output_names_for_source(native_c9, cfg)
    assert not output_names_for_source(native_c1, cfg)
    assert not output_names_for_source(source_right, cfg)

    copied_garment = garment.copy()
    copied_garment.data = garment.data.copy()
    copied_garment.name = "Component 2 Copied Garment"
    components[2].objects.link(copied_garment)
    assert settings.partition_auto_link_separated
    assert copied_garment.get(SOURCE_ID_KEY) == garment.get(SOURCE_ID_KEY)
    assert copied_garment not in {
        item.object for item in settings.partition_whole_mesh_items
    }
    settings.partition_auto_link_separated = False
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert copied_garment not in {
        item.object for item in settings.partition_whole_mesh_items
    }
    settings.partition_auto_link_separated = True
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert copied_garment in {
        item.object for item in settings.partition_whole_mesh_items
    }
    assert copied_garment.get(SOURCE_ID_KEY) != garment.get(SOURCE_ID_KEY)
    assert copied_garment.get(WHOLE_SPLIT_GROUP_KEY)
    assert copied_garment.get(WHOLE_SPLIT_GROUP_KEY) == garment.get(WHOLE_SPLIT_GROUP_KEY)
    linked_groups = {
        collection
        for obj in (garment, copied_garment)
        for collection in obj.users_collection
        if collection.get(WHOLE_SPLIT_COLLECTION_KEY)
    }
    assert len(linked_groups) == 1
    copied_outputs = output_names_for_source(copied_garment, cfg)
    assert len(copied_outputs) == 2

    nested_piece = copied_garment.copy()
    nested_piece.data = copied_garment.data.copy()
    nested_piece.name = "Renamed Nested Piece"
    next(iter(copied_garment.users_collection)).objects.link(nested_piece)
    assert nested_piece.get(SOURCE_ID_KEY) == copied_garment.get(SOURCE_ID_KEY)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert nested_piece.get(SOURCE_ID_KEY) != copied_garment.get(SOURCE_ID_KEY)
    assert nested_piece.get(WHOLE_SPLIT_GROUP_KEY) == copied_garment.get(WHOLE_SPLIT_GROUP_KEY)
    nested_outputs = output_names_for_source(nested_piece, cfg)
    assert len(nested_outputs) == 2
    assert all("Renamed Nested Piece" in name for name in nested_outputs)
    copied_garment.hide_set(True)
    nested_piece.hide_set(True)

    direct = mesh_object(
        "Unsplit Accessory",
        [(-0.2, -0.2, 0.3), (0.2, -0.2, 0.3), (0, 0.2, 0.3)],
        [(0, 1, 2)],
        components[2],
    )
    direct[SOURCE_ID_KEY] = body[SOURCE_ID_KEY]
    external = bpy.data.collections.new("External Accessory Working Set")
    scene.collection.children.link(external)
    external.objects.link(direct)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    direct.select_set(True)
    bpy.context.view_layer.objects.active = direct
    settings.partition_passthrough_component = 2
    assert bpy.ops.velo.partition_passthrough_join_selected() == {'FINISHED'}
    assert direct[SOURCE_ID_KEY] != body[SOURCE_ID_KEY]
    settings.partition_passthrough_component = 7
    assert bpy.ops.velo.partition_passthrough_join_selected() == {'FINISHED'}
    direct_rules = [
        item
        for item in settings.partition_passthrough_items
        if item.source_kind == 'OBJECT' and item.source_object is direct
    ]
    assert len(direct_rules) == 1
    direct_rule = next(
        item
        for item in settings.partition_passthrough_items
        if item.source_kind == 'OBJECT' and item.source_object is direct
    )
    assert direct_rule.target_component == 7
    component_7 = next(
        collection
        for collection in authoring.children
        if int(collection.get("velo_component_id", -1)) == 7
    )
    assert direct.name in component_7.objects
    assert direct.name not in components[2].objects
    assert direct.name in external.objects
    assert int(direct.get("velo_component_id", -1)) == 7
    assert not output_names_for_source(direct, cfg)
    components[2].objects.link(direct)
    component_7.objects.unlink(direct)
    assert direct.name in components[2].objects
    direct.data.vertices[0].co.x -= 0.1
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert direct.name in component_7.objects
    assert direct.name not in components[2].objects
    direct_outputs = output_names_for_source(direct, cfg)
    assert len(direct_outputs) == 1
    assert direct_outputs[0].startswith("Component 7 ")
    direct_output = next(obj for obj in settings.partition_export_collection.all_objects if obj.name == direct_outputs[0])
    assert abs(direct_output.data.vertices[0].co.x - direct.data.vertices[0].co.x) < 1e-6
    direct.hide_set(True)

    export = settings.partition_export_collection
    body_outputs = [
        obj
        for obj in export.all_objects
        if obj.get(SOURCE_ID_KEY) == body.get(SOURCE_ID_KEY)
    ]
    body_by_component = {int(obj.get("velo_component_id")): obj for obj in body_outputs}
    merge = settings.partition_merge_items.add()
    merge.source_object = body_by_component[1]
    merge.target_object = body_by_component[2]
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert len(output_names_for_source(body, cfg)) == 1
    assert len(output_names_for_source(garment, cfg)) == 1
    assert all(name.startswith("Component 2 ") for name in output_names_for_source(body, cfg))
    settings.partition_merge_items.remove(0)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert len(output_names_for_source(body, cfg)) == 2

    toggle = cfg.ini_toggles.add_new_var()
    toggle.states[1].objects[0].object = body
    cfg.use_ini_toggles = True
    compiled = cfg.ini_toggles.compile_conditions()
    assert set(compiled) == set(output_names_for_source(body, cfg))
    crossib = scene.crossib_settings.mappings.add()
    crossib.source_kind = 'OBJECT'
    crossib.source_object = body
    crossib.target_component = 0
    scene.crossib_settings.enabled = True
    assert set(_source_name_aliases(crossib, cfg, bpy.context)) == set(output_names_for_source(body, cfg))
    assert "请先点击" in validate_export_state(scene)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert validate_export_state(scene) is None

    invalid_toggle_object = toggle.states[1].add_new_state_object(toggle.name)
    invalid_toggle_object.object = source_left
    try:
        assert bpy.ops.velo.partition_sync_zones() == {'CANCELLED'}
    except RuntimeError as exc:
        assert source_left.name in str(exc)
        assert "不参与导出" in str(exc)
    assert source_left.name in settings.partition_status
    assert "不参与导出" in settings.partition_status
    toggle.states[1].objects.remove(len(toggle.states[1].objects) - 1)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert validate_export_state(scene) is None

    copied_source = source_left.copy()
    copied_source.data = source_left.data.copy()
    copied_source.name = source_left.name + ".004"
    components[1].objects.link(copied_source)
    copied_toggle_object = toggle.states[1].add_new_state_object(toggle.name)
    copied_toggle_object.object = copied_source
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert copied_source.get(ROLE_KEY) == ROLE_IMPORTED
    assert copied_source.get(SOURCE_ID_KEY)
    assert len(output_names_for_source(copied_source, cfg)) == 1
    assert validate_export_state(scene) is None
    toggle.states[1].objects.remove(len(toggle.states[1].objects) - 1)
    bpy.data.objects.remove(copied_source, do_unlink=True)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert validate_export_state(scene) is None

    body.vertex_groups[0].add([0], 0.75, 'REPLACE')
    assert "请先点击" in validate_export_state(scene)
    try:
        export_result = bpy.ops.vtef.export_mod()
        assert export_result == {'CANCELLED'}
    except RuntimeError as exc:
        assert "同步到分割区" in str(exc)
    first_export = export
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert settings.partition_export_collection is not first_export
    assert validate_export_state(scene) is None
    assert len([obj for obj in settings.partition_export_collection.all_objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]) == 5

    stable_export = settings.partition_export_collection
    stable_names = sorted(obj.name for obj in stable_export.all_objects if obj.get(ROLE_KEY) == ROLE_OUTPUT)
    modifier = body.modifiers.new(name="Unsafe Mirror", type='MIRROR')
    try:
        assert bpy.ops.velo.partition_sync_zones() == {'CANCELLED'}
    except RuntimeError as exc:
        assert "Unsafe Mirror" in str(exc)
    assert settings.partition_export_collection is stable_export
    assert stable_names == sorted(obj.name for obj in stable_export.all_objects if obj.get(ROLE_KEY) == ROLE_OUTPUT)
    body.modifiers.remove(modifier)

    garment.hide_set(True)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    outputs = [obj for obj in settings.partition_export_collection.all_objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
    assert len(outputs) == 3
    assert len(output_names_for_source(garment, cfg)) == 0
    assert len(output_names_for_source(passthrough, cfg)) == 1
    assert validate_export_state(scene) is None
    assert body.get(SOURCE_ID_KEY) and passthrough.get(SOURCE_ID_KEY)

    body.hide_set(True)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    outputs = [obj for obj in settings.partition_export_collection.all_objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
    assert len(outputs) == 1
    assert len(output_names_for_source(body, cfg)) == 0
    assert validate_export_state(scene) is None

    output = outputs[0]
    output.data.vertices[0].co.x += 0.01
    assert "分割区已被手工修改" in validate_export_state(scene)
    print("VELO_PARTITION_ZONE_SYNC_OK")
    velo_tools.unregister()


if __name__ == "__main__":
    main()
