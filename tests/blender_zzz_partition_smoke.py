"""Blender background smoke test for ZZZ DBMT Merged partition routing."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


REPO_ROOT = Path(os.environ.get("VELO_TEST_ADDON_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO_ROOT))

import velo_tools  # noqa: E402
from velo_tools.games.zenless_zone_zero._zzmi_core.config.main_config import (  # noqa: E402
    GlobalConfig,
)
from velo_tools.games.zenless_zone_zero._zzmi_core.generate_mod.component_model import (  # noqa: E402
    ComponentModel,
)
from velo_tools.partition.constants import (  # noqa: E402
    ROLE_KEY,
    ROLE_OUTPUT,
    ROUTES_KEY,
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


def add_group_zero(obj):
    group = obj.vertex_groups.new(name="0")
    group.add([vertex.index for vertex in obj.data.vertices], 1.0, "REPLACE")


def select_only(objects, active):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def main():
    temp_root = Path(tempfile.mkdtemp(prefix="velo-zzz-partition-"))
    workspace_name = "SyntheticZZZ"
    workspace = temp_root / "WorkSpace" / "ZZZ" / workspace_name
    workspace.mkdir(parents=True)
    (temp_root / "Configs").mkdir()
    (temp_root / "Configs" / "DBMT-Config.json").write_text(
        json.dumps(
            {
                "CurrentWorkSpace": workspace_name,
                "CurrentGameName": "ZZZ",
                "DBMTWorkFolder": str(temp_root),
                "CurrentGameMigotoFolder": str(temp_root / "3Dmigoto" / "ZZZ"),
            }
        ),
        encoding="utf-8",
    )
    (workspace / "VertexGroupMap.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "game": "ZZZ",
                "components": [
                    {
                        "draw_ib": "aabbccdd",
                        "alias": "Body",
                        "vg_map": {"0": 0, "1": 1},
                    },
                    {
                        "draw_ib": "eeff0011",
                        "alias": "Legs",
                        "vg_map": {"0": 0, "1": 2},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        velo_tools.register()
        scene = bpy.context.scene
        scene.velo_tools.active_game = "ZENLESS"
        scene.VTZZ_dbmt_path.use_specified_dbmt = True
        scene.VTZZ_dbmt_path.path = str(temp_root)
        GlobalConfig.gamename = "ZZZ"
        GlobalConfig.workspacename = workspace_name
        GlobalConfig.dbmtlocation = str(temp_root) + "\\"
        GlobalConfig.current_game_migoto_folder = str(temp_root / "3Dmigoto" / "ZZZ") + "\\"

        root = bpy.data.collections.new(workspace_name)
        scene.collection.children.link(root)
        draw_body = bpy.data.collections.new("aabbccdd_Body")
        draw_legs = bpy.data.collections.new("eeff0011_Legs")
        body_target = bpy.data.collections.new("Component 1 Body")
        legs_target = bpy.data.collections.new("Component 2 Legs")
        root.children.link(draw_body)
        root.children.link(draw_legs)
        draw_body.children.link(body_target)
        draw_legs.children.link(legs_target)

        source_body = make_mesh_object(
            "Body source",
            [(-1, -1, 0), (0, -1, 0), (0, 1, 0), (-1, 1, 0)],
            [(0, 1, 2), (0, 2, 3)],
            body_target,
        )
        source_legs = make_mesh_object(
            "Legs source",
            [(0, -1, 0), (1, -1, 0), (1, 1, 0), (0, 1, 0)],
            [(0, 1, 2), (0, 2, 3)],
            legs_target,
        )
        for obj, draw_ib in ((source_body, "aabbccdd"), (source_legs, "eeff0011")):
            add_group_zero(obj)
            obj["velo_zzz_draw_ib"] = draw_ib
            obj["velo_zzz_skeleton_mode"] = "MERGED"

        master_collection = bpy.data.collections.new("Master authoring")
        scene.collection.children.link(master_collection)
        master = make_mesh_object(
            "Complete ZZZ Master",
            [
                (-1, -1, 0),
                (0, -1, 0),
                (1, -1, 0),
                (-1, 1, 0),
                (0, 1, 0),
                (1, 1, 0),
                (3, 3, 3),
            ],
            [(0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4)],
            master_collection,
        )
        add_group_zero(master)
        master.shape_key_add(name="Basis")
        raised = master.shape_key_add(name="Raised")
        for point in raised.data:
            point.co.z += 0.1

        cfg = scene.VTZZ_properties_generate_mod
        cfg.component_collection = root
        cfg.skeleton_mode = "MERGED"
        settings = scene.velo_tools

        select_only([source_body, source_legs], source_body)
        assert bpy.ops.velo.partition_create_reference() == {"FINISHED"}
        reference = settings.partition_reference_object
        routes = json.loads(reference[ROUTES_KEY])
        assert [(route["draw_ib"], route["target_collection"]) for route in routes] == [
            ("aabbccdd", body_target.name),
            ("eeff0011", legs_target.name),
        ]

        settings.partition_master_object = master
        assert bpy.ops.velo.partition_project_split() == {"FINISHED"}
        outputs = [obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
        assert len(outputs) == 2
        assert sum(len(obj.data.polygons) for obj in outputs) == len(master.data.polygons)
        assert all(obj.get("velo_zzz_skeleton_mode") == "MERGED" for obj in outputs)
        assert all({group.name for group in obj.vertex_groups} == {"0"} for obj in outputs)
        output_by_draw = {obj["velo_zzz_draw_ib"]: obj for obj in outputs}
        assert set(output_by_draw["aabbccdd"].users_collection) == {body_target}
        assert set(output_by_draw["eeff0011"].users_collection) == {legs_target}
        assert all(obj.data.shape_keys is not None for obj in outputs)

        assert bpy.ops.velo.partition_project_split() == {"FINISHED"}
        replacements = [obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
        assert len(replacements) == 2
        replacement_by_draw = {obj["velo_zzz_draw_ib"]: obj for obj in replacements}

        source_body.hide_viewport = False
        source_body.hide_set(False)
        parsed = ComponentModel(body_target, None, "aabbccdd", read_ib_category_data=False)
        assert [item.obj_name for item in parsed.ordered_draw_obj_model_list] == [
            replacement_by_draw["aabbccdd"].name
        ]

        assert bpy.ops.velo.partition_restore_sources() == {"FINISHED"}
        assert not source_body.hide_viewport and not source_legs.hide_viewport
        assert not [obj for obj in bpy.data.objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
        print("VELO_ZZZ_PARTITION_SMOKE_OK")
    finally:
        try:
            velo_tools.unregister()
        except Exception:
            pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
