"""Read-only real-project test for grouped whole-body separation and EFMI export."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import bmesh
import bpy


REPO_ROOT = Path(os.environ.get("VELO_TEST_ADDON_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO_ROOT))

import velo_tools  # noqa: E402
from velo_tools.partition.constants import (  # noqa: E402
    ROLE_KEY,
    ROLE_OUTPUT,
    WHOLE_SPLIT_COLLECTION_KEY,
    WHOLE_SPLIT_GROUP_KEY,
    WHOLE_SPLIT_GUIDE_KEY,
)
from velo_tools.partition.sync import validate_export_state  # noqa: E402


def main():
    output_dir = Path(os.environ["VELO_TEST_EXPORT_DIR"])
    velo_tools.register()
    scene = bpy.context.scene
    cfg = scene.VTEF_settings
    cfg.mod_skeleton_type = "MERGED"
    settings = scene.velo_tools
    assert bpy.ops.velo.partition_initialize_zones() == {'FINISHED'}
    body = settings.partition_master_object
    assert body is not None and len(body.data.polygons) == 52774
    original_faces = len(body.data.polygons)

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    body.hide_viewport = False
    body.hide_set(False)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    assert bpy.ops.velo.partition_split_whole_mesh() == {'FINISHED'}
    bm = bmesh.from_edit_mesh(body.data)
    centers = sorted(face.calc_center_median().z for face in bm.faces)
    threshold = centers[len(centers) // 2]
    selected = 0
    for face in bm.faces:
        face.select = face.calc_center_median().z >= threshold
        selected += int(face.select)
    assert 0 < selected < len(bm.faces)
    bmesh.update_edit_mesh(body.data, loop_triangles=False, destructive=False)
    assert bpy.ops.velo.partition_split_whole_mesh() == {'FINISHED'}

    guide = settings.partition_master_object
    assert guide.get(WHOLE_SPLIT_GUIDE_KEY)
    group_id = str(body.get(WHOLE_SPLIT_GROUP_KEY, ""))
    pieces = [
        item.object
        for item in settings.partition_whole_mesh_items
        if item.object is not None
        and str(item.object.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert len(pieces) == 2
    assert sum(len(piece.data.polygons) for piece in pieces) == original_faces
    assert bpy.ops.velo.partition_sync_zones() == {'FINISHED'}
    assert validate_export_state(scene) is None

    outputs = [
        obj
        for obj in settings.partition_export_collection.all_objects
        if obj.get(ROLE_KEY) == ROLE_OUTPUT
        and str(obj.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert outputs
    assert sum(len(obj.data.polygons) for obj in outputs) == original_faces
    output_groups = [
        collection
        for component in settings.partition_export_collection.children
        for collection in component.children
        if str(collection.get(WHOLE_SPLIT_GROUP_KEY, "")) == group_id
    ]
    assert output_groups
    assert all(collection.get(WHOLE_SPLIT_COLLECTION_KEY) for collection in output_groups)
    assert all(not collection.children for collection in output_groups)

    output_dir.mkdir(parents=True, exist_ok=True)
    cfg.mod_output_folder = str(output_dir)
    cfg.copy_textures = False
    cfg.write_ini = True
    assert not cfg.ignore_nested_collections
    assert bpy.ops.vtef.export_mod() == {'FINISHED'}
    assert (output_dir / "mod.ini").is_file()
    assert any((output_dir / "Meshes").rglob("*.buf"))
    print(
        "VELO_JUE_WHOLE_SPLIT_EXPORT_OK",
        f"pieces={len(pieces)}",
        f"outputs={len(outputs)}",
        f"groups={len(output_groups)}",
    )
    velo_tools.unregister()


if __name__ == "__main__":
    main()
