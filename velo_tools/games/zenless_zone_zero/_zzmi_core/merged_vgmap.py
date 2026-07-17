"""Authoring-side unified vertex-group maps for ZZZ DBMT workspaces."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import struct
from datetime import datetime
from pathlib import Path

import bpy

from .config.main_config import GlobalConfig
from .utils.config_utils import ConfigUtils


FILENAME = "VertexGroupMap.json"
FORMAT_VERSION = 1
_BONE_STRIDE = 48


class VertexGroupMapError(ValueError):
    pass


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VertexGroupMapError(f"缺少 {path.name}: {path}") from exc
    except Exception as exc:
        raise VertexGroupMapError(f"读取 {path.name} 失败: {exc}") from exc


def _workspace_components(workspace: Path) -> list[dict]:
    raw = _load_json(workspace / "Config.Json")
    if not isinstance(raw, list):
        raise VertexGroupMapError("Config.Json 格式无效，预期为 DrawIB 列表。")

    result = []
    for item in raw:
        draw_ib = str((item or {}).get("DrawIB", "")).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{8}", draw_ib):
            continue
        result.append({"draw_ib": draw_ib, "alias": str((item or {}).get("Alias", ""))})
    if not result:
        raise VertexGroupMapError("Config.Json 中没有有效的 DrawIB。")
    return result


def _type_folder(workspace: Path, draw_ib: str, import_config: dict) -> Path:
    game_type = str(import_config.get(draw_ib, "")).strip()
    draw_ib_folder = workspace / draw_ib
    candidates = sorted(path for path in draw_ib_folder.glob("TYPE_*") if path.is_dir())
    if not candidates:
        raise VertexGroupMapError(f"{draw_ib}: 找不到 TYPE_* 数据目录。")
    try:
        selected = ConfigUtils._choose_import_folder(
            draw_ib=draw_ib,
            import_drawib_folder_path=str(draw_ib_folder),
            type_folder_path_list=[str(path) for path in candidates],
            preferred_game_type=game_type,
        )
    except Exception as exc:
        raise VertexGroupMapError(f"{draw_ib}: 无法选择有效的 TYPE_* 数据目录: {exc}") from exc
    return Path(selected)


def _format_decoder(dxgi_format: str):
    match = re.fullmatch(r"(?:DXGI_FORMAT_)?((?:R\d+)(?:G\d+)?(?:B\d+)?(?:A\d+)?)_(UINT|UNORM)", dxgi_format)
    if not match:
        raise VertexGroupMapError(f"不支持的 BLENDINDICES 格式: {dxgi_format}")
    widths = [int(value) for value in re.findall(r"[RGBA](\d+)", match.group(1))]
    if not widths or len(set(widths)) != 1 or widths[0] not in (8, 16, 32):
        raise VertexGroupMapError(f"不支持的 BLENDINDICES 格式: {dxgi_format}")
    code = {8: "B", 16: "H", 32: "I"}[widths[0]]
    return "<" + code * len(widths), widths[0] // 8 * len(widths)


def _blend_element(fmt_text: str):
    stride_match = re.search(r"^stride:\s*(\d+)", fmt_text, re.MULTILINE)
    if stride_match is None:
        raise VertexGroupMapError("fmt 缺少 stride。")

    for block in re.split(r"(?=^element\[\d+\]:)", fmt_text, flags=re.MULTILINE)[1:]:
        semantic = re.search(r"^\s*SemanticName:\s*(\S+)", block, re.MULTILINE)
        if semantic is None or semantic.group(1) != "BLENDINDICES":
            continue
        fmt = re.search(r"^\s*Format:\s*(\S+)", block, re.MULTILINE)
        offset = re.search(r"^\s*AlignedByteOffset:\s*(\d+)", block, re.MULTILINE)
        if fmt is None or offset is None:
            break
        return int(stride_match.group(1)), int(offset.group(1)), fmt.group(1)
    return None


def _vertex_group_count(type_folder: Path) -> int:
    max_index = -1
    found = False
    for fmt_path in sorted(type_folder.glob("*.fmt")):
        element = _blend_element(fmt_path.read_text(encoding="utf-8"))
        if element is None:
            continue
        stride, offset, dxgi_format = element
        decoder, byte_width = _format_decoder(dxgi_format)
        vb_path = fmt_path.with_suffix(".vb")
        if not vb_path.is_file():
            raise VertexGroupMapError(f"缺少 {vb_path.name}。")
        data = vb_path.read_bytes()
        if stride <= 0 or len(data) % stride:
            raise VertexGroupMapError(f"{vb_path.name}: buffer 大小不能被 stride {stride} 整除。")
        if offset + byte_width > stride:
            raise VertexGroupMapError(f"{fmt_path.name}: BLENDINDICES 超出 stride。")
        found = True
        for base in range(0, len(data), stride):
            values = struct.unpack_from(decoder, data, base + offset)
            max_index = max(max_index, *values)
    if not found:
        return 0
    if max_index > 4096:
        raise VertexGroupMapError(f"BLENDINDICES max={max_index} 超过安全上限 4096。")
    return max_index + 1


def _dedup_target(frame_analysis: Path, dump_path: Path) -> Path:
    if dump_path.is_file() and dump_path.stat().st_size:
        return dump_path
    log_path = frame_analysis / "log.txt"
    if not log_path.is_file():
        return dump_path
    marker = str(dump_path)
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if marker in line and " -> " in line:
            candidate = Path(line.rsplit(" -> ", 1)[1].strip())
            if candidate.is_file() and candidate.stat().st_size:
                return candidate
    return dump_path


def _skeleton_dump(frame_analysis: Path, position_hash: str, group_count: int):
    if group_count <= 0:
        return "", b"", 0

    draw_ids = set()
    pattern = str(frame_analysis / f"*-vb0={position_hash}-*.buf")
    for vb_path in glob.glob(pattern):
        match = re.match(r"(\d+)-", os.path.basename(vb_path))
        if match:
            draw_ids.add(match.group(1))

    candidates = []
    for draw_id in sorted(draw_ids):
        for raw_path in frame_analysis.glob(f"{draw_id}-vs-t0=*.buf"):
            path = _dedup_target(frame_analysis, raw_path)
            if not path.is_file():
                continue
            data = path.read_bytes()
            if len(data) >= group_count * _BONE_STRIDE and len(data) % _BONE_STRIDE == 0:
                candidates.append((raw_path, data))

    if not candidates:
        raise VertexGroupMapError(
            f"position hash {position_hash}: 找不到包含 {group_count} 个骨骼的 VS t0 skeleton buffer。"
        )
    candidates.sort(key=lambda item: (len(item[1]), item[0].name))
    raw_path, data = candidates[0]
    hash_match = re.search(r"-vs-t0=([0-9a-fA-F]{8})-", raw_path.name)
    resource_hash = hash_match.group(1).lower() if hash_match else ""
    return resource_hash, data[:group_count * _BONE_STRIDE], _BONE_STRIDE


def build_map(workspace_path, frame_analysis_path) -> Path:
    workspace = Path(workspace_path)
    frame_analysis = Path(frame_analysis_path)
    if not (frame_analysis / "log.txt").is_file():
        raise VertexGroupMapError(f"FrameAnalysis 缺少 log.txt: {frame_analysis}")

    components = _workspace_components(workspace)
    import_config = _load_json(workspace / "Import.json")
    if not isinstance(import_config, dict):
        raise VertexGroupMapError("Import.json 格式无效。")

    signature_atlas = {}
    next_global_id = 0
    output_components = []

    for component in components:
        draw_ib = component["draw_ib"]
        type_folder = _type_folder(workspace, draw_ib, import_config)
        tmp = _load_json(type_folder / "tmp.json")
        position_hash = str((tmp.get("CategoryHash") or {}).get("Position", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{8}", position_hash):
            raise VertexGroupMapError(f"{draw_ib}: tmp.json 缺少有效的 Position CategoryHash。")

        group_count = _vertex_group_count(type_folder)
        skeleton_hash, skeleton_data, skeleton_stride = _skeleton_dump(
            frame_analysis, position_hash, group_count
        )
        vg_map = {}
        signature_occurrences = {}
        for local_id in range(group_count):
            start = local_id * skeleton_stride
            bone_data = skeleton_data[start:start + skeleton_stride]
            occurrence = signature_occurrences.get(bone_data, 0)
            global_ids = signature_atlas.setdefault(bone_data, [])
            if occurrence < len(global_ids):
                global_id = global_ids[occurrence]
            else:
                global_id = next_global_id
                next_global_id += 1
                global_ids.append(global_id)
            signature_occurrences[bone_data] = occurrence + 1
            vg_map[str(local_id)] = global_id

        output_components.append({
            "draw_ib": draw_ib,
            "alias": component["alias"],
            "work_game_type": str(tmp.get("WorkGameType", "")),
            "position_hash": position_hash,
            "skeleton_hash": skeleton_hash,
            "skeleton_stride": skeleton_stride,
            "vg_count": group_count,
            "vg_map": vg_map,
        })

    output = {
        "format_version": FORMAT_VERSION,
        "game": "ZZZ",
        "source_frame_analysis": str(frame_analysis),
        "components": output_components,
    }
    path = workspace / FILENAME
    if path.is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, workspace / f"{FILENAME}.backup-{stamp}")
    path.write_text(json.dumps(output, indent=4, ensure_ascii=False), encoding="utf-8")
    return path


def load_map(workspace_path) -> dict:
    raw = _load_json(Path(workspace_path) / FILENAME)
    if raw.get("format_version") != FORMAT_VERSION or raw.get("game") != "ZZZ":
        raise VertexGroupMapError(f"{FILENAME} 版本或游戏类型不受支持。")
    components = raw.get("components")
    if not isinstance(components, list):
        raise VertexGroupMapError(f"{FILENAME} 缺少 components。")
    return raw


def entry_for_draw_ib(vertex_group_map: dict, draw_ib: str) -> dict:
    for entry in vertex_group_map.get("components", []):
        if str(entry.get("draw_ib", "")).lower() == draw_ib.lower():
            return entry
    raise VertexGroupMapError(f"{FILENAME} 中没有 DrawIB {draw_ib}。")


def global_palette(vertex_group_map: dict) -> list[int]:
    result = set()
    for entry in vertex_group_map.get("components", []):
        for value in (entry.get("vg_map") or {}).values():
            result.add(int(value))
    return sorted(result)


def apply_merged_vertex_group_names(obj, entry: dict, palette: list[int]) -> None:
    local_to_global = {int(key): int(value) for key, value in (entry.get("vg_map") or {}).items()}
    if not local_to_global:
        return

    grouped_names = {}
    for vg in obj.vertex_groups:
        if vg.name.isdigit() and int(vg.name) in local_to_global:
            grouped_names.setdefault(local_to_global[int(vg.name)], []).append(vg.name)

    for names in grouped_names.values():
        canonical = obj.vertex_groups.get(names[0])
        for duplicate_name in names[1:]:
            duplicate = obj.vertex_groups.get(duplicate_name)
            if canonical is None or duplicate is None:
                continue
            duplicate_index = duplicate.index
            for vertex in obj.data.vertices:
                for group in vertex.groups:
                    if group.group == duplicate_index and group.weight > 0:
                        canonical.add([vertex.index], group.weight, "ADD")
                        break
            obj.vertex_groups.remove(duplicate)

    prefix = "__vtzz_global_"
    for vg in obj.vertex_groups:
        if vg.name.isdigit():
            global_id = local_to_global.get(int(vg.name))
            if global_id is not None:
                vg.name = prefix + str(global_id)
    for vg in obj.vertex_groups:
        if vg.name.startswith(prefix):
            vg.name = vg.name[len(prefix):]

    existing = {vg.name for vg in obj.vertex_groups}
    for global_id in palette:
        name = str(global_id)
        if name not in existing:
            obj.vertex_groups.new(name=name)
            existing.add(name)

    obj["velo_zzz_draw_ib"] = str(entry.get("draw_ib", ""))
    obj["velo_zzz_skeleton_mode"] = "MERGED"


def export_group_index_remap(obj, entry: dict) -> dict[int, int]:
    inverse = {}
    for local_id, global_id in (entry.get("vg_map") or {}).items():
        local_id = int(local_id)
        global_id = int(global_id)
        previous = inverse.get(global_id)
        if previous is not None and previous != local_id:
            raise VertexGroupMapError(
                f"DrawIB {entry.get('draw_ib')} 的 global 顶点组 {global_id} 同时映射到 "
                f"local {previous} 和 {local_id}。请重新生成 {FILENAME}。"
            )
        inverse[global_id] = local_id

    weighted_indices = {
        group.group
        for vertex in obj.data.vertices
        for group in vertex.groups
        if group.weight > 1e-6
    }
    remap = {}
    invalid = []
    for vg in obj.vertex_groups:
        name = (vg.name or "").strip()
        local_id = inverse.get(int(name)) if name.isdigit() else None
        if local_id is not None:
            remap[vg.index] = local_id
        elif vg.index in weighted_indices:
            invalid.append(name or f"index {vg.index}")
    if invalid:
        preview = ", ".join(invalid[:12])
        raise VertexGroupMapError(
            f"对象 {obj.name} 存在无法回译到 DrawIB {entry.get('draw_ib')} 本地骨表的带权顶点组: {preview}"
        )
    return remap


class VTZZBuildMergedVertexGroupMap(bpy.types.Operator):
    bl_idname = "vtzz.build_merged_vertex_group_map"
    bl_label = "生成 ZZZ VertexGroupMap.json"
    bl_description = "从当前 DBMT workspace 和 FrameAnalysis 的 skeleton buffer 生成 Merged 顶点组映射"

    def execute(self, context):
        settings = context.scene.VTZZ_properties_import_model
        frame_analysis = settings.merged_frame_analysis.strip()
        if not frame_analysis:
            frame_analysis = GlobalConfig.path_latest_frame_analysis_folder()
        try:
            path = build_map(GlobalConfig.path_workspace_folder(), frame_analysis)
        except VertexGroupMapError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"已生成 {path}")
        return {"FINISHED"}
