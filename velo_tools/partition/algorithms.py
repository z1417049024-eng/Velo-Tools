"""Projection and weight rules for EFMI Component partitioning."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from mathutils.bvhtree import BVHTree

from .constants import COMPONENT_ATTRIBUTE


class PartitionError(RuntimeError):
    def __init__(self, message: str, *, vertex_ids=None):
        super().__init__(message)
        self.vertex_ids = list(vertex_ids or [])


@dataclass
class ProjectionResult:
    labels: list[int]
    confidence: list[float]
    ambiguous: list[bool]
    distance_max: float
    smoothed_faces: int


@dataclass
class WeightResult:
    weights: list[dict[int, float]]
    vertex_components: list[frozenset[int]]
    seam_vertices: int
    warning_vertices: list[int]
    minimum_retained_ratio: float


def _component_face_values(reference) -> list[int]:
    attribute = reference.data.attributes.get(COMPONENT_ATTRIBUTE)
    if attribute is None or attribute.domain != "FACE" or attribute.data_type != "INT":
        raise PartitionError(
            f"分区参考体 `{reference.name}` 缺少 FACE/INT 属性 `{COMPONENT_ATTRIBUTE}`。"
        )
    return [int(item.value) for item in attribute.data]


def _build_component_trees(reference) -> dict[int, BVHTree]:
    mesh = reference.data
    face_values = _component_face_values(reference)
    mesh.calc_loop_triangles()
    world_vertices = [reference.matrix_world @ vertex.co for vertex in mesh.vertices]
    triangles_by_component: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for triangle in mesh.loop_triangles:
        component_id = face_values[triangle.polygon_index]
        triangles_by_component[component_id].append(tuple(triangle.vertices))

    trees = {
        component_id: BVHTree.FromPolygons(
            world_vertices,
            triangles,
            all_triangles=True,
        )
        for component_id, triangles in triangles_by_component.items()
        if triangles
    }
    if len(trees) < 2:
        raise PartitionError("分区参考体必须至少包含两个有效 Component。")
    return trees


def _nearest_component(point, trees: dict[int, BVHTree]) -> tuple[int, float, float]:
    distances = []
    for component_id, tree in trees.items():
        hit = tree.find_nearest(point)
        if hit[0] is not None:
            distances.append((float(hit[3]), component_id))
    if not distances:
        raise PartitionError("无法在分区参考体上找到最近面。")
    distances.sort()
    nearest_distance, nearest_component = distances[0]
    second_distance = distances[1][0] if len(distances) > 1 else nearest_distance
    return nearest_component, nearest_distance, second_distance


def _face_adjacency(mesh) -> list[set[int]]:
    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    for polygon in mesh.polygons:
        for edge in polygon.edge_keys:
            edge_faces[tuple(sorted(edge))].append(polygon.index)

    adjacency = [set() for _ in mesh.polygons]
    for faces in edge_faces.values():
        if len(faces) != 2:
            continue
        left, right = faces
        adjacency[left].add(right)
        adjacency[right].add(left)
    return adjacency


def project_component_faces(reference, target) -> ProjectionResult:
    """Project discrete Component identity onto target polygons in world space."""
    trees = _build_component_trees(reference)
    labels: list[int] = []
    confidence: list[float] = []
    ambiguous: list[bool] = []
    maximum_distance = 0.0

    for polygon in target.data.polygons:
        center_component, center_distance, second_distance = _nearest_component(
            target.matrix_world @ polygon.center,
            trees,
        )
        maximum_distance = max(maximum_distance, center_distance)
        scores = {component_id: 0 for component_id in trees}
        scores[center_component] += 2
        for vertex_id in polygon.vertices:
            component_id, _distance, _second = _nearest_component(
                target.matrix_world @ target.data.vertices[vertex_id].co,
                trees,
            )
            scores[component_id] += 1

        ordered = sorted(
            scores.items(),
            key=lambda item: (item[1], item[0] == center_component, -item[0]),
            reverse=True,
        )
        winner, winner_score = ordered[0]
        runner_score = ordered[1][1]
        vote_total = max(1, sum(scores.values()))
        vote_confidence = max(0.0, min(1.0, (winner_score - runner_score) / vote_total))
        if second_distance <= 1e-12:
            distance_confidence = 1.0
        else:
            distance_confidence = max(
                0.0,
                min(1.0, (second_distance - center_distance) / second_distance),
            )
        labels.append(int(winner))
        confidence.append(float(max(vote_confidence, distance_confidence)))
        ambiguous.append(bool(winner_score - runner_score <= 1))

    adjacency = _face_adjacency(target.data)
    smoothed = 0
    for _iteration in range(4):
        updates = {}
        for face_id, is_ambiguous in enumerate(ambiguous):
            if not is_ambiguous or not adjacency[face_id]:
                continue
            neighbor_labels = {labels[item] for item in adjacency[face_id]}
            if len(neighbor_labels) == 1:
                new_label = next(iter(neighbor_labels))
                if new_label != labels[face_id]:
                    updates[face_id] = new_label
        if not updates:
            break
        for face_id, new_label in updates.items():
            labels[face_id] = new_label
        smoothed += len(updates)

    return ProjectionResult(
        labels=labels,
        confidence=confidence,
        ambiguous=ambiguous,
        distance_max=maximum_distance,
        smoothed_faces=smoothed,
    )


def prepare_component_weights(
    target,
    labels: list[int],
    palettes: dict[int, set[int]],
    *,
    warning_loss: float = 0.05,
    maximum_loss: float = 0.10,
) -> WeightResult:
    """Return palette-safe weights keyed by original target vertex index."""
    if len(labels) != len(target.data.polygons):
        raise PartitionError("分区标签数量与目标面数量不一致。")

    vertex_components: list[set[int]] = [set() for _ in target.data.vertices]
    for polygon in target.data.polygons:
        component_id = int(labels[polygon.index])
        if component_id not in palettes:
            raise PartitionError(f"Component {component_id} 在 VertexGroupMap.json 中不存在。")
        for vertex_id in polygon.vertices:
            vertex_components[vertex_id].add(component_id)

    loose_vertices = [index for index, values in enumerate(vertex_components) if not values]
    if loose_vertices:
        preview = ", ".join(str(item) for item in loose_vertices[:10])
        raise PartitionError(
            f"目标包含未被任何面使用的顶点：{preview}。",
            vertex_ids=loose_vertices,
        )

    group_names = {group.index: group.name for group in target.vertex_groups}
    prepared: list[dict[int, float]] = []
    warnings: list[int] = []
    failures: list[tuple[int, float]] = []
    minimum_retained = 1.0

    for vertex in target.data.vertices:
        component_ids = vertex_components[vertex.index]
        allowed = set.intersection(*(palettes[item] for item in component_ids))
        if not allowed:
            joined = ", ".join(f"C{item}" for item in sorted(component_ids))
            raise PartitionError(
                f"顶点 {vertex.index} 的相邻 Component ({joined}) 没有共同骨骼 palette。"
            )

        total_weight = 0.0
        kept: dict[int, float] = {}
        for assignment in vertex.groups:
            weight = float(assignment.weight)
            if weight <= 0.0:
                continue
            total_weight += weight
            try:
                global_group = int(group_names[assignment.group])
            except (KeyError, TypeError, ValueError):
                continue
            if global_group in allowed:
                kept[global_group] = kept.get(global_group, 0.0) + weight

        kept_weight = sum(kept.values())
        if total_weight <= 1e-12 or kept_weight <= 1e-12:
            raise PartitionError(
                f"顶点 {vertex.index} 合法化后没有可用权重。",
                vertex_ids=[vertex.index],
            )
        retained_ratio = kept_weight / total_weight
        minimum_retained = min(minimum_retained, retained_ratio)
        loss = max(0.0, 1.0 - retained_ratio)
        if loss > maximum_loss + 1e-7:
            failures.append((vertex.index, loss))
        elif loss >= warning_loss:
            warnings.append(vertex.index)

        prepared.append(
            {group_id: weight / kept_weight for group_id, weight in kept.items()}
        )

    if failures:
        preview = ", ".join(
            f"{vertex_id} ({loss * 100:.1f}%)" for vertex_id, loss in failures[:10]
        )
        raise PartitionError(
            f"以下顶点需要丢弃超过 10% 权重：{preview}。",
            vertex_ids=[vertex_id for vertex_id, _loss in failures],
        )

    frozen_components = [frozenset(values) for values in vertex_components]
    return WeightResult(
        weights=prepared,
        vertex_components=frozen_components,
        seam_vertices=sum(len(values) > 1 for values in frozen_components),
        warning_vertices=warnings,
        minimum_retained_ratio=minimum_retained,
    )


def component_counts(values: Iterable[int]) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for value in values:
        counts[int(value)] += 1
    return dict(sorted(counts.items()))
