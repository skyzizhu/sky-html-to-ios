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
    affine_multiplier = None
    affine_constant = None
    if kind == "fixed":
        affine_multiplier, affine_constant = 0.0, fixed
    elif kind == "percentage":
        affine_multiplier, affine_constant = relative_factor, 0.0
    elif kind == "calculation" and terms and all(item["unit"] in {"px", "%"} for item in terms):
        affine_multiplier = sum(item["coefficient"] / 100 for item in terms if item["unit"] == "%")
        affine_constant = sum(item["coefficient"] for item in terms if item["unit"] == "px")
    return {
        "raw": raw,
        "kind": kind,
        "referenceAxis": reference_axis,
        "fixedValuePt": fixed,
        "relativeFactor": relative_factor,
        "terms": terms,
        "affineMultiplier": affine_multiplier,
        "affineConstantPt": affine_constant,
        "nativeResolution": "parent-affine" if affine_multiplier is not None else "measured-fallback",
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


def grid_item_contract(style: dict[str, Any]) -> dict[str, Any]:
    column_start = grid_line(style.get("gridColumnStart"))
    column_end = grid_line(style.get("gridColumnEnd"))
    row_start = grid_line(style.get("gridRowStart"))
    row_end = grid_line(style.get("gridRowEnd"))
    column_span = column_end.get("span")
    if column_span is None and column_start.get("index") is not None and column_end.get("index") is not None:
        column_span = max(int(column_end["index"]) - int(column_start["index"]), 1)
    row_span = row_end.get("span")
    if row_span is None and row_start.get("index") is not None and row_end.get("index") is not None:
        row_span = max(int(row_end["index"]) - int(row_start["index"]), 1)
    return {
        "columnStart": column_start,
        "columnEnd": column_end,
        "columnSpan": column_span,
        "rowStart": row_start,
        "rowEnd": row_end,
        "rowSpan": row_span,
        "area": str(style.get("gridArea") or "auto"),
        "justifySelf": str(style.get("justifySelf") or "auto"),
        "alignSelf": str(style.get("alignSelf") or "auto"),
    }


def edges(values: Any) -> list[float]:
    source = values if isinstance(values, list) else []
    return [max(number(source[index]) if index < len(source) else 0, 0) for index in range(4)]


def authored_value(style: dict[str, Any], key: str) -> Any:
    evidence = (style.get("authoredLayout") or {}).get(key) or {}
    return evidence.get("value") if evidence.get("value") not in {None, ""} else style.get(key)


def rect(node: dict[str, Any]) -> dict[str, float]:
    raw = (node.get("layout") or {}).get("rect") or {}
    return {key: number(raw.get(key)) for key in ("x", "y", "width", "height")}


def median(values: list[float]) -> float | None:
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def nearly_uniform(values: list[float], tolerance: float = 1.5) -> bool:
    measured = [value for value in values if value > 0]
    return bool(measured) and max(measured) - min(measured) <= tolerance


def inferred_column_count(item_nodes: list[dict[str, Any]], tolerance: float = 2.0) -> int:
    columns: list[float] = []
    for node in sorted(item_nodes, key=lambda item: (rect(item)["x"], rect(item)["y"])):
        x = rect(node)["x"]
        if not any(abs(x - existing) <= tolerance for existing in columns):
            columns.append(x)
    return max(len(columns), 1)


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
            "rowGapContract": length_contract(authored_value(container_style, "rowGap"), "vertical"),
            "columnGapContract": length_contract(authored_value(container_style, "columnGap"), "horizontal"),
            "alignment": str(relation.get("alignment") or "normal"),
            "distribution": str(relation.get("distribution") or "normal"),
            "alignContent": str(container_style.get("alignContent") or "normal"),
            "justifyItems": str(container_style.get("justifyItems") or "normal"),
            "wraps": bool(relation.get("wraps")),
            "reverse": flex_direction in {"row-reverse", "column-reverse"},
            "writingDirection": str(container_style.get("direction") or "ltr"),
            "childSizing": relation.get("childSizing") or [],
            "grid": {
                "columnTracks": grid_tracks(authored_value(container_style, "gridTemplateColumns"), "horizontal"),
                "rowTracks": grid_tracks(authored_value(container_style, "gridTemplateRows"), "vertical"),
                "autoFlow": str(container_style.get("gridAutoFlow") or "row"),
                "autoColumns": grid_tracks(authored_value(container_style, "gridAutoColumns"), "horizontal"),
                "autoRows": grid_tracks(authored_value(container_style, "gridAutoRows"), "vertical"),
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
                "widthContract": length_contract(authored_value(style, "width"), "horizontal"),
                "heightContract": length_contract(authored_value(style, "height"), "vertical"),
                "minWidthContract": length_contract(authored_value(style, "minWidth"), "horizontal"),
                "maxWidthContract": length_contract(authored_value(style, "maxWidth"), "horizontal"),
                "minHeightContract": length_contract(authored_value(style, "minHeight"), "vertical"),
                "maxHeightContract": length_contract(authored_value(style, "maxHeight"), "vertical"),
                "minWidthPt": min_width,
                "maxWidthPt": max_width,
                "minHeightPt": min_height,
                "maxHeightPt": max_height,
                "transform": str(style.get("transform") or "none"),
            },
            "flex": {
                "grow": number(style.get("flexGrow")),
                "shrink": number(style.get("flexShrink"), 1),
                "basis": str(authored_value(style, "flexBasis") or "auto"),
                "basisContract": length_contract(authored_value(style, "flexBasis"), "horizontal"),
                "order": int(number(style.get("order"))),
                "alignSelf": str(style.get("alignSelf") or "auto"),
            },
            "gridItem": grid_item_contract(style),
            "positioning": {
                "scheme": position,
                "coordinateSpace": coordinate_space,
                "containingBlockNodeId": containing_block_id,
                "nativeOwnerNodeId": (root_node_id if position == "fixed" else containing_block_id),
                "offsetFromContainingBlockPt": position_offset,
                "insets": {
                    edge: length_contract(authored_value(style, edge), "vertical" if edge in {"top", "bottom"} else "horizontal")
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
    reusable = layers.get("reusableContent") or {}
    strategies = {
        str(item.get("nodeId") or ""): str(item.get("kind") or "")
        for item in content.get("nodeStrategies") or []
        if isinstance(item, dict)
    }
    collection_layouts = []
    for section in reusable.get("sections") or []:
        if not isinstance(section, dict):
            continue
        source_id = str(section.get("sourceNodeId") or "")
        strategy = strategies.get(source_id, "")
        if strategy not in {"table-view", "collection-view", "compositional-collection"}:
            continue
        source = nodes.get(source_id) or {}
        source_style = source.get("style") or {}
        item_ids = [str(item) for item in section.get("itemNodeIds") or [] if str(item) in nodes]
        item_nodes = [nodes[item] for item in item_ids]
        widths = [rect(item)["width"] for item in item_nodes]
        heights = [rect(item)["height"] for item in item_nodes]
        width_value = median(widths)
        height_value = median(heights)
        ratios = [width / height for width, height in zip(widths, heights) if width > 0 and height > 0]
        ratio_value = median(ratios)
        section_kind = str(section.get("kind") or "list")
        horizontal = str(section.get("scrollAxis") or "vertical") == "horizontal"
        container_plan = container_by_id.get(source_id) or {}
        grid_tracks_plan = (container_plan.get("grid") or {}).get("columnTracks") or []
        column_count = (
            1 if section_kind == "list" or horizontal
            else len(grid_tracks_plan) or inferred_column_count(item_nodes)
        )
        explicit_heights = [
            str((((item.get("style") or {}).get("authoredLayout") or {}).get("height") or {}).get("value") or "")
            for item in item_nodes
        ]
        fixed_height = bool(
            height_value is not None
            and nearly_uniform(heights)
            and explicit_heights
            and all(re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)px", value.strip().lower()) for value in explicit_heights)
        )
        fixed_horizontal_width = bool(
            horizontal
            and width_value is not None
            and nearly_uniform(widths)
            and all(
                str((item.get("style") or {}).get("flexShrink") or "1") == "0"
                or str((item.get("style") or {}).get("whiteSpace") or "") == "nowrap"
                or bool(((item.get("style") or {}).get("authoredLayout") or {}).get("width"))
                or bool(((item.get("style") or {}).get("authoredLayout") or {}).get("flexBasis"))
                for item in item_nodes
            )
        )
        consistent_ratio = bool(ratio_value and ratios and max(ratios) - min(ratios) <= 0.03)
        header_id = str(section.get("headerNodeId") or "") or None
        footer_id = str(section.get("footerNodeId") or "") or None
        collection_layouts.append({
            "sectionId": str(section.get("id") or f"{screen_id}.section.{len(collection_layouts)}"),
            "containerNodeId": source_id,
            "nativeContainerKind": strategy,
            "layoutEngine": (
                "table" if strategy == "table-view"
                else "compositional" if content.get("kind") == "compositional-collection"
                else "flow"
            ),
            "scrollAxis": "horizontal" if horizontal else "vertical",
            "itemNodeIds": item_ids,
            "headerNodeId": header_id,
            "footerNodeId": footer_id,
            "pinsHeader": section.get("headerBehavior") == "pinned",
            "pinsFooter": section.get("footerBehavior") == "pinned",
            "headerHeightPt": rect(nodes.get(header_id) or {})["height"] if header_id else None,
            "footerHeightPt": rect(nodes.get(footer_id) or {})["height"] if footer_id else None,
            "columnCount": max(column_count, 1),
            "contentInsetsPt": edges(source_style.get("padding")),
            "lineSpacingPt": max(number(container_plan.get("rowGapPt")), 0),
            "interItemSpacingPt": max(number(container_plan.get("columnGapPt")), 0),
            "mainAxisSpacingPt": max(number(container_plan.get("columnGapPt" if horizontal else "rowGapPt")), 0),
            "crossAxisSpacingPt": max(number(container_plan.get("rowGapPt" if horizontal else "columnGapPt")), 0),
            "itemSizing": {
                "widthMode": "full-width" if strategy == "table-view" else "fixed" if fixed_horizontal_width else "fractional",
                "widthPt": width_value if fixed_horizontal_width else None,
                "widthFraction": None if strategy == "table-view" or fixed_horizontal_width else 1 / max(column_count, 1),
                "heightMode": "fixed" if fixed_height else "aspect-ratio" if section_kind == "grid" and consistent_ratio else "estimated",
                "heightPt": height_value if fixed_height else None,
                "estimatedHeightPt": height_value or 72,
                "aspectRatio": ratio_value if section_kind == "grid" and consistent_ratio and not fixed_height else None,
                "preservesIntrinsicWidth": horizontal,
            },
            "directionalLockEnabled": True,
            "allowsSameAxisNestedScroll": False,
        })
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
        "collectionLayouts": collection_layouts,
        "compoundControls": compounds,
        "stateLayouts": state_layouts,
        "summary": {
            "containerCount": len(containers),
            "nodeCount": len(node_plans),
            "collectionLayoutCount": len(collection_layouts),
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
