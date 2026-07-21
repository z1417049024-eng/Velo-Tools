"""Authoring-zone to export-zone synchronization for EFMI Merged."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from array import array
from pathlib import Path

import bpy

from . import operators as legacy
from .algorithms import PartitionError, component_counts, prepare_component_weights, project_component_faces
from .constants import (
    AMBIGUOUS_ATTRIBUTE,
    COMPONENT_ATTRIBUTE,
    EXPORT_ZONE_KEY,
    GENERATED_KEY,
    OUTPUT_MANIFEST_KEY,
    PARTITION_ID_KEY,
    PART_COLLECTION_KEY,
    PART_INDEX_KEY,
    PREVIEW_HIDE_KEY,
    ROLE_DIAGNOSTIC,
    ROLE_IMPORTED,
    ROLE_KEY,
    ROLE_MASTER,
    ROLE_OUTPUT,
    ROLE_REFERENCE,
    ROLE_SOURCE,
    ROUTES_KEY,
    SOURCE_COMPONENTS_KEY,
    SOURCE_ID_KEY,
    SOURCE_NAMES_KEY,
    SYNC_FORMAT_VERSION,
    SYNC_MANIFEST_KEY,
    SYNC_VERSION_KEY,
)


_COMPONENT_RE = re.compile(r"component[_ -]*(\d+)", re.IGNORECASE)
_COMPONENT_PREFIX_RE = re.compile(
    r"^\s*component[_ -]*\d+(?:\s+[0-9a-f]{8})?(?:\s+|[_ -]+)?",
    re.IGNORECASE,
)
_GENERATED_SUFFIX_RE = re.compile(r"\s*\[(?:Velo Split|Velo Sync)\](?:\.\d{3})?$", re.IGNORECASE)
_DUPLICATE_SUFFIX_RE = re.compile(r"\.\d{3}$")
_TOPOLOGY_MODIFIERS = legacy._TOPOLOGY_MODIFIERS


def _settings(scene):
    settings = getattr(scene, "velo_tools", None)
    cfg = getattr(scene, "VTEF_settings", None)
    if settings is None or cfg is None:
        raise PartitionError("未启用 Velo Tools Endfield EFMI 工作流。")
    return settings, cfg


def _ensure_merged(scene):
    settings, cfg = _settings(scene)
    settings.active_game = "ENDFIELD"
    if getattr(cfg, "mod_skeleton_type", None) != "MERGED":
        raise PartitionError("分割操作要求先在游戏页把骨架模式设为 MERGED。")
    return settings, cfg


def _source_path(cfg) -> Path:
    source = Path(bpy.path.abspath(str(getattr(cfg, "object_source_folder", "") or "")))
    if not source.is_dir():
        raise PartitionError("EFMI 对象源目录不存在。")
    if not (source / "VertexGroupMap.json").is_file():
        raise PartitionError("EFMI 对象源目录缺少 VertexGroupMap.json。")
    return source


def _load_config(scene):
    settings, cfg = _ensure_merged(scene)
    if cfg.component_collection is None:
        cfg.component_collection = settings.partition_export_collection or settings.partition_authoring_collection
    return settings, cfg, legacy._load_configuration(scene)


def _load_routes_and_palettes(scene, reference):
    cfg, root, source, component_palettes = legacy._load_configuration(scene)
    values = {
        int(item.value)
        for item in reference.data.attributes[COMPONENT_ATTRIBUTE].data
    }
    routes = {
        component_id: {
            "label": component_id,
            "game": "ENDFIELD",
            "component_id": component_id,
            "display_name": f"C{component_id}",
        }
        for component_id in sorted(values)
    }
    palettes = {
        component_id: set(component_palettes.get(component_id, set()))
        for component_id in routes
    }
    missing = [f"C{component_id}" for component_id, palette in palettes.items() if not palette]
    if missing:
        raise PartitionError("以下分区没有可用的 Merged palette：" + ", ".join(missing))
    return (cfg, root, source, component_palettes), routes, palettes


def _collection_parents(collection):
    parents = [item for item in bpy.data.collections if collection.name in item.children]
    for scene in bpy.data.scenes:
        if collection.name in scene.collection.children:
            parents.append(scene.collection)
    return parents


def _link_as_sibling(scene, collection, sibling):
    parents = _collection_parents(sibling) if sibling is not None else []
    if not parents:
        parents = [scene.collection]
    for parent in parents:
        if collection.name not in parent.children:
            parent.children.link(collection)


def _walk_collections(root):
    yield root
    for child in root.children:
        yield from _walk_collections(child)


def _collection_contains(root, obj) -> bool:
    return obj in set(root.all_objects) if root is not None else False


def _collection_visible_for_object(root, obj) -> bool:
    if root is None:
        return False

    def walk(collection, hidden):
        hidden = hidden or bool(collection.hide_viewport)
        if obj.name in collection.objects and not hidden:
            return True
        return any(walk(child, hidden) for child in collection.children)

    return walk(root, False)


def _user_hidden(obj) -> bool:
    if PREVIEW_HIDE_KEY in obj:
        return bool(obj[PREVIEW_HIDE_KEY])
    try:
        return bool(obj.hide_get()) or bool(obj.hide_viewport)
    except RuntimeError:
        return bool(obj.hide_viewport)


def _authoring_root(settings, cfg):
    candidates = (
        settings.partition_authoring_collection,
        settings.partition_previous_export_collection,
        cfg.component_collection,
    )
    for root in candidates:
        if root is not None and not root.get(EXPORT_ZONE_KEY):
            settings.partition_authoring_collection = root
            settings.partition_previous_export_collection = root
            return root
    raise PartitionError("请先在 EFMI 游戏页选择原始 Character Component 集合。")


def _whole_mesh_rows(settings, *, prune=True):
    if prune:
        seen = set()
        for index in reversed(range(len(settings.partition_whole_mesh_items))):
            item = settings.partition_whole_mesh_items[index]
            obj = item.object
            if obj is None or obj.type != "MESH" or obj in seen:
                settings.partition_whole_mesh_items.remove(index)
                continue
            seen.add(obj)

    rows = []
    used_ids = {}
    for item in settings.partition_whole_mesh_items:
        obj = item.object
        if obj is None or obj.type != "MESH":
            continue
        source_id = str(item.source_id or obj.get(SOURCE_ID_KEY, "") or "")
        if not source_id or (source_id in used_ids and used_ids[source_id] is not obj):
            source_id = uuid.uuid4().hex
        item.source_id = source_id
        obj[SOURCE_ID_KEY] = source_id
        used_ids[source_id] = obj
        rows.append((item, obj))
    return rows


def _whole_meshes(settings, *, include_hidden=False):
    root = settings.partition_authoring_collection
    result = []
    for _item, obj in _whole_mesh_rows(settings):
        hidden = _user_hidden(obj) or not _collection_visible_for_object(root, obj)
        if include_hidden or not hidden:
            result.append(obj)
    return result


def _iter_collection_meshes(collection):
    if collection is None:
        return
    seen = set()
    for obj in collection.all_objects:
        if obj.type == "MESH" and obj not in seen:
            seen.add(obj)
            yield obj


def _passthrough_sources(settings):
    seen = set()
    for item in settings.partition_passthrough_items:
        if item.source_kind == "COLLECTION":
            candidates = _iter_collection_meshes(item.source_collection)
        else:
            candidates = (item.source_object,) if item.source_object is not None else ()
        for obj in candidates:
            if obj is not None and obj.type == "MESH" and obj not in seen:
                seen.add(obj)
                yield obj


def _ensure_source_id(obj) -> str:
    value = str(obj.get(SOURCE_ID_KEY, "") or "")
    if not value:
        value = uuid.uuid4().hex
        obj[SOURCE_ID_KEY] = value
    return value


def _component_collection(root, component_id: int):
    collection = legacy._component_collection(root, component_id)
    if collection is not None:
        return collection
    collection = bpy.data.collections.new(f"C{component_id}")
    collection["velo_component_id"] = component_id
    collection.color_tag = f"COLOR_{(component_id % 8) + 1:02d}"
    root.children.link(collection)
    return collection


def _rename_component_prefix(obj, component_id: int):
    name = str(obj.name or "")
    if _COMPONENT_RE.match(name):
        obj.name = re.sub(
            r"^\s*component[_ -]*\d+",
            f"Component {component_id}",
            name,
            count=1,
            flags=re.IGNORECASE,
        )


def move_whole_mesh_to_home(scene, obj, component_id: int):
    settings, cfg = _settings(scene)
    root = settings.partition_authoring_collection
    if root is None:
        root = _authoring_root(settings, cfg)
    component_id = int(component_id)
    if not 0 <= component_id <= 15:
        raise PartitionError("整体模型归属 Component 必须位于 C0-C15。")
    destination = _component_collection(root, component_id)
    if obj.name not in destination.objects:
        destination.objects.link(obj)
    for collection in list(obj.users_collection):
        if collection != destination:
            collection.objects.unlink(obj)
    obj["velo_component_id"] = component_id
    _rename_component_prefix(obj, component_id)
    settings.partition_status = "整体区已修改，需要重新同步。"


def _register_whole_mesh(settings, root, obj, component_id: int):
    if obj is None or obj.type != "MESH":
        raise PartitionError("请选择有效的 Mesh 作为整体模型。")
    if obj.get(ROLE_KEY) in {ROLE_SOURCE, ROLE_REFERENCE, ROLE_OUTPUT, ROLE_DIAGNOSTIC}:
        raise PartitionError(f"`{obj.name}` 是分区来源或派生对象，不能登记为整体模型。")
    item = next((row for row in settings.partition_whole_mesh_items if row.object is obj), None)
    if item is None:
        item = settings.partition_whole_mesh_items.add()
        item.object = obj
    source_id = _ensure_source_id(obj)
    used = {
        str(row.source_id or row.object.get(SOURCE_ID_KEY, "") or "")
        for row in settings.partition_whole_mesh_items
        if row.object is not None and row.object is not obj
    }
    if source_id in used:
        source_id = uuid.uuid4().hex
        obj[SOURCE_ID_KEY] = source_id
    item.source_id = source_id
    item.home_component = int(component_id)
    obj[ROLE_KEY] = ROLE_MASTER
    obj[PARTITION_ID_KEY] = source_id
    move_whole_mesh_to_home(bpy.context.scene, obj, int(component_id))
    return item


def _object_component(obj, default=0):
    component_id = legacy._component_id_from_object(obj)
    if component_id is None:
        component_id = default
    return max(0, min(15, int(component_id)))


def _strip_component_name(name: str) -> str:
    value = _GENERATED_SUFFIX_RE.sub("", str(name or "")).strip()
    value = _COMPONENT_PREFIX_RE.sub("", value, count=1).strip()
    return value or str(name or "Mesh")


def _component_from_material_slot(slot):
    material = getattr(slot, "material", None)
    name = getattr(material, "name", "") if material is not None else ""
    match = _COMPONENT_RE.search(name or "")
    if match is None:
        raise PartitionError(f"材质 `{name or '<空材质>'}` 无法解析 Component 编号。")
    component_id = int(match.group(1))
    if not 0 <= component_id <= 15:
        raise PartitionError(f"材质 `{name}` 的 Component 编号超出 C0-C15。")
    return component_id


def _normalized_source_name(name: str):
    return _DUPLICATE_SUFFIX_RE.sub("", str(name or "").strip()).casefold()


def _reference_source_names(reference):
    if reference is None:
        return []
    try:
        value = json.loads(str(reference.get(SOURCE_NAMES_KEY, "[]") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(name) for name in value if name]


def _match_reference_sources(authoring, legacy_obj, previous_reference=None):
    candidates = [
        obj
        for obj in authoring.all_objects
        if obj.type == "MESH"
        and obj is not legacy_obj
        and obj.get(ROLE_KEY) not in {ROLE_REFERENCE, ROLE_MASTER, ROLE_OUTPUT, ROLE_DIAGNOSTIC}
    ]
    material_names = []
    for slot in legacy_obj.material_slots:
        material = slot.material
        name = getattr(material, "name", "") if material is not None else ""
        if name and _normalized_source_name(name) not in {
            _normalized_source_name(item) for item in material_names
        }:
            material_names.append(name)
    previous_names = _reference_source_names(previous_reference)
    requested = previous_names or material_names
    matched = []
    for name in requested:
        normalized = _normalized_source_name(name)
        hits = [obj for obj in candidates if _normalized_source_name(obj.name) == normalized and obj not in matched]
        if len(hits) != 1:
            raise PartitionError(
                f"原部件 `{name}` 匹配到 {len(hits)} 个对象，无法安全识别基准身体来源。"
            )
        matched.append(hits[0])
    if len(matched) < 2:
        raise PartitionError("原部件手工合并体无法匹配至少两个原始 Component 对象。")
    return matched


def _build_reference_from_legacy(context, legacy_obj, partition_id: str, authoring, previous_reference=None):
    if legacy_obj is None or legacy_obj.type != "MESH":
        raise PartitionError("请选择有效的原部件手工合并体。")
    if legacy_obj.data.shape_keys is not None:
        raise PartitionError("原部件手工合并体不能带 ShapeKey。")
    if legacy_obj.modifiers:
        raise PartitionError("原部件手工合并体不能带 modifier。")
    slots = list(legacy_obj.material_slots)
    if not slots:
        raise PartitionError("原部件手工合并体没有可识别的材质分区。")
    slot_components = [_component_from_material_slot(slot) for slot in slots]
    labels = []
    for polygon in legacy_obj.data.polygons:
        if polygon.material_index >= len(slot_components):
            raise PartitionError("原部件手工合并体存在无效材质索引。")
        labels.append(slot_components[polygon.material_index])
    components = sorted(set(labels))
    if len(components) < 2:
        raise PartitionError("原部件手工合并体至少需要两个 Component 材质分区。")

    reference = legacy_obj.copy()
    reference.data = legacy_obj.data.copy()
    workspace = legacy._workspace_collection(context.scene)
    workspace.objects.link(reference)
    legacy._set_attribute(reference.data, COMPONENT_ATTRIBUTE, "INT", "FACE", labels)
    routes = {
        component_id: {
            "label": component_id,
            "game": "ENDFIELD",
            "component_id": component_id,
            "display_name": f"C{component_id}",
        }
        for component_id in components
    }
    reference.name = "VELO Partition Reference " + "_".join(f"C{item}" for item in components)
    reference.data.name = reference.name
    reference[ROLE_KEY] = ROLE_REFERENCE
    reference[PARTITION_ID_KEY] = partition_id
    reference[GENERATED_KEY] = True
    reference[ROUTES_KEY] = json.dumps(list(routes.values()), sort_keys=True)
    sources = _match_reference_sources(authoring, legacy_obj, previous_reference)
    reference[SOURCE_NAMES_KEY] = json.dumps([obj.name for obj in sources], ensure_ascii=False)
    reference[SOURCE_COMPONENTS_KEY] = json.dumps(
        [_object_component(obj) for obj in sources],
        ensure_ascii=False,
    )
    for source in sources:
        source[ROLE_KEY] = ROLE_SOURCE
        source[PARTITION_ID_KEY] = partition_id
        legacy._remember_and_hide(source)
    legacy_obj[ROLE_KEY] = ROLE_REFERENCE
    legacy_obj[PARTITION_ID_KEY] = partition_id
    legacy._remember_and_hide(legacy_obj)
    counts = component_counts(labels)
    return reference, counts


def _validate_reference(reference):
    if reference is None or reference.type != "MESH":
        raise PartitionError("分区参考体无效，请重新初始化。")
    attribute = reference.data.attributes.get(COMPONENT_ATTRIBUTE)
    if attribute is None or attribute.domain != "FACE" or attribute.data_type != "INT":
        raise PartitionError("分区参考体缺少面级 Component ID，请重新初始化。")
    if len(set(int(item.value) for item in attribute.data)) < 2:
        raise PartitionError("分区参考体至少需要两个 Component。")


def _migrate_legacy_registry(settings, authoring):
    if settings.partition_registry_migrated:
        return
    body = settings.partition_master_object
    old_partition_id = str(body.get(PARTITION_ID_KEY, "") or "") if body is not None else ""
    candidates = []
    if body is not None and body.type == "MESH":
        candidates.append(body)
    for obj in bpy.data.objects:
        if obj in candidates or obj.type != "MESH" or obj.get(ROLE_KEY) != ROLE_MASTER:
            continue
        if old_partition_id and str(obj.get(PARTITION_ID_KEY, "") or "") != old_partition_id:
            continue
        candidates.append(obj)
    for obj in candidates:
        _register_whole_mesh(settings, authoring, obj, _object_component(obj))
    settings.partition_registry_migrated = True


def _capture_imported_objects(settings, authoring):
    if settings.partition_imported_captured:
        return
    registered = {obj for _item, obj in _whole_mesh_rows(settings)}
    explicit = set(_passthrough_sources(settings))
    excluded = {ROLE_SOURCE, ROLE_REFERENCE, ROLE_MASTER, ROLE_OUTPUT, ROLE_DIAGNOSTIC}
    for obj in authoring.all_objects:
        if obj.type != "MESH" or obj in registered or obj in explicit:
            continue
        if obj.get(ROLE_KEY) in excluded:
            continue
        component_id = legacy._component_id_from_object(obj)
        if component_id is None or not 0 <= int(component_id) <= 15:
            continue
        obj[ROLE_KEY] = ROLE_IMPORTED
        obj["velo_component_id"] = int(component_id)
        _ensure_source_id(obj)
    settings.partition_imported_captured = True


def _reference_sources(authoring, reference):
    names = {_normalized_source_name(name) for name in _reference_source_names(reference)}
    if names:
        return [
            obj
            for obj in authoring.all_objects
            if obj.type == "MESH"
            and obj.get(ROLE_KEY) != ROLE_REFERENCE
            and _normalized_source_name(obj.name) in names
        ]
    return [
        obj
        for obj in authoring.all_objects
        if obj.type == "MESH" and obj.get(ROLE_KEY) == ROLE_SOURCE
    ]


def _prepare_workflow(context, *, rebuild_reference=False):
    settings, cfg = _ensure_merged(context.scene)
    _source_path(cfg)
    authoring = _authoring_root(settings, cfg)
    _migrate_legacy_registry(settings, authoring)
    body = settings.partition_master_object
    if body is None or body.type != "MESH":
        raise PartitionError("请先选择身体并点击“创建基准身体”。")
    if not any(obj is body for _item, obj in _whole_mesh_rows(settings)):
        _register_whole_mesh(settings, authoring, body, _object_component(body))
    body_id = _ensure_source_id(body)
    reference = settings.partition_reference_object
    if rebuild_reference or reference is None:
        legacy_obj = settings.partition_legacy_object
        old_reference = reference
        reference, counts = _build_reference_from_legacy(
            context,
            legacy_obj,
            body_id,
            authoring,
            previous_reference=old_reference,
        )
        settings.partition_reference_object = reference
        if old_reference is not None and old_reference is not reference and old_reference.get(GENERATED_KEY):
            legacy._remove_object(old_reference)
    else:
        _validate_reference(reference)
        counts = component_counts(int(item.value) for item in reference.data.attributes[COMPONENT_ATTRIBUTE].data)
    sources = _reference_sources(authoring, reference)
    if len(sources) < 2:
        raise PartitionError("分区参考体无法找到原始 Component 来源，请重新创建基准身体。")
    for obj in authoring.all_objects:
        if obj.type == "MESH" and obj.get(ROLE_KEY) == ROLE_SOURCE and obj not in sources:
            obj[ROLE_KEY] = ROLE_IMPORTED
            legacy._restore_visibility(obj)
    for source in sources:
        source[ROLE_KEY] = ROLE_SOURCE
        source[PARTITION_ID_KEY] = body_id
        legacy._remember_and_hide(source)
    body[ROLE_KEY] = ROLE_MASTER
    body[PARTITION_ID_KEY] = body_id
    _capture_imported_objects(settings, authoring)
    return settings, cfg, authoring, body, reference, counts


def _validate_modifiers(obj):
    unsafe = [modifier.name for modifier in obj.modifiers if modifier.type in _TOPOLOGY_MODIFIERS]
    if unsafe:
        raise PartitionError(
            f"整体模型 `{obj.name}` 含改变拓扑的 modifier，请先应用或移除："
            + ", ".join(unsafe)
        )


def _resolve_merge_map(settings, routes, config, body):
    body_id = _ensure_source_id(body)
    for index, item in enumerate(settings.partition_merge_items):
        for obj in (item.source_object, item.target_object):
            if obj is None:
                continue
            if obj.get(ROLE_KEY) == ROLE_OUTPUT and obj.get(GENERATED_KEY):
                # Old part.N projects stored the selected Cx beside a pointer to
                # a disposable output. The integer survives migration and is
                # rebound to the new standard-body output after the first sync.
                continue
            belongs_to_body = (
                str(obj.get(SOURCE_ID_KEY, "") or "") == body_id
                or (
                    obj.get(ROLE_KEY) == ROLE_SOURCE
                    and str(obj.get(PARTITION_ID_KEY, "") or "") == body_id
                )
            )
            if not belongs_to_body:
                raise PartitionError(
                    f"分块归并第 {index + 1} 行只能吸取当前基准身体或它的原始 Component 片段。"
                )
    direct = {}
    for index, item in enumerate(settings.partition_merge_items):
        source = legacy._component_id_from_object(item.source_object) if item.source_object else None
        target = legacy._component_id_from_object(item.target_object) if item.target_object else None
        if source is None:
            source = int(item.source_component)
        if target is None:
            target = int(item.target_component)
        if source < 0 or target < 0:
            raise PartitionError(f"分块归并第 {index + 1} 行没有选择完整的左右对象。")
        if source == target:
            raise PartitionError(f"分块归并第 {index + 1} 行左右对象属于同一个 C{source}。")
        if source not in routes or target not in routes:
            raise PartitionError(f"分块归并第 {index + 1} 行的 C{source} → C{target} 不属于当前参考体。")
        if source in direct and direct[source] != target:
            raise PartitionError(f"C{source} 同时归并到了 C{direct[source]} 和 C{target}。")
        direct[source] = target
        item.source_component = source
        item.target_component = target
    resolved = {}
    for source, first_target in direct.items():
        target = first_target
        path = {source}
        while target in direct:
            if target in path:
                raise PartitionError("分块归并规则形成循环。")
            path.add(target)
            target = direct[target]
        resolved[source] = target
    return resolved


def _apply_component_merges(projection, merge_map):
    for face, label in enumerate(projection.labels):
        target = merge_map.get(label)
        if target is not None and target != label:
            projection.labels[face] = target
            projection.ambiguous[face] = True


def _refresh_component_merge_objects(settings, body, outputs):
    body_id = _ensure_source_id(body)

    def find(component_id):
        output = outputs.get(component_id)
        if output is not None:
            return output
        return next(
            (
                obj
                for obj in bpy.data.objects
                if obj.get(ROLE_KEY) == ROLE_SOURCE
                and str(obj.get(PARTITION_ID_KEY, "") or "") == body_id
                and legacy._component_id_from_object(obj) == component_id
            ),
            None,
        )

    for item in settings.partition_merge_items:
        item.source_object = find(int(item.source_component))
        item.target_object = find(int(item.target_component))


def _new_staging_root(scene, authoring):
    root = bpy.data.collections.new(f".__VELO_SPLIT_STAGING__{uuid.uuid4().hex[:8]}")
    _link_as_sibling(scene, root, authoring)
    root[EXPORT_ZONE_KEY] = True
    root[SYNC_VERSION_KEY] = SYNC_FORMAT_VERSION
    components = {}
    for component_id in range(16):
        collection = bpy.data.collections.new(f"C{component_id}")
        collection["velo_component_id"] = component_id
        collection.color_tag = f"COLOR_{(component_id % 8) + 1:02d}"
        root.children.link(collection)
        components[component_id] = collection
    return root, components


def _remove_collection_tree(root):
    if root is None or root.name not in bpy.data.collections:
        return
    collections = list(_walk_collections(root))
    objects = set(root.all_objects)
    for obj in objects:
        if obj.get(ROLE_KEY) == ROLE_OUTPUT and obj.name in bpy.data.objects:
            legacy._remove_object(obj)
    for collection in reversed(collections):
        if collection.name in bpy.data.collections:
            bpy.data.collections.remove(collection)


def _place_output(output, destination, component_id: int, source_obj, desired_names):
    source_id = _ensure_source_id(source_obj)
    base_name = _strip_component_name(source_obj.name)
    desired = f"Component {component_id} {base_name} [Velo Sync]"
    output[ROLE_KEY] = ROLE_OUTPUT
    output[GENERATED_KEY] = True
    output[SOURCE_ID_KEY] = source_id
    output["velo_component_id"] = component_id
    output[PARTITION_ID_KEY] = source_id
    for key in (PART_INDEX_KEY, PART_COLLECTION_KEY):
        if key in output:
            del output[key]
    if output.name not in destination.objects:
        destination.objects.link(output)
    for collection in list(output.users_collection):
        if collection != destination:
            collection.objects.unlink(output)
    output.name = f".__VELO_STAGING_OBJECT__{uuid.uuid4().hex}"
    output.data.name = output.name
    desired_names[output] = desired


def _split_one(
    context,
    config,
    settings,
    reference,
    source_obj,
    routes,
    palettes,
    components,
    desired_names,
    *,
    emit=True,
    merge_map=None,
):
    _validate_modifiers(source_obj)
    source_id = _ensure_source_id(source_obj)
    projection = project_component_faces(reference, source_obj)
    if merge_map is None:
        merge_map = _resolve_merge_map(settings, routes, config, settings.partition_master_object)
    _apply_component_merges(projection, merge_map)
    unknown = sorted(set(projection.labels) - set(routes))
    if unknown:
        raise PartitionError("投射结果包含未知 Component：" + ", ".join(map(str, unknown)))
    legacy._write_partition_attributes(source_obj.data, projection)
    source_obj[ROLE_KEY] = ROLE_MASTER
    source_obj[PARTITION_ID_KEY] = source_id
    if not emit:
        return [], projection, None
    try:
        weights = prepare_component_weights(source_obj, projection.labels, palettes)
    except PartitionError as exc:
        wrapped = PartitionError(
            f"整体模型 `{source_obj.name}`：{exc}",
            vertex_ids=getattr(exc, "vertex_ids", ()),
        )
        wrapped.object_name = source_obj.name
        raise wrapped from exc
    outputs = []
    source_faces = []
    for component_id in sorted(set(projection.labels)):
        output, faces = legacy._make_component_output(
            context,
            source_obj,
            component_id,
            projection,
            weights.weights,
            source_id,
        )
        if output is None:
            continue
        _place_output(output, components[component_id], component_id, source_obj, desired_names)
        outputs.append(output)
        source_faces.extend(faces)
    if sorted(source_faces) != list(range(len(source_obj.data.polygons))):
        raise PartitionError(f"整体模型 `{source_obj.name}` 的分割结果存在丢面或重复面。")
    if sum(len(obj.data.polygons) for obj in outputs) != len(source_obj.data.polygons):
        raise PartitionError(f"整体模型 `{source_obj.name}` 的分割总面数不一致。")
    return outputs, projection, weights


def _copy_passthrough(source_obj, component_id, destination, desired_names):
    source_id = _ensure_source_id(source_obj)
    output = source_obj.copy()
    output.data = source_obj.data.copy()
    destination.objects.link(output)
    output[ROLE_KEY] = ROLE_OUTPUT
    output[GENERATED_KEY] = True
    output[SOURCE_ID_KEY] = source_id
    output[PARTITION_ID_KEY] = source_id
    output["velo_component_id"] = component_id
    for key in (PART_INDEX_KEY, PART_COLLECTION_KEY):
        if key in output:
            del output[key]
    output.name = f".__VELO_STAGING_OBJECT__{uuid.uuid4().hex}"
    output.data.name = output.name
    desired_names[output] = f"Component {component_id} {_strip_component_name(source_obj.name)} [Velo Sync]"
    return output


def _visible_for_sync(settings, obj):
    root = settings.partition_authoring_collection
    return not _user_hidden(obj) and (
        not _collection_contains(root, obj) or _collection_visible_for_object(root, obj)
    )


def _collect_imported_rows(settings, *, include_hidden=False):
    root = settings.partition_authoring_collection
    explicit = set(_passthrough_sources(settings))
    registered = {obj for _item, obj in _whole_mesh_rows(settings)}
    rows = []
    for obj in root.all_objects:
        if obj.type != "MESH" or obj in explicit or obj in registered:
            continue
        if obj.get(ROLE_KEY) != ROLE_IMPORTED:
            continue
        component_id = legacy._component_id_from_object(obj)
        if component_id is None or not 0 <= int(component_id) <= 15:
            raise PartitionError(f"原始对象 `{obj.name}` 无法确定所属 Cx。")
        if include_hidden or _visible_for_sync(settings, obj):
            rows.append((obj, int(component_id)))
    rows.sort(key=lambda row: (row[1], row[0].name.casefold(), row[0].name))
    return rows


def _collect_passthrough_rows(settings, *, include_hidden=False):
    rows = []
    for index, item in enumerate(settings.partition_passthrough_items):
        if item.source_kind == "COLLECTION":
            if item.source_collection is None:
                raise PartitionError(f"原样同步第 {index + 1} 行没有选择来源集合。")
            sources = list(_iter_collection_meshes(item.source_collection))
        else:
            if item.source_object is None or item.source_object.type != "MESH":
                raise PartitionError(f"原样同步第 {index + 1} 行没有选择有效来源对象。")
            sources = [item.source_object]
        if not sources:
            raise PartitionError(f"原样同步第 {index + 1} 行没有 Mesh。")
        for source in sources:
            if include_hidden or _visible_for_sync(settings, source):
                rows.append((source, int(item.target_component)))
    return rows


def _hash_bytes(hasher, value):
    if isinstance(value, str):
        value = value.encode("utf-8", "surrogatepass")
    hasher.update(value)
    hasher.update(b"\0")


def _hash_rna_scalars(hasher, owner):
    for prop in getattr(owner.bl_rna, "properties", ()):
        if prop.identifier == "rna_type" or prop.is_readonly:
            continue
        if prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
            continue
        try:
            _hash_bytes(hasher, f"{prop.identifier}={getattr(owner, prop.identifier)!r}")
        except Exception:
            continue


def _hash_mesh(hasher, obj, *, include_name=True):
    mesh = obj.data
    mesh_name = mesh.name if include_name else ""
    _hash_bytes(hasher, f"mesh:{mesh_name}:{len(mesh.vertices)}:{len(mesh.loops)}:{len(mesh.polygons)}")
    coordinates = array('f', [0.0]) * (len(mesh.vertices) * 3)
    if coordinates:
        mesh.vertices.foreach_get("co", coordinates)
        _hash_bytes(hasher, coordinates.tobytes())
    loop_vertices = array('i', [0]) * len(mesh.loops)
    if loop_vertices:
        mesh.loops.foreach_get("vertex_index", loop_vertices)
        _hash_bytes(hasher, loop_vertices.tobytes())
    for polygon in mesh.polygons:
        _hash_bytes(hasher, f"p:{polygon.loop_start}:{polygon.loop_total}:{polygon.material_index}")
    for layer in mesh.uv_layers:
        _hash_bytes(hasher, f"uv:{layer.name}")
        values = array('f', [0.0]) * (len(layer.data) * 2)
        if values:
            layer.data.foreach_get("uv", values)
            _hash_bytes(hasher, values.tobytes())
    for attribute in mesh.attributes:
        if attribute.name.startswith("velo_partition_") or attribute.name.startswith("__velo_partition_"):
            continue
        _hash_bytes(hasher, f"attr:{attribute.name}:{attribute.domain}:{attribute.data_type}")
        for item in attribute.data:
            for field in ("value", "vector", "color"):
                if hasattr(item, field):
                    _hash_bytes(hasher, repr(getattr(item, field)))
                    break
    for slot in obj.material_slots:
        material = slot.material
        _hash_bytes(hasher, f"mat:{material.name if material else ''}:{slot.link}")
    for group in obj.vertex_groups:
        _hash_bytes(hasher, f"vg:{group.index}:{group.name}:{group.lock_weight}")
    for vertex in mesh.vertices:
        for assignment in vertex.groups:
            _hash_bytes(hasher, f"w:{vertex.index}:{assignment.group}:{assignment.weight:.9g}")
    keys = mesh.shape_keys
    if keys is not None:
        for key in keys.key_blocks:
            _hash_bytes(hasher, f"key:{key.name}:{key.value:.9g}:{key.mute}")
            values = array('f', [0.0]) * (len(key.data) * 3)
            if values:
                key.data.foreach_get("co", values)
                _hash_bytes(hasher, values.tobytes())
        _hash_animation(hasher, keys)


def _hash_animation(hasher, owner):
    animation = getattr(owner, "animation_data", None)
    if animation is None:
        return
    action = getattr(animation, "action", None)
    _hash_bytes(hasher, f"action:{getattr(action, 'name', '')}")
    for driver in getattr(animation, "drivers", ()):
        _hash_bytes(hasher, f"driver:{driver.data_path}:{driver.array_index}:{driver.driver.expression}")
        for variable in driver.driver.variables:
            _hash_bytes(hasher, f"var:{variable.name}:{variable.type}")
            for target in variable.targets:
                _hash_bytes(hasher, f"target:{getattr(target.id, 'name', '')}:{target.data_path}")


def _hash_object(hasher, obj, *, include_visibility=True, include_name=True):
    visibility = _user_hidden(obj) if include_visibility else False
    object_name = obj.name if include_name else ""
    _hash_bytes(hasher, f"obj:{object_name}:{obj.get(SOURCE_ID_KEY, '')}:{visibility}")
    _hash_bytes(hasher, repr(tuple(value for row in obj.matrix_world for value in row)))
    _hash_bytes(hasher, f"parent:{getattr(obj.parent, 'name', '')}:{obj.parent_type}:{obj.parent_bone}")
    for key in sorted(obj.keys()):
        if str(key).startswith("velo_partition_") or str(key).startswith("_RNA_UI"):
            continue
        _hash_bytes(hasher, f"prop:{key}:{obj[key]!r}")
    for modifier in obj.modifiers:
        _hash_bytes(hasher, f"modifier:{modifier.name}:{modifier.type}")
        _hash_rna_scalars(hasher, modifier)
    for constraint in obj.constraints:
        _hash_bytes(hasher, f"constraint:{constraint.name}:{constraint.type}")
        _hash_rna_scalars(hasher, constraint)
        _hash_bytes(hasher, f"constraint_target:{getattr(getattr(constraint, 'target', None), 'name', '')}")
    _hash_animation(hasher, obj)
    _hash_mesh(hasher, obj, include_name=include_name)


def _hash_bindings(hasher, scene, cfg):
    if bool(getattr(cfg, "use_ini_toggles", False)):
        for var in cfg.ini_toggles.vars:
            _hash_bytes(hasher, f"toggle_var:{getattr(var, 'name', '')}")
            _hash_rna_scalars(hasher, var)
            for state in var.states:
                _hash_bytes(hasher, f"toggle_state:{getattr(state, 'name', '')}")
                _hash_rna_scalars(hasher, state)
                for item in state.objects:
                    obj = item.object
                    _hash_bytes(
                        hasher,
                        f"toggle_obj:{getattr(obj, 'name', '')}:{getattr(obj, 'get', lambda *_: '')(SOURCE_ID_KEY, '') if obj else ''}",
                    )
                    _hash_rna_scalars(hasher, item)
    crossib = getattr(scene, "crossib_settings", None)
    if crossib is not None and bool(getattr(crossib, "enabled", False)):
        for mapping in crossib.mappings:
            _hash_bytes(
                hasher,
                f"crossib:{mapping.source_kind}:{mapping.target_component}:"
                f"{getattr(mapping.source_object, 'name', '')}:{getattr(mapping.source_collection, 'name', '')}",
            )


def build_sync_manifest(scene) -> str:
    settings, cfg = _settings(scene)
    authoring = settings.partition_authoring_collection
    body = settings.partition_master_object
    reference = settings.partition_reference_object
    export = settings.partition_export_collection
    if authoring is None or body is None or reference is None or export is None:
        return ""
    hasher = hashlib.sha256()
    _hash_bytes(hasher, f"format:{SYNC_FORMAT_VERSION}")
    _hash_bytes(hasher, f"authoring:{authoring.name}:body:{body.name}:reference:{reference.name}")
    source = Path(bpy.path.abspath(str(getattr(cfg, "object_source_folder", "") or "")))
    vgmap_path = source / "VertexGroupMap.json"
    if vgmap_path.is_file():
        _hash_bytes(hasher, vgmap_path.read_bytes())
    all_whole = _whole_meshes(settings, include_hidden=True)
    visible_whole = set(_whole_meshes(settings, include_hidden=False))
    for item, obj in _whole_mesh_rows(settings):
        _hash_bytes(hasher, f"whole:{item.source_id}:{item.home_component}:{obj in visible_whole}")
        if obj in visible_whole or obj is body:
            _hash_object(hasher, obj)
    all_imported = _collect_imported_rows(settings, include_hidden=True)
    visible_imported = set(obj for obj, _component_id in _collect_imported_rows(settings))
    for obj, component_id in all_imported:
        _hash_bytes(hasher, f"imported:{component_id}:{obj.name}:{obj in visible_imported}")
        if obj in visible_imported:
            _hash_object(hasher, obj)
    for item in settings.partition_merge_items:
        _hash_bytes(
            hasher,
            f"merge:{item.source_component}:{item.target_component}",
        )
    for index, item in enumerate(settings.partition_passthrough_items):
        _hash_bytes(
            hasher,
            f"pass:{index}:{item.source_kind}:{item.target_component}:"
            f"{getattr(item.source_object, 'name', '')}:{getattr(item.source_collection, 'name', '')}",
        )
    all_passthrough = _collect_passthrough_rows(settings, include_hidden=True)
    visible_passthrough = set(obj for obj, _component_id in _collect_passthrough_rows(settings))
    for obj, component_id in all_passthrough:
        _hash_bytes(hasher, f"pass_obj:{component_id}:{obj in visible_passthrough}")
        if obj in visible_passthrough:
            _hash_object(hasher, obj)
    _hash_bindings(hasher, scene, cfg)
    return hasher.hexdigest()


def build_output_manifest(root, desired_names=None) -> str:
    if root is None:
        return ""
    hasher = hashlib.sha256()
    _hash_bytes(hasher, f"format:{SYNC_FORMAT_VERSION}")
    rows = []
    for obj in root.all_objects:
        if obj.type != "MESH" or obj.get(ROLE_KEY) != ROLE_OUTPUT:
            continue
        component_id = legacy._component_id_from_object(obj)
        rows.append((int(component_id) if component_id is not None else -1, obj))
    names = desired_names or {}
    for component_id, obj in sorted(
        rows,
        key=lambda row: (row[0], names.get(row[1], row[1].name).casefold()),
    ):
        _hash_bytes(hasher, f"output_component:{component_id}")
        _hash_bytes(hasher, f"output_name:{names.get(obj, obj.name)}")
        _hash_object(hasher, obj, include_visibility=False, include_name=False)
    return hasher.hexdigest()


def _normalize_names(desired_names):
    for obj, desired in desired_names.items():
        if obj.name not in bpy.data.objects:
            continue
        obj.name = desired
        obj.data.name = obj.name


def _remove_legacy_generated_outputs(exclude_root=None):
    protected = set(exclude_root.all_objects) if exclude_root is not None else set()
    for obj in list(bpy.data.objects):
        if obj in protected:
            continue
        if obj.get(ROLE_KEY) != ROLE_OUTPUT or not obj.get(GENERATED_KEY):
            continue
        if obj.name in bpy.data.objects:
            legacy._remove_object(obj)
    for collection in list(bpy.data.collections):
        if collection == exclude_root or not collection.get(PART_COLLECTION_KEY):
            continue
        if not collection.objects and not collection.children and collection.name in bpy.data.collections:
            bpy.data.collections.remove(collection)


def _sync_impl(context):
    settings, cfg, authoring, body, reference, _counts = _prepare_workflow(context)
    config, routes, palettes = _load_routes_and_palettes(context.scene, reference)
    _validate_reference(reference)
    whole_rows = _whole_mesh_rows(settings)
    whole_all = [obj for _item, obj in whole_rows]
    if body not in whole_all:
        raise PartitionError("基准身体没有登记为整体模型。")
    whole_visible = set(_whole_meshes(settings))
    imported_rows = _collect_imported_rows(settings)
    passthrough_rows = _collect_passthrough_rows(settings)
    passthrough_sources = {obj for obj, _component_id in passthrough_rows}
    overlap = passthrough_sources.intersection(whole_all)
    if overlap:
        raise PartitionError(f"整体模型 `{next(iter(overlap)).name}` 不能同时配置为原样同步。")
    merge_map = _resolve_merge_map(settings, routes, config, body)

    stage_root = None
    staged_outputs = []
    desired_names = {}
    old_zone = settings.partition_export_collection
    old_cfg_root = cfg.component_collection
    committed = False
    progress = context.window_manager
    progress_total = max(1, len(whole_all) + len(imported_rows) + len(passthrough_rows))
    progress_value = 0
    progress.progress_begin(0, progress_total)
    settings.partition_status = "正在同步整体区到分割区..."
    try:
        stage_root, components = _new_staging_root(context.scene, authoring)
        body_outputs, _body_projection, _body_weights = _split_one(
            context,
            config,
            settings,
            reference,
            body,
            routes,
            palettes,
            components,
            desired_names,
            emit=body in whole_visible,
            merge_map=merge_map,
        )
        staged_outputs.extend(body_outputs)
        progress_value += 1
        progress.progress_update(progress_value)
        for source_obj in whole_all:
            if source_obj is body:
                continue
            if source_obj not in whole_visible:
                progress_value += 1
                progress.progress_update(progress_value)
                continue
            outputs, _projection, _weights = _split_one(
                context,
                config,
                settings,
                body,
                source_obj,
                routes,
                palettes,
                components,
                desired_names,
                merge_map={},
            )
            staged_outputs.extend(outputs)
            progress_value += 1
            progress.progress_update(progress_value)
        for source_obj, component_id in imported_rows + passthrough_rows:
            staged_outputs.append(
                _copy_passthrough(source_obj, component_id, components[component_id], desired_names)
            )
            progress_value += 1
            progress.progress_update(progress_value)
        if not staged_outputs:
            raise PartitionError("分割区没有生成任何可导出 Mesh。")
        if any(collection.get(PART_COLLECTION_KEY) for collection in _walk_collections(stage_root)):
            raise PartitionError("新分割区意外生成了 part.N Collection。")

        desired_root_name = f"{authoring.name} [分割区]"
        settings.partition_export_collection = stage_root
        cfg.component_collection = stage_root
        manifest = build_sync_manifest(context.scene)
        if not manifest:
            raise PartitionError("无法生成整体区同步指纹。")
        staged_output_manifest = build_output_manifest(stage_root, desired_names)
        if not staged_output_manifest:
            raise PartitionError("无法生成分割区校验指纹。")
        body_output_map = {
            int(obj.get("velo_component_id")): obj
            for obj in body_outputs
            if obj.get("velo_component_id") is not None
        }
        stage_root[SYNC_MANIFEST_KEY] = manifest
        stage_root[SYNC_VERSION_KEY] = SYNC_FORMAT_VERSION
        committed = True
        if old_zone is not None and old_zone != stage_root and old_zone.get(EXPORT_ZONE_KEY):
            _remove_collection_tree(old_zone)
        _remove_legacy_generated_outputs(stage_root)
        stage_root.name = desired_root_name
        _normalize_names(desired_names)
        _refresh_component_merge_objects(settings, body, body_output_map)
        output_manifest = build_output_manifest(stage_root)
        stage_root[OUTPUT_MANIFEST_KEY] = output_manifest
        settings.partition_sync_manifest = manifest
        settings.partition_output_manifest = output_manifest
        apply_preview_mode(context.scene)
        settings.partition_status = (
            f"分割区同步完成，可以导出 Mod。整体模型 {len(whole_all)} 个，"
            f"原始对象 {len(imported_rows)} 个，原样同步 {len(passthrough_rows)} 个，"
            f"输出 {len(staged_outputs)} 个。"
        )
        progress.progress_end()
        return staged_outputs
    except Exception:
        if not committed:
            if settings.partition_export_collection == stage_root:
                settings.partition_export_collection = old_zone
            if cfg.component_collection == stage_root:
                cfg.component_collection = old_cfg_root
        if not committed and stage_root is not None and stage_root.name in bpy.data.collections:
            _remove_collection_tree(stage_root)
        progress.progress_end()
        raise


def apply_preview_mode(scene):
    settings = getattr(scene, "velo_tools", None)
    if settings is None:
        return
    authoring = settings.partition_authoring_collection
    export = settings.partition_export_collection
    show_export = settings.partition_preview_mode == "EXPORT"
    if authoring is not None:
        for obj in authoring.all_objects:
            if show_export:
                if PREVIEW_HIDE_KEY not in obj:
                    try:
                        obj[PREVIEW_HIDE_KEY] = bool(obj.hide_get())
                    except RuntimeError:
                        obj[PREVIEW_HIDE_KEY] = bool(obj.hide_viewport)
                try:
                    obj.hide_set(True)
                except RuntimeError:
                    pass
            elif PREVIEW_HIDE_KEY in obj:
                previous = bool(obj[PREVIEW_HIDE_KEY])
                try:
                    obj.hide_set(previous)
                except RuntimeError:
                    pass
                del obj[PREVIEW_HIDE_KEY]
    if export is not None:
        for obj in export.all_objects:
            if obj.get(ROLE_KEY) != ROLE_OUTPUT:
                continue
            try:
                obj.hide_set(not show_export)
            except RuntimeError:
                pass


def output_names_for_source(source_obj, cfg=None):
    if source_obj is None:
        return []
    source_id = str(source_obj.get(SOURCE_ID_KEY, "") or "")
    scene = bpy.context.scene
    settings = getattr(scene, "velo_tools", None)
    if not source_id and settings is not None and settings.partition_authoring_collection is not None:
        wanted = _strip_component_name(source_obj.name).casefold()
        matches = [
            obj
            for obj in settings.partition_authoring_collection.all_objects
            if obj.type == "MESH" and _strip_component_name(obj.name).casefold() == wanted
        ]
        if len(matches) == 1:
            source_id = str(matches[0].get(SOURCE_ID_KEY, "") or "")
    if not source_id:
        return []
    root = getattr(settings, "partition_export_collection", None) if settings else None
    if root is None and cfg is not None:
        root = getattr(cfg, "component_collection", None)
    if root is None or not root.get(EXPORT_ZONE_KEY):
        return []
    names = [
        obj.name
        for obj in root.all_objects
        if obj.type == "MESH"
        and obj.get(ROLE_KEY) == ROLE_OUTPUT
        and str(obj.get(SOURCE_ID_KEY, "") or "") == source_id
    ]
    return sorted(set(names), key=str.casefold)


def _object_in_collection_tree(root, obj):
    return root is not None and obj in set(root.all_objects)


def _omitted_by_visibility(scene, obj):
    settings = getattr(scene, "velo_tools", None)
    root = getattr(settings, "partition_authoring_collection", None) if settings else None
    return obj is not None and _collection_contains(root, obj) and not _visible_for_sync(settings, obj)


def _validate_export_bindings(scene, cfg, export):
    if bool(getattr(cfg, "use_ini_toggles", False)):
        for var in cfg.ini_toggles.vars:
            for state in var.states:
                for item in state.objects:
                    obj = item.object
                    if obj is None:
                        return f"INI 开关 `{var.name}` / 状态 `{state.name}` 存在空对象绑定。"
                    if output_names_for_source(obj, cfg):
                        continue
                    if _omitted_by_visibility(scene, obj):
                        continue
                    if _object_in_collection_tree(export, obj) and obj.get(ROLE_KEY) == ROLE_OUTPUT:
                        continue
                    return f"INI 开关对象 `{obj.name}` 没有对应的分割区输出，请调整后重新同步。"
    crossib = getattr(scene, "crossib_settings", None)
    if crossib is not None and bool(getattr(crossib, "enabled", False)):
        for index, mapping in enumerate(crossib.mappings):
            if mapping.source_kind == 'COLLECTION':
                sources = list(_iter_collection_meshes(mapping.source_collection))
            else:
                sources = [mapping.source_object] if mapping.source_object is not None else []
            if not sources:
                continue
            if all(_omitted_by_visibility(scene, obj) for obj in sources):
                continue
            valid = [
                obj
                for obj in sources
                if output_names_for_source(obj, cfg)
                or (_object_in_collection_tree(export, obj) and obj.get(ROLE_KEY) == ROLE_OUTPUT)
            ]
            if not valid:
                return f"CrossIB 第 {index + 1} 行没有对应的分割区来源，请调整后重新同步。"
    return None


def validate_export_state(scene):
    settings = getattr(scene, "velo_tools", None)
    cfg = getattr(scene, "VTEF_settings", None)
    if settings is None or cfg is None or settings.partition_export_collection is None:
        return None
    export = settings.partition_export_collection
    if getattr(cfg, "mod_skeleton_type", None) != "MERGED":
        return "分割工作流要求骨架模式为 MERGED。"
    if cfg.component_collection != export:
        return "EFMI 组件集合不是当前工作分割区，请先同步。"
    if not export.get(EXPORT_ZONE_KEY):
        return "当前工作分割区缺少 Velo 同步标记，请重新同步。"
    stored = str(settings.partition_sync_manifest or "")
    if not stored or str(export.get(SYNC_MANIFEST_KEY, "") or "") != stored:
        return "工作分割区同步记录无效，请重新同步。"
    try:
        current = build_sync_manifest(scene)
    except Exception as exc:
        return f"无法校验整体区同步状态：{exc}"
    if current != stored:
        return "整体区或分割规则已经修改，请先点击“同步到分割区”。"
    stored_output = str(settings.partition_output_manifest or "")
    if not stored_output or str(export.get(OUTPUT_MANIFEST_KEY, "") or "") != stored_output:
        return "分割区校验记录无效，请重新同步。"
    if build_output_manifest(export) != stored_output:
        return "分割区已被手工修改，请重新同步后再导出。"
    outputs = [obj for obj in export.all_objects if obj.get(ROLE_KEY) == ROLE_OUTPUT]
    if not outputs:
        return "工作分割区没有可导出对象，请重新同步。"
    if any(collection.get(PART_COLLECTION_KEY) for collection in _walk_collections(export)):
        return "工作分割区仍包含旧 part.N，请重新同步。"
    binding_error = _validate_export_bindings(scene, cfg, export)
    if binding_error:
        return binding_error
    return None


class VELO_OT_partition_passthrough_add(bpy.types.Operator):
    bl_idname = "velo.partition_passthrough_add"
    bl_label = "添加原样同步"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.velo_tools.partition_passthrough_items.add()
        return {'FINISHED'}


class VELO_OT_partition_merge_add(bpy.types.Operator):
    bl_idname = "velo.partition_merge_add"
    bl_label = "添加分块归并"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.velo_tools.partition_merge_items.add()
        return {'FINISHED'}


class VELO_OT_partition_merge_remove(bpy.types.Operator):
    bl_idname = "velo.partition_merge_remove"
    bl_label = "删除分块归并"
    bl_options = {'REGISTER', 'UNDO'}

    mapping_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        items = context.scene.velo_tools.partition_merge_items
        if 0 <= self.mapping_index < len(items):
            items.remove(self.mapping_index)
        return {'FINISHED'}


class VELO_OT_partition_passthrough_remove(bpy.types.Operator):
    bl_idname = "velo.partition_passthrough_remove"
    bl_label = "删除原样同步"
    bl_options = {'REGISTER', 'UNDO'}

    item_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        items = context.scene.velo_tools.partition_passthrough_items
        if 0 <= self.item_index < len(items):
            items.remove(self.item_index)
        return {'FINISHED'}


class VELO_OT_partition_create_standard_body(bpy.types.Operator):
    bl_idname = "velo.partition_create_standard_body"
    bl_label = "创建基准身体"
    bl_description = "把活动 Mesh 登记为基准身体，并从手工合并体建立分区参考"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.velo_tools
        old_reference = settings.partition_reference_object
        new_reference = None
        try:
            _settings_obj, cfg = _ensure_merged(context.scene)
            _source_path(cfg)
            authoring = _authoring_root(settings, cfg)
            body = context.active_object
            if body is None or body.type != "MESH":
                raise PartitionError("请把要作为基准身体的 Mesh 设为活动对象。")
            _register_whole_mesh(
                settings,
                authoring,
                body,
                int(settings.partition_register_component),
            )
            settings.partition_master_object = body
            settings.partition_registry_migrated = True
            _settings_obj, _cfg, _authoring, _body, new_reference, counts = _prepare_workflow(
                context,
                rebuild_reference=True,
            )
            summary = ", ".join(f"C{key}={value}" for key, value in sorted(counts.items()))
            settings.partition_status = f"基准身体创建完成（{summary}），请设置规则后同步到分割区。"
            self.report({'INFO'}, "基准身体创建完成。")
            return {'FINISHED'}
        except Exception as exc:
            if (
                new_reference is not None
                and new_reference is not old_reference
                and new_reference.name in bpy.data.objects
            ):
                legacy._remove_object(new_reference)
            settings.partition_reference_object = old_reference
            settings.partition_status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class VELO_OT_partition_add_whole_meshes(bpy.types.Operator):
    bl_idname = "velo.partition_add_whole_meshes"
    bl_label = "加入整体模型"
    bl_description = "把选中的 Mesh 登记为整体模型，并移到选定 Cx"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.velo_tools
        try:
            _settings_obj, cfg = _ensure_merged(context.scene)
            authoring = _authoring_root(settings, cfg)
            if settings.partition_master_object is None:
                raise PartitionError("请先创建基准身体。")
            selected = [obj for obj in context.selected_objects if obj.type == "MESH"]
            if not selected:
                raise PartitionError("请至少选择一个身体或衣服 Mesh。")
            added = 0
            for obj in selected:
                _register_whole_mesh(
                    settings,
                    authoring,
                    obj,
                    int(settings.partition_register_component),
                )
                added += 1
            settings.partition_status = f"已加入 {added} 个整体模型，需要同步到分割区。"
            self.report({'INFO'}, f"已加入 {added} 个整体模型。")
            return {'FINISHED'}
        except Exception as exc:
            settings.partition_status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class VELO_OT_partition_initialize_zones(bpy.types.Operator):
    bl_idname = "velo.partition_initialize_zones"
    bl_label = "初始化并生成工作分割区"
    bl_description = "兼容旧工程：迁移整体模型、重建参考体并执行第一次同步"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.velo_tools
        try:
            _prepare_workflow(context, rebuild_reference=settings.partition_reference_object is None)
            _sync_impl(context)
            self.report({'INFO'}, "旧工程迁移和首次同步完成，可以导出 Mod。")
            return {'FINISHED'}
        except Exception as exc:
            settings.partition_status = str(exc)
            object_name = getattr(exc, "object_name", "")
            problem_object = bpy.data.objects.get(object_name) if object_name else None
            if problem_object is not None and getattr(exc, "vertex_ids", None):
                legacy._select_vertices(context, problem_object, exc.vertex_ids)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class VELO_OT_partition_sync_zones(bpy.types.Operator):
    bl_idname = "velo.partition_sync_zones"
    bl_label = "同步到分割区"
    bl_description = "把整体区完整同步并拆分成新的原子导出快照"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.velo_tools
        try:
            outputs = _sync_impl(context)
            self.report({'INFO'}, "分割区同步完成，可以导出 Mod。")
            return {'FINISHED'}
        except Exception as exc:
            settings.partition_status = str(exc)
            object_name = getattr(exc, "object_name", "")
            problem_object = bpy.data.objects.get(object_name) if object_name else None
            if problem_object is not None and getattr(exc, "vertex_ids", None):
                legacy._select_vertices(context, problem_object, exc.vertex_ids)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


_CLASSES = (
    VELO_OT_partition_merge_add,
    VELO_OT_partition_merge_remove,
    VELO_OT_partition_passthrough_add,
    VELO_OT_partition_passthrough_remove,
    VELO_OT_partition_create_standard_body,
    VELO_OT_partition_add_whole_meshes,
    VELO_OT_partition_initialize_zones,
    VELO_OT_partition_sync_zones,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
