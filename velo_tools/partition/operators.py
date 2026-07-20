"""Blender operators for non-destructive Merged Component partitioning."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import bpy

from ..games.arknights_endfield import vgmap
from ..mesh.split_normals import (
    capture_split_corner_normals,
    restore_split_corner_normals,
)
from .algorithms import (
    PartitionError,
    component_counts,
    prepare_component_weights,
    project_component_faces,
)
from .constants import (
    AMBIGUOUS_ATTRIBUTE,
    COMPONENT_ATTRIBUTE,
    CONFIDENCE_ATTRIBUTE,
    GENERATED_KEY,
    PARTITION_ID_KEY,
    PREVIOUS_HIDE_GET_KEY,
    PREVIOUS_HIDE_RENDER_KEY,
    PREVIOUS_HIDE_VIEWPORT_KEY,
    ROLE_DIAGNOSTIC,
    ROLE_KEY,
    ROLE_MASTER,
    ROLE_OUTPUT,
    ROLE_REFERENCE,
    ROLE_SOURCE,
    ROUTES_KEY,
    SOURCE_COMPONENTS_KEY,
    SOURCE_FACE_ATTRIBUTE,
    SOURCE_LOOP_ATTRIBUTE,
    SOURCE_NAMES_KEY,
    SOURCE_VERTEX_ATTRIBUTE,
    WORK_COLLECTION_NAME,
)


_COMPONENT_NAME_RE = re.compile(r"component[_ -]*(\d+)", re.IGNORECASE)
_COMPONENT_COLLECTION_RE = re.compile(r"^c(\d+)$", re.IGNORECASE)
_DRAW_IB_RE = re.compile(r"^([0-9a-f]{8})(?:_|$)", re.IGNORECASE)
_TOPOLOGY_MODIFIERS = {
    "ARRAY",
    "BEVEL",
    "BOOLEAN",
    "BUILD",
    "DECIMATE",
    "EDGE_SPLIT",
    "EXPLODE",
    "MASK",
    "MIRROR",
    "MULTIRES",
    "NODES",
    "PARTICLE_INSTANCE",
    "REMESH",
    "SCREW",
    "SKIN",
    "SOLIDIFY",
    "SUBSURF",
    "TRIANGULATE",
    "WELD",
    "WIREFRAME",
}


@dataclass
class PartitionConfiguration:
    game: str
    settings: object
    root: bpy.types.Collection
    source: Path
    palettes: dict


def _set_status(settings, text: str) -> None:
    settings.partition_status = str(text)


def _ensure_object_mode(context) -> None:
    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _clear_selection(context) -> None:
    _ensure_object_mode(context)
    for obj in list(context.selected_objects):
        obj.select_set(False)
    context.view_layer.objects.active = None


def _select_objects(context, objects, *, active=None) -> None:
    _clear_selection(context)
    for obj in objects:
        if obj is not None and obj.name in bpy.data.objects:
            obj.hide_viewport = False
            obj.hide_set(False)
            obj.select_set(True)
    if active is not None:
        context.view_layer.objects.active = active


def _select_vertices(context, obj, vertex_ids) -> None:
    _select_objects(context, [obj], active=obj)
    selected = set(int(item) for item in vertex_ids)
    for vertex in obj.data.vertices:
        vertex.select = vertex.index in selected
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="VERT")


def _set_attribute(mesh, name: str, data_type: str, domain: str, values) -> None:
    attribute = mesh.attributes.get(name)
    if attribute is not None and (
        attribute.data_type != data_type or attribute.domain != domain
    ):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new(name, data_type, domain)
    values = list(values)
    if len(attribute.data) != len(values):
        raise PartitionError(f"属性 `{name}` 的数据长度不匹配。")
    property_name = "value"
    attribute.data.foreach_set(property_name, values)


def _remove_attribute(mesh, name: str) -> None:
    attribute = mesh.attributes.get(name)
    if attribute is not None:
        mesh.attributes.remove(attribute)


def _component_id_from_collection(collection):
    raw = collection.get("velo_component_id") if hasattr(collection, "get") else None
    try:
        if raw is not None:
            return int(raw)
    except (TypeError, ValueError):
        pass
    match = _COMPONENT_COLLECTION_RE.match(collection.name or "")
    return int(match.group(1)) if match else None


def _component_id_from_object(obj):
    raw = obj.get("velo_component_id") if hasattr(obj, "get") else None
    try:
        if raw is not None:
            return int(raw)
    except (TypeError, ValueError):
        pass
    for collection in obj.users_collection:
        component_id = _component_id_from_collection(collection)
        if component_id is not None:
            return component_id
    match = _COMPONENT_NAME_RE.search(obj.name or "")
    return int(match.group(1)) if match else None


def _load_configuration(scene):
    tools = getattr(scene, "velo_tools", None)
    game = getattr(tools, "active_game", "ENDFIELD")
    if game == "ENDFIELD":
        cfg = getattr(scene, "VTEF_settings", None)
        if cfg is None:
            raise PartitionError("未启用 Endfield EFMI 工作流。")
        if getattr(cfg, "mod_skeleton_type", None) != "MERGED":
            raise PartitionError("分割操作只支持 EFMI Merged。")
        root = getattr(cfg, "component_collection", None)
        if root is None:
            raise PartitionError("请先在 EFMI 导出设置中选择组件集合。")
        source = Path(bpy.path.abspath(str(getattr(cfg, "object_source_folder", "") or "")))
        if not source.is_dir():
            raise PartitionError("EFMI 对象源目录不存在。")
        vertex_group_map = vgmap.read_map(source)
        palettes = {
            component_id: {int(value) for value in component.vg_map.values()}
            for component_id, component in enumerate(vertex_group_map.components)
        }
        return PartitionConfiguration(game, cfg, root, source, palettes)

    if game == "ZENLESS":
        from ..games.zenless_zone_zero._zzmi_core.config.main_config import GlobalConfig
        from ..games.zenless_zone_zero._zzmi_core.merged_vgmap import (
            VertexGroupMapError,
            load_map,
        )

        cfg = getattr(scene, "VTZZ_properties_generate_mod", None)
        if cfg is None:
            raise PartitionError("未启用绝区零 ZZMI / DBMT 工作流。")
        if getattr(cfg, "skeleton_mode", None) != "MERGED":
            raise PartitionError("分割操作只支持 ZZZ Merged。")
        root = getattr(cfg, "component_collection", None)
        if root is None:
            raise PartitionError("请先在 ZZZ 导出设置中选择部件集合。")
        GlobalConfig.read_from_main_json()
        if GlobalConfig.gamename != "ZZZ" or not GlobalConfig.workspacename:
            raise PartitionError("DBMT 当前工作空间不是有效的 ZZZ workspace。")
        source = Path(GlobalConfig.path_workspace_folder())
        if not source.is_dir():
            raise PartitionError(f"ZZZ workspace 不存在：{source}")
        try:
            vertex_group_map = load_map(source)
        except VertexGroupMapError as exc:
            raise PartitionError(str(exc)) from exc
        palettes = {
            str(entry.get("draw_ib", "")).lower(): {
                int(value) for value in (entry.get("vg_map") or {}).values()
            }
            for entry in vertex_group_map.get("components", [])
        }
        return PartitionConfiguration(game, cfg, root, source, palettes)

    raise PartitionError("分割操作目前只支持 Endfield EFMI 和 Zenless ZZZ Merged。")


def _workspace_collection(scene):
    collection = bpy.data.collections.get(WORK_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(WORK_COLLECTION_NAME)
    if collection.name not in {item.name for item in scene.collection.children}:
        scene.collection.children.link(collection)
    return collection


def _walk_collections(root):
    yield root
    for child in root.children:
        yield from _walk_collections(child)


def _collection_paths(root, parents=()):
    path = parents + (root,)
    yield path
    for child in root.children:
        yield from _collection_paths(child, path)


def _component_collection(root, component_id: int):
    for collection in _walk_collections(root):
        if _component_id_from_collection(collection) == component_id:
            return collection
    collection = bpy.data.collections.new(f"C{component_id}")
    collection["velo_component_id"] = int(component_id)
    root.children.link(collection)
    return collection


def _draw_ib_from_collection(collection):
    name = re.sub(r"\.\d{3}$", "", collection.name or "")
    match = _DRAW_IB_RE.match(name)
    return match.group(1).lower() if match else None


def _zzz_route_for_object(obj, root):
    draw_ib = str(obj.get("velo_zzz_draw_ib", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{8}", draw_ib):
        raise PartitionError(f"对象 `{obj.name}` 缺少有效的 velo_zzz_draw_ib。")
    if obj.get("velo_zzz_skeleton_mode") != "MERGED":
        raise PartitionError(f"对象 `{obj.name}` 不是 ZZZ Merged 导入对象。")

    candidates = []
    user_collections = set(obj.users_collection)
    for path in _collection_paths(root):
        targets = [collection for collection in path if collection in user_collections]
        draw_collections = [
            collection for collection in path if _draw_ib_from_collection(collection) == draw_ib
        ]
        if targets and draw_collections:
            candidates.append((draw_collections[-1], targets[-1]))
    unique = {(draw.name, target.name): (draw, target) for draw, target in candidates}
    if len(unique) != 1:
        raise PartitionError(
            f"对象 `{obj.name}` 无法在导出集合 `{root.name}` 中唯一确定 DrawIB/目标集合。"
        )
    draw_collection, target_collection = next(iter(unique.values()))
    return {
        "draw_ib": draw_ib,
        "draw_ib_collection": draw_collection.name,
        "target_collection": target_collection.name,
        "display_name": target_collection.name,
    }


def _routes_json(routes) -> str:
    return json.dumps(
        [routes[label] for label in sorted(routes)],
        ensure_ascii=False,
        sort_keys=True,
    )


def _load_routes(reference, config):
    raw = reference.get(ROUTES_KEY)
    if raw:
        try:
            records = json.loads(raw)
            routes = {int(record["label"]): record for record in records}
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise PartitionError("分区参考体的路由数据已损坏。") from exc
    else:
        labels = sorted(set(_component_values(reference.data)))
        routes = {
            label: {
                "label": label,
                "game": "ENDFIELD",
                "component_id": label,
                "display_name": f"C{label}",
            }
            for label in labels
        }

    if len(routes) < 2:
        raise PartitionError("分区参考体至少需要两个有效路由。")
    route_games = {str(route.get("game", "")) for route in routes.values()}
    if route_games != {config.game}:
        raise PartitionError("分区参考体与当前选择的游戏工作流不匹配。")
    return routes


def _palettes_for_routes(config, routes):
    if config.game == "ENDFIELD":
        palettes = {
            label: config.palettes.get(int(route.get("component_id", label)), set())
            for label, route in routes.items()
        }
    else:
        palettes = {
            label: config.palettes.get(str(route.get("draw_ib", "")).lower(), set())
            for label, route in routes.items()
        }
    missing = [
        routes[label].get("display_name", str(label))
        for label, palette in palettes.items()
        if not palette
    ]
    if missing:
        raise PartitionError("以下分区没有可用的 Merged palette：" + ", ".join(missing))
    return palettes


def _remember_and_hide(obj) -> None:
    if PREVIOUS_HIDE_VIEWPORT_KEY not in obj:
        obj[PREVIOUS_HIDE_VIEWPORT_KEY] = bool(obj.hide_viewport)
        obj[PREVIOUS_HIDE_RENDER_KEY] = bool(obj.hide_render)
        obj[PREVIOUS_HIDE_GET_KEY] = bool(obj.hide_get())
    obj.hide_set(True)
    obj.hide_viewport = True


def _restore_visibility(obj) -> None:
    hide_viewport = bool(obj.get(PREVIOUS_HIDE_VIEWPORT_KEY, False))
    hide_render = bool(obj.get(PREVIOUS_HIDE_RENDER_KEY, False))
    hide_get = bool(obj.get(PREVIOUS_HIDE_GET_KEY, False))
    obj.hide_viewport = hide_viewport
    obj.hide_render = hide_render
    try:
        obj.hide_set(hide_get)
    except RuntimeError:
        pass
    for key in (
        PREVIOUS_HIDE_VIEWPORT_KEY,
        PREVIOUS_HIDE_RENDER_KEY,
        PREVIOUS_HIDE_GET_KEY,
    ):
        if key in obj:
            del obj[key]


def _remove_object(obj) -> None:
    if obj is None or obj.name not in bpy.data.objects:
        return
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _safe_reference_sources(selected, legacy, config):
    candidates = []
    for obj in selected:
        if obj == legacy or obj.type != "MESH":
            continue
        if obj.get(ROLE_KEY) in {ROLE_REFERENCE, ROLE_MASTER, ROLE_OUTPUT, ROLE_DIAGNOSTIC}:
            continue
        if config.game == "ENDFIELD":
            component_id = _component_id_from_object(obj)
            if component_id is None:
                continue
            route = {
                "label": component_id,
                "game": "ENDFIELD",
                "component_id": component_id,
                "display_name": f"C{component_id}",
            }
            candidates.append((obj, component_id, route))
        else:
            route = _zzz_route_for_object(obj, config.root)
            candidates.append((obj, None, route))

    if config.game == "ZENLESS":
        route_keys = sorted(
            {
                (route["draw_ib"], route["target_collection"])
                for _obj, _label, route in candidates
            }
        )
        labels = {route_key: index + 1 for index, route_key in enumerate(route_keys)}
        sources = []
        routes = {}
        for obj, _label, route in candidates:
            label = labels[(route["draw_ib"], route["target_collection"])]
            route = dict(route, label=label, game="ZENLESS")
            sources.append((obj, label))
            routes[label] = route
    else:
        sources = [(obj, label) for obj, label, _route in candidates]
        routes = {route["label"]: route for _obj, _label, route in candidates}

    if len(routes) < 2:
        game_name = "EFMI Component" if config.game == "ENDFIELD" else "ZZZ DrawIB/目标集合"
        raise PartitionError(f"请至少选择两个不同的原始 {game_name} 网格。")
    if any(obj.data.shape_keys for obj, _component_id in sources):
        raise PartitionError("原始 Component 带有 ShapeKey；请使用未修改的导入对象创建参考体。")
    modified = [obj.name for obj, _component_id in sources if obj.modifiers]
    if modified:
        raise PartitionError(f"原始 Component 含 modifier：{', '.join(modified)}。")
    return sources, routes


def _make_reference(context, sources, routes, partition_id: str):
    workspace = _workspace_collection(context.scene)
    duplicates = []
    duplicate_names = []
    try:
        for source, component_id in sources:
            duplicate = source.copy()
            duplicate.data = source.data.copy()
            workspace.objects.link(duplicate)
            duplicate[ROLE_KEY] = ROLE_DIAGNOSTIC
            _set_attribute(
                duplicate.data,
                COMPONENT_ATTRIBUTE,
                "INT",
                "FACE",
                [component_id] * len(duplicate.data.polygons),
            )
            duplicates.append(duplicate)
            duplicate_names.append(duplicate.name)

        _select_objects(context, duplicates, active=duplicates[0])
        bpy.ops.object.join()
        reference = context.view_layer.objects.active
        if reference is None:
            raise PartitionError("Blender 合并分区参考体失败。")
        expected_faces = sum(len(obj.data.polygons) for obj, _component_id in sources)
        if len(reference.data.polygons) != expected_faces:
            raise PartitionError("分区参考体合并后面数不一致。")
        attribute = reference.data.attributes.get(COMPONENT_ATTRIBUTE)
        if attribute is None or len(attribute.data) != expected_faces:
            raise PartitionError("分区参考体合并时丢失了 Component 属性。")
        components = sorted({component_id for _obj, component_id in sources})
        counts = component_counts(item.value for item in attribute.data)
        if set(counts) != set(components):
            raise PartitionError("分区参考体的 Component 覆盖不完整。")
        suffix = "_".join(f"C{item}" for item in components)
        reference.name = f"VELO Partition Reference {suffix}"
        reference.data.name = reference.name
        reference[ROLE_KEY] = ROLE_REFERENCE
        reference[PARTITION_ID_KEY] = partition_id
        reference[GENERATED_KEY] = True
        reference[SOURCE_NAMES_KEY] = json.dumps(
            [obj.name for obj, _component_id in sources], ensure_ascii=False
        )
        reference[SOURCE_COMPONENTS_KEY] = json.dumps(components)
        reference[ROUTES_KEY] = _routes_json(routes)
        return reference, counts
    except Exception:
        for name in duplicate_names:
            duplicate = bpy.data.objects.get(name)
            if duplicate is not None:
                _remove_object(duplicate)
        raise


def _validate_master_modifiers(master) -> None:
    blocked = [modifier.name for modifier in master.modifiers if modifier.type in _TOPOLOGY_MODIFIERS]
    if blocked:
        raise PartitionError(
            "Master 含改变拓扑的 modifier，请先应用或移除：" + ", ".join(blocked)
        )


def _write_partition_attributes(mesh, projection) -> None:
    _set_attribute(mesh, COMPONENT_ATTRIBUTE, "INT", "FACE", projection.labels)
    _set_attribute(mesh, CONFIDENCE_ATTRIBUTE, "FLOAT", "FACE", projection.confidence)
    _set_attribute(mesh, AMBIGUOUS_ATTRIBUTE, "BOOLEAN", "FACE", projection.ambiguous)


def _strip_component_prefix(name: str) -> str:
    cleaned = re.sub(r"^\s*component[_ -]*\d+\s*", "", name, flags=re.IGNORECASE)
    return cleaned.strip() or "Partition"


def _apply_weights(output, source_weights) -> None:
    source_attribute = output.data.attributes.get(SOURCE_VERTEX_ATTRIBUTE)
    if source_attribute is None or source_attribute.domain != "POINT":
        raise PartitionError(f"输出 `{output.name}` 丢失源顶点索引。")
    source_indices = [int(item.value) for item in source_attribute.data]
    used_groups = sorted(
        {
            group_id
            for source_index in source_indices
            for group_id in source_weights[source_index]
        }
    )
    while output.vertex_groups:
        output.vertex_groups.remove(output.vertex_groups[-1])
    groups = {group_id: output.vertex_groups.new(name=str(group_id)) for group_id in used_groups}
    for vertex in output.data.vertices:
        source_index = source_indices[vertex.index]
        for group_id, weight in source_weights[source_index].items():
            groups[group_id].add([vertex.index], float(weight), "REPLACE")


def _validate_geometry_and_weights(master, output, source_indices, source_weights) -> None:
    group_names = {group.index: group.name for group in output.vertex_groups}
    for output_vertex, source_index in zip(output.data.vertices, source_indices):
        if (output_vertex.co - master.data.vertices[source_index].co).length > 1e-6:
            raise PartitionError(f"输出 `{output.name}` 的基础顶点坐标不一致。")
        actual = {}
        for assignment in output_vertex.groups:
            try:
                group_id = int(group_names[assignment.group])
            except (KeyError, TypeError, ValueError):
                raise PartitionError(f"输出 `{output.name}` 包含非 Merged 顶点组。")
            actual[group_id] = float(assignment.weight)
        expected = source_weights[source_index]
        if set(actual) != set(expected) or any(
            abs(actual[group_id] - weight) > 1e-6
            for group_id, weight in expected.items()
        ):
            raise PartitionError(f"输出 `{output.name}` 的 palette 权重不一致。")


def _validate_shape_keys(master, output, source_indices) -> None:
    master_keys = master.data.shape_keys
    output_keys = output.data.shape_keys
    if master_keys is None:
        if output_keys is not None:
            raise PartitionError("拆分结果意外生成了 ShapeKey。")
        return
    if output_keys is None:
        raise PartitionError(f"输出 `{output.name}` 丢失了 ShapeKey。")
    master_names = [item.name for item in master_keys.key_blocks]
    output_names = [item.name for item in output_keys.key_blocks]
    if master_names != output_names:
        raise PartitionError(f"输出 `{output.name}` 的 ShapeKey 列表不一致。")
    for master_key, output_key in zip(master_keys.key_blocks, output_keys.key_blocks):
        for output_index, source_index in enumerate(source_indices):
            if (output_key.data[output_index].co - master_key.data[source_index].co).length > 1e-6:
                raise PartitionError(
                    f"输出 `{output.name}` 的 ShapeKey `{output_key.name}` 坐标不一致。"
                )


def _validate_uvs(master, output, source_loops) -> None:
    for source_layer in master.data.uv_layers:
        target_layer = output.data.uv_layers.get(source_layer.name)
        if target_layer is None:
            raise PartitionError(f"输出 `{output.name}` 丢失 UV `{source_layer.name}`。")
        for output_loop, source_loop in enumerate(source_loops):
            if (target_layer.data[output_loop].uv - source_layer.data[source_loop].uv).length > 1e-6:
                raise PartitionError(f"输出 `{output.name}` 的 UV `{source_layer.name}` 不一致。")


def _validate_normals(master, output, source_loops) -> None:
    output.data.update()
    maximum = (0.0, -1, -1)
    for output_loop, source_loop in enumerate(source_loops):
        delta = output.data.loops[output_loop].normal - master.data.loops[source_loop].normal
        if delta.length > maximum[0]:
            maximum = (delta.length, output_loop, source_loop)
    if maximum[0] > 1e-3:
        raise PartitionError(
            f"输出 `{output.name}` 的 custom normal 不一致："
            f"max={maximum[0]:.6f}, loop={maximum[1]}, source={maximum[2]}。"
        )


def _component_values(mesh) -> list[int]:
    attribute = mesh.attributes.get(COMPONENT_ATTRIBUTE)
    if attribute is None:
        return []
    return [int(item.value) for item in attribute.data]


def _make_component_output(
    context,
    master,
    component_id: int,
    projection,
    source_weights,
    partition_id: str,
):
    expected_faces = projection.labels.count(component_id)
    if expected_faces == 0:
        return None, []

    workspace = _workspace_collection(context.scene)
    before = set(context.scene.objects)
    working = master.copy()
    working.data = master.data.copy()
    working_name = working.name
    workspace.objects.link(working)
    working[ROLE_KEY] = ROLE_DIAGNOSTIC
    working[PARTITION_ID_KEY] = partition_id
    _write_partition_attributes(working.data, projection)
    _set_attribute(
        working.data,
        SOURCE_VERTEX_ATTRIBUTE,
        "INT",
        "POINT",
        range(len(working.data.vertices)),
    )
    _set_attribute(
        working.data,
        SOURCE_FACE_ATTRIBUTE,
        "INT",
        "FACE",
        range(len(working.data.polygons)),
    )
    _set_attribute(
        working.data,
        SOURCE_LOOP_ATTRIBUTE,
        "INT",
        "CORNER",
        range(len(working.data.loops)),
    )
    normal_attribute = capture_split_corner_normals(working.data)

    try:
        _select_objects(context, [working], active=working)
        if expected_faces != len(working.data.polygons):
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.object.mode_set(mode="OBJECT")
            for polygon in working.data.polygons:
                polygon.select = projection.labels[polygon.index] == component_id
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.separate(type="SELECTED")
            bpy.ops.object.mode_set(mode="OBJECT")

        candidates = [obj for obj in context.scene.objects if obj not in before]
        if working.name in bpy.data.objects and working not in candidates:
            candidates.append(working)
        matching = [
            obj
            for obj in candidates
            if obj.type == "MESH"
            and len(obj.data.polygons) == expected_faces
            and set(_component_values(obj.data)) == {component_id}
        ]
        if len(matching) != 1:
            raise PartitionError(f"Component {component_id} 拆分结果无法唯一识别。")
        output = matching[0]
        for candidate in list(candidates):
            if candidate != output and candidate.name in bpy.data.objects:
                _remove_object(candidate)

        restore_split_corner_normals([output], normal_attribute)
        source_vertices = [
            int(item.value) for item in output.data.attributes[SOURCE_VERTEX_ATTRIBUTE].data
        ]
        source_faces = [
            int(item.value) for item in output.data.attributes[SOURCE_FACE_ATTRIBUTE].data
        ]
        source_loops = [
            int(item.value) for item in output.data.attributes[SOURCE_LOOP_ATTRIBUTE].data
        ]
        _validate_shape_keys(master, output, source_vertices)
        _validate_uvs(master, output, source_loops)
        _validate_normals(master, output, source_loops)
        _apply_weights(output, source_weights)
        _validate_geometry_and_weights(master, output, source_vertices, source_weights)

        base_name = _strip_component_prefix(master.name)
        output.name = f"Partition {component_id} {base_name} [Velo Split]"
        output.data.name = output.name
        output[ROLE_KEY] = ROLE_DIAGNOSTIC
        output[PARTITION_ID_KEY] = partition_id
        output[GENERATED_KEY] = True
        for attribute_name in (
            SOURCE_VERTEX_ATTRIBUTE,
            SOURCE_FACE_ATTRIBUTE,
            SOURCE_LOOP_ATTRIBUTE,
        ):
            _remove_attribute(output.data, attribute_name)
        return output, source_faces
    except Exception:
        for candidate in [obj for obj in context.scene.objects if obj not in before]:
            if candidate.name in bpy.data.objects:
                _remove_object(candidate)
        remaining = bpy.data.objects.get(working_name)
        if remaining is not None:
            _remove_object(remaining)
        raise


def _commit_outputs(
    context,
    config,
    master,
    reference,
    outputs,
    routes,
    partition_id,
    projection,
) -> None:
    old_outputs = [
        obj
        for obj in bpy.data.objects
        if obj.get(ROLE_KEY) == ROLE_OUTPUT and obj.get(PARTITION_ID_KEY) == partition_id
    ]
    for component_id, output in outputs.items():
        route = routes[component_id]
        if config.game == "ENDFIELD":
            destination = _component_collection(config.root, int(route["component_id"]))
            output["velo_component_id"] = int(route["component_id"])
        else:
            destination = bpy.data.collections.get(str(route["target_collection"]))
            if destination is None or destination not in set(_walk_collections(config.root)):
                raise PartitionError(
                    f"ZZZ 目标集合 `{route['target_collection']}` 不存在或不属于导出集合。"
                )
            output["velo_zzz_draw_ib"] = str(route["draw_ib"])
            output["velo_zzz_skeleton_mode"] = "MERGED"
        if output.name not in destination.objects:
            destination.objects.link(output)
        for collection in list(output.users_collection):
            if collection != destination:
                collection.objects.unlink(output)
        output[ROLE_KEY] = ROLE_OUTPUT

    _write_partition_attributes(master.data, projection)
    master[ROLE_KEY] = ROLE_MASTER
    master[PARTITION_ID_KEY] = partition_id
    master[ROUTES_KEY] = _routes_json(routes)
    if reference.get(ROLE_KEY) == ROLE_REFERENCE and reference.get(GENERATED_KEY):
        _remember_and_hide(reference)

    for old_output in old_outputs:
        if old_output not in outputs.values():
            _remove_object(old_output)
    base_name = _strip_component_prefix(master.name)
    for component_id, output in outputs.items():
        route = routes[component_id]
        if config.game == "ENDFIELD":
            prefix = f"Component {int(route['component_id'])}"
        else:
            prefix = str(route["display_name"])
        output.name = f"{prefix} {base_name} [Velo Split]"
        output.data.name = output.name


def _partition_id_from_settings(settings):
    for obj in (settings.partition_master_object, settings.partition_reference_object):
        if obj is not None:
            value = obj.get(PARTITION_ID_KEY)
            if value:
                return str(value)
    return ""


def _partition_id_for_target(reference, master) -> str:
    existing = str(master.get(PARTITION_ID_KEY, "") or "")
    if existing:
        return existing
    reference_id = str(reference.get(PARTITION_ID_KEY, "") or "")
    if reference_id and reference.get(ROLE_KEY) == ROLE_REFERENCE:
        occupied = any(
            obj != master
            and str(obj.get(PARTITION_ID_KEY, "")) == reference_id
            and obj.get(ROLE_KEY) in {ROLE_MASTER, ROLE_OUTPUT}
            for obj in bpy.data.objects
        )
        if not occupied:
            return reference_id
    return uuid.uuid4().hex


class VELO_OT_partition_create_reference(bpy.types.Operator):
    bl_idname = "velo.partition_create_reference"
    bl_label = "创建分区参考体"
    bl_description = "从选中的原始 Merged 部件创建带面级路由 ID 的合并参考体"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.velo_tools
        old_reference = settings.partition_reference_object
        try:
            config = _load_configuration(context.scene)
            legacy = settings.partition_legacy_object
            sources, routes = _safe_reference_sources(
                list(context.selected_objects), legacy, config
            )
            _palettes_for_routes(config, routes)
            partition_id = uuid.uuid4().hex
            reference, counts = _make_reference(
                context, sources, routes, partition_id
            )

            for source_object, _component_id in sources:
                source_object[ROLE_KEY] = ROLE_SOURCE
                source_object[PARTITION_ID_KEY] = partition_id
                _remember_and_hide(source_object)
            if legacy is not None:
                legacy[ROLE_KEY] = ROLE_REFERENCE
                legacy[PARTITION_ID_KEY] = partition_id
                legacy[GENERATED_KEY] = False
                _remember_and_hide(legacy)

            settings.partition_reference_object = reference
            if old_reference is not None and old_reference != reference and old_reference.get(GENERATED_KEY):
                _remove_object(old_reference)
            summary = ", ".join(
                f"{routes[key]['display_name']}: {value} faces"
                for key, value in counts.items()
            )
            _set_status(settings, f"参考体已创建：{summary}")
            _select_objects(context, [reference], active=reference)
            self.report({"INFO"}, "分区参考体已创建。")
            return {"FINISHED"}
        except PartitionError as exc:
            _set_status(settings, str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            _set_status(settings, f"创建参考体失败：{exc}")
            self.report({"ERROR"}, f"创建参考体失败：{exc}")
            return {"CANCELLED"}


class VELO_OT_partition_project_split(bpy.types.Operator):
    bl_idname = "velo.partition_project_split"
    bl_label = "投射分区并拆分"
    bl_description = "将参考体的 Component 分区投射到完整 Master，并生成可导出的 Component 副本"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.velo_tools
        reference = settings.partition_reference_object
        master = settings.partition_master_object
        staged = []
        try:
            config = _load_configuration(context.scene)
            if reference is None or reference.type != "MESH":
                raise PartitionError("请选择有效的分区参考体。")
            if master is None or master.type != "MESH":
                raise PartitionError("请选择有效的完整 Master。")
            if master == reference:
                raise PartitionError("分区参考体和完整 Master 不能是同一对象。")
            _validate_master_modifiers(master)
            partition_id = _partition_id_for_target(reference, master)
            if not reference.get(PARTITION_ID_KEY):
                reference[PARTITION_ID_KEY] = partition_id

            routes = _load_routes(reference, config)
            palettes = _palettes_for_routes(config, routes)
            projection = project_component_faces(reference, master)
            unknown_labels = sorted(set(projection.labels) - set(routes))
            if unknown_labels:
                raise PartitionError(
                    "投射结果包含没有路由记录的分区："
                    + ", ".join(str(item) for item in unknown_labels)
                )
            weights = prepare_component_weights(master, projection.labels, palettes)
            outputs = {}
            source_face_ids = []
            for component_id in sorted(set(projection.labels)):
                output, faces = _make_component_output(
                    context,
                    master,
                    component_id,
                    projection,
                    weights.weights,
                    partition_id,
                )
                if output is not None:
                    outputs[component_id] = output
                    staged.append(output)
                    source_face_ids.extend(faces)

            expected_faces = list(range(len(master.data.polygons)))
            if sorted(source_face_ids) != expected_faces:
                raise PartitionError("拆分输出存在丢面或重复面。")
            if sum(len(obj.data.polygons) for obj in outputs.values()) != len(master.data.polygons):
                raise PartitionError("拆分输出总面数与 Master 不一致。")

            _commit_outputs(
                context,
                config,
                master,
                reference,
                outputs,
                routes,
                partition_id,
                projection,
            )
            settings.partition_master_object = master
            counts = component_counts(projection.labels)
            summary = ", ".join(
                f"{routes[key]['display_name']}: {value}"
                for key, value in counts.items()
            )
            warning = (
                f"；{len(weights.warning_vertices)} 个顶点丢弃 5%-10% 权重"
                if weights.warning_vertices
                else ""
            )
            _set_status(
                settings,
                f"拆分完成：{summary}；边界顶点 {weights.seam_vertices}；"
                f"模糊面 {sum(projection.ambiguous)}{warning}",
            )
            _select_objects(context, list(outputs.values()), active=next(iter(outputs.values())))
            self.report({"INFO"}, "Component 分割完成。")
            return {"FINISHED"}
        except PartitionError as exc:
            for output in staged:
                _remove_object(output)
            if master is not None and getattr(exc, "vertex_ids", None):
                _select_vertices(context, master, exc.vertex_ids)
            _set_status(settings, str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            for output in staged:
                _remove_object(output)
            _set_status(settings, f"分割失败：{exc}")
            self.report({"ERROR"}, f"分割失败：{exc}")
            return {"CANCELLED"}


class VELO_OT_partition_select_ambiguous(bpy.types.Operator):
    bl_idname = "velo.partition_select_ambiguous"
    bl_label = "选择模糊面"
    bl_description = "在完整 Master 上选择投射置信度较低的面"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        master = context.scene.velo_tools.partition_master_object
        if master is None or master.type != "MESH":
            self.report({"ERROR"}, "未设置完整 Master。")
            return {"CANCELLED"}
        attribute = master.data.attributes.get(AMBIGUOUS_ATTRIBUTE)
        if attribute is None or attribute.domain != "FACE":
            self.report({"ERROR"}, "Master 尚未生成模糊面标记。")
            return {"CANCELLED"}
        _select_objects(context, [master], active=master)
        for polygon, item in zip(master.data.polygons, attribute.data):
            polygon.select = bool(item.value)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(type="FACE")
        self.report({"INFO"}, f"已选择 {sum(bool(item.value) for item in attribute.data)} 个模糊面。")
        return {"FINISHED"}


class VELO_OT_partition_restore_sources(bpy.types.Operator):
    bl_idname = "velo.partition_restore_sources"
    bl_label = "恢复原始部件"
    bl_description = "移除当前分区生成结果并恢复原始 Component 的可见状态"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.velo_tools
        partition_id = _partition_id_from_settings(settings)
        if not partition_id:
            self.report({"ERROR"}, "当前没有可恢复的分区记录。")
            return {"CANCELLED"}

        master = settings.partition_master_object
        reference = settings.partition_reference_object
        settings.partition_reference_object = None
        settings.partition_legacy_object = None

        for obj in list(bpy.data.objects):
            if str(obj.get(PARTITION_ID_KEY, "")) != partition_id:
                continue
            role = obj.get(ROLE_KEY)
            if role == ROLE_OUTPUT or (role == ROLE_REFERENCE and obj.get(GENERATED_KEY)):
                _remove_object(obj)
                continue
            if role in {ROLE_SOURCE, ROLE_REFERENCE, ROLE_MASTER, ROLE_DIAGNOSTIC}:
                if role in {ROLE_SOURCE, ROLE_REFERENCE, ROLE_DIAGNOSTIC}:
                    _restore_visibility(obj)
                for key in (ROLE_KEY, PARTITION_ID_KEY, GENERATED_KEY, ROUTES_KEY):
                    if key in obj:
                        del obj[key]

        if master is not None and master.name in bpy.data.objects:
            for attribute_name in (
                COMPONENT_ATTRIBUTE,
                CONFIDENCE_ATTRIBUTE,
                AMBIGUOUS_ATTRIBUTE,
            ):
                _remove_attribute(master.data, attribute_name)
            if ROUTES_KEY in master:
                del master[ROUTES_KEY]
        settings.partition_master_object = None
        _set_status(settings, "已恢复原始 Component，并移除分区生成结果。")
        self.report({"INFO"}, "原始 Component 已恢复。")
        return {"FINISHED"}


_CLASSES = (
    VELO_OT_partition_create_reference,
    VELO_OT_partition_project_split,
    VELO_OT_partition_select_ambiguous,
    VELO_OT_partition_restore_sources,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
