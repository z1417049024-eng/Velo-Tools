"""Blender background smoke test for the EFMI partition workflow."""

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
    PART_COLLECTION_KEY,
    PART_INDEX_KEY,
    ROLE_KEY,
    ROLE_OUTPUT,
    SOURCE_VERTEX_ATTRIBUTE,
)


def make_mesh_object(name, vertices, faces, collection):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        coordinate = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (coordinate.x, coordinate.y)
    return obj


def add_weights(obj):
    left = obj.vertex_groups.new(name="0")
    right = obj.vertex_groups.new(name="1")
    for vertex in obj.data.vertices:
        factor = max(0.0, min(1.0, (vertex.co.x + 1.0) / 2.0))
        left.add([vertex.index], 1.0 - factor, "REPLACE")
        right.add([vertex.index], factor, "REPLACE")


def select_only(objects, active):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def walk_collections(root):
    yield root
    for child in root.children:
        yield from walk_collections(child)


def find_component_collection(root, component_id):
    return next(
        collection
        for collection in walk_collections(root)
        if collection.get("velo_component_id") == component_id
    )


def find_part_collection(root, component_id, part_index):
    component = find_component_collection(root, component_id)
    return next(
        (
            collection
            for collection in component.children
            if collection.get(PART_INDEX_KEY) == part_index
        ),
        None,
    )


