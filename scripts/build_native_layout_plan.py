#!/usr/bin/env python3
"""Lower UI IR geometry and architecture relations into one executable native layout plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-layout-plan-1.1"
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


def split_css_tokens(value: Any) -> list[str]:
    raw = str(value or "").strip()
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for character in raw:
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(depth - 1, 0)
        if character.isspace() and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(character)
    if current:
        tokens.append("".join(current))
    return tokens


def calculation_terms(raw: str) -> list[dict[str, Any]]:
    body = raw[5:-1].strip() if raw.startswith("calc(") and raw.endswith(")") else raw
    matches = re.findall(r"([+-]?)\s*(\d+(?:\.\d+)?|\.\d+)\s*(px|%|vw|vh|vmin|vmax|rem|em|ch|ex)", body)
    return [
        {
            "coefficient": (-1 if sign == "-" else 1) * float(value),
            "unit": unit,
        }
        for sign, value, unit in matches
    ]


def length_contract(value: Any, reference_axis: str) -> dict[str, Any]:
    raw = str(value or "auto").strip().lower()
    fixed = px(raw)
    relative_factor = None
    terms: list[dict[str, Any]] = []
    if fixed is not None:
        kind = "fixed"
    elif raw.endswith("%"):
        kind = "percentage"
        relative_factor = number(raw) / 100
    elif raw.startswith("calc("):
        kind = "calculation"
        terms = calculation_terms(raw)
    elif raw.endswith(("vw", "vh", "vmin", "vmax")):
        kind = "viewport-relative"
        relative_factor = number(raw) / 100
    elif raw.endswith(("em", "rem", "ch", "ex")):
        kind = "font-relative"
        relative_factor = number(raw)
    elif raw in {"min-content", "max-content", "fit-content", "fit-content()"}:
        kind = "intrinsic-keyword"
    else:
        kind = "automatic"
    return {
        "raw": raw,
        "kind": kind,
        "referenceAxis": reference_axis,
        "fixedValuePt": fixed,
        "relativeFactor": relative_factor,
        "terms": terms,
        "requiresRuntimeResolution": kind in {
            "percentage", "calculation", "viewport-relative", "font-relative",
        },
    }


def grid_line(value: Any) -> dict[str, Any]:
    raw = str(value or "auto").strip().lower()
    match = re.fullmatch(r"(?:span\s+)?(-?\d+)", raw)
    return {
        "raw": raw,
        "index": int(match.group(1)) if match and not raw.startswith("span") else None,
        "span": int(match.group(1)) if match and raw.startswith("span") else None,
        "isAuto": raw == "auto",
    }


def grid_track(value: str, axis: str) -> dict[str, Any]:
    raw = value.strip().lower()
    minmax = re.fullmatch(r"minmax\((.+),\s*(.+)\)", raw)
    fraction = re.fullmatch(r"(\d+(?:\.\d+)?)fr", raw)
    if minmax:
        return {
            "raw": raw,
            "kind": "minmax",
            "minimum": grid_track(minmax.group(1), axis),
            "maximum": grid_track(minmax.group(2), axis),
        }
    if fraction:
        return {"raw": raw, "kind": "fraction", "fraction": float(fraction.group(1))}
    if raw in {"auto", "min-content", "max-content"} or raw.startswith("fit-content("):
        return {"raw": raw, "kind": "intrinsic"}
    contract = length_contract(raw, axis)
    return {"raw": raw, "kind": "length", "length": contract}


def grid_tracks(value: Any, axis: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for token in split_css_tokens(value):
        repeated = re.fullmatch(r"repeat\((\d+),\s*(.+)\)", token, re.IGNORECASE)
        if repeated:
            count = min(max(int(repeated.group(1)), 0), 64)
            repeated_tracks = grid_tracks(repeated.group(2), axis)
            result.extend(repeated_tracks * count)
        else:
            result.append(grid_track(token, axis))
    return result


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
    states: list[dict[str, Any]],
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

    root_node_id = str(screen.get("rootNodeId") or "")

    def containing_block(node_id: str, position: str) -> tuple[str | None, str]:
        if position == "fixed":
            return None, "viewport"
        current = str((nodes.get(node_id) or {}).get("parentId") or "")
        if position == "sticky":
            while current:
                current_node = nodes.get(current) or {}
                if str((current_node.get("layout") or {}).get("scrollAxis") or "none") != "none":
                    return current, "scroll-container"
                current = str(current_node.get("parentId") or "")
            return root_node_id or None, "viewport-scroll-root"
        if position == "absolute":
            current = str((nodes.get(node_id) or {}).get("parentId") or "")
            while current:
                current_node = nodes.get(current) or {}
                if str((current_node.get("style") or {}).get("position") or "static") != "static":
                    return current, "positioned-ancestor"
                current = str(current_node.get("parentId") or "")
            return root_node_id or None, "initial-containing-block"
        return str((nodes.get(node_id) or {}).get("parentId") or "") or None, "flow-parent"

    containers = []
    for container_id, relation in architecture_relations.items():
        graph_container = graph_containers.get(container_id) or {}
        ordered = [str(item) for item in relation.get("orderedChildNodeIds") or []]
        container_style = (nodes.get(container_id) or {}).get("style") or {}
        axis = str(relation.get("axis") or graph_container.get("axis") or "vertical")
        flex_direction = str(container_style.get("flexDirection") or "column")
        row_gap = px(container_style.get("rowGap"))
        column_gap = px(container_style.get("columnGap"))
        measured_gap = max(number(relation.get("gap")), 0)
        layout_algorithm = {
            "grid": "grid",
            "overlay": "positioned-overlay",
        }.get(axis, "wrapping-stack" if bool(relation.get("wraps")) else "stack")
        containers.append({
            "containerNodeId": container_id,
            "layoutAlgorithm": layout_algorithm,
            "axis": axis,
            "orderedChildNodeIds": ordered,
            "sourceChildNodeIds": [str(item) for item in relation.get("sourceChildNodeIds") or []],
            "gapPt": measured_gap,
            "rowGapPt": row_gap if row_gap is not None else measured_gap,
            "columnGapPt": column_gap if column_gap is not None else measured_gap,
            "alignment": str(relation.get("alignment") or "normal"),
            "distribution": str(relation.get("distribution") or "normal"),
            "alignContent": str(container_style.get("alignContent") or "normal"),
            "justifyItems": str(container_style.get("justifyItems") or "normal"),
            "wraps": bool(relation.get("wraps")),
            "reverse": flex_direction in {"row-reverse", "column-reverse"},
            "writingDirection": str(container_style.get("direction") or "ltr"),
            "childSizing": relation.get("childSizing") or [],
            "grid": {
                "columnTracks": grid_tracks(container_style.get("gridTemplateColumns"), "horizontal"),
                "rowTracks": grid_tracks(container_style.get("gridTemplateRows"), "vertical"),
                "autoFlow": str(container_style.get("gridAutoFlow") or "row"),
                "autoColumns": grid_tracks(container_style.get("gridAutoColumns"), "horizontal"),
                "autoRows": grid_tracks(container_style.get("gridAutoRows"), "vertical"),
            } if axis == "grid" else None,
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
        position = str(style.get("position") or (node.get("layout") or {}).get("position") or "static")
        containing_block_id, coordinate_space = containing_block(node_id, position)
        containing_rect = rect(nodes.get(containing_block_id) or {}) if containing_block_id else {
            "x": 0, "y": 0, "width": rect(nodes.get(root_node_id) or {}).get("width", 0),
            "height": rect(nodes.get(root_node_id) or {}).get("height", 0),
        }
        position_offset = {
            "x": measured["x"] - containing_rect["x"],
            "y": measured["y"] - containing_rect["y"],
        }
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
                "widthContract": length_contract(style.get("width"), "horizontal"),
                "heightContract": length_contract(style.get("height"), "vertical"),
                "minWidthContract": length_contract(style.get("minWidth"), "horizontal"),
                "maxWidthContract": length_contract(style.get("maxWidth"), "horizontal"),
                "minHeightContract": length_contract(style.get("minHeight"), "vertical"),
                "maxHeightContract": length_contract(style.get("maxHeight"), "vertical"),
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
                "alignSelf": str(style.get("alignSelf") or "auto"),
            },
            "gridItem": {
                "columnStart": grid_line(style.get("gridColumnStart")),
                "columnEnd": grid_line(style.get("gridColumnEnd")),
                "rowStart": grid_line(style.get("gridRowStart")),
                "rowEnd": grid_line(style.get("gridRowEnd")),
                "area": str(style.get("gridArea") or "auto"),
                "justifySelf": str(style.get("justifySelf") or "auto"),
                "alignSelf": str(style.get("alignSelf") or "auto"),
            },
            "positioning": {
                "scheme": position,
                "coordinateSpace": coordinate_space,
                "containingBlockNodeId": containing_block_id,
                "offsetFromContainingBlockPt": position_offset,
                "insets": {
                    edge: length_contract(style.get(edge), "vertical" if edge in {"top", "bottom"} else "horizontal")
                    for edge in ("top", "right", "bottom", "left")
                },
                "zIndex": number(style.get("zIndex")),
                "transform": str(style.get("transform") or "none"),
                "transformOrigin": str(style.get("transformOrigin") or "50% 50%"),
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

    node_plan_by_id = {item["nodeId"]: item for item in node_plans}
    state_layouts = []
    for state in states:
        delta = state.get("stateDelta") or {}
        operations = []
        for operation in delta.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            generated_id = str(operation.get("generatedRootNodeId") or "")
            target_id = str(operation.get("targetNodeId") or "")
            parent_id = str(operation.get("targetParentNodeId") or "")
            generated_plan = node_plan_by_id.get(generated_id)
            target_plan = node_plan_by_id.get(target_id)
            operations.append({
                "kind": str(operation.get("kind") or ""),
                "targetNodeId": target_id or None,
                "generatedRootNodeId": generated_id or None,
                "targetParentNodeId": parent_id or None,
                "generatedLayoutNodeId": generated_id if generated_plan else None,
                "targetBaselineLayoutNodeId": target_id if target_plan else None,
                "changesLayout": bool(
                    generated_plan
                    and (
                        not target_plan
                        or generated_plan.get("boxModel") != target_plan.get("boxModel")
                        or generated_plan.get("positioning") != target_plan.get("positioning")
                    )
                ),
            })
        if operations:
            state_layouts.append({
                "stateId": str(state.get("id") or ""),
                "ownerScreenId": screen_id,
                "nativeStrategy": str(delta.get("nativeStrategy") or ""),
                "operations": operations,
                "affectedContainerNodeIds": sorted({
                    str(item.get("targetParentNodeId") or "")
                    for item in operations
                    if item.get("targetParentNodeId")
                }),
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
        "stateLayouts": state_layouts,
        "summary": {
            "containerCount": len(containers),
            "nodeCount": len(node_plans),
            "compoundControlCount": len(compounds),
            "stateLayoutCount": len(state_layouts),
            "runtimeLengthContractCount": sum(
                contract.get("requiresRuntimeResolution") is True
                for item in node_plans
                for contract in (item["boxModel"][key] for key in (
                    "widthContract", "heightContract", "minWidthContract", "maxWidthContract",
                    "minHeightContract", "maxHeightContract",
                ))
            ),
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
        payload_screens = payload.get("screens") or []
        default_state_owner = str(payload_screens[0].get("id") or "") if len(payload_screens) == 1 else ""
        for screen in payload_screens:
            screen_id = str(screen.get("id") or "")
            if not screen_id or screen_id not in architecture_screens or screen_id not in graph_screens:
                raise ValueError(f"screen {screen_id!r} is missing from architecture or layout graph")
            screen_states = [
                item for item in payload.get("states") or []
                if str(item.get("ownerScreenId") or default_state_owner) == screen_id
            ]
            screens.append(build_screen(
                screen,
                architecture_screens[screen_id],
                graph_screens[screen_id],
                screen_states,
            ))
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
            "stateLayoutCount": sum(item["summary"]["stateLayoutCount"] for item in screens),
            "runtimeLengthContractCount": sum(item["summary"]["runtimeLengthContractCount"] for item in screens),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **result["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
