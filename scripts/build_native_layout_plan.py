#!/usr/bin/env python3
"""Lower UI IR geometry and architecture relations into one executable native layout plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-layout-plan-1.2"
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


def css_px_to_target_pt(value: Any, design_scale: float) -> float | None:
    parsed = px(value)
    return parsed * design_scale if parsed is not None else None


def normalize_alignment(value: Any, *, main_axis: bool = False) -> str:
    raw = str(value or "normal").strip().lower()
    if raw in {"flex-start", "start", "left", "top", "self-start", "normal", ""}:
        return "start" if main_axis else ("stretch" if raw in {"normal", ""} else "start")
    if raw in {"flex-end", "end", "right", "bottom", "self-end"}:
        return "end"
    if raw in {"safe center", "unsafe center"}:
        return "center"
    if raw in {"first baseline", "last baseline"}:
        return "baseline"
    if raw == "auto":
        return "start" if main_axis else "stretch"
    return raw


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
        repeated = re.fullmatch(r"repeat\((\d+|auto-fit|auto-fill),\s*(.+)\)", token, re.IGNORECASE)
        if repeated:
            repeated_tracks = grid_tracks(repeated.group(2), axis)
            repeat_value = repeated.group(1).lower()
            if repeat_value.isdigit():
                count = min(max(int(repeat_value), 0), 64)
                result.extend(repeated_tracks * count)
            else:
                result.append({
                    "raw": token.strip().lower(),
                    "kind": "repeat",
                    "repeatMode": repeat_value,
                    "tracks": repeated_tracks,
                })
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


def edge_strings(values: Any, default: str) -> list[str]:
    source = values if isinstance(values, list) else []
    return [str(source[index] or default) if index < len(source) else default for index in range(4)]


def corner_radius_pair(value: Any, width: float, height: float) -> tuple[float, float]:
    tokens = split_css_tokens(value)
    if not tokens:
        return 0.0, 0.0

    def resolve(token: str, extent: float) -> float:
        raw = token.strip().lower()
        if raw.endswith("%"):
            return max(extent * number(raw) / 100, 0)
        return max(number(raw), 0)

    horizontal = resolve(tokens[0], width)
    vertical = resolve(tokens[1] if len(tokens) > 1 else tokens[0], height)
    return horizontal, vertical


def appearance_contract(style: dict[str, Any], measured: dict[str, float]) -> dict[str, Any]:
    radii = list(style.get("cornerRadii") or [])
    radii = (radii + ["0px"] * 4)[:4]
    pairs = [
        corner_radius_pair(value, measured["width"], measured["height"])
        for value in radii
    ]
    return {
        "cornerRadiiXPt": [pair[0] for pair in pairs],
        "cornerRadiiYPt": [pair[1] for pair in pairs],
        "borderWidthsPt": edges(style.get("borderWidths")),
        "borderColors": edge_strings(style.get("borderColors"), "transparent"),
        "borderStyles": edge_strings(style.get("borderStyles"), "none"),
        "backgroundColor": str(style.get("backgroundColor") or "transparent"),
        "backgroundImage": str(style.get("backgroundImage") or "none"),
        "backgroundSize": str(style.get("backgroundSize") or "auto"),
        "backgroundPosition": str(style.get("backgroundPosition") or "0% 0%"),
        "backgroundRepeat": str(style.get("backgroundRepeat") or "repeat"),
        "opacity": min(max(number(style.get("opacity"), 1), 0), 1),
        "clipsDescendants": (
            str(style.get("overflowX") or "visible") in {"hidden", "clip"}
            or str(style.get("overflowY") or "visible") in {"hidden", "clip"}
        ),
        "clipPath": str(style.get("clipPath") or "none"),
        "maskImage": str(style.get("maskImage") or "none"),
        "boxShadow": str(style.get("boxShadow") or "none"),
        "preservesPerCornerGeometry": True,
        "preservesPerEdgeBorders": True,
    }


def authored_value(style: dict[str, Any], key: str) -> Any:
    evidence = (style.get("authoredLayout") or {}).get(key) or {}
    return evidence.get("value") if evidence.get("value") not in {None, ""} else style.get(key)


def explicit_authored_value(style: dict[str, Any], key: str) -> Any:
    evidence = (style.get("authoredLayout") or {}).get(key) or {}
    return evidence.get("value") if evidence.get("value") not in {None, ""} else None


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


def container_geometry_system(
    container: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    node_plans: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Lower browser geometry into one executable parent/child sizing system."""
    container_id = str(container.get("containerNodeId") or "")
    axis = str(container.get("axis") or "vertical")
    dimension = "width" if axis == "horizontal" else "height"
    content_key = "contentWidthPt" if axis == "horizontal" else "contentHeightPt"
    source_key = "sourceWidthPt" if axis == "horizontal" else "sourceHeightPt"
    mode_key = "widthMode" if axis == "horizontal" else "heightMode"
    contract_key = "widthContract" if axis == "horizontal" else "heightContract"
    ordered = [str(item) for item in container.get("orderedChildNodeIds") or []]
    sizing_by_id = {
        str(item.get("nodeId") or ""): item
        for item in container.get("childSizing") or []
        if isinstance(item, dict) and item.get("nodeId")
    }
    available = number(((node_plans.get(container_id) or {}).get("boxModel") or {}).get(content_key))
    source_sizes = [
        number(((node_plans.get(node_id) or {}).get("contentGeometry") or {}).get(source_key))
        for node_id in ordered
    ]
    gap_total = sum(
        number((sizing_by_id.get(node_id) or {}).get("gapBeforePt"), number(container.get("gapPt")))
        for node_id in ordered[1:]
    )
    grows = [number(((nodes.get(node_id) or {}).get("style") or {}).get("flexGrow")) for node_id in ordered]
    contracts = [((node_plans.get(node_id) or {}).get("boxModel") or {}).get(contract_key) or {} for node_id in ordered]
    explicit_fixed = [contract.get("kind") == "fixed" for contract in contracts]
    relative_multipliers = [contract.get("affineMultiplier") for contract in contracts]
    fills_available = bool(
        available > 0
        and abs(sum(source_sizes) + gap_total - available) <= max(2.0, available * 0.02)
    )
    equal_grow = bool(
        len(ordered) >= 2
        and all(value > 0 for value in grows)
        and nearly_uniform(grows, tolerance=0.001)
    )
    equal_relative = bool(
        len(ordered) >= 2
        and all(value is not None and number(value) > 0 for value in relative_multipliers)
        and nearly_uniform([number(value) for value in relative_multipliers], tolerance=0.001)
        and fills_available
    )
    observed_equal_fill = bool(
        axis == "horizontal"
        and container.get("layoutAlgorithm") == "stack"
        and len(ordered) >= 2
        and not any(explicit_fixed)
        and str(container.get("sourceDistribution") or "normal") not in {"space-between", "space-around", "space-evenly"}
        and not any(bool((sizing_by_id.get(node_id) or {}).get("flexibleGapBefore")) for node_id in ordered)
        and nearly_uniform(source_sizes, tolerance=max(1.5, (median(source_sizes) or 0) * 0.03))
        and fills_available
    )
    equal_share = bool(equal_grow or equal_relative or observed_equal_fill)
    evidence = []
    if equal_grow:
        evidence.append("equal-flex-grow")
    if equal_relative:
        evidence.append("equal-parent-relative-width")
    if observed_equal_fill:
        evidence.append("uniform-measured-widths-fill-content-box")

    child_contracts = []
    for index, node_id in enumerate(ordered):
        content_geometry = (node_plans.get(node_id) or {}).get("contentGeometry") or {}
        box = (node_plans.get(node_id) or {}).get("boxModel") or {}
        source_mode = str(content_geometry.get(mode_key) or "intrinsic")
        size_mode = "equal-share" if equal_share else source_mode
        child_contracts.append({
            "nodeId": node_id,
            "mainAxisSizingMode": size_mode,
            "sourceSizingMode": source_mode,
            "sourceSizePt": number(content_geometry.get(source_key)),
            "weight": 1.0 if equal_share else max(grows[index], 0),
            "minSizePt": box.get("minWidthPt" if axis == "horizontal" else "minHeightPt"),
            "maxSizePt": box.get("maxWidthPt" if axis == "horizontal" else "maxHeightPt"),
            "resistsCompression": bool(content_geometry.get("resistsHorizontalCompression")) if axis == "horizontal" else False,
            "gapBeforePt": (sizing_by_id.get(node_id) or {}).get("gapBeforePt") if index else None,
            "flexibleGapBefore": bool((sizing_by_id.get(node_id) or {}).get("flexibleGapBefore")) if index else False,
        })

    distribution = "equal-share" if equal_share else "source-sized"
    return {
        "schemaVersion": "container-geometry-system-1.0",
        "containerNodeId": container_id,
        "axis": axis,
        "availableContentSizePt": available,
        "mainAxisDistribution": distribution,
        "childContracts": child_contracts,
        "solveOrder": [
            "resolve-container-content-box",
            "measure-intrinsic-children",
            "resolve-parent-relative-children",
            "distribute-residual-main-axis-space",
            "resolve-cross-axis-alignment",
        ],
        "requiresIntrinsicMeasurementPass": any(item["sourceSizingMode"] == "intrinsic" for item in child_contracts),
        "requiresResidualDistributionPass": equal_share,
        "confidence": "high" if equal_share else "deterministic-source-contracts",
        "evidence": evidence or ["per-child-source-sizing"],
    }