def main():
    velo_tools.register()
    scene = bpy.context.scene
    root = bpy.data.collections.new("Synthetic EFMI")
    scene.collection.children.link(root)
    components = {}
    for component_id in (1, 2):
        collection = bpy.data.collections.new(f"C{component_id}")
        collection["velo_component_id"] = component_id
        root.children.link(collection)
        components[component_id] = collection

    source_left = make_mesh_object(
        "Component 1 left",
        [(-1, -1, 0), (0, -1, 0), (0, 1, 0), (-1, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
        components[1],
    )
    source_right = make_mesh_object(
        "Component 2 right",
        [(0, -1, 0), (1, -1, 0), (1, 1, 0), (0, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
        components[2],
    )
    for component_id, source in ((1, source_left), (2, source_right)):
        source["velo_component_id"] = component_id
        add_weights(source)

    master_collection = bpy.data.collections.new("Master")
    scene.collection.children.link(master_collection)
    master = make_mesh_object(
        "Complete Master",
        [
            (-1, -1, 0), (0, -1, 0), (1, -1, 0),
            (-1, 1, 0), (0, 1, 0), (1, 1, 0),
        ],
        [(0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4)],
        master_collection,
    )
    add_weights(master)
    master.shape_key_add(name="Basis")
    shape = master.shape_key_add(name="Raised")
    for point in shape.data:
        point.co.z += 0.125

    legacy = make_mesh_object(
        "Component 2 old joined",
        [(-0.2, -0.2, 0), (0.2, -0.2, 0), (0, 0.2, 0)],
        [(0, 1, 2)],
        components[2],
    )

    source_folder = Path(tempfile.mkdtemp(prefix="velo-partition-test-"))
    payload = {
        "format_version": 1,
        "metadata_format_version": 3,
        "components": [
            {"mesh_name": "Component 0", "ib_hash": "", "vb0_hash": "", "vg_map": {}},
            {"mesh_name": "Component 1", "ib_hash": "", "vb0_hash": "", "vg_map": {"0": 0, "1": 1}},
            {"mesh_name": "Component 2", "ib_hash": "", "vb0_hash": "", "vg_map": {"0": 0, "1": 1}},
        ],
    }
    (source_folder / "VertexGroupMap.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    cfg = scene.VTEF_settings
    cfg.mod_skeleton_type = "MERGED"
    cfg.component_collection = root
    cfg.object_source_folder = str(source_folder)
    settings = scene.velo_tools
    settings.partition_legacy_object = legacy

    select_only([source_left, source_right], source_left)
    assert bpy.ops.velo.partition_create_reference() == {"FINISHED"}
    reference = settings.partition_reference_object
    assert reference is not None
    assert len(reference.data.polygons) == 4
    assert source_left.hide_viewport and source_right.hide_viewport
    assert legacy.hide_viewport

    settings.partition_master_object = master
    assert bpy.ops.velo.partition_project_split() == {"FINISHED"}
    outputs = sorted(
        [obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT],
        key=lambda obj: int(obj["velo_component_id"]),
    )
    assert [obj["velo_component_id"] for obj in outputs] == [1, 2]
    assert sum(len(obj.data.polygons) for obj in outputs) == len(master.data.polygons)
    assert all(obj.data.shape_keys is not None for obj in outputs)
    assert all([key.name for key in obj.data.shape_keys.key_blocks] == ["Basis", "Raised"] for obj in outputs)
    assert all(obj.data.uv_layers.get("UVMap") is not None for obj in outputs)
    assert all(obj.rotation_euler[:] == master.rotation_euler[:] for obj in outputs)
    assert all(obj.data.attributes.get(SOURCE_VERTEX_ATTRIBUTE) is not None for obj in outputs)
    assert len(master.data.polygons) == 4
    for output in outputs:
        component_id = int(output["velo_component_id"])
        part_one = find_part_collection(root, component_id, 1)
        assert part_one is not None
        assert part_one.name.startswith("part.1 (")
        assert part_one.get(PART_COLLECTION_KEY)
        assert part_one.color_tag == "COLOR_01"
        assert set(part_one.objects) == {output}

    original_outputs = set(outputs)
    for vertex in master.data.vertices:
        master.vertex_groups[0].add([vertex.index], 0.25, "REPLACE")
        master.vertex_groups[1].add([vertex.index], 0.75, "REPLACE")
    settings.partition_output_mode = "WEIGHTS"
    assert bpy.ops.velo.partition_project_split() == {"FINISHED"}
    weighted_outputs = {
        obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT
    }
    assert weighted_outputs == original_outputs
    for output in weighted_outputs:
        source_indices = [
            int(item.value)
            for item in output.data.attributes[SOURCE_VERTEX_ATTRIBUTE].data
        ]
        group_names = {group.index: group.name for group in output.vertex_groups}
        for vertex, source_index in zip(output.data.vertices, source_indices):
            actual = {group_names[item.group]: item.weight for item in vertex.groups}
            assert abs(actual["0"] - 0.25) < 1e-6
            assert abs(actual["1"] - 0.75) < 1e-6

    settings.partition_output_mode = "REPLACE"

    assert bpy.ops.velo.partition_project_split() == {"FINISHED"}
    replacement_outputs = sorted(
        [obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT],
        key=lambda obj: int(obj["velo_component_id"]),
    )
    assert len(replacement_outputs) == 2
    assert all(not obj.name.endswith(".001") for obj in replacement_outputs)

    merge = settings.partition_merge_items.add()
    merge.source_object = replacement_outputs[0]
    merge.target_object = replacement_outputs[1]
    assert bpy.ops.velo.partition_project_split() == {"FINISHED"}
    merged_outputs = [
        obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT
    ]
    assert len(merged_outputs) == 1
    assert merged_outputs[0]["velo_component_id"] == 2
    assert len(merged_outputs[0].data.polygons) == len(master.data.polygons)
    assert merge.source_object is source_left
    assert merge.target_object is merged_outputs[0]
    settings.partition_merge_items.clear()
    assert bpy.ops.velo.partition_project_split() == {"FINISHED"}

    settings.partition_output_mode = "APPEND"
    assert bpy.ops.velo.partition_project_split() == {"FINISHED"}
    appended_outputs = [
        obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT
    ]
    assert len(appended_outputs) == 4
    assert sum(len(obj.data.polygons) for obj in appended_outputs) == 2 * len(master.data.polygons)
    for component_id in (1, 2):
        part_two = find_part_collection(root, component_id, 2)
        assert part_two is not None
        assert part_two.name.startswith("part.2 (")
        assert part_two.color_tag == "COLOR_02"
        assert len(part_two.objects) == 1

    settings.partition_output_mode = "REPLACE"
    assert bpy.ops.velo.partition_project_split() == {"FINISHED"}
    replacement_outputs = [
        obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT
    ]
    assert len(replacement_outputs) == 2
    assert all(find_part_collection(root, component_id, 2) is None for component_id in (1, 2))

    garment = make_mesh_object(
        "Complete Garment",
        [
            (-1, -1, 0.05), (0, -1, 0.05), (1, -1, 0.05),
            (-1, 1, 0.05), (0, 1, 0.05), (1, 1, 0.05),
        ],
        [(0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4)],
        master_collection,
    )
    add_weights(garment)
    garment_two = make_mesh_object(
        "Complete Garment Two",
        [
            (-1, -1, 0.1), (0, -1, 0.1), (1, -1, 0.1),
            (-1, 1, 0.1), (0, 1, 0.1), (1, 1, 0.1),
        ],
        [(0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4)],
        master_collection,
    )
    add_weights(garment_two)
    body_partition_id = master["velo_partition_id"]
    saved_reference = settings.partition_reference_object
    saved_master = settings.partition_master_object
    saved_mode = settings.partition_output_mode
    settings.partition_new_part_items.add().object = garment
    settings.partition_new_part_items.add().object = garment_two
    assert bpy.ops.velo.partition_apply_new_part() == {"FINISHED"}
    assert garment["velo_partition_id"] != body_partition_id
    assert garment_two["velo_partition_id"] != body_partition_id
    assert garment[PART_INDEX_KEY] == 2
    assert garment_two[PART_INDEX_KEY] == 2
    assert len([obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]) == 6
    assert master.get(ROLE_KEY) == "master" and not master.hide_viewport
    assert settings.partition_reference_object is saved_reference
    assert settings.partition_master_object is saved_master
    assert settings.partition_output_mode == saved_mode
    assert bpy.ops.velo.partition_apply_new_part() == {"FINISHED"}
    assert len([obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]) == 6
    for component_id in (1, 2):
        part_two = find_part_collection(root, component_id, 2)
        assert part_two is not None
        assert len(part_two.objects) == 2

    settings.partition_part_target = "1"
    assert bpy.ops.velo.partition_apply_new_part() == {"FINISHED"}
    assert garment[PART_INDEX_KEY] == 1
    assert garment_two[PART_INDEX_KEY] == 1
    assert sum(
        len(find_part_collection(root, component_id, 1).objects)
        for component_id in (1, 2)
    ) == 6
    assert all(find_part_collection(root, component_id, 2) is None for component_id in (1, 2))
    settings.partition_part_target = "AUTO"

    settings.partition_reference_object = master
    settings.partition_master_object = garment
    assert bpy.ops.velo.partition_restore_sources() == {"FINISHED"}
    settings.partition_reference_object = master
    settings.partition_master_object = garment_two
    assert bpy.ops.velo.partition_restore_sources() == {"FINISHED"}
    assert len([obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]) == 2

    settings.partition_reference_object = reference
    settings.partition_master_object = master
    modifier = master.modifiers.new(name="Unsafe Mirror", type="MIRROR")
    try:
        result = bpy.ops.velo.partition_project_split()
        assert result == {"CANCELLED"}
    except RuntimeError as exc:
        assert "Unsafe Mirror" in str(exc)
    assert len([obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]) == 2
    master.modifiers.remove(modifier)

    assert bpy.ops.velo.partition_restore_sources() == {"FINISHED"}
    assert not source_left.hide_viewport and not source_right.hide_viewport
    assert not legacy.hide_viewport
    assert not [obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
    print("VELO_PARTITION_SMOKE_OK")
    velo_tools.unregister()


if __name__ == "__main__":
    main()
