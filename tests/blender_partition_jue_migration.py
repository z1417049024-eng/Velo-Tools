"""Read-only migration smoke test for the real Jue project copy."""

from __future__ import annotations

import hashlib
import os
import sys
from collections import Counter
from pathlib import Path

import bpy


REPO_ROOT = Path(os.environ.get("VELO_TEST_ADDON_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO_ROOT))

import velo_tools  # noqa: E402
from velo_tools.partition.constants import (  # noqa: E402
    COMPONENT_ATTRIBUTE,
    PART_COLLECTION_KEY,
    ROLE_IMPORTED,
    ROLE_KEY,
    ROLE_OUTPUT,
    ROLE_SOURCE,
    SOURCE_ID_KEY,
)
from velo_tools.partition.sync import validate_export_state  # noqa: E402


def main():
    velo_tools.register()
    scene = bpy.context.scene
    settings = scene.velo_tools
    cfg = scene.VTEF_settings
    raw_root = bpy.data.collections.get("Character 71393")
    assert raw_root is not None
    if cfg.component_collection is None or cfg.component_collection.get("velo_partition_export_zone"):
        cfg.component_collection = raw_root
    cfg.mod_skeleton_type = "MERGED"

    body = settings.partition_master_object or bpy.data.objects.get("Component 3 9911bdec 完整身体")
    assert body is not None
    settings.partition_master_object = body
    assert len(body.data.vertices) == 52829
    assert len(body.data.polygons) == 52774
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.get(ROLE_KEY) == "master":
            obj.hide_set(False)
            obj.hide_viewport = False

    legacy_outputs = {
        obj.name
        for obj in bpy.data.objects
        if obj.get(ROLE_KEY) == ROLE_OUTPUT and obj.get("velo_partition_generated")
    }
    assert legacy_outputs
    assert bpy.ops.velo.partition_initialize_zones() == {'FINISHED'}

    assert settings.partition_authoring_collection == raw_root, "authoring root"
    assert cfg.component_collection == settings.partition_export_collection, "export root"
    assert settings.partition_export_collection.name == "Character 71393 [分割区]", "export name"
    assert len(settings.partition_whole_mesh_items) == 4, "whole count"
    assert settings.partition_master_object == body, "standard body"
    assert all(item.home_component == 3 for item in settings.partition_whole_mesh_items), "home components"
    assert all(item.object in set(raw_root.all_objects) for item in settings.partition_whole_mesh_items), "whole links"

    reference = settings.partition_reference_object
    values = [int(item.value) for item in reference.data.attributes[COMPONENT_ATTRIBUTE].data]
    assert Counter(values) == Counter({1: 408, 2: 1334, 8: 236, 9: 9551}), Counter(values)
    source_objects = [obj for obj in raw_root.all_objects if obj.get(ROLE_KEY) == ROLE_SOURCE]
    assert len(source_objects) == 4, [obj.name for obj in source_objects]
    assert [obj for obj in raw_root.all_objects if obj.get(ROLE_KEY) == ROLE_IMPORTED], "imported objects"

    export = settings.partition_export_collection
    assert len(export.children) == 16, "component collections"
    assert not [collection for collection in export.children_recursive if collection.get(PART_COLLECTION_KEY)], "part.N"
    assert not [collection for collection in bpy.data.collections if collection.get(PART_COLLECTION_KEY)], "legacy part.N"
    assert not [name for name in legacy_outputs if bpy.data.objects.get(name) is not None], "legacy outputs"

    outputs = [obj for obj in export.all_objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
    assert outputs
    for item in settings.partition_whole_mesh_items:
        source = item.object
        source_id = str(source.get(SOURCE_ID_KEY, "") or "")
        derived = [obj for obj in outputs if str(obj.get(SOURCE_ID_KEY, "") or "") == source_id]
        assert derived, f"missing outputs: {source.name}"
        assert sum(len(obj.data.polygons) for obj in derived) == len(source.data.polygons), f"face total: {source.name}"
        assert all(obj.rotation_euler[:] == source.rotation_euler[:] for obj in derived), f"rotation: {source.name}"

    validation = validate_export_state(scene)
    assert validation is None, validation

    def export_mod(export_dir):
        cfg.mod_output_folder = str(export_dir)
        cfg.copy_textures = False
        cfg.write_ini = True
        assert bpy.ops.vtef.export_mod() == {'FINISHED'}
        assert (export_dir / "mod.ini").is_file()
        assert (export_dir / "Meshes").is_dir()
        assert any(path.is_file() for path in (export_dir / "Meshes").rglob("*"))
        return {
            path.relative_to(export_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in export_dir.rglob("*")
            if path.is_file()
        }

    compare_path = os.environ.get("VELO_TEST_EXPORT_COMPARE_DIR", "")
    if compare_path:
        compare_dir = Path(compare_path)
        settings.partition_preview_mode = "AUTHORING"
        authoring_files = export_mod(compare_dir / "authoring")
        settings.partition_preview_mode = "EXPORT"
        export_files = export_mod(compare_dir / "export")
        assert authoring_files == export_files, {
            "missing_in_authoring": sorted(set(export_files) - set(authoring_files)),
            "missing_in_export": sorted(set(authoring_files) - set(export_files)),
            "content_mismatch": sorted(
                name
                for name in set(authoring_files).intersection(export_files)
                if authoring_files[name] != export_files[name]
            ),
        }
    else:
        export_path = os.environ.get("VELO_TEST_EXPORT_DIR", "")
        if export_path:
            export_mod(Path(export_path))
    print(
        "VELO_JUE_MIGRATION_OK",
        f"whole={len(settings.partition_whole_mesh_items)}",
        f"outputs={len(outputs)}",
    )
    velo_tools.unregister()


if __name__ == "__main__":
    main()