def responsive_node_index(analysis: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for sample in analysis.get("samples") or []:
        width = number(sample.get("targetWidthPt"))
        for node in sample.get("nodes") or []:
            selector = str(node.get("selector") or "")
            selector_parts = [part.strip() for part in selector.split(" > ") if part.strip()]
            suffixes = {
                f"selector-suffix:{' > '.join(selector_parts[-length:])}"
                for length in range(1, min(len(selector_parts), 6) + 1)
            }
            for key in ({str(node.get("nodeId") or ""), selector} | suffixes) - {""}:
                indexed.setdefault(key, []).append({"widthPt": width, **node})
    return indexed


def responsive_entries(node: dict[str, Any], indexed: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    source = node.get("source") or {}
    candidates = (
        str(source.get("runtimeId") or ""),
        str(source.get("selector") or ""),
        str(source.get("domId") or ""),
    )
    exact = next((indexed[key] for key in candidates if key and key in indexed), None)
    if exact is not None:
        return exact
    selector_parts = [part.strip() for part in str(source.get("selector") or "").split(" > ") if part.strip()]
    for length in range(min(len(selector_parts), 6), 0, -1):
        matches = indexed.get(f"selector-suffix:{' > '.join(selector_parts[-length:])}") or []
        if matches:
            # A stable nth-of-type suffix should resolve to one node per sampled width.
            widths = [number(item.get("widthPt")) for item in matches]
            if len(widths) == len(set(widths)):
                return matches
    return []


def adaptive_track_contract(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    repeated = next((track for track in tracks if track.get("kind") == "repeat" and track.get("repeatMode") in {"auto-fit", "auto-fill"}), None)
    if not repeated:
        return None
    track = next(iter(repeated.get("tracks") or []), {})
    minimum = track.get("minimum") if track.get("kind") == "minmax" else track
    maximum = track.get("maximum") if track.get("kind") == "minmax" else track
    minimum_length = (minimum.get("length") or {}) if minimum.get("kind") == "length" else {}
    maximum_fraction = maximum.get("fraction") if maximum.get("kind") == "fraction" else None
    return {
        "mode": repeated.get("repeatMode"),
        "minimumItemWidthPt": minimum_length.get("fixedValuePt"),
        "maximumFraction": maximum_fraction,
        "raw": repeated.get("raw"),
    }


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


def content_geometry_contract(
    node: dict[str, Any],
    parent: dict[str, Any] | None,
    *,
    has_children: bool,
) -> dict[str, Any]:
    style = node.get("style") or {}
    content = node.get("content") or {}
    measured = rect(node)
    parent_rect = rect(parent or {})
    semantic = str(node.get("semanticType") or "container")
    # Computed style resolves auto/flex/Grid widths to px. Only authored evidence may
    # turn an ordinary content dimension into a fixed native constraint.
    width_contract = length_contract(explicit_authored_value(style, "width"), "horizontal")
    height_contract = length_contract(explicit_authored_value(style, "height"), "vertical")
    width_fraction = measured["width"] / parent_rect["width"] if parent_rect["width"] > 0 else 0
    background = str(style.get("backgroundColor") or "").lower()
    has_visual_style = bool(
        background not in {"", "transparent", "rgba(0, 0, 0, 0)"}
        or str(style.get("backgroundImage") or "none") != "none"
        or any(number(item) > 0 for item in style.get("borderWidths") or [])
        or any(number(item) > 0 for item in style.get("cornerRadii") or [])
        or str(style.get("boxShadow") or "none") != "none"
    )
    is_media = semantic in {"icon", "image", "canvas-artwork"}
    is_text = semantic in {"text", "label", "heading", "link"}
    is_control = semantic in CONTROL_SEMANTICS
    compact_visual = bool(
        measured["width"] > 0
        and measured["height"] > 0
        and measured["width"] <= 120
        and measured["height"] <= 56
        and width_fraction < 0.5
        and has_visual_style
        and (is_text or is_control or not has_children)
    )
    if is_media:
        role = "media"
    elif compact_visual:
        role = "compact-visual"
    elif is_text:
        role = "text"
    elif is_control:
        role = "control"
    else:
        role = "content"

    def dimension_mode(contract: dict[str, Any], *, horizontal: bool) -> str:
        kind = str(contract.get("kind") or "automatic")
        if number(style.get("flexGrow")) > 0 and horizontal:
            return "flexible"
        if kind == "fixed":
            return "fixed"
        if kind in {"percentage", "calculation", "viewport-relative", "font-relative"}:
            return "parent-relative"
        if compact_visual:
            return "fixed"
        if is_media and (not horizontal or width_fraction < 0.88):
            return "fixed"
        return "intrinsic"

    width_mode = dimension_mode(width_contract, horizontal=True)
    height_mode = dimension_mode(height_contract, horizontal=False)
    ratio = measured["width"] / measured["height"] if measured["width"] > 0 and measured["height"] > 0 else None
    authored_aspect_ratio = str(style.get("aspectRatio") or "").strip().lower()
    has_authored_aspect_ratio = authored_aspect_ratio not in {"", "auto", "none", "normal"} and ratio is not None
    lines = int(number(content.get("lines"), 0))
    explicit_single_line = str(style.get("whiteSpace") or "").lower() == "nowrap"
    single_line = bool(
        explicit_single_line
        or ((compact_visual or is_control) and lines <= 1 and bool(str(content.get("text") or "").strip()))
    )
    resists_compression = bool(
        str(style.get("flexShrink") or "1") == "0"
        or explicit_single_line
        or is_media
        or compact_visual
    )
    preserves_intrinsic = bool(
        width_mode in {"fixed", "intrinsic"}
        and (is_media or compact_visual or explicit_single_line)
    )
    return {
        "role": role,
        "sourceWidthPt": measured["width"],
        "sourceHeightPt": measured["height"],
        "widthMode": width_mode,
        "heightMode": height_mode,
        "aspectRatio": ratio if is_media or compact_visual or has_authored_aspect_ratio else None,
        "singleLine": single_line,
        "lineCount": lines or None,
        "preservesIntrinsicWidth": preserves_intrinsic,
        "resistsHorizontalCompression": resists_compression,
        "horizontalAlignment": str(style.get("textAlign") or style.get("justifyContent") or "start"),
        "verticalAlignment": str(style.get("alignItems") or "center"),
        "mediaContentMode": str(style.get("objectFit") or "contain") if is_media else None,
        "mediaPosition": str(style.get("objectPosition") or "50% 50%") if is_media else None,
    }


def compound_slots(
    node: dict[str, Any],
    child_ids: list[str],
    nodes: dict[str, dict[str, Any]],
    axis: str,
    design_scale: float,
) -> list[dict[str, Any]]:
    content = node.get("content") or {}
    text = re.sub(r"\s+", " ", str(content.get("text") or "")).strip()
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    text_index = 0

    def is_slot_node(child_id: str) -> bool:
        child = nodes.get(child_id) or {}
        tag = str((child.get("source") or {}).get("tag") or "").lower()
        if tag in {"br", "wbr"}:
            return False
        if str(child.get("semanticType") or "") in {"decoration", "spacer"}:
            return False
        if bool((child.get("content") or {}).get("isDecorative")):
            return False
        return True

    for run_index, run in enumerate(content.get("runs") or []):
        if not isinstance(run, dict):
            continue
        child_id = str(run.get("nodeId") or "")
        kind = str(run.get("kind") or "")
        # UI IR run rects are already normalized into the target-point coordinate
        # space. sourceRectCssPx remains provenance and must not drive native slots.
        source_rect = run.get("rect") or run.get("sourceRectCssPx") or {}
        if kind == "node" and child_id in child_ids and child_id not in seen and is_slot_node(child_id):
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
        if child_id in seen or not is_slot_node(child_id):
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
    parent_style = node.get("style") or {}
    parent_rect = rect(node)
    default_gap = max(number(parent_style.get("gap")) * design_scale, 0)
    distribution = str(parent_style.get("justifyContent") or "normal").lower()
    main_origin = "x" if axis == "horizontal" else "y"
    main_extent = "width" if axis == "horizontal" else "height"
    for index, slot in enumerate(slots):
        child = nodes.get(str(slot.get("nodeId") or ""))
        slot_rect = slot.get("rect") or {}
        parent_font_size = number(parent_style.get("fontSize"), 16)
        parent_line_height = number(parent_style.get("lineHeight")) or parent_font_size * 1.2
        measured_single_line = bool(
            number(slot_rect.get("height")) > 0
            and number(slot_rect.get("height")) <= max(parent_line_height * 1.35, parent_font_size * 1.8)
        )
        fallback_single_line = (
            str(parent_style.get("whiteSpace") or "").lower() == "nowrap"
            or measured_single_line
        )
        geometry = content_geometry_contract(child, node, has_children=False) if child else {
            "role": "text",
            "sourceWidthPt": number((slot.get("rect") or {}).get("width")),
            "sourceHeightPt": number((slot.get("rect") or {}).get("height")),
            "widthMode": "intrinsic",
            "heightMode": "intrinsic",
            "aspectRatio": None,
            "singleLine": fallback_single_line,
            "lineCount": 1,
            "preservesIntrinsicWidth": fallback_single_line,
            "resistsHorizontalCompression": fallback_single_line,
            "horizontalAlignment": str(parent_style.get("textAlign") or "start"),
            "verticalAlignment": str(parent_style.get("alignItems") or "center"),
            "mediaContentMode": None,
            "mediaPosition": None,
        }
        slot["contentGeometry"] = geometry
        if index == 0:
            slot["gapBeforePt"] = None
            slot["flexibleGapBefore"] = False
            continue
        previous_rect = slots[index - 1].get("rect") or {}
        current_rect = slot.get("rect") or {}
        measured_gap = max(
            number(current_rect.get(main_origin))
            - number(previous_rect.get(main_origin))
            - number(previous_rect.get(main_extent)),
            0,
        )
        child_style = (child or {}).get("style") or {}
        auto_margin = str(authored_value(
            child_style,
            "marginLeft" if axis == "horizontal" else "marginTop",
        ) or "").lower() == "auto"
        flexible_gap = bool(distribution == "space-between" or auto_margin)
        slot["gapBeforePt"] = default_gap if flexible_gap else measured_gap
        slot["flexibleGapBefore"] = flexible_gap
    return slots


def build_screen(
    screen: dict[str, Any],
    architecture: dict[str, Any],
    graph: dict[str, Any],
    states: list[dict[str, Any]],
    responsive: dict[str, Any] | None = None,
    design_scale: float = 1.0,
) -> dict[str, Any]:
    screen_id = str(screen.get("id") or "")
    nodes = {str(item.get("id") or ""): item for item in screen.get("nodes") or [] if item.get("id")}
    responsive_index = responsive_node_index(responsive or {})
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
        source_positioning = ((nodes.get(node_id) or {}).get("source") or {}).get("positioning") or {}
        if position == "fixed":
            return None, "viewport"
        current = str((nodes.get(node_id) or {}).get("parentId") or "")
        if position == "sticky":
            extracted_scroll_owner = str(source_positioning.get("scrollAncestorNodeId") or "")
            if extracted_scroll_owner in nodes:
                return extracted_scroll_owner, "scroll-container"
            while current:
                current_node = nodes.get(current) or {}
                if str((current_node.get("layout") or {}).get("scrollAxis") or "none") != "none":
                    return current, "scroll-container"
                current = str(current_node.get("parentId") or "")
            return root_node_id or None, "viewport-scroll-root"
        if position == "absolute":
            extracted_offset_parent = str(source_positioning.get("offsetParentNodeId") or "")
            if extracted_offset_parent in nodes:
                return extracted_offset_parent, "positioned-ancestor"
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
        paint_ordered = [str(item) for item in graph_container.get("paintOrderNodeIds") or ordered]
        container_style = (nodes.get(container_id) or {}).get("style") or {}
        axis = str(relation.get("axis") or graph_container.get("axis") or "vertical")
        flex_direction = str(container_style.get("flexDirection") or "column")
        row_gap = css_px_to_target_pt(container_style.get("rowGap"), design_scale)
        column_gap = css_px_to_target_pt(container_style.get("columnGap"), design_scale)
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
            "paintOrderNodeIds": paint_ordered,
            "sourceChildNodeIds": [str(item) for item in relation.get("sourceChildNodeIds") or []],
            "gapPt": measured_gap,
            "rowGapPt": row_gap if row_gap is not None else measured_gap,
            "columnGapPt": column_gap if column_gap is not None else measured_gap,
            "rowGapContract": length_contract(authored_value(container_style, "rowGap"), "vertical"),
            "columnGapContract": length_contract(authored_value(container_style, "columnGap"), "horizontal"),
            "alignment": normalize_alignment(relation.get("alignment") or container_style.get("alignItems")),
            "sourceAlignment": str(relation.get("sourceAlignment") or container_style.get("alignItems") or "normal"),
            "distribution": normalize_alignment(relation.get("distribution") or container_style.get("justifyContent"), main_axis=True),
            "sourceDistribution": str(relation.get("sourceDistribution") or container_style.get("justifyContent") or "normal"),
            "alignContent": normalize_alignment(container_style.get("alignContent")),
            "justifyItems": normalize_alignment(container_style.get("justifyItems")),
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
        source_positioning = (node.get("source") or {}).get("positioning") or {}
        extracted_containing_rect = source_positioning.get("offsetParentRectCssPx") or {}
        containing_rect = rect(nodes.get(containing_block_id) or {}) if containing_block_id else {
            "x": 0, "y": 0, "width": rect(nodes.get(root_node_id) or {}).get("width", 0),
            "height": rect(nodes.get(root_node_id) or {}).get("height", 0),
        }
        if position == "absolute" and not containing_block_id and extracted_containing_rect:
            containing_rect = {
                key: number(extracted_containing_rect.get(key))
                for key in ("x", "y", "width", "height")
            }
        position_offset = {
            "x": measured["x"] - containing_rect["x"],
            "y": measured["y"] - containing_rect["y"],
        }
        node_plans.append({
            "nodeId": node_id,
            "appearance": appearance_contract(style, measured),
            "contentGeometry": content_geometry_contract(
                node,
                nodes.get(str(node.get("parentId") or "")),
                has_children=bool(children.get(node_id)),
            ),
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
            "compositing": {
                "sourceOrder": number((node.get("paint") or {}).get("sourceOrder")),
                "paintGroup": int(number((node.get("paint") or {}).get("paintGroup"), 2)),
                "stackingLevel": number((node.get("paint") or {}).get("stackingLevel")),
                "createsStackingContext": bool((node.get("paint") or {}).get("createsStackingContext")),
                "stackingContextReasons": (node.get("paint") or {}).get("stackingContextReasons") or [],
                "stackingContextOwnerNodeId": (node.get("paint") or {}).get("stackingContextOwnerNodeId"),
                "clipOwnerNodeId": (
                    node_id
                    if str(style.get("overflowX") or "visible") in {"hidden", "clip"}
                    or str(style.get("overflowY") or "visible") in {"hidden", "clip"}
                    else None
                ),
                "clipPath": str(style.get("clipPath") or "none"),
                "maskImage": str(style.get("maskImage") or "none"),
                "mixBlendMode": str(style.get("mixBlendMode") or "normal"),
                "isolation": str(style.get("isolation") or "auto"),
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
        slots = compound_slots(node, child_ids, nodes, axis, design_scale)
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
    for container in containers:
        container["geometrySystem"] = container_geometry_system(container, nodes, node_plan_by_id)
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
        adaptive_track = adaptive_track_contract(grid_tracks_plan)
        if adaptive_track and adaptive_track.get("minimumItemWidthPt") is not None:
            adaptive_track["minimumItemWidthPt"] = number(adaptive_track["minimumItemWidthPt"]) * design_scale
        column_count = (
            1 if section_kind == "list" or horizontal
            else (0 if adaptive_track else len(grid_tracks_plan)) or inferred_column_count(item_nodes)
        )
        source_samples = responsive_entries(source, responsive_index)
        item_sample_sets = {item_id: responsive_entries(nodes[item_id], responsive_index) for item_id in item_ids}
        responsive_breakpoints = []
        for sample in source_samples:
            width_pt = number(sample.get("widthPt"))
            sampled_items = [
                next((entry for entry in entries if abs(number(entry.get("widthPt")) - width_pt) <= 0.1), None)
                for entries in item_sample_sets.values()
            ]
            sampled_items = [item for item in sampled_items if item]
            if not sampled_items:
                continue
            sampled_nodes = [{"layout": {"rect": item.get("rect") or {}}} for item in sampled_items]
            responsive_breakpoints.append({
                "containerWidthPt": number((sample.get("rect") or {}).get("width")),
                "targetWidthPt": width_pt,
                "columnCount": 1 if section_kind == "list" or horizontal else inferred_column_count(sampled_nodes),
                "itemWidthPt": median([number((item.get("rect") or {}).get("width")) for item in sampled_items]),
                "itemHeightPt": median([number((item.get("rect") or {}).get("height")) for item in sampled_items]),
                "maximumTextLineCount": max([int(item.get("lineCount") or 0) for item in sampled_items] or [0]),
            })
        observed_column_counts = {int(item["columnCount"]) for item in responsive_breakpoints}
        if not adaptive_track and len(observed_column_counts) > 1:
            multi_column_widths = [
                number(item.get("itemWidthPt"))
                for item in responsive_breakpoints
                if int(item.get("columnCount") or 1) > 1 and number(item.get("itemWidthPt")) > 0
            ]
            adaptive_track = {
                "mode": "responsive-observed",
                "minimumItemWidthPt": min(multi_column_widths) if multi_column_widths else None,
                "maximumFraction": 1,
                "raw": None,
            }
        item_sizing_by_node_id = {}
        for item_id, item_node in zip(item_ids, item_nodes):
            item_rect = rect(item_node)
            item_style = item_node.get("style") or {}
            authored_width = authored_value(item_style, "width")
            authored_height = authored_value(item_style, "height")
            item_ratio = item_rect["width"] / item_rect["height"] if item_rect["width"] > 0 and item_rect["height"] > 0 else None
            samples_for_item = item_sample_sets.get(item_id) or []
            sample_widths = [number((entry.get("rect") or {}).get("width")) for entry in samples_for_item]
            sample_heights = [number((entry.get("rect") or {}).get("height")) for entry in samples_for_item]
            item_sizing_by_node_id[item_id] = {
                "widthMode": "fixed" if px(authored_width) is not None or (horizontal and nearly_uniform(sample_widths or [item_rect["width"]])) else "fractional",
                "widthPt": item_rect["width"] if px(authored_width) is not None or horizontal else None,
                "widthFraction": None if horizontal else 1 / max(column_count, 1),
                "heightMode": "fixed" if px(authored_height) is not None else "aspect-ratio" if section_kind == "grid" and item_ratio else "estimated",
                "heightPt": item_rect["height"] if px(authored_height) is not None else None,
                "estimatedHeightPt": median(sample_heights) or item_rect["height"] or 72,
                "aspectRatio": item_ratio if section_kind == "grid" and px(authored_height) is None else None,
                "columnSpan": int((node_plan_by_id.get(item_id) or {}).get("gridItem", {}).get("columnSpan") or 1),
                "rowSpan": int((node_plan_by_id.get(item_id) or {}).get("gridItem", {}).get("rowSpan") or 1),
                "lineCountsByWidth": [
                    {"targetWidthPt": number(entry.get("widthPt")), "count": int(entry.get("lineCount") or 0)}
                    for entry in samples_for_item if entry.get("lineCount") is not None
                ],
            }
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
            "adaptiveColumns": adaptive_track,
            "responsiveBreakpoints": responsive_breakpoints,
            "contentInsetsPt": [value * design_scale for value in edges(source_style.get("padding"))],
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
            "itemSizingByNodeId": item_sizing_by_node_id,
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
    parser.add_argument("--responsive-analysis", action="append", type=Path)
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
    responsive_payloads = [load_json(path) for path in (args.responsive_analysis or [])]
    responsive_cursor = 0
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
                responsive_payloads[responsive_cursor] if responsive_cursor < len(responsive_payloads) else None,
                max(number((payload.get("target") or {}).get("scale"), 1), 0.01),
            ))
            responsive_cursor += 1
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "architecturePlanSha256": sha256(args.architecture_plan),
        "layoutRelationGraphSha256": sha256(args.layout_graph),
        "inputs": inputs,
        "responsiveInputs": [
            {"path": str(path.resolve()), "sha256": sha256(path)}
            for path in (args.responsive_analysis or [])
        ],
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
