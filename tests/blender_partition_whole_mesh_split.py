"""Smoke test for authoring-time whole-mesh separation and grouped sync."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import bmesh
import bpy


REPO_ROOT = Path(os.environ.get("VELO_TEST_ADDON_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO_ROOT))

import velo_tools  # noqa: E402
from velo_tools.partition.constants import (  # noqa: E402
    ROLE_DIAGNOSTIC,
    ROLE_KEY,
    ROLE_MASTER,
    ROLE_OUTPUT,
    SOURCE_ID_KEY,
    WHOLE_SPLIT_COLLECTION_KEY,
    WHOLE_SPLIT_GROUP_KEY,
    WHOLE_SPLIT_GUIDE_KEY,
)
from velo_tools.partition.sync import output_names_for_source, validate_export_state  # noqa: E402


def mesh_object(name, vertices, faces, collection):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def weighted_grid(name, collection):
    obj = mesh_object(
        name,
        [
            (-1, -1, 0), (0, -1, 0), (1, -1, 0),
            (-1, 1, 0), (0, 1, 0), (1, 1, 0),
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
    obj.shape_key_add(name="Basis")
    raised = obj.shape_key_add(name="Raised")
    for point in raised.data:
        point.co.z += 0.1
    return obj


def select_only(obj):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.hide_viewport = False
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def select_face_indices(indices):
    obj = bpy.context.edit_object
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select = face.index in indices
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def main():
    velo_tools.register()
    scene = bpy.context.scene
    raw_root = bpy.data.collections.new("Split Character")
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
    legacy = mesh_object(
        "Component 2 old joined",
        [
            (-1, -1, 0), (0, -1, 0), (1, -1, 0),
            (-1, 1, 0), (0, 1, 0), (1, 1, 0),
        ],
        [(0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4)],
        components[2],
    )
    left_material = bpy.data.materials.new("Component 1 left")
    right_material = bpy.data.materials.new("Component 2 right")
    legacy.data.materials.append(left_material)
    legacy.data.materials.append(right_material)
    for polygon in legacy.data.polygons:
        polygon.material_index = 0 if polygon.center.x < 0 else 1

    source_folder = Path(tempfile.mkdtemp(prefix="velo-whole-split-"))
    (source_folder / "VertexGroupMap.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "metadata_format_version": 3,
                "components": [
                    {"mesh_name": "Component 0", "vg_map": {"0": 0, "1": 1}},
                    {"mesh_name": "Component 1", "vg_map": {"0": 0, "1": 1}},
                    {"mesh_name": "Component 2", "vg_map": {"0": 0, "1": 1}},
                ],
            }
        ),
        encoding="utf-8",
    )

    cfg = scene.VTEF_settings
    cfg.component_collection = raw_root
    cfg.object_source_folder = str(source_folder)
    cfg.mod_skeleton_type = "MERGED"
    settings = scene.velo_tools
    settings.partition_master_object = body
    settings.partition_legacy_object = legacy
    settings.partition_authoring_collection = raw_root
    assert bpy.ops.velo.partition_initialize_zones() == {'FINISHED'}

    select_only(body)
    assert bpy.ops.velo.partition_split_whole_mesh() == {'FINISHED'}
    assert settings.partition_mesh_split_active
    assert bpy.context.mode == 'EDIT_MESH'
    select_face_indices({0, 1})
    assert bpy.ops.velo.partition_split_whole_mesh() == {'FINISHED'}
    assert bpy.context.mode == 'OBJECT'
    assert not settings.partition_mesh_split_active

    guide = settings.partition_master_object
    assert guide is not body
    assert guide.get(WHOLE_SPLIT_GUIDE_KEY)
    assert guide.get(ROLE_KEY) == ROLE_DIAGNOSTIC
    group_id = str(body.get(WHOLE_SPLIT_GROUP_KEY, ""))
    assert str(guide.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    pieces = [
        item.object
        for item in settings.partition_whole_mesh_items
        if item.object is not None
        and str(item.object.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert len(pieces) == 2
    assert len({piece.get(SOURCE_ID_KEY) for piece in pieces}) == 2
    assert all(piece.data.shape_keys is not None for piece in pieces)
    assert all([key.name for key in piece.data.shape_keys.key_blocks] == ["Basis", "Raised"] for piece in pieces)
    authoring_groups = [
        collection
        for collection in components[2].children
        if str(collection.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert len(authoring_groups) == 1
    assert not authoring_groups[0].children
    assert set(authoring_groups[0].objects) == set(pieces)

    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    outputs = [
        obj
        for obj in settings.partition_export_collection.all_objects
        if obj.get(ROLE_KEY) == ROLE_OUTPUT
        and str(obj.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert sum(len(obj.data.polygons) for obj in outputs) == 4
    assert all(output_names_for_source(piece, cfg) for piece in pieces)
    output_groups = [
        collection
        for component in settings.partition_export_collection.children
        for collection in component.children
        if str(collection.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert len(output_groups) == 2
    assert all(collection.get(WHOLE_SPLIT_COLLECTION_KEY) for collection in output_groups)
    assert all(not collection.children for collection in output_groups)
    assert validate_export_state(scene) is None

    non_original_output = next(
        obj for obj in outputs if obj.get(SOURCE_ID_KEY) != guide.get(SOURCE_ID_KEY)
    )
    other_output = next(
        obj
        for obj in outputs
        if obj.get("velo_component_id") != non_original_output.get("velo_component_id")
    )
    merge = settings.partition_merge_items.add()
    merge.source_object = non_original_output
    merge.target_object = other_output
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    settings.partition_merge_items.remove(0)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}

    second_source = next(piece for piece in pieces if len(piece.data.polygons) == 2)
    select_only(second_source)
    assert bpy.ops.velo.partition_split_whole_mesh() == {'FINISHED'}
    select_face_indices({0})
    assert bpy.ops.velo.partition_split_whole_mesh() == {'FINISHED'}
    pieces = [
        item.object
        for item in settings.partition_whole_mesh_items
        if item.object is not None
        and str(item.object.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert len(pieces) == 3
    authoring_groups = [
        collection
        for collection in components[2].children
        if str(collection.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert len(authoring_groups) == 1
    assert not authoring_groups[0].children
    assert set(authoring_groups[0].objects) == set(pieces)
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    outputs = [
        obj
        for obj in settings.partition_export_collection.all_objects
        if obj.get(ROLE_KEY) == ROLE_OUTPUT
        and str(obj.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert sum(len(obj.data.polygons) for obj in outputs) == 4
    assert len({obj.get(SOURCE_ID_KEY) for obj in outputs}) == 3
    assert validate_export_state(scene) is None
    assert source_left.get(ROLE_KEY) != ROLE_OUTPUT
    assert source_right.get(ROLE_KEY) != ROLE_OUTPUT
    print("VELO_WHOLE_MESH_SPLIT_OK", f"pieces={len(pieces)}", f"outputs={len(outputs)}")
    velo_tools.unregister()


if __name__ == "__main__":
    main()
