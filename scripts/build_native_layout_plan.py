#!/usr/bin/env python3
"""Lower UI IR geometry and architecture relations into one executable native layout plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-layout-plan-1.0"
CONTROL_SEMANTICS = {
    "button", "icon-button", "link", "menu-item", "tab-item", "file-input",
    "checkbox", "radio", "switch", "toggle", "select", "picker",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else default


def px(value: Any) -> float | None:
    raw = str(value or "").strip().lower()
    if not raw or raw in {"auto", "none", "normal", "initial", "inherit"}:
        return None
    if re.fullmatch(r"-?(?:\d+(?:\.\d+)?|\.\d+)px", raw):
        return float(raw[:-2])
    if re.fullmatch(r"0(?:\.0+)?", raw):
        return 0.0
    return None


def length_contract(value: Any) -> dict[str, Any]:
    raw = str(value or "auto").strip().lower()
    fixed = px(raw)
    if fixed is not None:
        kind = "fixed"
    elif raw.endswith("%"):
        kind = "percentage"
    elif raw.startswith("calc("):
        kind = "calculation"
    elif raw.endswith(("vw", "vh", "vmin", "vmax")):
        kind = "viewport-relative"
    elif raw.endswith(("em", "rem", "ch", "ex")):
        kind = "font-relative"
    elif raw in {"min-content", "max-content", "fit-content", "fit-content()"}:
        kind = "intrinsic-keyword"
    else:
        kind = "automatic"
    return {"raw": raw, "kind": kind, "fixedValuePt": fixed}


def edges(values: Any) -> list[float]:
    source = values if isinstance(values, list) else []
    return [max(number(source[index]) if index < len(source) else 0, 0) for index in range(4)]


def rect(node: dict[str, Any]) -> dict[str, float]:
    raw = (node.get("layout") or {}).get("rect") or {}
    return {key: number(raw.get(key)) for key in ("x", "y", "width", "height")}


def classify_slot(node: dict[str, Any], container_text: bool) -> str:
    semantic = str(node.get("semanticType") or "")
    source = node.get("source") or {}
    hint = " ".join(str(source.get(key) or "") for key in ("selector", "domId", "runtimeId")).lower()
    text = str((node.get("content") or {}).get("text") or "")
    measured = rect(node)
    if semantic in {"icon", "image"}:
        return "icon"
    if re.search(r"badge|count|counter|pill|角标|数量", hint) or (
        semantic in {"label", "text"} and len(text.strip()) <= 3 and measured["width"] <= 44 and container_text
    ):
        return "badge"
    if semantic in {"loading", "progress"}:
        return "indicator"
    return "content"


def compound_slots(
    node: dict[str, Any],
    child_ids: list[str],
    nodes: dict[str, dict[str, Any]],
    axis: str,
) -> list[dict[str, Any]]:
    content = node.get("content") or {}
    text = re.sub(r"\s+", " ", str(content.get("text") or "")).strip()
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    text_index = 0
    for run_index, run in enumerate(content.get("runs") or []):
        if not isinstance(run, dict):
            continue
        child_id = str(run.get("nodeId") or "")
        kind = str(run.get("kind") or "")
        source_rect = run.get("sourceRectCssPx") or run.get("rect") or {}
        if kind == "node" and child_id in child_ids and child_id not in seen:
            slots.append({
                "slotId": child_id,
                "kind": classify_slot(nodes[child_id], bool(text)),
                "nodeId": child_id,
                "rect": {key: number(source_rect.get(key), rect(nodes[child_id])[key]) for key in ("x", "y", "width", "height")},
                "sourceOrder": int(number(run.get("domIndex"), run_index)),
            })
            seen.add(child_id)
        elif kind == "text" and str(run.get("text") or "").strip():
            slot_id = f"{node.get('id')}.__text.{text_index}"
            slots.append({
                "slotId": slot_id,
                "kind": "title",
                "nodeId": None,
                "rect": {key: number(source_rect.get(key)) for key in ("x", "y", "width", "height")},
                "sourceOrder": int(number(run.get("domIndex"), run_index)),
            })
            text_index += 1
    for index, child_id in enumerate(child_ids):
        if child_id in seen:
            continue
        slots.append({
            "slotId": child_id,
            "kind": classify_slot(nodes[child_id], bool(text)),
            "nodeId": child_id,
            "rect": rect(nodes[child_id]),
            "sourceOrder": len(slots) + index,
        })
    if text and not any(item["kind"] == "title" for item in slots):
        line_rects = content.get("lineRects") or []
        source_rect = line_rects[0] if line_rects else rect(node)
        slots.append({
            "slotId": f"{node.get('id')}.__text.0",
            "kind": "title",
            "nodeId": None,
            "rect": {key: number(source_rect.get(key)) for key in ("x", "y", "width", "height")},
            "sourceOrder": len(slots),
        })
    coordinate = "x" if axis == "horizontal" else "y"
    secondary = "y" if axis == "horizontal" else "x"
    slots.sort(key=lambda item: (
        number((item.get("rect") or {}).get(coordinate)),
        number((item.get("rect") or {}).get(secondary)),
        int(item.get("sourceOrder") or 0),
    ))
    for index, slot in enumerate(slots):
        slot["visualOrder"] = index
        slot.pop("sourceOrder", None)
    title_index = next((index for index, item in enumerate(slots) if item["kind"] == "title"), None)
    if title_index is not None:
        for index, slot in enumerate(slots):
            if slot["kind"] == "icon":
                slot["kind"] = "leadingIcon" if index < title_index else "trailingIcon"
    return slots


def build_screen(
    screen: dict[str, Any],
    architecture: dict[str, Any],
    graph: dict[str, Any],
) -> dict[str, Any]:
    screen_id = str(screen.get("id") or "")
    nodes = {str(item.get("id") or ""): item for item in screen.get("nodes") or [] if item.get("id")}
    children: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        children.setdefault(str(node.get("parentId") or ""), []).append(node_id)
    layers = architecture.get("layers") or {}
    content = layers.get("contentContainer") or {}
    architecture_relations = {
        str(item.get("containerNodeId") or ""): item
        for item in content.get("layoutRelations") or []
        if isinstance(item, dict) and item.get("containerNodeId")
    }
    graph_containers = {
        str(item.get("containerNodeId") or ""): item
        for item in graph.get("containers") or []
        if isinstance(item, dict) and item.get("containerNodeId")
    }
    graph_relations_by_container: dict[str, list[str]] = {}
    for relation in graph.get("relations") or []:
        container_id = str(relation.get("containerNodeId") or relation.get("parentNodeId") or "")
        if container_id:
            graph_relations_by_container.setdefault(container_id, []).append(str(relation.get("id") or ""))

    containers = []
    for container_id, relation in architecture_relations.items():
        graph_container = graph_containers.get(container_id) or {}
        ordered = [str(item) for item in relation.get("orderedChildNodeIds") or []]
        containers.append({
            "containerNodeId": container_id,
            "axis": str(relation.get("axis") or graph_container.get("axis") or "vertical"),
            "orderedChildNodeIds": ordered,
            "sourceChildNodeIds": [str(item) for item in relation.get("sourceChildNodeIds") or []],
            "gapPt": max(number(relation.get("gap")), 0),
            "alignment": str(relation.get("alignment") or "normal"),
            "distribution": str(relation.get("distribution") or "normal"),
            "wraps": bool(relation.get("wraps")),
            "childSizing": relation.get("childSizing") or [],
            "relationIds": sorted(set(graph_relations_by_container.get(container_id) or [])),
        })

    node_plans = []
    for node_id, node in nodes.items():
        style = node.get("style") or {}
        measured = rect(node)
        padding = edges(style.get("padding"))
        border = edges(style.get("borderWidths"))
        margin = edges(style.get("margin"))
        horizontal_insets = padding[1] + padding[3] + border[1] + border[3]
        vertical_insets = padding[0] + padding[2] + border[0] + border[2]
        box_sizing = str(style.get("boxSizing") or "content-box")
        min_width = px(style.get("minWidth"))
        max_width = px(style.get("maxWidth"))
        min_height = px(style.get("minHeight"))
        max_height = px(style.get("maxHeight"))
        if box_sizing == "content-box":
            min_width = min_width + horizontal_insets if min_width is not None else None
            max_width = max_width + horizontal_insets if max_width is not None else None
            min_height = min_height + vertical_insets if min_height is not None else None
            max_height = max_height + vertical_insets if max_height is not None else None
        node_plans.append({
            "nodeId": node_id,
            "boxModel": {
                "boxSizing": box_sizing,
                "constraintReferenceBox": "border-box",
                "borderBoxWidthPt": measured["width"],
                "borderBoxHeightPt": measured["height"],
                "contentWidthPt": max(measured["width"] - horizontal_insets, 0),
                "contentHeightPt": max(measured["height"] - vertical_insets, 0),
                "paddingPt": padding,
                "borderWidthsPt": border,
                "marginPt": margin,
                "widthContract": length_contract(style.get("width")),
                "heightContract": length_contract(style.get("height")),
                "minWidthPt": min_width,
                "maxWidthPt": max_width,
                "minHeightPt": min_height,
                "maxHeightPt": max_height,
                "transform": str(style.get("transform") or "none"),
            },
            "flex": {
                "grow": number(style.get("flexGrow")),
                "shrink": number(style.get("flexShrink"), 1),
                "basis": str(style.get("flexBasis") or "auto"),
                "order": int(number(style.get("order"))),
            },
        })

    compounds = []
    container_by_id = {item["containerNodeId"]: item for item in containers}
    for node_id, node in nodes.items():
        child_ids = children.get(node_id) or []
        text = str((node.get("content") or {}).get("text") or "").strip()
        semantic = str(node.get("semanticType") or "")
        if not child_ids or (semantic not in CONTROL_SEMANTICS and not text):
            continue
        axis = str((container_by_id.get(node_id) or {}).get("axis") or "vertical")
        slots = compound_slots(node, child_ids, nodes, axis)
        if len(slots) < 2:
            continue
        compounds.append({
            "nodeId": node_id,
            "semanticType": semantic,
            "axis": axis,
            "orderedSlots": slots,
            "orderedSlotIds": [item["slotId"] for item in slots],
            "singleLine": str((node.get("style") or {}).get("whiteSpace") or "") == "nowrap",
        })

    return {
        "screenId": screen_id,
        "rootNodeId": screen.get("rootNodeId"),
        "contentContainer": {
            "nodeId": content.get("nodeId"),
            "kind": content.get("kind"),
            "scrollAxis": content.get("scrollAxis"),
        },
        "containers": containers,
        "nodes": node_plans,
        "compoundControls": compounds,
        "summary": {
            "containerCount": len(containers),
            "nodeCount": len(node_plans),
            "compoundControlCount": len(compounds),
            "relationReferenceCount": sum(len(item["relationIds"]) for item in containers),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--architecture-plan", required=True, type=Path)
    parser.add_argument("--layout-graph", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    architecture = load_json(args.architecture_plan)
    graph = load_json(args.layout_graph)
    if architecture.get("schemaVersion") != "native-architecture-plan-1.1":
        raise ValueError("--architecture-plan must use native-architecture-plan-1.1")
    if graph.get("schemaVersion") != "layout-relation-graph-1.0":
        raise ValueError("--layout-graph must use layout-relation-graph-1.0")
    architecture_screens = {str(item.get("screenId") or ""): item for item in architecture.get("screens") or []}
    graph_screens = {str(item.get("screenId") or ""): item for item in graph.get("screens") or []}
    screens = []
    inputs = []
    for path in args.ir:
        payload = load_json(path)
        inputs.append({"path": str(path.resolve()), "sha256": sha256(path)})
        for screen in payload.get("screens") or []:
            screen_id = str(screen.get("id") or "")
            if not screen_id or screen_id not in architecture_screens or screen_id not in graph_screens:
                raise ValueError(f"screen {screen_id!r} is missing from architecture or layout graph")
            screens.append(build_screen(screen, architecture_screens[screen_id], graph_screens[screen_id]))
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "architecturePlanSha256": sha256(args.architecture_plan),
        "layoutRelationGraphSha256": sha256(args.layout_graph),
        "inputs": inputs,
        "screens": screens,
        "summary": {
            "screenCount": len(screens),
            "containerCount": sum(item["summary"]["containerCount"] for item in screens),
            "nodeCount": sum(item["summary"]["nodeCount"] for item in screens),
            "compoundControlCount": sum(item["summary"]["compoundControlCount"] for item in screens),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **result["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
