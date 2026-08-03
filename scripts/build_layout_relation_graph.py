#!/usr/bin/env python3
"""Build a deterministic layout-relation graph from validated UI IR files."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from build_native_architecture_plan import layout_relation_plan, node_index


SCHEMA_VERSION = "layout-relation-graph-1.0"
TOLERANCE_PT = 1.5


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def rect(node: dict[str, Any]) -> dict[str, float] | None:
    value = (node.get("layout") or {}).get("rect")
    if not isinstance(value, dict):
        return None
    values = {key: number(value.get(key)) for key in ("x", "y", "width", "height")}
    if any(item is None for item in values.values()):
        return None
    return {key: float(item) for key, item in values.items() if item is not None}


def close(left: float, right: float, tolerance: float = TOLERANCE_PT) -> bool:
    return abs(left - right) <= max(tolerance, max(abs(left), abs(right)) * 0.02)


def intersects(left: dict[str, float], right: dict[str, float]) -> bool:
    return not (
        left["x"] + left["width"] <= right["x"]
        or right["x"] + right["width"] <= left["x"]
        or left["y"] + left["height"] <= right["y"]
        or right["y"] + right["height"] <= left["y"]
    )


def append_relation(
    relations: list[dict[str, Any]],
    relation_id: str,
    kind: str,
    node_ids: list[str],
    **payload: Any,
) -> None:
    relations.append({"id": relation_id, "kind": kind, "nodeIds": node_ids, **payload})


def build_screen_graph(screen: dict[str, Any]) -> dict[str, Any]:
    screen_id = str(screen.get("id") or "screen")
    nodes, children = node_index(screen)
    container_relations = layout_relation_plan(nodes, children)
    relations: list[dict[str, Any]] = []

    for node_id, node in nodes.items():
        parent_id = str(node.get("parentId") or "")
        if parent_id:
            append_relation(
                relations,
                f"{screen_id}.containment.{len(relations)}",
                "containment",
                [parent_id, node_id],
                parentNodeId=parent_id,
                childNodeId=node_id,
            )
        node_rect = rect(node)
        if node_rect and node_rect["height"] > 0 and close(node_rect["width"], node_rect["height"]):
            append_relation(
                relations,
                f"{screen_id}.square.{len(relations)}",
                "square-aspect",
                [node_id],
                ratio=round(node_rect["width"] / node_rect["height"], 6),
                tolerancePt=TOLERANCE_PT,
            )
        axis = str((node.get("layout") or {}).get("scrollAxis") or "none")
        if axis != "none":
            append_relation(
                relations,
                f"{screen_id}.scroll-owner.{len(relations)}",
                "scroll-axis-ownership",
                [node_id],
                ownerNodeId=node_id,
                axis=axis,
            )

    for container in container_relations:
        container_id = str(container["containerNodeId"])
        ordered = [str(item) for item in container.get("orderedChildNodeIds") or []]
        axis = str(container.get("axis") or "vertical")
        for index, (left_id, right_id) in enumerate(zip(ordered, ordered[1:])):
            left_rect = rect(nodes[left_id]) if left_id in nodes else None
            right_rect = rect(nodes[right_id]) if right_id in nodes else None
            gap = None
            if left_rect and right_rect and axis in {"horizontal", "vertical"}:
                origin = "x" if axis == "horizontal" else "y"
                extent = "width" if axis == "horizontal" else "height"
                gap = max(right_rect[origin] - left_rect[origin] - left_rect[extent], 0)
            append_relation(
                relations,
                f"{screen_id}.sequence.{container_id}.{index}",
                "visual-sequence",
                [left_id, right_id],
                containerNodeId=container_id,
                axis=axis,
                beforeNodeId=left_id,
                afterNodeId=right_id,
                gap=round(gap, 4) if gap is not None else None,
            )

        child_rects = [(node_id, rect(nodes[node_id])) for node_id in ordered if node_id in nodes]
        child_rects = [(node_id, value) for node_id, value in child_rects if value]
        for dimension in ("width", "height"):
            groups: list[list[str]] = []
            pending = list(child_rects)
            while pending:
                seed_id, seed_rect = pending.pop(0)
                group = [seed_id]
                remaining = []
                for candidate_id, candidate_rect in pending:
                    if close(seed_rect[dimension], candidate_rect[dimension]):
                        group.append(candidate_id)
                    else:
                        remaining.append((candidate_id, candidate_rect))
                pending = remaining
                if len(group) >= 2:
                    groups.append(group)
            for group_index, group in enumerate(groups):
                append_relation(
                    relations,
                    f"{screen_id}.equal-{dimension}.{container_id}.{group_index}",
                    f"equal-{dimension}",
                    group,
                    containerNodeId=container_id,
                    tolerancePt=TOLERANCE_PT,
                )

        for alignment, coordinate in (
            ("leading", "x"),
            ("top", "y"),
            ("trailing", "right"),
            ("bottom", "bottom"),
            ("center-x", "centerX"),
            ("center-y", "centerY"),
        ):
            values: list[tuple[str, float]] = []
            for node_id, value in child_rects:
                if coordinate == "right":
                    measured = value["x"] + value["width"]
                elif coordinate == "bottom":
                    measured = value["y"] + value["height"]
                elif coordinate == "centerX":
                    measured = value["x"] + value["width"] / 2
                elif coordinate == "centerY":
                    measured = value["y"] + value["height"] / 2
                else:
                    measured = value[coordinate]
                values.append((node_id, measured))
            if len(values) >= 2 and max(value for _, value in values) - min(value for _, value in values) <= TOLERANCE_PT:
                append_relation(
                    relations,
                    f"{screen_id}.align-{alignment}.{container_id}",
                    "alignment",
                    [node_id for node_id, _ in values],
                    containerNodeId=container_id,
                    alignment=alignment,
                    tolerancePt=TOLERANCE_PT,
                )

        if axis == "overlay":
            for index, (left_id, left_rect) in enumerate(child_rects):
                for right_id, right_rect in child_rects[index + 1:]:
                    if intersects(left_rect, right_rect):
                        left_z = number((nodes[left_id].get("style") or {}).get("zIndex")) or 0
                        right_z = number((nodes[right_id].get("style") or {}).get("zIndex")) or 0
                        append_relation(
                            relations,
                            f"{screen_id}.overlap.{container_id}.{len(relations)}",
                            "overlap-order",
                            [left_id, right_id],
                            containerNodeId=container_id,
                            backNodeId=left_id if left_z <= right_z else right_id,
                            frontNodeId=right_id if left_z <= right_z else left_id,
                        )

    return {
        "screenId": screen_id,
        "rootNodeId": screen.get("rootNodeId"),
        "sourceCoverage": screen.get("sourceCoverage"),
        "nodes": [
            {
                "nodeId": node_id,
                "parentNodeId": node.get("parentId"),
                "semanticType": node.get("semanticType"),
                "sourceRuntimeId": (node.get("source") or {}).get("runtimeId"),
                "rect": rect(node),
                "scrollAxis": str((node.get("layout") or {}).get("scrollAxis") or "none"),
            }
            for node_id, node in nodes.items()
        ],
        "containers": container_relations,
        "relations": relations,
        "summary": {
            "nodeCount": len(nodes),
            "containerCount": len(container_relations),
            "relationCount": len(relations),
            "containmentCount": sum(item["kind"] == "containment" for item in relations),
            "sequenceCount": sum(item["kind"] == "visual-sequence" for item in relations),
            "scrollOwnerCount": sum(item["kind"] == "scroll-axis-ownership" for item in relations),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    screens: list[dict[str, Any]] = []
    for path in args.ir:
        data = load_json(path)
        if data.get("schemaVersion") != "1.2":
            raise ValueError(f"{path}: expected UI IR schemaVersion 1.2")
        screens.extend(build_screen_graph(screen) for screen in data.get("screens") or [])
    screen_ids = [str(screen.get("screenId") or "") for screen in screens]
    if not screens or any(not screen_id for screen_id in screen_ids) or len(screen_ids) != len(set(screen_ids)):
        raise ValueError("UI IR inputs must contain unique non-empty screens")
    output = {
        "schemaVersion": SCHEMA_VERSION,
        "screens": screens,
        "summary": {
            "screenCount": len(screens),
            "nodeCount": sum(screen["summary"]["nodeCount"] for screen in screens),
            "relationCount": sum(screen["summary"]["relationCount"] for screen in screens),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **output["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
