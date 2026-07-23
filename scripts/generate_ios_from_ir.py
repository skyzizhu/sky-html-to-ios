#!/usr/bin/env python3
"""Generate a deterministic native iOS implementation from resolved UI IR files."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import shutil
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GENERATOR_VERSION = "1.13.0"
MANIFEST_NAME = ".html-to-ios-generation.json"
SYSTEM_CHROME_TOKENS = (
    "statusbar",
    "status-bar",
    "dynamicisland",
    "dynamic-island",
    "homeindicator",
    "home-indicator",
    "notch",
)
PRESENTATION_KINDS = {
    "sheet",
    "full-screen",
    "fullscreen",
    "full-screen-overlay",
    "popover",
    "popover-overlay",
    "overlay",
    "dialog",
}
SYMBOL_SYSTEM_IMAGES = {
    "→": "arrow.right", "←": "arrow.left", "↑": "arrow.up", "↓": "arrow.down",
    "›": "chevron.right", "‹": "chevron.left", "⌄": "chevron.down", "⌃": "chevron.up",
    "✓": "checkmark", "✔": "checkmark", "✕": "xmark", "×": "xmark",
    "+": "plus", "−": "minus",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", action="append", required=True, type=Path, help="Resolved UI IR; repeat for multiple screens")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--ui-stack", choices=("swiftui", "uikit"))
    parser.add_argument("--module-name", default="HTMLToIOSGenerated")
    parser.add_argument("--architecture-plan", type=Path)
    parser.add_argument("--naming-plan", type=Path)
    parser.add_argument("--conflict-dir", type=Path)
    parser.add_argument("--allow-unresolved", action="store_true")
    parser.add_argument("--overwrite-modified", action="store_true")
    parser.add_argument(
        "--allow-nonstandard-output",
        action="store_true",
        help="Allow a deliberate project-specific output path instead of Generated/HTMLToIOS",
    )
    return parser.parse_args()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


def load_ir(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "1.2":
        raise ValueError(f"{path}: expected UI IR schemaVersion 1.2")
    if not data.get("screens"):
        raise ValueError(f"{path}: no screens found")
    return data


def load_architecture_plan(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "native-architecture-plan-1.0":
        raise ValueError(f"{path}: expected native-architecture-plan-1.0")
    if not (data.get("invariants") or {}).get("safeAreaNeverSubtractedFromWidthOrHeight"):
        raise ValueError(f"{path}: Safe Area dimension invariant is missing")
    screens = data.get("screens") or []
    result = {str(screen.get("screenId") or ""): screen for screen in screens}
    if "" in result:
        raise ValueError(f"{path}: every architecture screen needs a screenId")
    for screen_id, screen in result.items():
        safe_area = screen.get("safeArea") or {}
        scroll = screen.get("scroll") or {}
        if safe_area.get("subtractFromContainerDimensions") is not False:
            raise ValueError(f"{path}: {screen_id} attempts to subtract Safe Area from container dimensions")
        if scroll.get("subtractSafeAreaFromFrame") is not False:
            raise ValueError(f"{path}: {screen_id} attempts to subtract Safe Area from a scroll frame")
    return result


def load_naming_prefix(path: Path | None) -> tuple[str, str | None, set[str]]:
    if path is None:
        return "HTMLToIOS", None, set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "native-naming-plan-1.0":
        raise ValueError(f"{path}: expected native-naming-plan-1.0")
    prefix = re.sub(r"[^A-Za-z0-9_]", "", str(data.get("prefix") or ""))
    if not prefix or prefix[0].isdigit():
        raise ValueError(f"{path}: invalid generated prefix {prefix!r}")
    return prefix, str(data.get("source") or "") or None, {str(item) for item in data.get("existingTypeNames") or []}


def safe_identifier(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not result or result[0].isdigit():
        result = "screen_" + result
    return result


SWIFT_RESERVED_TYPE_NAMES = {
    "Any", "AnyObject", "Class", "Controller", "Protocol", "Self", "Type", "View",
}


def swift_type_name(value: str, fallback: str = "Screen") -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    result = "".join(part[:1].upper() + part[1:] for part in parts) or fallback
    if result[0].isdigit():
        result = fallback + result
    return result + "Feature" if result in SWIFT_RESERVED_TYPE_NAMES else result


def assign_screen_modules(screens: list[dict[str, Any]]) -> None:
    explicit_ids = {
        str(screen["moduleId"])
        for screen in screens
        if screen.get("moduleId")
    }
    screen_ids = {str(screen["id"]) for screen in screens}
    for screen in screens:
        screen_id = str(screen["id"])
        module_id = str(screen.get("moduleId") or "")
        if not module_id:
            candidates = [
                candidate
                for candidate in explicit_ids | screen_ids
                if candidate != screen_id and screen_id.startswith(candidate + "-")
            ]
            module_id = max(candidates, key=len) if candidates else screen_id
        screen["moduleId"] = module_id
        screen["moduleType"] = swift_type_name(module_id, "Feature")
        screen["screenType"] = swift_type_name(screen_id, "Screen")

    normalized_modules: dict[str, str] = {}
    for screen in screens:
        module_type = str(screen["moduleType"])
        module_id = str(screen["moduleId"])
        previous = normalized_modules.setdefault(module_type, module_id)
        if previous != module_id:
            raise ValueError(f"module IDs {previous!r} and {module_id!r} normalize to the same Swift directory {module_type!r}")


def number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return default


def compact_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def compact_html_text(value: Any, limit: int = 80) -> str:
    return compact_text(re.sub(r"<[^>]+>", "", str(value or "")), limit)


def is_system_chrome(node: dict[str, Any]) -> bool:
    source = node.get("source") or {}
    haystack = " ".join(
        str(source.get(key) or "") for key in ("selector", "domId", "runtimeId")
    ).lower()
    return any(token in haystack for token in SYSTEM_CHROME_TOKENS)


def is_status_bar_chrome(node: dict[str, Any]) -> bool:
    source = node.get("source") or {}
    haystack = " ".join(
        str(source.get(key) or "") for key in ("selector", "domId", "runtimeId")
    ).lower()
    return any(token in haystack for token in ("statusbar", "status-bar", "dynamicisland", "dynamic-island", "notch"))


def color_string(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"transparent", "rgba(0, 0, 0, 0)"}:
        return None
    return text


def gradient_colors(value: Any) -> list[str]:
    return gradient_spec(value)["colors"]


def split_css_commas(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(depth - 1, 0)
        elif character == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return [part for part in parts if part]


def gradient_spec(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    match = re.search(r"(linear|radial)-gradient\((.*)\)", text, re.IGNORECASE)
    if not match:
        return {"kind": None, "angle": None, "colors": [], "locations": []}
    kind = match.group(1).lower()
    parts = split_css_commas(match.group(2))
    angle = 180.0
    if kind == "linear" and parts:
        direction = parts[0].lower()
        angle_match = re.fullmatch(r"(-?\d+(?:\.\d+)?)deg", direction)
        directions = {
            "to top": 0.0, "to top right": 45.0, "to right top": 45.0,
            "to right": 90.0, "to bottom right": 135.0, "to right bottom": 135.0,
            "to bottom": 180.0, "to bottom left": 225.0, "to left bottom": 225.0,
            "to left": 270.0, "to top left": 315.0, "to left top": 315.0,
        }
        if angle_match:
            angle = float(angle_match.group(1)) % 360
            parts = parts[1:]
        elif direction in directions:
            angle = directions[direction]
            parts = parts[1:]
    colors: list[str] = []
    locations: list[float | None] = []
    for part in parts:
        color_match = re.search(r"rgba?\([^)]*\)|hsla?\([^)]*\)|#[0-9a-fA-F]{3,8}\b", part)
        if not color_match:
            continue
        colors.append(color_match.group(0))
        location_match = re.search(r"(-?\d+(?:\.\d+)?)%\s*$", part)
        locations.append(float(location_match.group(1)) / 100 if location_match else None)
    return {"kind": kind, "angle": angle if kind == "linear" else None, "colors": colors[:8], "locations": locations[:8]}


def shadow_spec(value: Any, scale: float) -> dict[str, Any]:
    first = split_css_commas(str(value or "none"))[0]
    if first == "none" or "inset" in first.lower():
        return {"color": None, "x": 0.0, "y": 0.0, "radius": 0.0, "spread": 0.0}
    color_match = re.search(r"rgba?\([^)]*\)|hsla?\([^)]*\)|#[0-9a-fA-F]{3,8}\b", first)
    color = color_match.group(0) if color_match else None
    dimensions = re.findall(r"-?\d+(?:\.\d+)?(?:px)?", first[:color_match.start()] + first[color_match.end():] if color_match else first)
    values = [number(item) * scale for item in dimensions[:4]]
    values += [0.0] * (4 - len(values))
    return {"color": color, "x": values[0], "y": values[1], "radius": max(values[2] / 2, 0), "spread": values[3]}


def system_image_name(node: dict[str, Any], parent: dict[str, Any] | None = None) -> str | None:
    symbol = compact_text((node.get("content") or {}).get("text"), 4)
    if symbol in SYMBOL_SYSTEM_IMAGES:
        return SYMBOL_SYSTEM_IMAGES[symbol]
    source = node.get("source") or {}
    selector = str(source.get("selector") or "")
    leaf_selector = selector.rsplit(">", 1)[-1]
    haystack = " ".join(
        [leaf_selector, str(source.get("domId") or ""), str(source.get("runtimeId") or "")]
    ).lower()
    parent_source = (parent or {}).get("source") or {}
    parent_selector = str(parent_source.get("selector") or "")
    parent_haystack = " ".join(
        [
            parent_selector.rsplit(">", 1)[-1],
            str(parent_source.get("domId") or ""),
            str(parent_source.get("runtimeId") or ""),
        ]
    ).lower()
    if "cta" in parent_haystack:
        return "sparkles"
    parent_mappings = (
        (("fullscreen",), "arrow.up.left.and.arrow.down.right"),
        (("paste",), "doc.on.clipboard"),
        (("import", "upload", "ispick"), "square.and.arrow.up"),
        (("emoji",), "face.smiling"),
        (("dt-arrow",), "chevron.down"),
        (("chk",), "checkmark"),
        (("fsedit",), "pencil"),
        (("fsdone",), "checkmark"),
        (("di-typo",), "textformat"),
        (("di-spell",), "textformat.abc"),
        (("di-gram",), "text.book.closed"),
        (("di-flu",), "text.line.first.and.arrowtriangle.forward"),
        (("di-idiom",), "quote.bubble"),
        (("di-punc",), "textformat.alt"),
        (("di-dup",), "doc.on.doc"),
        (("di-name",), "person.text.rectangle"),
        (("span.ai",), "sparkles"),
    )
    for tokens, name in parent_mappings:
        if any(token in parent_haystack for token in tokens):
            return name
    mappings = (
        (("fullscreen", "expand"), "arrow.up.left.and.arrow.down.right"),
        (("paste", "clipboard"), "doc.on.clipboard"),
        (("upload", "share"), "square.and.arrow.up"),
        (("emoji", "smile"), "face.smiling"),
        (("chevron", "arrow"), "chevron.down"),
        (("check", "selected"), "checkmark"),
        (("spark", "magic"), "sparkles"),
        (("close", "dismiss"), "xmark"),
        (("back",), "chevron.left"),
        (("copy",), "doc.on.doc"),
        (("download", "export"), "square.and.arrow.down"),
        (("info",), "info.circle"),
        (("plus", "add"), "plus"),
    )
    for tokens, name in mappings:
        if any(token in haystack for token in tokens):
            return name
    return "circle.fill" if str(node.get("semanticType")) == "icon" else None


def edge_values(value: Any) -> list[float]:
    if isinstance(value, list):
        values = [number(item) for item in value]
    else:
        values = [number(item) for item in str(value or "0").split()]
    if len(values) == 1:
        return values * 4
    if len(values) == 2:
        return [values[0], values[1], values[0], values[1]]
    if len(values) == 3:
        return [values[0], values[1], values[2], values[1]]
    return (values + [0, 0, 0, 0])[:4]


def scaled_edges(value: Any, scale: float) -> list[float]:
    return [item * scale for item in edge_values(value)]


def scaled_css_value(value: Any, scale: float, default: float = 0.0) -> float:
    text = str(value or "").strip().lower()
    if not text or text in {"normal", "auto", "none"}:
        return default
    return number(value, default) * scale


def grid_column_count(value: Any) -> int:
    text = str(value or "").strip()
    if not text or text == "none":
        return 1
    repeat = re.fullmatch(r"repeat\(\s*(\d+)\s*,.*\)", text)
    if repeat:
        return max(int(repeat.group(1)), 1)
    depth = 0
    columns = 1
    for character in text:
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(depth - 1, 0)
        elif character.isspace() and depth == 0:
            columns += 1
    return min(max(columns, 1), 12)


def transform_component(value: Any, name: str, default: float) -> float:
    text = str(value or "").strip().lower()
    if name == "rotation":
        match = re.search(r"rotate\(\s*(-?\d+(?:\.\d+)?)deg\s*\)", text)
    else:
        match = re.search(r"scale\(\s*(-?\d+(?:\.\d+)?)\s*\)", text)
    return float(match.group(1)) if match else default


def motion_payload(raw: dict[str, Any]) -> dict[str, Any] | None:
    keyframes = raw.get("keyframes") or []
    properties = set(raw.get("properties") or [])
    if not keyframes or not properties.intersection({"transform", "opacity"}):
        return None
    ordered = sorted(keyframes, key=lambda item: number(item.get("computedOffset"), number(item.get("offset"))))
    start = ordered[0]
    middle = min(ordered, key=lambda item: abs(number(item.get("computedOffset"), number(item.get("offset"))) - 0.5))
    end = ordered[-1]
    rotation_start = transform_component(start.get("transform"), "rotation", 0)
    rotation_end = transform_component(end.get("transform"), "rotation", rotation_start)
    scale_start = transform_component(start.get("transform"), "scale", 1)
    scale_middle = transform_component(middle.get("transform"), "scale", scale_start)
    scale_end = transform_component(end.get("transform"), "scale", scale_start)
    opacity_start = number(start.get("opacity"), 1)
    opacity_middle = number(middle.get("opacity"), opacity_start)
    opacity_end = number(end.get("opacity"), opacity_start)
    return {
        "id": str(raw.get("id") or "motion"),
        "durationMilliseconds": max(int(number(raw.get("durationMs"), 0)), 1),
        "delayMilliseconds": max(int(number(raw.get("delayMs"), 0)), 0),
        "repeats": str(raw.get("iterationCount") or "1").lower() in {"infinity", "infinite"},
        "reverses": str(raw.get("direction") or "normal").lower() in {"reverse", "alternate-reverse"},
        "autoreverses": str(raw.get("direction") or "normal").lower() in {"alternate", "alternate-reverse"},
        "rotationDegrees": rotation_end - rotation_start,
        "scaleValues": [scale_start, scale_middle, scale_end],
        "opacityValues": [opacity_start, opacity_middle, opacity_end],
    }


def primary_transition(interaction: dict[str, Any]) -> dict[str, Any]:
    effects = (((interaction.get("evidence") or {}).get("ast") or {}).get("effects") or [])
    feedback_effect = next((item for item in effects if item.get("type") == "content-mutation" and item.get("value")), None)
    delayed_effects = [item for item in effects if ((item.get("schedule") or {}).get("ms") or 0) > 0]
    feedback_duration = max((int((item.get("schedule") or {}).get("ms") or 0) for item in delayed_effects), default=0)
    transitions = ((interaction.get("payload") or {}).get("transitions") or [])
    if transitions:
        first = transitions[0]
        schedule = first.get("schedule") or {}
        return {
            "interactionID": interaction.get("id"),
            "action": first.get("action") or interaction.get("action") or "none",
            "target": first.get("target") or interaction.get("target"),
            "targetScreenID": first.get("targetScreenId"),
            "targetStateID": first.get("targetStateId"),
            "delayMilliseconds": int(schedule.get("delayMs") or schedule.get("ms") or 0),
            "feedbackText": compact_html_text((feedback_effect or {}).get("value"), 80) or None,
            "feedbackDurationMilliseconds": feedback_duration,
            "presentation": interaction.get("presentation"),
        }
    return {
        "interactionID": interaction.get("id"),
        "action": interaction.get("action") or "none",
        "target": interaction.get("target"),
        "targetScreenID": None,
        "targetStateID": None,
        "delayMilliseconds": 0,
        "feedbackText": compact_html_text((feedback_effect or {}).get("value"), 80) or None,
        "feedbackDurationMilliseconds": feedback_duration,
        "presentation": interaction.get("presentation"),
    }


@dataclass
class ScreenBuildContext:
    screen_id: str
    root_width: float
    design_scale: float
    nodes: dict[str, dict[str, Any]]
    children: dict[str | None, list[str]]
    actions: dict[str, dict[str, Any]]
    assets: dict[str, dict[str, Any]]
    expansion_states: dict[str, str]
    selection_bindings: dict[str, dict[str, Any]]
    selection_count_bindings: dict[str, dict[str, Any]]
    motions: dict[str, list[dict[str, Any]]]
    detached_root_ids: set[str]
    has_bottom_bar: bool


def rich_text_runs(
    context: ScreenBuildContext,
    node: dict[str, Any],
    *,
    allow_block_children: bool = False,
) -> list[dict[str, Any]]:
    content_runs = (node.get("content") or {}).get("runs") or []
    if any(str(item.get("nodeId") or "") in context.selection_count_bindings for item in content_runs):
        return []
    referenced = [context.nodes.get(str(item.get("nodeId") or "")) for item in content_runs if item.get("nodeId")]
    if not referenced or (not allow_block_children and any(
        str((child.get("style") or {}).get("display") or "") not in {"inline", "inline-block", "contents"}
        for child in referenced
        if child
    )):
        return []
    result = []
    for item in content_runs:
        text = re.sub(r"\s+", " ", str(item.get("text") or ""))
        if not text.strip():
            continue
        run_node = context.nodes.get(str(item.get("nodeId") or "")) or node
        style = run_node.get("style") or {}
        foreground = color_string(style.get("color"))
        background = color_string(style.get("backgroundColor"))
        colors = gradient_colors(style.get("backgroundImage"))
        if colors and foreground is None:
            foreground = colors[0]
        elif colors and background is None:
            background = colors[0]
        result.append({
            "text": text,
            "fontSize": min(max(number(style.get("fontSize"), 16) * context.design_scale, 8), 72),
            "fontWeight": str(style.get("fontWeight") or "400"),
            "foreground": foreground,
            "background": background,
            "lineHeight": scaled_css_value(style.get("lineHeight"), context.design_scale) or None,
            "letterSpacing": scaled_css_value(style.get("letterSpacing"), context.design_scale),
        })
    return result


def node_payload(context: ScreenBuildContext, node_id: str, presentation: bool = False) -> dict[str, Any] | None:
    node = context.nodes.get(node_id)
    if not node or is_system_chrome(node):
        return None
    if not presentation and node_id in context.detached_root_ids:
        return None
    style = node.get("style") or {}
    parent_id = str(node.get("parentId") or "")
    parent_state_id = context.expansion_states.get(parent_id)
    node_rect = (node.get("layout") or {}).get("rect") or {}
    expansion_content = bool(
        parent_state_id
        and (
            (node.get("state") or {}).get("initiallyVisible") is False
            or number(node_rect.get("height")) <= 0
            or str(style.get("overflowY") or "visible") == "hidden"
        )
    )
    if not presentation and (node.get("state") or {}).get("initiallyVisible") is False and not expansion_content:
        return None
    if style.get("display") == "none" and not presentation and not expansion_content:
        return None
    if (
        not presentation
        and number(style.get("opacity"), 1) <= 0
        and str(style.get("pointerEvents") or "") == "none"
    ):
        return None

    child_entries = []
    for child_id in context.children.get(node_id, []):
        child = node_payload(context, child_id, presentation=presentation)
        if child:
            child_node = context.nodes.get(child_id) or {}
            child_layout = child_node.get("layout") or {}
            child_style = child_node.get("style") or {}
            child_position = str(child_layout.get("position") or child_style.get("position") or "")
            child_entries.append((child, child_position in {"absolute", "fixed"}))

    flow_child_payloads = [child for child, is_positioned_child in child_entries if not is_positioned_child]
    absolute_child_payloads = [child for child, is_positioned_child in child_entries if is_positioned_child]
    # Pure absolute-positioned groups are native overlays themselves. In mixed
    # containers, keep positioned children out of Stack layout so CSS decoration
    # and floating controls cannot change the parent's measured size.
    if flow_child_payloads and absolute_child_payloads:
        child_payloads = flow_child_payloads
        overlay_child_payloads = absolute_child_payloads
    else:
        child_payloads = [child for child, _ in child_entries]
        overlay_child_payloads = []

    semantic = str(node.get("semanticType") or "container")
    content = node.get("content") or {}
    text = compact_text(content.get("text"))
    if semantic in {"text", "label"} and text in SYMBOL_SYSTEM_IMAGES:
        semantic = "icon"
    placeholder = compact_text(content.get("placeholder"), 120)
    action = context.actions.get(node_id)
    if presentation and node_id in context.detached_root_ids and (action or {}).get("action") == "dismiss":
        action = None
    layout = node.get("layout") or {}
    rect = layout.get("rect") or {}
    width = number(rect.get("width"))
    height = number(rect.get("height"))
    width_fraction = min(max(width / context.root_width, 0.0), 1.0) if context.root_width else 0.0
    mode = str(layout.get("mode") or "flow")
    display = str(style.get("display") or "").lower()
    flex_direction = str(style.get("flexDirection") or "row").lower()
    absolute_child_count = len(absolute_child_payloads)
    flow_child_count = len(flow_child_payloads)
    if absolute_child_count > 0 and flow_child_count == 0:
        axis = "overlay"
    elif display in {"flex", "inline-flex"}:
        axis = "vertical" if flex_direction.startswith("column") else "horizontal"
    elif display in {"grid", "inline-grid"}:
        axis = "grid"
    elif mode in {"flex-row", "grid-row"}:
        axis = "horizontal"
    elif "grid" in mode:
        axis = "grid"
    else:
        axis = "vertical"
    if display in {"flex", "inline-flex"} and flex_direction.endswith("reverse"):
        child_payloads.reverse()
    inline_runs = rich_text_runs(context, node, allow_block_children=True)
    inline_text_container = bool(
        semantic == "container"
        and axis == "horizontal"
        and inline_runs
        and child_payloads
        and all(
            child.get("semantic") in {"text", "label", "icon"} and child.get("action") is None
            for child in child_payloads
        )
    )
    if inline_text_container:
        semantic = "text"
        text = "".join(str(run.get("text") or "") for run in inline_runs)
        child_payloads = []
    padding = scaled_edges(style.get("padding"), context.design_scale)
    margin = scaled_edges(style.get("margin"), context.design_scale)
    border_widths = scaled_edges(style.get("borderWidths"), context.design_scale)
    border_index = max(range(4), key=lambda index: border_widths[index])
    border_colors = style.get("borderColors") or []
    border_styles = style.get("borderStyles") or []
    border_color = color_string(border_colors[border_index]) if border_index < len(border_colors) else None
    border_style = str(border_styles[border_index] or "solid") if border_index < len(border_styles) else "solid"
    gradient = gradient_spec(style.get("backgroundImage"))
    shadow = shadow_spec(style.get("boxShadow"), context.design_scale)
    if context.has_bottom_bar and semantic == "scroll":
        padding[2] = 0
    radii = style.get("cornerRadii") or [0]
    radius_values = []
    for item in radii:
        raw_radius = str(item or "").strip()
        if raw_radius.endswith("%") and width > 0 and height > 0:
            radius_values.append(min(width, height) * min(max(number(raw_radius), 0), 100) / 100)
        else:
            radius_values.append(number(item) * context.design_scale)
    corner_radius = max(radius_values) if radius_values else 0.0
    measured_height = min(max(number(rect.get("height")), 0.0), 160.0)
    min_height = measured_height if semantic in {"button", "input", "text-field", "secure-field", "toggle", "switch", "progress", "progress-view"} else 0
    decorative = bool(content.get("isDecorative"))
    has_visual_style = (
        color_string(style.get("backgroundColor")) is not None
        or bool(gradient["colors"])
        or corner_radius > 0
        or max(border_widths) > 0
        or shadow["color"] is not None
        or bool(node.get("assetRef"))
    )
    parent = context.nodes.get(parent_id) or {}
    parent_style = parent.get("style") or {}
    parent_layout = parent.get("layout") or {}
    parent_rect = parent_layout.get("rect") or {}
    is_positioned = str(layout.get("position") or "") in {"absolute", "fixed"}
    offset_x = 0.0
    offset_y = 0.0
    if is_positioned and number(parent_rect.get("width")) > 0 and number(parent_rect.get("height")) > 0:
        offset_x = (
            number(rect.get("x"))
            + width / 2
            - number(parent_rect.get("x"))
            - number(parent_rect.get("width")) / 2
        )
        offset_y = (
            number(rect.get("y"))
            + height / 2
            - number(parent_rect.get("y"))
            - number(parent_rect.get("height")) / 2
        )
    if presentation and node_id in context.detached_root_ids:
        offset_x = 0.0
        offset_y = 0.0
    parent_flex_direction = str(parent_style.get("flexDirection") or "row").lower()
    parent_horizontal = (
        (
            str(parent_style.get("display") or "").lower() in {"flex", "inline-flex"}
            and parent_flex_direction.startswith("row")
        )
        or str(parent_layout.get("mode") or "") in {"flex-row", "grid-row"}
        or str(parent_layout.get("scrollAxis") or "") == "horizontal"
    )
    scroll_axis = str(layout.get("scrollAxis") or "none")
    parent_scroll_axis = str(parent_layout.get("scrollAxis") or "none")
    line_count = int(number(content.get("lines"), 0))
    explicit_line_clamp = int(number(style.get("webkitLineClamp"), 0))
    explicit_no_wrap = str(style.get("whiteSpace") or "").lower() == "nowrap"
    inferred_compact_single_line = bool(
        parent_horizontal
        and line_count == 1
        and semantic in {"text", "label", "heading", "button", "link", "menu-item", "tab-item"}
        and not content.get("clippedHorizontally")
    )
    text_line_limit = explicit_line_clamp if explicit_line_clamp > 0 else (1 if explicit_no_wrap or inferred_compact_single_line else None)
    ratio = width / height if width > 0 and height > 0 else None
    compact_visual_container = bool(
        width > 0
        and height > 0
        and width <= 180
        and height <= 180
        and ratio is not None
        and 0.8 <= ratio <= 1.25
        and semantic not in {"text", "label", "heading", "image", "icon"}
        and has_visual_style
        and not text
    )
    measured_visual_leaf = bool(
        width > 0
        and height > 0
        and width <= context.root_width
        and height <= context.root_width * 2
        and width_fraction < 0.88
        and semantic not in {"text", "label", "heading", "image", "icon"}
        and has_visual_style
        and not child_payloads
        and not text
        and not action
    )
    horizontal_scroll_item = parent_scroll_axis == "horizontal" and width_fraction < 0.95
    compact_overlay_geometry = bool(
        overlay_child_payloads
        and width > 0
        and height > 0
        and width_fraction < 0.75
        and semantic not in {"text", "label", "heading", "image", "icon"}
    )
    preserves_intrinsic_width = bool(
        str(style.get("flexShrink") or "1") == "0"
        or explicit_no_wrap
        or inferred_compact_single_line
        or horizontal_scroll_item
        or compact_visual_container
        or measured_visual_leaf
        or compact_overlay_geometry
    )
    fixed_width = width if (
        compact_visual_container
        or measured_visual_leaf
        or compact_overlay_geometry
        or (horizontal_scroll_item and semantic not in {"image", "icon"})
    ) else None
    fixed_height = height if (
        compact_visual_container
        or measured_visual_leaf
        or compact_overlay_geometry
        or (semantic == "carousel" and scroll_axis == "horizontal")
    ) else None
    preserves_aspect_ratio = bool(
        ratio is not None
        and (
            compact_visual_container
            or measured_visual_leaf
            or compact_overlay_geometry
            or semantic in {"image", "icon", "canvas-artwork"}
        )
    )

    if (
        not child_payloads
        and not overlay_child_payloads
        and not text
        and not placeholder
        and not action
        and decorative
        and not has_visual_style
        and not node.get("assetRef")
    ):
        return None
    if (
        semantic == "container"
        and not child_payloads
        and not overlay_child_payloads
        and not text
        and not action
        and not has_visual_style
    ):
        return None

    asset = context.assets.get(str(node.get("assetRef") or "")) or {}
    asset_kind = str(asset.get("kind") or "")
    is_foreground_asset = semantic in {"image", "icon"}
    selection = context.selection_bindings.get(node_id) or {}
    selection_count = context.selection_count_bindings.get(node_id) or {}
    payload = {
        "id": node_id,
        "semantic": semantic,
        "text": text,
        "placeholder": placeholder,
        "axis": axis,
        "children": child_payloads,
        "overlayChildren": overlay_child_payloads,
        "action": action,
        "style": {
            "fontSize": min(max(number(style.get("fontSize"), 16) * context.design_scale, 8), 72),
            "fontWeight": str(style.get("fontWeight") or "400"),
            "lineHeight": scaled_css_value(style.get("lineHeight"), context.design_scale) or None,
            "letterSpacing": scaled_css_value(style.get("letterSpacing"), context.design_scale),
            "foreground": color_string(style.get("color")),
            "background": color_string(style.get("backgroundColor")),
            "gradientColors": gradient["colors"],
            "gradientLocations": gradient["locations"],
            "gradientKind": gradient["kind"],
            "gradientAngle": gradient["angle"],
            "cornerRadius": min(corner_radius, 120),
            "borderWidth": min(max(border_widths), 20),
            "borderColor": border_color,
            "borderStyle": border_style,
            "opacity": min(max(number(style.get("opacity"), 1), 0), 1),
            "shadowColor": shadow["color"],
            "shadowOffsetX": shadow["x"],
            "shadowOffsetY": shadow["y"],
            "shadowRadius": min(shadow["radius"], 80),
            "shadowSpread": min(max(shadow["spread"], -40), 40),
            "offsetX": offset_x,
            "offsetY": offset_y,
            "zIndex": number(style.get("zIndex"), 0),
            "clipsContent": str(style.get("overflowX") or "visible") in {"hidden", "clip"}
                or str(style.get("overflowY") or "visible") in {"hidden", "clip"},
            "padding": padding,
            "margin": margin,
            "spacing": min(max(scaled_css_value(style.get("gap"), context.design_scale), 0), 40),
            "widthFraction": width_fraction,
            "minHeight": min_height,
            "preferredWidth": min(max(width, 0), context.root_width),
            "preferredHeight": min(max(number(rect.get("height")), 0), max(context.root_width * 3, 1200)),
            "resistsCompression": str(style.get("flexShrink") or "1") == "0",
            "preservesIntrinsicWidth": preserves_intrinsic_width,
            "fixedWidth": min(max(fixed_width, 0), context.root_width) if fixed_width is not None else None,
            "fixedHeight": min(max(fixed_height, 0), 240) if fixed_height is not None else None,
            "aspectRatio": ratio if preserves_aspect_ratio else None,
            "scrollAxis": scroll_axis,
            "textLineLimit": text_line_limit,
            "textOverflow": str(style.get("textOverflow") or "clip"),
            "textAlignment": str(style.get("textAlign") or "start"),
            "justifyContent": str(style.get("justifyContent") or "normal"),
            "alignItems": str(style.get("alignItems") or "normal"),
            "gridColumnCount": grid_column_count(style.get("gridTemplateColumns")) if axis == "grid" else None,
            "mediaContentMode": str(asset.get("renderMode") or style.get("objectFit") or "contain"),
            "mediaPosition": str(asset.get("position") or style.get("objectPosition") or "50% 50%"),
            "backgroundContentMode": str(asset.get("renderMode") or "cover") if asset_kind == "css-background" else None,
            "backgroundPosition": str(asset.get("position") or "50% 50%") if asset_kind == "css-background" else None,
            "backgroundRepeat": str(asset.get("repeat") or "no-repeat") if asset_kind == "css-background" else None,
        },
        "systemImage": system_image_name(node, context.nodes.get(str(node.get("parentId") or ""))),
        "assetName": asset.get("iosName") if is_foreground_asset else None,
        "backgroundAssetName": asset.get("iosName") if asset_kind == "css-background" else None,
        "accessibilityLabel": compact_text(content.get("accessibilityLabel"), 120) or None,
        "visibleWhenStateID": None,
        "selectionStateID": selection.get("stateID"),
        "isInitiallySelected": selection.get("initiallySelected"),
        "selectedForeground": selection.get("selectedForeground"),
        "selectedBackground": selection.get("selectedBackground"),
        "selectedGradientColors": selection.get("selectedGradientColors"),
        "unselectedForeground": selection.get("unselectedForeground"),
        "unselectedBackground": selection.get("unselectedBackground"),
        "unselectedGradientColors": selection.get("unselectedGradientColors"),
        "selectionIndicator": selection.get("selectionIndicator", False),
        "selectionCountStateID": selection_count.get("stateID"),
        "selectionCountInitial": selection_count.get("initial"),
        "selectionCountTotal": selection_count.get("total"),
        "richTextRuns": inline_runs if inline_text_container else rich_text_runs(context, node),
        "motions": context.motions.get(node_id) or [],
    }
    if expansion_content:
        payload["visibleWhenStateID"] = parent_state_id
    return payload


def build_screen(ir: dict[str, Any], architecture: dict[str, Any] | None = None) -> dict[str, Any]:
    screen = ir["screens"][0]
    screen_id = str(screen.get("id") or "screen")
    nodes_list = screen.get("nodes") or []
    nodes = {str(node["id"]): node for node in nodes_list}
    children: dict[str | None, list[str]] = {}
    for node in nodes_list:
        children.setdefault(node.get("parentId"), []).append(str(node["id"]))

    states_by_id = {str(state.get("id")): state for state in ir.get("states") or []}
    actions: dict[str, dict[str, Any]] = {}
    automatic_actions = []
    presentation_by_state: dict[str, dict[str, Any]] = {}
    for interaction in ir.get("interactions") or []:
        action = primary_transition(interaction)
        if (
            interaction.get("automatic")
            and int(action.get("delayMilliseconds") or 0) < 1000
            and re.search(r"(?:检测中|加载中|处理中|分析中|progress|loading|processing|analyzing)", str(screen.get("name") or ""), re.IGNORECASE)
        ):
            action["delayMilliseconds"] = 3200
        target_state = states_by_id.get(str(action.get("targetStateID") or "")) or {}
        state_kind = str(target_state.get("kind") or "") or None
        transitions = (interaction.get("payload") or {}).get("transitions") or []
        duplicate_state_transitions = sum(
            str(item.get("targetStateId") or "") == str(action.get("targetStateID") or "")
            for item in transitions
        )
        action["stateKind"] = state_kind
        action["selectionMode"] = "exclusive" if state_kind == "selection" and duplicate_state_transitions > 1 else "multiple"
        if action.get("targetStateID") and action.get("presentation"):
            presentation_by_state[str(action["targetStateID"])] = dict(action["presentation"])
        if interaction.get("automatic"):
            automatic_actions.append(action)
        source_ids = [str(item) for item in (interaction.get("sourceNodeIds") or [interaction.get("sourceNodeId")]) if item]
        target_ids = [str(item) for item in target_state.get("targetNodeIds") or []]
        for index, source_id in enumerate(source_ids):
            if source_id:
                node_action = dict(action)
                node_action["sourceNodeID"] = source_id
                if state_kind == "selection" and len(source_ids) == len(target_ids):
                    node_action["targetNodeID"] = target_ids[index]
                elif state_kind == "local-state":
                    current = source_id
                    while current:
                        if current in target_ids:
                            node_action["targetNodeID"] = current
                            break
                        current = str((nodes.get(current) or {}).get("parentId") or "")
                    if node_action.get("targetNodeID") and node_action.get("targetNodeID") != source_id:
                        node_action["localEffect"] = "remove"
                actions[source_id] = node_action

    selection_bindings: dict[str, dict[str, Any]] = {}
    for state in states_by_id.values():
        if str(state.get("kind") or "") != "selection":
            continue
        target_ids = [str(item) for item in state.get("targetNodeIds") or []]
        candidates = [nodes[item] for item in target_ids if item in nodes]
        if not candidates:
            continue
        def selected(node: dict[str, Any]) -> bool:
            selector = str((node.get("source") or {}).get("selector") or "").lower()
            leaf = selector.rsplit(">", 1)[-1]
            style = node.get("style") or {}
            explicit = (node.get("state") or {}).get("selected")
            if explicit is not None:
                return bool(explicit)
            if ".off" in leaf:
                return False
            if ".chk" in leaf:
                return True
            return bool(gradient_colors(style.get("backgroundImage")) and color_string(style.get("color")) == "rgb(255, 255, 255)")
        selected_node = next((item for item in candidates if selected(item)), candidates[0])
        unselected_node = next((item for item in candidates if not selected(item)), candidates[0])
        selected_style = selected_node.get("style") or {}
        unselected_style = unselected_node.get("style") or {}
        for item in candidates:
            selector = str((item.get("source") or {}).get("selector") or "").lower().rsplit(">", 1)[-1]
            selection_bindings[str(item["id"])] = {
                "stateID": str(state.get("id")),
                "initiallySelected": selected(item),
                "selectedForeground": color_string(selected_style.get("color")),
                "selectedBackground": color_string(selected_style.get("backgroundColor")),
                "selectedGradientColors": gradient_colors(selected_style.get("backgroundImage")),
                "unselectedForeground": color_string(unselected_style.get("color")),
                "unselectedBackground": color_string(unselected_style.get("backgroundColor")),
                "unselectedGradientColors": gradient_colors(unselected_style.get("backgroundImage")),
                "selectionIndicator": ".chk" in selector,
            }
    for action in actions.values():
        binding = selection_bindings.get(str(action.get("targetNodeID") or ""))
        if binding:
            action["initiallySelected"] = bool(binding.get("initiallySelected"))
            group = [item for item in selection_bindings.values() if item.get("stateID") == binding.get("stateID")]
            action["selectionCountInitial"] = sum(bool(item.get("initiallySelected")) for item in group)
            action["selectionCountTotal"] = len(group)

    selection_count_bindings: dict[str, dict[str, Any]] = {}
    for interaction in ir.get("interactions") or []:
        transitions = (interaction.get("payload") or {}).get("transitions") or []
        selection_transition = next((
            item for item in transitions
            if str((states_by_id.get(str(item.get("targetStateId") or "")) or {}).get("kind") or "") == "selection"
        ), None)
        if not selection_transition:
            continue
        selection_state_id = str(selection_transition.get("targetStateId"))
        group = [item for item in selection_bindings.values() if item.get("stateID") == selection_state_id]
        for transition_item in transitions:
            if str(transition_item.get("action") or "") != "update-value":
                continue
            derived_state = states_by_id.get(str(transition_item.get("targetStateId") or "")) or {}
            for target_id in derived_state.get("targetNodeIds") or []:
                target_node = nodes.get(str(target_id)) or {}
                source = target_node.get("source") or {}
                target_hint = " ".join(str(source.get(key) or "") for key in ("selector", "domId", "runtimeId")).lower()
                target_text = compact_text((target_node.get("content") or {}).get("text"))
                if not re.search(r"\d+\s*/\s*\d+", target_text) and not any(token in target_hint for token in ("count", "badge")):
                    continue
                selection_count_bindings[str(target_id)] = {
                    "stateID": selection_state_id,
                    "initial": sum(bool(item.get("initiallySelected")) for item in group),
                    "total": len(group),
                }

    expansion_states: dict[str, str] = {}
    for state in ir.get("states") or []:
        if str(state.get("kind") or "") != "expansion":
            continue
        for target_id in state.get("targetNodeIds") or []:
            expansion_states[str(target_id)] = str(state.get("id"))

    presentation_states = []
    presentation_root_ids: set[str] = set()
    for state in ir.get("states") or []:
        kind = str(state.get("kind") or "")
        if kind not in PRESENTATION_KINDS:
            continue
        target_ids = [str(item) for item in state.get("targetNodeIds") or []]
        presentation_root_ids.update(target_ids)
        presentation_states.append((state, target_ids))

    root_id = str(screen.get("rootNodeId") or nodes_list[0]["id"])
    root_rect = (nodes.get(root_id, {}).get("layout") or {}).get("rect") or {}
    root_width = max(number(root_rect.get("width"), 393), 1)
    root_height = max(number(root_rect.get("height"), 852), 1)
    regions = screen.get("regions") or {}
    navigation_source = screen.get("navigation") or {}
    navigation_style = str(navigation_source.get("style") or (screen.get("systemChrome") or {}).get("navigationBar") or "hidden")
    status_bar_heights = []
    for node in nodes_list:
        if not is_status_bar_chrome(node):
            continue
        rect = (node.get("layout") or {}).get("rect") or {}
        if number(rect.get("width")) < root_width * 0.72:
            continue
        if number(rect.get("y")) > number(root_rect.get("y")) + root_height * 0.12:
            continue
        height = number(rect.get("height"))
        if 12 <= height <= min(root_height * 0.12, 90):
            status_bar_heights.append(height)
    source_status_bar_height = max(status_bar_heights, default=0.0)
    aligns_to_source_status_bar = bool(
        source_status_bar_height
        and (screen.get("systemChrome") or {}).get("statusBar") == "native"
        and navigation_style != "native"
    )
    navigation = {
        "style": navigation_style,
        "title": compact_text(navigation_source.get("title") or screen.get("name") or screen_id, 80),
        "titleMode": str(navigation_source.get("titleMode") or "inline"),
        "scrollEdgeAppearance": str(navigation_source.get("scrollEdgeAppearance") or "automatic"),
        "backButton": str(navigation_source.get("backButton") or "system"),
        "toolbarItems": [],
    }
    for item in navigation_source.get("toolbarItems") or []:
        source_node_id = str(item.get("sourceNodeId") or "")
        action = actions.get(source_node_id)
        if not action:
            continue
        navigation["toolbarItems"].append({
            "id": str(item.get("id") or source_node_id),
            "title": compact_text(item.get("title"), 80),
            "icon": item.get("icon"),
            "placement": str(item.get("placement") or "trailing"),
            "action": action,
        })
    tab_container = screen.get("tabContainer") if isinstance(screen.get("tabContainer"), dict) else None
    top_bar_id = str(((regions.get("topBar") or {}).get("nodeId")) or "") or None
    bottom_bar_id = str(((regions.get("bottomBar") or {}).get("nodeId")) or "") or None

    # Backward-compatible fallback for older IR files. Geometry and semantics are
    # stronger evidence than author-selected class names.
    if not top_bar_id or not bottom_bar_id:
        edge_candidates: dict[str, list[tuple[float, str]]] = {"top": [], "bottom": []}
        for node in nodes_list:
            node_id = str(node["id"])
            if node_id in presentation_root_ids or is_system_chrome(node):
                continue
            if (node.get("state") or {}).get("initiallyVisible") is False:
                continue
            layout = node.get("layout") or {}
            rect = layout.get("rect") or {}
            semantic = str(node.get("semanticType") or "")
            source = node.get("source") or {}
            hint = " ".join(str(source.get(key) or "") for key in ("selector", "domId", "runtimeId")).lower()
            width_fraction = number(rect.get("width")) / root_width
            height = number(rect.get("height"))
            y = number(rect.get("y"))
            if width_fraction < 0.72 or height < 32 or height > min(root_height * 0.22, 180):
                continue
            fixed = layout.get("position") in {"absolute", "fixed", "sticky"}
            top_score = (2 if fixed else 0) + (2 if semantic in {"header", "navigation", "navigation-bar"} else 0)
            bottom_score = (2 if fixed else 0) + (2 if semantic in {"footer", "navigation", "tab-bar"} else 0)
            if re.search(r"nav|header|top.?bar|app.?bar|toolbar", hint): top_score += 1.5
            if re.search(r"bottom|footer|tab.?bar|actions?|toolbar|dock", hint): bottom_score += 1.5
            if y <= root_height * 0.13 and top_score >= 2:
                edge_candidates["top"].append((top_score, node_id))
            if y + height >= root_height * 0.965 and y >= root_height * 0.62 and bottom_score >= 2:
                edge_candidates["bottom"].append((bottom_score, node_id))
        if not top_bar_id and edge_candidates["top"]:
            top_bar_id = max(edge_candidates["top"])[1]
        if not bottom_bar_id and edge_candidates["bottom"]:
            bottom_bar_id = max(edge_candidates["bottom"])[1]
    if navigation_style != "custom":
        top_bar_id = None
    if tab_container:
        bottom_bar_id = None
    detached_root_ids = set(presentation_root_ids)
    if top_bar_id:
        detached_root_ids.add(top_bar_id)
    if bottom_bar_id:
        detached_root_ids.add(bottom_bar_id)
    context = ScreenBuildContext(
        screen_id=screen_id,
        root_width=root_width,
        design_scale=min(max(number((ir.get("target") or {}).get("scale"), 1), 0.5), 3.0),
        nodes=nodes,
        children=children,
        actions=actions,
        assets={str(asset.get("id")): asset for asset in ir.get("assets") or []},
        expansion_states=expansion_states,
        selection_bindings=selection_bindings,
        selection_count_bindings=selection_count_bindings,
        motions={
            node_id: [
                payload
                for item in (ir.get("motions") or [])
                if str(item.get("sourceNodeId") or "") == node_id
                for payload in [motion_payload(item)]
                if payload
            ]
            for node_id in nodes
        },
        detached_root_ids=detached_root_ids,
        has_bottom_bar=bottom_bar_id is not None,
    )
    root = node_payload(context, root_id) or {
        "id": root_id,
        "semantic": "container",
        "text": "",
        "placeholder": "",
        "axis": "vertical",
        "children": [],
        "overlayChildren": [],
        "action": None,
        "style": {},
        "accessibilityLabel": None,
        "motions": [],
    }
    root["style"]["cornerRadius"] = 0
    top_bar = node_payload(context, top_bar_id, presentation=True) if top_bar_id else None
    bottom_bar = node_payload(context, bottom_bar_id, presentation=True) if bottom_bar_id else None
    presentations = []
    for state, target_ids in presentation_states:
        for target_id in target_ids:
            presentation_node = node_payload(context, target_id, presentation=True)
            if presentation_node:
                # The native presentation lifecycle owns root visibility. HTML
                # presentations are often opacity-zero in their resting DOM state;
                # preserving that value would create a transparent native sheet,
                # popover, or overlay. Descendant opacity remains unchanged.
                presentation_node["style"]["opacity"] = 1
                presentation_source_rect = ((nodes.get(target_id) or {}).get("layout") or {}).get("rect") or {}
                source_rect = [
                    number(presentation_source_rect.get("x")),
                    number(presentation_source_rect.get("y")),
                    number(presentation_source_rect.get("width")),
                    number(presentation_source_rect.get("height")),
                ]
                uses_custom_overlay = str(state.get("kind") or "") == "popover-overlay"
                if uses_custom_overlay:
                    presentation_node["style"]["fixedWidth"] = source_rect[2]
                    presentation_node["style"]["fixedHeight"] = source_rect[3]
                presentations.append({
                    "stateID": str(state.get("id")),
                    "kind": str(state.get("kind") or "sheet"),
                    "node": presentation_node,
                    "style": str((presentation_by_state.get(str(state.get("id"))) or {}).get("style") or "page-sheet"),
                    "detents": (presentation_by_state.get(str(state.get("id"))) or {}).get("detents") or [],
                    "grabberVisible": (presentation_by_state.get(str(state.get("id"))) or {}).get("grabberVisible"),
                    "interactiveDismissDisabled": bool((presentation_by_state.get(str(state.get("id"))) or {}).get("interactiveDismissDisabled", False)),
                    "usesCustomOverlay": uses_custom_overlay,
                    "sourceRect": source_rect,
                })
                break

    architecture = architecture or {}
    safe_area = architecture.get("safeArea") if isinstance(architecture.get("safeArea"), dict) else {}
    scroll_plan = architecture.get("scroll") if isinstance(architecture.get("scroll"), dict) else {}
    safe_area_payload = {
        "owner": str(safe_area.get("owner") or "system"),
        "contentInsetAdjustment": str(scroll_plan.get("contentInsetAdjustment") or "automatic"),
        "containerWidthPolicy": "full-parent-bounds",
        "containerHeightPolicy": "full-parent-bounds",
        "subtractFromContainerDimensions": False,
    }
    return {
        "id": screen_id,
        "swiftCase": safe_identifier(screen_id),
        "moduleId": str(screen.get("moduleId") or "").strip() or None,
        "title": navigation["title"],
        "showsNavigationBar": navigation_style == "native",
        "sourceStatusBarHeight": source_status_bar_height if aligns_to_source_status_bar and safe_area_payload["owner"] != "system" else None,
        "safeArea": safe_area_payload,
        "navigation": navigation,
        "tabContainer": tab_container,
        "root": root,
        "topBar": top_bar,
        "bottomBar": bottom_bar,
        "presentations": presentations,
        "automaticActions": automatic_actions,
    }


def models_swift(routes: list[dict[str, Any]]) -> str:
    cases = "\n".join(f'    case {route["swiftCase"]} = {json.dumps(route["id"])}' for route in routes)
    return rf'''// Generated by sky-html-to-ios {GENERATOR_VERSION}. Do not edit directly.
import Foundation

enum HTMLToIOSGeneratedRoute: String, CaseIterable, Codable, Hashable {{
{cases}
}}

struct HTMLToIOSGeneratedCatalog: Codable {{
    let initialRoute: String
    let screens: [HTMLToIOSScreenSpec]
    let tabContainer: HTMLToIOSTabContainerSpec?

    func screen(_ route: HTMLToIOSGeneratedRoute) -> HTMLToIOSScreenSpec? {{
        screens.first {{ $0.id == route.rawValue }}
    }}

    func presentation(_ stateID: String) -> HTMLToIOSPresentationSpec? {{
        screens.lazy.flatMap(\.presentations).first {{ $0.stateID == stateID }}
    }}
}}

struct HTMLToIOSScreenSpec: Codable, Identifiable {{
    let id: String
    let swiftCase: String
    let title: String
    let showsNavigationBar: Bool
    let sourceStatusBarHeight: Double?
    let safeArea: HTMLToIOSSafeAreaSpec
    let navigation: HTMLToIOSNavigationSpec
    let root: HTMLToIOSNodeSpec
    let topBar: HTMLToIOSNodeSpec?
    let bottomBar: HTMLToIOSNodeSpec?
    let presentations: [HTMLToIOSPresentationSpec]
    let automaticActions: [HTMLToIOSActionSpec]
}}

struct HTMLToIOSSafeAreaSpec: Codable {{
    let owner: String
    let contentInsetAdjustment: String
    let containerWidthPolicy: String
    let containerHeightPolicy: String
    let subtractFromContainerDimensions: Bool
}}

enum HTMLToIOSLaunchConfiguration {{
    static var initialRoute: String? {{
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "-HTMLToIOSInitialRoute"), arguments.indices.contains(index + 1) else {{
            return nil
        }}
        let value = arguments[index + 1].trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }}

    static var motionProgress: Double? {{
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "-HTMLToIOSMotionProgress"), arguments.indices.contains(index + 1) else {{
            return nil
        }}
        return Double(arguments[index + 1]).map {{ min(max($0, 0), 1) }}
    }}
}}

struct HTMLToIOSNavigationSpec: Codable {{
    let style: String
    let title: String
    let titleMode: String
    let scrollEdgeAppearance: String
    let backButton: String
    let toolbarItems: [HTMLToIOSToolbarItemSpec]
}}

struct HTMLToIOSToolbarItemSpec: Codable, Identifiable {{
    let id: String
    let title: String
    let icon: String?
    let placement: String
    let action: HTMLToIOSActionSpec
}}

struct HTMLToIOSTabContainerSpec: Codable, Identifiable {{
    let id: String
    let initialTabId: String
    let reselectBehavior: String
    let visibility: String
    let items: [HTMLToIOSTabItemSpec]
}}

struct HTMLToIOSTabItemSpec: Codable, Identifiable {{
    let id: String
    let title: String
    let targetScreenId: String
    let icon: String?
    let selectedIcon: String?
    let badge: String?
    let role: String
}}

struct HTMLToIOSPresentationSpec: Codable, Identifiable {{
    var id: String {{ stateID }}
    let stateID: String
    let kind: String
    let node: HTMLToIOSNodeSpec
    let style: String
    let detents: [String]
    let grabberVisible: Bool?
    let interactiveDismissDisabled: Bool
    let usesCustomOverlay: Bool
    let sourceRect: [Double]
}}

struct HTMLToIOSActionSpec: Codable {{
    let interactionID: String?
    let action: String
    let target: String?
    let targetScreenID: String?
    let targetStateID: String?
    let delayMilliseconds: Int
    let sourceNodeID: String?
    let targetNodeID: String?
    let stateKind: String?
    let selectionMode: String?
    let localEffect: String?
    let feedbackText: String?
    let feedbackDurationMilliseconds: Int?
    let initiallySelected: Bool?
    let selectionCountInitial: Int?
    let selectionCountTotal: Int?
}}

struct HTMLToIOSNodeSpec: Codable, Identifiable {{
    let id: String
    let semantic: String
    let text: String
    let placeholder: String
    let axis: String
    let children: [HTMLToIOSNodeSpec]
    let overlayChildren: [HTMLToIOSNodeSpec]
    let action: HTMLToIOSActionSpec?
    let style: HTMLToIOSStyleSpec
    let systemImage: String?
    let assetName: String?
    let backgroundAssetName: String?
    let accessibilityLabel: String?
    let visibleWhenStateID: String?
    let selectionStateID: String?
    let isInitiallySelected: Bool?
    let selectedForeground: String?
    let selectedBackground: String?
    let selectedGradientColors: [String]?
    let unselectedForeground: String?
    let unselectedBackground: String?
    let unselectedGradientColors: [String]?
    let selectionIndicator: Bool?
    let selectionCountStateID: String?
    let selectionCountInitial: Int?
    let selectionCountTotal: Int?
    let richTextRuns: [HTMLToIOSRichTextRunSpec]?
    let motions: [HTMLToIOSMotionSpec]
}}

struct HTMLToIOSMotionSpec: Codable, Identifiable {{
    let id: String
    let durationMilliseconds: Int
    let delayMilliseconds: Int
    let repeats: Bool
    let reverses: Bool
    let autoreverses: Bool
    let rotationDegrees: Double
    let scaleValues: [Double]
    let opacityValues: [Double]
}}

struct HTMLToIOSRichTextRunSpec: Codable {{
    let text: String
    let fontSize: Double?
    let fontWeight: String?
    let foreground: String?
    let background: String?
    let lineHeight: Double?
    let letterSpacing: Double?
}}

struct HTMLToIOSStyleSpec: Codable {{
    let fontSize: Double?
    let fontWeight: String?
    let lineHeight: Double?
    let letterSpacing: Double?
    let foreground: String?
    let background: String?
    let gradientColors: [String]?
    let gradientLocations: [Double?]?
    let gradientKind: String?
    let gradientAngle: Double?
    let cornerRadius: Double?
    let borderWidth: Double?
    let borderColor: String?
    let borderStyle: String?
    let opacity: Double?
    let shadowColor: String?
    let shadowOffsetX: Double?
    let shadowOffsetY: Double?
    let shadowRadius: Double?
    let shadowSpread: Double?
    let offsetX: Double?
    let offsetY: Double?
    let zIndex: Double?
    let clipsContent: Bool?
    let padding: [Double]?
    let margin: [Double]?
    let spacing: Double?
    let widthFraction: Double?
    let minHeight: Double?
    let preferredWidth: Double?
    let preferredHeight: Double?
    let resistsCompression: Bool?
    let preservesIntrinsicWidth: Bool?
    let fixedWidth: Double?
    let fixedHeight: Double?
    let aspectRatio: Double?
    let scrollAxis: String?
    let textLineLimit: Int?
    let textOverflow: String?
    let textAlignment: String?
    let justifyContent: String?
    let alignItems: String?
    let gridColumnCount: Int?
    let mediaContentMode: String?
    let mediaPosition: String?
    let backgroundContentMode: String?
    let backgroundPosition: String?
    let backgroundRepeat: String?
}}
'''


def data_swift(payload: dict[str, Any]) -> str:
    return rf'''// Generated by sky-html-to-ios {GENERATOR_VERSION}. Do not edit directly.
import Foundation

enum HTMLToIOSGeneratedData {{
    static let catalog: HTMLToIOSGeneratedCatalog = {{
        guard let url = Bundle.main.url(forResource: "HTMLToIOSGeneratedPayload", withExtension: "json") else {{
            preconditionFailure("Missing HTMLToIOSGeneratedPayload.json in the app target")
        }}
        do {{
            let data = try Data(contentsOf: url)
            return try JSONDecoder().decode(HTMLToIOSGeneratedCatalog.self, from: data)
        }} catch {{
            preconditionFailure("Cannot decode generated HTML-to-iOS payload: \(error)")
        }}
    }}()
}}
'''


SWIFTUI_RUNTIME = r'''// Generated by sky-html-to-ios. Native SwiftUI rendering runtime.
import SwiftUI

@MainActor
final class HTMLToIOSGeneratedStore: ObservableObject {
    struct PresentedState: Identifiable { let id: String }

    @Published var path: [HTMLToIOSGeneratedRoute] = []
    @Published var selectedTab: String?
    @Published var tabPaths: [String: [HTMLToIOSGeneratedRoute]] = [:]
    @Published var tabScrollToTopID: String?
    @Published var tabScrollToTopNonce = 0
    @Published var values: [String: String] = [:]
    @Published var flags: Set<String> = []
    @Published var selectedByState: [String: String] = [:]
    @Published var selectionOverrides: [String: Bool] = [:]
    @Published var selectionCounts: [String: Int] = [:]
    @Published var hiddenNodeIDs: Set<String> = []
    @Published var feedbackText: [String: String] = [:]
    @Published var sheet: PresentedState?
    @Published var fullScreen: PresentedState?
    @Published var popover: PresentedState?
    @Published var overlay: PresentedState?
    private var tabIDByTargetScreen: [String: String] = [:]
    private var tabBarVisibilityMode = "automatic"

    func configureTabs(_ container: HTMLToIOSTabContainerSpec) {
        tabIDByTargetScreen = Dictionary(uniqueKeysWithValues: container.items.map { ($0.targetScreenId, $0.id) })
        tabBarVisibilityMode = container.visibility
        if selectedTab == nil { selectedTab = container.initialTabId }
    }

    func tabPathBinding(for tabID: String) -> Binding<[HTMLToIOSGeneratedRoute]> {
        Binding(
            get: { self.tabPaths[tabID, default: []] },
            set: { self.tabPaths[tabID] = $0 }
        )
    }

    func selectTab(_ tabID: String, reselectBehavior: String) {
        if selectedTab == tabID {
            if reselectBehavior == "pop-to-root" {
                tabPaths[tabID] = []
            } else if reselectBehavior == "scroll-to-top" {
                tabPaths[tabID] = []
                tabScrollToTopID = tabID
                tabScrollToTopNonce += 1
            }
        }
        selectedTab = tabID
    }

    func tabBarVisibility(for screenID: String) -> Visibility {
        guard tabBarVisibilityMode == "hide-on-push", let selectedTab else { return .automatic }
        let isSelectedRoot = tabIDByTargetScreen[screenID] == selectedTab && (tabPaths[selectedTab] ?? []).isEmpty
        return isSelectedRoot ? .visible : .hidden
    }

    func binding(for nodeID: String) -> Binding<String> {
        Binding(get: { self.values[nodeID, default: ""] }, set: { self.values[nodeID] = $0 })
    }

    func flagBinding(for nodeID: String) -> Binding<Bool> {
        Binding(get: { self.flags.contains(nodeID) }, set: { enabled in
            if enabled { self.flags.insert(nodeID) } else { self.flags.remove(nodeID) }
        })
    }

    func isSelected(_ spec: HTMLToIOSNodeSpec) -> Bool {
        guard let stateID = spec.selectionStateID else { return false }
        let key = stateID + "|" + spec.id
        if let override = selectionOverrides[key] { return override }
        if let selected = selectedByState[stateID] { return selected == spec.id }
        return spec.isInitiallySelected ?? false
    }

    func perform(_ spec: HTMLToIOSActionSpec?) {
        guard let spec else { return }
        let routeID = spec.targetScreenID ?? spec.target
        let stateID = spec.targetStateID ?? spec.target
        switch spec.action {
        case "push":
            if let routeID, let route = HTMLToIOSGeneratedRoute(rawValue: routeID) {
                if let selectedTab { tabPaths[selectedTab, default: []].append(route) }
                else { path.append(route) }
            }
        case "replace-stack", "set-flow-state":
            if let routeID, let route = HTMLToIOSGeneratedRoute(rawValue: routeID) {
                if let selectedTab { tabPaths[selectedTab] = [route] }
                else { path = [route] }
            }
        case "pop":
            if let selectedTab, !(tabPaths[selectedTab] ?? []).isEmpty { tabPaths[selectedTab]?.removeLast() }
            else if !path.isEmpty { path.removeLast() }
        case "pop-to-root":
            if let selectedTab { tabPaths[selectedTab] = [] } else { path.removeAll() }
        case "switch-tab", "select-tab":
            if let routeID { selectedTab = tabIDByTargetScreen[routeID] ?? routeID }
        case "present-sheet":
            if let stateID { sheet = PresentedState(id: stateID) }
        case "present-fullscreen", "present-full-screen":
            if let stateID { fullScreen = PresentedState(id: stateID) }
        case "present-popover":
            if let stateID { popover = PresentedState(id: stateID) }
        case "present-overlay", "show-dialog":
            if let stateID { overlay = PresentedState(id: stateID) }
        case "dismiss", "dismiss-sheet", "dismiss-fullscreen", "dismiss-popover", "dismiss-overlay":
            sheet = nil; fullScreen = nil; popover = nil; overlay = nil
        case "toggle-state", "toggle-selection", "toggle-expanded":
            if let stateID {
                if spec.stateKind == "selection", let nodeID = spec.targetNodeID ?? spec.sourceNodeID {
                    if spec.selectionMode == "exclusive" {
                        selectedByState[stateID] = nodeID
                    } else {
                        let key = stateID + "|" + nodeID
                        let current = selectionOverrides[key] ?? spec.initiallySelected ?? false
                        let next = !current
                        selectionOverrides[key] = next
                        let total = spec.selectionCountTotal ?? 0
                        let count = selectionCounts[stateID] ?? spec.selectionCountInitial ?? 0
                        selectionCounts[stateID] = min(max(count + (next ? 1 : -1), 0), total)
                    }
                } else if spec.localEffect == "remove", let nodeID = spec.targetNodeID {
                    var nextHiddenNodeIDs = hiddenNodeIDs
                    nextHiddenNodeIDs.insert(nodeID)
                    withAnimation(.easeInOut(duration: 0.35)) { hiddenNodeIDs = nextHiddenNodeIDs }
                } else if flags.contains(stateID) {
                    flags.remove(stateID)
                } else {
                    flags.insert(stateID)
                }
            }
        case "update-value":
            if let nodeID = spec.sourceNodeID, let text = spec.feedbackText {
                feedbackText[nodeID] = text
                let duration = spec.feedbackDurationMilliseconds ?? 1600
                Task { @MainActor in
                    try? await Task.sleep(for: .milliseconds(duration))
                    self.feedbackText.removeValue(forKey: nodeID)
                }
            }
        default:
            break
        }
    }
}

private extension Color {
    init(htmlToIOS value: String?) {
        guard let value else { self = .clear; return }
        let numbers = value.split(whereSeparator: { !$0.isNumber && $0 != "." }).compactMap { Double($0) }
        if numbers.count >= 3 {
            self = Color(red: numbers[0] / 255, green: numbers[1] / 255, blue: numbers[2] / 255,
                         opacity: numbers.count > 3 ? numbers[3] : 1)
        } else { self = .clear }
    }
}

private struct HTMLToIOSRichTextView: View {
    let runs: [HTMLToIOSRichTextRunSpec]
    let style: HTMLToIOSStyleSpec

    var body: some View {
        Text(attributedText)
    }

    private var attributedText: AttributedString {
        var result = AttributedString()
        for run in runs {
            var piece = AttributedString(run.text)
            piece.font = .system(
                size: run.fontSize ?? style.fontSize ?? 16,
                weight: fontWeight(run.fontWeight ?? style.fontWeight)
            )
            piece.foregroundColor = Color(htmlToIOS: run.foreground ?? style.foreground)
            if let background = run.background {
                piece.backgroundColor = Color(htmlToIOS: background)
            }
            piece.kern = run.letterSpacing ?? style.letterSpacing ?? 0
            result.append(piece)
        }
        return result
    }

    private func fontWeight(_ raw: String?) -> Font.Weight {
        let value = Int(raw ?? "400") ?? 400
        if value >= 700 { return .bold }; if value >= 600 { return .semibold }; if value >= 500 { return .medium }
        return .regular
    }
}

private struct HTMLToIOSBackgroundModifier: ViewModifier {
    let style: HTMLToIOSStyleSpec
    let assetName: String?
    let backgroundOverride: String?
    let gradientOverride: [String]?

    func body(content: Content) -> some View {
        let colors = (gradientOverride ?? style.gradientColors ?? []).map { Color(htmlToIOS: $0) }
        let locations = style.gradientLocations ?? []
        let stops = colors.enumerated().map { index, color in
            Gradient.Stop(
                color: color,
                location: locations.indices.contains(index) ? (locations[index] ?? evenlySpacedLocation(index, count: colors.count)) : evenlySpacedLocation(index, count: colors.count)
            )
        }
        return content
            .background(Color(htmlToIOS: backgroundOverride ?? style.background))
            .background {
                if colors.count >= 2 {
                    if style.gradientKind == "radial" {
                        RadialGradient(gradient: Gradient(stops: stops), center: .center, startRadius: 0, endRadius: 240)
                    } else {
                        LinearGradient(gradient: Gradient(stops: stops), startPoint: gradientStart, endPoint: gradientEnd)
                    }
                }
            }
            .background(alignment: backgroundAlignment) {
                if let assetName {
                    GeometryReader { proxy in
                        Image(assetName)
                            .resizable(resizingMode: style.backgroundRepeat == "repeat" ? .tile : .stretch)
                            .aspectRatio(contentMode: backgroundContentMode)
                            .frame(width: proxy.size.width, height: proxy.size.height, alignment: backgroundAlignment)
                            .clipped()
                    }
                }
            }
    }

    private var backgroundContentMode: ContentMode {
        String(style.backgroundContentMode ?? "cover").lowercased().contains("contain") ? .fit : .fill
    }

    private func evenlySpacedLocation(_ index: Int, count: Int) -> CGFloat {
        count <= 1 ? 0 : CGFloat(index) / CGFloat(count - 1)
    }

    private var gradientVector: (CGFloat, CGFloat) {
        let radians = (style.gradientAngle ?? 180) * .pi / 180
        return (CGFloat(sin(radians)), CGFloat(-cos(radians)))
    }

    private var gradientStart: UnitPoint {
        let vector = gradientVector
        return UnitPoint(x: 0.5 - vector.0 / 2, y: 0.5 - vector.1 / 2)
    }

    private var gradientEnd: UnitPoint {
        let vector = gradientVector
        return UnitPoint(x: 0.5 + vector.0 / 2, y: 0.5 + vector.1 / 2)
    }

    private var backgroundAlignment: Alignment {
        let value = String(style.backgroundPosition ?? "50% 50%").lowercased()
        let horizontal: HorizontalAlignment = value.contains("left") || value.hasPrefix("0%") ? .leading : (value.contains("right") || value.hasPrefix("100%") ? .trailing : .center)
        let vertical: VerticalAlignment = value.contains("top") || value.hasSuffix("0%") ? .top : (value.contains("bottom") || value.hasSuffix("100%") ? .bottom : .center)
        return Alignment(horizontal: horizontal, vertical: vertical)
    }
}

private struct HTMLToIOSClipModifier: ViewModifier {
    let style: HTMLToIOSStyleSpec

    @ViewBuilder func body(content: Content) -> some View {
        if style.clipsContent == true || (style.cornerRadius ?? 0) > 0 {
            content.clipShape(RoundedRectangle(cornerRadius: style.cornerRadius ?? 0, style: .continuous))
        } else {
            content
        }
    }
}

private struct HTMLToIOSOverlayClipModifier: ViewModifier {
    let style: HTMLToIOSStyleSpec

    @ViewBuilder func body(content: Content) -> some View {
        if style.clipsContent == true {
            content.clipShape(RoundedRectangle(cornerRadius: style.cornerRadius ?? 0, style: .continuous))
        } else {
            content
        }
    }
}

private struct HTMLToIOSBorderModifier: ViewModifier {
    let style: HTMLToIOSStyleSpec

    @ViewBuilder func body(content: Content) -> some View {
        if let width = style.borderWidth, width > 0, let color = style.borderColor {
            content.overlay {
                RoundedRectangle(cornerRadius: style.cornerRadius ?? 0, style: .continuous)
                    .stroke(
                        Color(htmlToIOS: color),
                        style: StrokeStyle(
                            lineWidth: width,
                            dash: style.borderStyle == "dashed" ? [6, 4] : (style.borderStyle == "dotted" ? [1, 3] : [])
                        )
                    )
            }
        } else {
            content
        }
    }
}

private struct HTMLToIOSFrameModifier: ViewModifier {
    let fixedWidth: CGFloat?
    let fixedHeight: CGFloat?
    let minWidth: CGFloat?
    let idealWidth: CGFloat?
    let maxWidth: CGFloat?
    let minHeight: CGFloat?

    @ViewBuilder func body(content: Content) -> some View {
        if fixedWidth != nil || fixedHeight != nil {
            content.frame(width: fixedWidth, height: fixedHeight)
        } else if minWidth != nil || idealWidth != nil {
            firstFrame(content)
        } else if maxWidth != nil || minHeight != nil {
            secondFrame(content)
        } else {
            content
        }
    }

    @ViewBuilder private func firstFrame(_ content: Content) -> some View {
        let framed = content.frame(minWidth: minWidth, idealWidth: idealWidth)
        if maxWidth != nil || minHeight != nil {
            framed.frame(maxWidth: maxWidth, minHeight: minHeight)
        } else {
            framed
        }
    }

    private func secondFrame(_ content: Content) -> some View {
        content.frame(maxWidth: maxWidth, minHeight: minHeight)
    }
}

private struct HTMLToIOSAspectRatioModifier: ViewModifier {
    let ratio: CGFloat?

    @ViewBuilder func body(content: Content) -> some View {
        if let ratio, ratio > 0 {
            content.aspectRatio(ratio, contentMode: .fit)
        } else {
            content
        }
    }
}

private struct HTMLToIOSMotionModifier: ViewModifier {
    let motions: [HTMLToIOSMotionSpec]

    @ViewBuilder func body(content: Content) -> some View {
        if motions.isEmpty {
            content
        } else {
            TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: HTMLToIOSLaunchConfiguration.motionProgress != nil)) { timeline in
                content
                    .rotationEffect(.degrees(rotation(at: timeline.date)))
                    .scaleEffect(scale(at: timeline.date))
                    .opacity(opacity(at: timeline.date))
            }
        }
    }

    private func progress(_ motion: HTMLToIOSMotionSpec, at date: Date) -> Double {
        if let forced = HTMLToIOSLaunchConfiguration.motionProgress {
            return motion.reverses ? 1 - forced : forced
        }
        let duration = max(Double(motion.durationMilliseconds) / 1000, 0.001)
        let delayed = max(date.timeIntervalSinceReferenceDate - Double(motion.delayMilliseconds) / 1000, 0)
        var value: Double
        if motion.autoreverses {
            let phase = delayed.truncatingRemainder(dividingBy: duration * 2) / duration
            value = phase <= 1 ? phase : 2 - phase
        } else if motion.repeats {
            value = delayed.truncatingRemainder(dividingBy: duration) / duration
        } else {
            value = min(delayed / duration, 1)
        }
        return motion.reverses ? 1 - value : value
    }

    private func sampled(_ values: [Double], progress: Double, fallback: Double) -> Double {
        guard values.count >= 3 else { return values.first ?? fallback }
        if progress <= 0.5 {
            return values[0] + (values[1] - values[0]) * progress * 2
        }
        return values[1] + (values[2] - values[1]) * (progress - 0.5) * 2
    }

    private func rotation(at date: Date) -> Double {
        motions.reduce(0) { $0 + $1.rotationDegrees * progress($1, at: date) }
    }

    private func scale(at date: Date) -> Double {
        motions.reduce(1) { $0 * sampled($1.scaleValues, progress: progress($1, at: date), fallback: 1) }
    }

    private func opacity(at date: Date) -> Double {
        motions.reduce(1) { $0 * sampled($1.opacityValues, progress: progress($1, at: date), fallback: 1) }
    }
}

private struct HTMLToIOSStyleModifier: ViewModifier {
    let style: HTMLToIOSStyleSpec
    let assetName: String?
    let foregroundOverride: String?
    let backgroundOverride: String?
    let gradientOverride: [String]?
    let constrainsPreferredWidth: Bool
    let enforcesPreferredWidth: Bool

    func body(content: Content) -> some View {
        let padding = style.padding ?? [0, 0, 0, 0]
        let margin = style.margin ?? [0, 0, 0, 0]
        let foregroundValue = foregroundOverride ?? style.foreground
        let foreground = foregroundValue == nil ? Color.primary : Color(htmlToIOS: foregroundValue)
        let alignment: TextAlignment = style.textAlignment == "center" ? .center : (style.textAlignment == "end" ? .trailing : .leading)
        let preferredWidth = constrainsPreferredWidth ? CGFloat(style.preferredWidth ?? 0) : 0
        let maxWidth: CGFloat? = constrainsPreferredWidth && (style.widthFraction ?? 0) > 0.88 ? .infinity : nil
        let idealWidth: CGFloat? = preferredWidth > 0 && maxWidth == nil ? preferredWidth : nil
        let minWidth: CGFloat? = (enforcesPreferredWidth || style.resistsCompression == true) && preferredWidth > 0 ? preferredWidth : nil
        let rawMinHeight = style.minHeight ?? 0
        let minHeight: CGFloat? = rawMinHeight > 0 ? CGFloat(rawMinHeight) : nil
        let fixedWidth: CGFloat? = style.fixedWidth.map { CGFloat($0) }
        let fixedHeight: CGFloat? = style.fixedHeight.map { CGFloat($0) }
        let lineSpacing = max((style.lineHeight ?? style.fontSize ?? 16) - (style.fontSize ?? 16), 0)
        let typography = content
            .font(.system(size: style.fontSize ?? 16, weight: fontWeight(style.fontWeight)))
            .foregroundStyle(foreground)
            .multilineTextAlignment(alignment)
            .lineLimit(style.textLineLimit)
            .lineSpacing(lineSpacing)
            .tracking(style.letterSpacing ?? 0)
            .fixedSize(horizontal: style.preservesIntrinsicWidth == true, vertical: false)
            .layoutPriority(style.preservesIntrinsicWidth == true ? 1 : 0)
        let insetContent = typography
            .padding(.top, padding.indices.contains(0) ? padding[0] : 0)
            .padding(.trailing, padding.indices.contains(1) ? padding[1] : 0)
            .padding(.bottom, padding.indices.contains(2) ? padding[2] : 0)
            .padding(.leading, padding.indices.contains(3) ? padding[3] : 0)
            .modifier(HTMLToIOSAspectRatioModifier(ratio: style.aspectRatio.map { CGFloat($0) }))
        let framedContent = insetContent
            .modifier(HTMLToIOSFrameModifier(
                fixedWidth: fixedWidth,
                fixedHeight: fixedHeight,
                minWidth: minWidth,
                idealWidth: idealWidth,
                maxWidth: maxWidth,
                minHeight: minHeight
            ))
        return framedContent
            .modifier(HTMLToIOSBackgroundModifier(style: style, assetName: assetName, backgroundOverride: backgroundOverride, gradientOverride: gradientOverride))
            .modifier(HTMLToIOSClipModifier(style: style))
            .modifier(HTMLToIOSBorderModifier(style: style))
            .shadow(
                color: Color(htmlToIOS: style.shadowColor).opacity(style.shadowColor == nil ? 0 : 1),
                radius: style.shadowRadius ?? 0,
                x: style.shadowOffsetX ?? 0,
                y: style.shadowOffsetY ?? 0
            )
            .opacity(style.opacity ?? 1)
            .offset(x: style.offsetX ?? 0, y: style.offsetY ?? 0)
            .zIndex(style.zIndex ?? 0)
            .padding(.top, margin.indices.contains(0) ? margin[0] : 0)
            .padding(.trailing, margin.indices.contains(1) ? margin[1] : 0)
            .padding(.bottom, margin.indices.contains(2) ? margin[2] : 0)
            .padding(.leading, margin.indices.contains(3) ? margin[3] : 0)
    }

    private func fontWeight(_ raw: String?) -> Font.Weight {
        let value = Int(raw ?? "400") ?? 400
        if value >= 700 { return .bold }
        if value >= 600 { return .semibold }
        if value >= 500 { return .medium }
        return .regular
    }
}

private struct HTMLToIOSAccessibilityModifier: ViewModifier {
    let spec: HTMLToIOSNodeSpec

    @ViewBuilder func body(content: Content) -> some View {
        if spec.action != nil || (spec.children.isEmpty && spec.overlayChildren.isEmpty) {
            content
                .accessibilityIdentifier(spec.id)
                .accessibilityLabel(spec.accessibilityLabel ?? spec.text)
                .accessibilityAddTraits(spec.action == nil ? [] : .isButton)
        } else {
            content
        }
    }
}

struct HTMLToIOSNativeNodeView: View {
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let spec: HTMLToIOSNodeSpec

    @ViewBuilder var body: some View {
        if !store.hiddenNodeIDs.contains(spec.id) && (spec.visibleWhenStateID == nil || store.flags.contains(spec.visibleWhenStateID!)) {
            interactiveContent
                .transition(.asymmetric(insertion: .opacity, removal: .move(edge: .trailing).combined(with: .opacity)))
        }
    }

    @ViewBuilder private var interactiveContent: some View {
        if spec.action != nil && !isNativeControl {
            Button(action: { store.perform(spec.action) }) { styledContent }
                .buttonStyle(.plain)
                .modifier(HTMLToIOSAccessibilityModifier(spec: spec))
        } else {
            styledContent.modifier(HTMLToIOSAccessibilityModifier(spec: spec))
        }
    }

    private var styledContent: some View {
        content.modifier(HTMLToIOSStyleModifier(
            style: spec.style,
            assetName: spec.backgroundAssetName,
            foregroundOverride: selectionForeground,
            backgroundOverride: selectionBackground,
            gradientOverride: selectionGradient,
            constrainsPreferredWidth: spec.children.isEmpty || isNativeControl,
            enforcesPreferredWidth: isNativeControl
        ))
        .overlay {
            ZStack {
                ForEach(spec.overlayChildren) { child in
                    HTMLToIOSNativeNodeView(store: store, spec: child)
                }
            }
        }
        .modifier(HTMLToIOSOverlayClipModifier(style: spec.style))
        .modifier(HTMLToIOSMotionModifier(motions: spec.motions))
    }

    private var selectionForeground: String? {
        guard spec.selectionStateID != nil else { return nil }
        return store.isSelected(spec) ? spec.selectedForeground : spec.unselectedForeground
    }
    private var selectionBackground: String? {
        guard spec.selectionStateID != nil else { return nil }
        return store.isSelected(spec) ? spec.selectedBackground : spec.unselectedBackground
    }
    private var selectionGradient: [String]? {
        guard spec.selectionStateID != nil else { return nil }
        return store.isSelected(spec) ? spec.selectedGradientColors : spec.unselectedGradientColors
    }
    private var isNativeControl: Bool {
        ["button", "link", "menu-item", "tab-item", "toggle", "switch", "checkbox"].contains(spec.semantic)
    }

    @ViewBuilder private var content: some View {
        switch spec.semantic {
        case "button", "link", "menu-item", "tab-item":
            if spec.action != nil {
                Button(action: { store.perform(spec.action) }) { buttonContent }
                    .buttonStyle(.plain)
            } else {
                buttonContent
            }
        case "text-field", "input", "search-field":
            TextField(spec.placeholder, text: store.binding(for: spec.id))
                .textFieldStyle(.roundedBorder)
        case "secure-field":
            SecureField(spec.placeholder, text: store.binding(for: spec.id))
                .textFieldStyle(.roundedBorder)
        case "toggle", "switch", "checkbox":
            Toggle(spec.text, isOn: store.flagBinding(for: spec.id))
        case "progress", "progress-view":
            ProgressView(value: 0.55)
        case "carousel":
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: verticalAlignment, spacing: spec.style.spacing ?? 0) { children }
                    .fixedSize(horizontal: true, vertical: false)
            }
            .clipped()
        case "scroll":
            scrollContainer
        case "icon":
            let height = max(spec.style.preferredHeight ?? 18, 1)
            let width = max(spec.style.preferredWidth ?? height, 1)
            if let assetName = spec.assetName {
                Image(assetName)
                    .resizable()
                    .aspectRatio(contentMode: mediaContentMode)
                    .frame(width: width, height: height)
                    .clipped()
            } else {
                Image(systemName: spec.systemImage ?? "circle.fill")
                    .font(.system(size: min(width, height), weight: .semibold))
                    .frame(width: width, height: height)
            }
        case "image":
            if let assetName = spec.assetName {
                Image(assetName)
                    .resizable()
                    .aspectRatio(contentMode: mediaContentMode)
                    .frame(width: mediaWidth, height: mediaHeight)
                    .frame(maxWidth: (spec.style.widthFraction ?? 0) > 0.88 ? .infinity : nil)
                    .clipped()
            } else {
                Image(systemName: spec.systemImage ?? "photo")
                    .resizable()
                    .scaledToFit()
                    .frame(maxHeight: min(spec.style.preferredHeight ?? 96, 120))
            }
        case "divider", "separator":
            Divider()
        case "text", "label", "heading":
            if let runs = spec.richTextRuns, !runs.isEmpty {
                HTMLToIOSRichTextView(runs: runs, style: spec.style)
            } else if spec.children.isEmpty {
                styledText(displayValue)
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 0) {
                    styledText(spec.text)
                    ForEach(spec.children) { child in
                        HTMLToIOSNativeNodeView(store: store, spec: child)
                    }
                }
            }
        default:
            if spec.selectionIndicator == true {
                ZStack {
                    Color.clear
                    if store.isSelected(spec) {
                        Image(systemName: "checkmark")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(.white)
                    }
                }
                .frame(
                    width: CGFloat(spec.style.preferredWidth ?? 15),
                    height: CGFloat(spec.style.preferredHeight ?? 15)
                )
            } else {
                childContent
                    .contentShape(Rectangle())
            }
        }
    }

    @ViewBuilder private func styledText(_ value: String) -> some View {
        let colors = (spec.style.gradientColors ?? []).map { Color(htmlToIOS: $0) }
        if colors.count >= 2 && spec.style.foreground == nil {
            Text(value)
                .foregroundStyle(
                    LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
                )
        } else {
            Text(value)
        }
    }

    private var mediaContentMode: ContentMode {
        let value = String(spec.style.mediaContentMode ?? "contain").lowercased()
        return value.contains("cover") || value == "fill" ? .fill : .fit
    }
    private var mediaWidth: CGFloat? {
        guard (spec.style.widthFraction ?? 0) <= 0.88, let value = spec.style.preferredWidth else { return nil }
        return CGFloat(value)
    }
    private var mediaHeight: CGFloat? {
        guard let value = spec.style.preferredHeight else { return nil }
        return CGFloat(value)
    }

    @ViewBuilder private var scrollContainer: some View {
        switch spec.style.scrollAxis {
        case "horizontal":
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: verticalAlignment, spacing: spec.style.spacing ?? 0) { children }
                    .fixedSize(horizontal: true, vertical: false)
            }
            .clipped()
        case "both":
            ScrollView([.horizontal, .vertical]) { childContent }
                .clipped()
        case "none":
            childContent
        default:
            ScrollView(.vertical, showsIndicators: true) {
                childContent.frame(maxWidth: .infinity, alignment: .topLeading)
            }
            .clipped()
        }
    }

    @ViewBuilder private var buttonContent: some View {
        if spec.axis == "grid" {
            LazyVGrid(columns: gridColumns, spacing: spec.style.spacing ?? 0) { buttonChildren }
        } else if spec.axis == "vertical" {
            VStack(alignment: .center, spacing: spec.style.spacing ?? 8) { buttonChildren }
        } else {
            HStack(alignment: .center, spacing: spec.style.spacing ?? 8) { buttonChildren }
        }
    }

    @ViewBuilder private var buttonChildren: some View {
        ForEach(spec.children) { child in HTMLToIOSNativeNodeView(store: store, spec: child) }
        let value = displayValue
        if !value.isEmpty { Text(value) }
    }

    private var displayValue: String {
        if let stateID = spec.selectionCountStateID,
           let initial = spec.selectionCountInitial,
           let total = spec.selectionCountTotal {
            return "\(store.selectionCounts[stateID] ?? initial) / \(total)"
        }
        return store.feedbackText[spec.id] ?? spec.text
    }

    @ViewBuilder private var childContent: some View {
        if spec.axis == "horizontal" {
            HStack(alignment: verticalAlignment, spacing: spec.style.spacing ?? 0) { distributedChildren }
                .frame(maxWidth: fillsAvailableWidth ? .infinity : nil, alignment: horizontalFrameAlignment)
        } else if spec.axis == "grid" {
            LazyVGrid(columns: gridColumns, spacing: spec.style.spacing ?? 0) { children }
        } else if spec.axis == "overlay" {
            ZStack(alignment: .center) { children }
                .frame(width: overlayWidth, height: overlayHeight)
        } else {
            VStack(alignment: horizontalAlignment, spacing: spec.style.spacing ?? 0) { distributedChildren }
                .frame(maxWidth: fillsAvailableWidth ? .infinity : nil, alignment: verticalFrameAlignment)
        }
    }

    private var fillsAvailableWidth: Bool { (spec.style.widthFraction ?? 0) > 0.88 }
    private var gridColumns: [GridItem] {
        Array(
            repeating: GridItem(.flexible(), spacing: spec.style.spacing ?? 0),
            count: max(spec.style.gridColumnCount ?? 2, 1)
        )
    }
    private var overlayWidth: CGFloat? { spec.style.preferredWidth.map { CGFloat($0) } }
    private var overlayHeight: CGFloat? { spec.style.preferredHeight.map { CGFloat($0) } }
    private var distributesChildren: Bool { spec.style.justifyContent == "space-between" }
    private var horizontalAlignment: HorizontalAlignment {
        switch spec.style.alignItems {
        case "center": return .center
        case "flex-end", "end": return .trailing
        default: return .leading
        }
    }
    private var verticalAlignment: VerticalAlignment {
        switch spec.style.alignItems {
        case "flex-start", "start": return .top
        case "flex-end", "end": return .bottom
        default: return .center
        }
    }
    private var horizontalFrameAlignment: Alignment {
        switch spec.style.justifyContent {
        case "center": return .center
        case "flex-end", "end": return .trailing
        default: return .leading
        }
    }
    private var verticalFrameAlignment: Alignment {
        switch spec.style.justifyContent {
        case "center": return .center
        case "flex-end", "end": return .bottom
        default: return .top
        }
    }

    @ViewBuilder private var distributedChildren: some View {
        let indexed = Array(spec.children.enumerated())
        ForEach(indexed, id: \.element.id) { index, child in
            HTMLToIOSNativeNodeView(store: store, spec: child)
            if distributesChildren && index < indexed.count - 1 { Spacer(minLength: spec.style.spacing ?? 0) }
        }
    }

    @ViewBuilder private var children: some View {
        if !spec.text.isEmpty && spec.children.isEmpty { Text(spec.text) }
        ForEach(spec.children) { child in HTMLToIOSNativeNodeView(store: store, spec: child) }
    }
}

struct HTMLToIOSGeneratedToolbarContent: ToolbarContent {
    let store: HTMLToIOSGeneratedStore
    let items: [HTMLToIOSToolbarItemSpec]

    @ToolbarContentBuilder var body: some ToolbarContent {
        ToolbarItemGroup(placement: .navigationBarLeading) { buttons(for: "leading") }
        ToolbarItemGroup(placement: .principal) { buttons(for: "principal") }
        ToolbarItemGroup(placement: .primaryAction) { buttons(for: "primary") }
        ToolbarItemGroup(placement: .navigationBarTrailing) { buttons(for: "trailing") }
    }

    @ViewBuilder private func buttons(for placement: String) -> some View {
        ForEach(items.filter { normalizedPlacement($0.placement) == placement }) { item in
            Button(action: { store.perform(item.action) }) {
                if let icon = item.icon { Image(systemName: icon) }
                else { Text(item.title) }
            }
            .accessibilityIdentifier(item.id)
            .accessibilityLabel(item.title)
        }
    }

    private func normalizedPlacement(_ value: String) -> String {
        ["leading", "principal", "primary"].contains(value) ? value : "trailing"
    }
}

struct HTMLToIOSGeneratedScrollContent: View {
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let screen: HTMLToIOSScreenSpec

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical) {
                HTMLToIOSNativeNodeView(store: store, spec: scrollRoot)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .id(screen.root.id)
            }
            .clipped()
            .accessibilityIdentifier(screen.root.id)
            .background {
                Color(htmlToIOS: screen.root.style.background)
                    .ignoresSafeArea()
            }
            .onChange(of: store.tabScrollToTopNonce) { _ in
                guard store.selectedTab == store.tabScrollToTopID else { return }
                withAnimation { proxy.scrollTo(screen.root.id, anchor: .top) }
            }
        }
    }

    private var scrollRoot: HTMLToIOSNodeSpec {
        screen.root.primaryScrollContent ?? screen.root
    }
}

private extension HTMLToIOSNodeSpec {
    var primaryScrollContent: HTMLToIOSNodeSpec? {
        if semantic == "scroll" {
            return children.count == 1 ? children[0] : nil
        }
        guard action == nil, children.count == 1, semantic == "container" else { return nil }
        return children[0].primaryScrollContent
    }
}

struct HTMLToIOSGeneratedScreenView: View {
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let screen: HTMLToIOSScreenSpec

    var body: some View {
        insetContent
            .task(id: screen.id) { await performAutomaticActions() }
    }

    private var scrollContent: some View {
        HTMLToIOSGeneratedScrollContent(store: store, screen: screen)
    }

    private var navigationContent: some View {
        scrollContent
        .navigationTitle(screen.navigation.title)
        .navigationBarTitleDisplayMode(screen.navigation.titleMode == "large" ? .large : .inline)
        .navigationBarBackButtonHidden(screen.navigation.backButton == "hidden")
        .toolbar(screen.showsNavigationBar ? .visible : .hidden, for: .navigationBar)
        .toolbarBackground(screen.navigation.scrollEdgeAppearance == "transparent" ? .hidden : .visible, for: .navigationBar)
        .toolbar(store.tabBarVisibility(for: screen.id), for: .tabBar)
        .toolbar {
            HTMLToIOSGeneratedToolbarContent(store: store, items: screen.navigation.toolbarItems)
        }
    }

    @ViewBuilder private var chromeAlignedNavigationContent: some View {
        if screen.safeArea.owner != "system", let sourceStatusBarHeight = screen.sourceStatusBarHeight, sourceStatusBarHeight > 0 {
            navigationContent
                .padding(.top, sourceStatusBarHeight)
                .ignoresSafeArea(.container, edges: .top)
        } else {
            navigationContent
        }
    }

    @ViewBuilder private var insetContent: some View {
        if screen.safeArea.owner == "system" {
            chromeAlignedNavigationContent
                .safeAreaInset(edge: .top, spacing: 0) { topBarContent }
                .safeAreaInset(edge: .bottom, spacing: 0) { bottomBarContent }
        } else {
            chromeAlignedNavigationContent
                .ignoresSafeArea(.container)
                .overlay(alignment: .top) { topBarContent }
                .overlay(alignment: .bottom) { bottomBarContent }
        }
    }

    @ViewBuilder private var topBarContent: some View {
        if let topBar = screen.topBar {
            HTMLToIOSNativeNodeView(store: store, spec: topBar)
                .frame(maxWidth: .infinity)
        }
    }

    @ViewBuilder private var bottomBarContent: some View {
        if let bottomBar = screen.bottomBar {
            HTMLToIOSNativeNodeView(store: store, spec: bottomBar)
                .frame(maxWidth: .infinity)
                .background { Color(htmlToIOS: bottomBar.style.background).ignoresSafeArea(edges: .bottom) }
        }
    }

    private func performAutomaticActions() async {
        for action in screen.automaticActions {
            if action.delayMilliseconds > 0 {
                try? await Task.sleep(for: .milliseconds(action.delayMilliseconds))
            }
            if !Task.isCancelled { store.perform(action) }
        }
    }
}
'''


SWIFTUI_ROOT = r'''// Generated by sky-html-to-ios. App entry surface for SwiftUI integration.
import SwiftUI

struct HTMLToIOSGeneratedRootView: View {
    @StateObject private var store = HTMLToIOSGeneratedStore()
    private let catalog = HTMLToIOSGeneratedData.catalog

    var body: some View {
        rootContent
        .sheet(item: $store.sheet) { state in presentationView(state.id) }
        .fullScreenCover(item: $store.fullScreen) { state in presentationView(state.id) }
        .popover(isPresented: systemPopoverIsPresented) {
            if let state = store.popover { presentationView(state.id) }
        }
        .overlay(alignment: .topLeading) { customPopoverOverlay }
        .overlay { if let state = store.overlay { presentationView(state.id) } }
    }

    private var systemPopoverIsPresented: Binding<Bool> {
        Binding(
            get: {
                guard let state = store.popover, let presentation = catalog.presentation(state.id) else { return false }
                return !presentation.usesCustomOverlay
            },
            set: { if !$0 { store.popover = nil } }
        )
    }

    @ViewBuilder private var rootContent: some View {
        if let tabs = catalog.tabContainer {
            TabView(selection: Binding(
                get: { store.selectedTab ?? tabs.initialTabId },
                set: { store.selectTab($0, reselectBehavior: tabs.reselectBehavior) }
            )) {
                ForEach(tabs.items) { item in tabRoot(item, container: tabs) }
            }
            .task {
                store.configureTabs(tabs)
                if let route = HTMLToIOSLaunchConfiguration.initialRoute,
                   let item = tabs.items.first(where: { $0.id == route || $0.targetScreenId == route }) {
                    store.selectTab(item.id, reselectBehavior: tabs.reselectBehavior)
                }
            }
        } else {
            NavigationStack(path: $store.path) {
                routeView(HTMLToIOSGeneratedRoute(rawValue: HTMLToIOSLaunchConfiguration.initialRoute ?? catalog.initialRoute))
                    .navigationDestination(for: HTMLToIOSGeneratedRoute.self) { route in routeView(route) }
            }
        }
    }

    @ViewBuilder private func tabRoot(_ item: HTMLToIOSTabItemSpec, container: HTMLToIOSTabContainerSpec) -> some View {
        let content = NavigationStack(path: store.tabPathBinding(for: item.id)) {
            routeView(HTMLToIOSGeneratedRoute(rawValue: item.targetScreenId))
                .navigationDestination(for: HTMLToIOSGeneratedRoute.self) { route in routeView(route) }
        }
        if let badge = item.badge, !badge.isEmpty {
            content
                .tabItem { Label(item.title, systemImage: tabIcon(item)) }
                .badge(badge)
                .tag(item.id)
        } else {
            content
                .tabItem { Label(item.title, systemImage: tabIcon(item)) }
                .tag(item.id)
        }
    }

    private func tabIcon(_ item: HTMLToIOSTabItemSpec) -> String {
        store.selectedTab == item.id ? (item.selectedIcon ?? item.icon ?? "circle.fill") : (item.icon ?? "circle")
    }

    @ViewBuilder private func routeView(_ route: HTMLToIOSGeneratedRoute?) -> some View {
        if let route {
            HTMLToIOSGeneratedScreenFactory.view(route: route, store: store, catalog: catalog)
        } else {
            VStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle")
                Text("Generated screen unavailable")
            }
        }
    }

    @ViewBuilder private func presentationView(_ stateID: String) -> some View {
        if let presentation = catalog.presentation(stateID) {
            ScrollView { HTMLToIOSNativeNodeView(store: store, spec: presentation.node) }
                .presentationDetents(presentationDetents(presentation.detents))
                .presentationDragIndicator(presentation.grabberVisible == false ? .hidden : .visible)
                .interactiveDismissDisabled(presentation.interactiveDismissDisabled)
        } else {
            EmptyView()
        }
    }

    @ViewBuilder private var customPopoverOverlay: some View {
        if let state = store.popover,
           let presentation = catalog.presentation(state.id),
           presentation.usesCustomOverlay {
            GeometryReader { proxy in
                let rect = presentation.sourceRect
                let width = min(CGFloat(rect.indices.contains(2) ? rect[2] : 0), proxy.size.width)
                let height = min(CGFloat(rect.indices.contains(3) ? rect[3] : 0), proxy.size.height)
                let centerX = min(max(CGFloat(rect.indices.contains(0) ? rect[0] : 0) + width / 2, width / 2), proxy.size.width - width / 2)
                let centerY = min(max(CGFloat(rect.indices.contains(1) ? rect[1] : 0) + height / 2, height / 2), proxy.size.height - height / 2)
                ZStack(alignment: .topLeading) {
                    Color.clear
                        .contentShape(Rectangle())
                        .onTapGesture { store.popover = nil }
                    HTMLToIOSNativeNodeView(store: store, spec: presentation.node)
                        .frame(width: width, height: height, alignment: .topLeading)
                        .position(x: centerX, y: centerY)
                }
            }
            .ignoresSafeArea()
            .transition(.opacity)
            .zIndex(1000)
        }
    }

    private func presentationDetents(_ values: [String]) -> Set<PresentationDetent> {
        let mapped = values.compactMap { raw -> PresentationDetent? in
            let value = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if value == "medium" { return .medium }
            if value == "large" { return .large }
            if value.hasPrefix("fraction:"), let fraction = Double(value.dropFirst("fraction:".count)) {
                return .fraction(min(max(fraction, 0.1), 1))
            }
            if value.hasPrefix("height:"), let height = Double(value.dropFirst("height:".count)) {
                return .height(max(height, 44))
            }
            return nil
        }
        return Set(mapped.isEmpty ? [.large] : mapped)
    }
}
'''


UIKIT_RUNTIME = r'''// Generated by sky-html-to-ios. Native UIKit rendering runtime.
import UIKit

private extension UIColor {
    convenience init?(htmlToIOS value: String?) {
        guard let value else { return nil }
        let parts = value.split(whereSeparator: { !$0.isNumber && $0 != "." }).compactMap { Double($0) }
        guard parts.count >= 3 else { return nil }
        self.init(red: parts[0] / 255, green: parts[1] / 255, blue: parts[2] / 255,
                  alpha: parts.count > 3 ? parts[3] : 1)
    }
}

private final class HTMLToIOSUIKitState {
    var flags = Set<String>()
    var hiddenNodeIDs = Set<String>()
    var selectedByState: [String: String] = [:]
    var selectionOverrides: [String: Bool] = [:]
    var selectionCounts: [String: Int] = [:]

    func isSelected(_ spec: HTMLToIOSNodeSpec) -> Bool {
        guard let stateID = spec.selectionStateID else { return false }
        if let selected = selectedByState[stateID] { return selected == spec.id }
        return selectionOverrides[stateID + "|" + spec.id] ?? spec.isInitiallySelected ?? false
    }

    func perform(_ spec: HTMLToIOSActionSpec) {
        let stateID = spec.targetStateID ?? spec.target
        guard let stateID else { return }
        if spec.stateKind == "selection", let nodeID = spec.targetNodeID ?? spec.sourceNodeID {
            if spec.selectionMode == "exclusive" {
                selectedByState[stateID] = nodeID
            } else {
                let key = stateID + "|" + nodeID
                let current = selectionOverrides[key] ?? spec.initiallySelected ?? false
                let next = !current
                selectionOverrides[key] = next
                let total = spec.selectionCountTotal ?? 0
                let count = selectionCounts[stateID] ?? spec.selectionCountInitial ?? 0
                selectionCounts[stateID] = min(max(count + (next ? 1 : -1), 0), total)
            }
        } else if spec.localEffect == "remove", let nodeID = spec.targetNodeID {
            hiddenNodeIDs.insert(nodeID)
        } else if flags.contains(stateID) {
            flags.remove(stateID)
        } else {
            flags.insert(stateID)
        }
    }
}

final class HTMLToIOSNodeRenderer {
    typealias ActionHandler = (HTMLToIOSActionSpec?) -> Void
    private let actionHandler: ActionHandler
    private let state: HTMLToIOSUIKitState

    fileprivate init(state: HTMLToIOSUIKitState, actionHandler: @escaping ActionHandler) {
        self.state = state
        self.actionHandler = actionHandler
    }

    func makeView(_ spec: HTMLToIOSNodeSpec) -> UIView {
        let view: UIView
        switch spec.semantic {
        case "button", "link", "menu-item", "tab-item":
            if spec.children.isEmpty && spec.overlayChildren.isEmpty {
                let button = UIButton(type: .system)
                button.setTitle(displayText(spec), for: .normal)
                button.titleLabel?.numberOfLines = spec.style.textLineLimit ?? 0
                button.titleLabel?.lineBreakMode = lineBreakMode(spec)
                button.contentHorizontalAlignment = .leading
                button.addAction(UIAction { [actionHandler] _ in actionHandler(spec.action) }, for: .touchUpInside)
                view = button
            } else {
                let control = UIControl()
                let content = spec.axis == "grid" ? makeGrid(spec) : makeStack(spec)
                content.translatesAutoresizingMaskIntoConstraints = false
                control.addSubview(content)
                let padding = spec.style.padding ?? [0, 0, 0, 0]
                NSLayoutConstraint.activate([
                    content.topAnchor.constraint(equalTo: control.topAnchor, constant: padding.indices.contains(0) ? padding[0] : 0),
                    content.trailingAnchor.constraint(equalTo: control.trailingAnchor, constant: -(padding.indices.contains(1) ? padding[1] : 0)),
                    content.bottomAnchor.constraint(equalTo: control.bottomAnchor, constant: -(padding.indices.contains(2) ? padding[2] : 0)),
                    content.leadingAnchor.constraint(equalTo: control.leadingAnchor, constant: padding.indices.contains(3) ? padding[3] : 0),
                ])
                control.addAction(UIAction { [actionHandler] _ in actionHandler(spec.action) }, for: .touchUpInside)
                view = control
            }
        case "text-field", "input", "search-field", "secure-field":
            let field = UITextField()
            field.borderStyle = .roundedRect
            field.placeholder = spec.placeholder
            field.isSecureTextEntry = spec.semantic == "secure-field"
            view = field
        case "toggle", "switch", "checkbox":
            let row = UIStackView()
            row.axis = .horizontal; row.spacing = 8
            let label = makeLabel(spec.text, spec: spec)
            let toggle = UISwitch()
            toggle.isOn = spec.selectionStateID == nil ? (spec.isInitiallySelected ?? false) : state.isSelected(spec)
            if spec.action != nil {
                toggle.addAction(UIAction { [actionHandler] _ in actionHandler(spec.action) }, for: .valueChanged)
            }
            row.addArrangedSubview(label); row.addArrangedSubview(toggle)
            view = row
        case "progress", "progress-view":
            let progress = UIProgressView(progressViewStyle: .default); progress.progress = 0.55
            view = progress
        case "carousel", "scroll":
            view = makeScrollContainer(spec)
        case "image", "icon":
            let image = UIImageView(image: UIImage(named: spec.assetName ?? "") ?? UIImage(systemName: spec.systemImage ?? (spec.semantic == "icon" ? "circle.fill" : "photo")))
            let mode = (spec.style.mediaContentMode ?? "contain").lowercased()
            image.contentMode = mode.contains("cover") || mode == "fill" ? .scaleAspectFill : .scaleAspectFit
            image.clipsToBounds = true
            if spec.semantic == "icon" {
                let height = max(spec.style.preferredHeight ?? 18, 1)
                let width = max(spec.style.preferredWidth ?? height, 1)
                image.heightAnchor.constraint(equalToConstant: height).isActive = true
                image.widthAnchor.constraint(equalToConstant: width).isActive = true
            } else if let height = spec.style.preferredHeight, height > 0 {
                image.heightAnchor.constraint(equalToConstant: height).isActive = true
            }
            view = image
        case "divider", "separator":
            let divider = UIView(); divider.backgroundColor = .separator
            divider.heightAnchor.constraint(equalToConstant: 1).isActive = true
            view = divider
        case "text", "label", "heading":
            view = makeLabel(flattenedText(spec), spec: spec)
        default:
            let stack: UIView
            if spec.axis == "grid" {
                stack = makeGrid(spec)
            } else if spec.axis == "overlay" {
                stack = makeOverlay(spec)
            } else {
                stack = makeStack(spec)
            }
            if spec.action != nil {
                stack.isUserInteractionEnabled = true
                stack.addGestureRecognizer(HTMLToIOSClosureTapGestureRecognizer { [actionHandler] in actionHandler(spec.action) })
            }
            view = stack
        }
        applyStyle(spec, to: view)
        attachOverlayChildren(spec, to: view)
        applyMotion(spec, to: view)
        view.isHidden = state.hiddenNodeIDs.contains(spec.id)
            || (spec.visibleWhenStateID != nil && !state.flags.contains(spec.visibleWhenStateID!))
        view.accessibilityIdentifier = spec.id
        view.accessibilityLabel = spec.accessibilityLabel ?? (spec.text.isEmpty ? nil : spec.text)
        return view
    }

    private func makeStack(_ spec: HTMLToIOSNodeSpec) -> UIStackView {
        let stack = UIStackView()
        stack.axis = spec.axis == "horizontal" ? .horizontal : .vertical
        stack.alignment = spec.axis == "horizontal" ? .center : .fill
        stack.spacing = spec.style.spacing ?? 8
        if !stateText(spec).isEmpty && spec.children.isEmpty {
            stack.addArrangedSubview(makeLabel(stateText(spec), spec: spec))
        }
        spec.children.forEach { stack.addArrangedSubview(makeView($0)) }
        return stack
    }

    private func makeGrid(_ spec: HTMLToIOSNodeSpec) -> UIStackView {
        let grid = UIStackView()
        grid.axis = .vertical
        grid.alignment = .fill
        grid.spacing = spec.style.spacing ?? 0
        let columns = max(spec.style.gridColumnCount ?? 2, 1)
        for start in stride(from: 0, to: spec.children.count, by: columns) {
            let row = UIStackView()
            row.axis = .horizontal
            row.alignment = .fill
            row.distribution = .fillEqually
            row.spacing = spec.style.spacing ?? 0
            let end = min(start + columns, spec.children.count)
            for index in start..<end {
                row.addArrangedSubview(makeView(spec.children[index]))
            }
            if end - start < columns {
                for _ in 0..<(columns - (end - start)) {
                    let placeholder = UIView()
                    placeholder.isUserInteractionEnabled = false
                    row.addArrangedSubview(placeholder)
                }
            }
            grid.addArrangedSubview(row)
        }
        return grid
    }

    private func makeOverlay(_ spec: HTMLToIOSNodeSpec) -> UIView {
        let overlay = UIView()
        for childSpec in spec.children {
            let child = makeView(childSpec)
            overlay.addSubview(child)
            NSLayoutConstraint.activate([
                child.centerXAnchor.constraint(equalTo: overlay.centerXAnchor, constant: childSpec.style.offsetX ?? 0),
                child.centerYAnchor.constraint(equalTo: overlay.centerYAnchor, constant: childSpec.style.offsetY ?? 0),
            ])
        }
        return overlay
    }

    private func attachOverlayChildren(_ spec: HTMLToIOSNodeSpec, to parent: UIView) {
        for childSpec in spec.overlayChildren.sorted(by: {
            ($0.style.zIndex ?? 0) < ($1.style.zIndex ?? 0)
        }) {
            let child = makeView(childSpec)
            parent.addSubview(child)
            var constraints = [
                child.centerXAnchor.constraint(equalTo: parent.centerXAnchor, constant: childSpec.style.offsetX ?? 0),
                child.centerYAnchor.constraint(equalTo: parent.centerYAnchor, constant: childSpec.style.offsetY ?? 0),
            ]
            if childSpec.style.fixedWidth == nil, let width = childSpec.style.preferredWidth, width > 0 {
                constraints.append(child.widthAnchor.constraint(equalToConstant: width))
            }
            if childSpec.style.fixedHeight == nil, let height = childSpec.style.preferredHeight, height > 0 {
                constraints.append(child.heightAnchor.constraint(equalToConstant: height))
            }
            NSLayoutConstraint.activate(constraints)
        }
    }

    private func makeScrollContainer(_ spec: HTMLToIOSNodeSpec) -> UIView {
        let axis = spec.semantic == "carousel" ? "horizontal" : (spec.style.scrollAxis ?? "vertical")
        if axis == "none" { return makeStack(spec) }
        let scroll = UIScrollView()
        let stack = makeStack(spec)
        scroll.directionalLockEnabled = axis != "both"
        scroll.alwaysBounceHorizontal = false
        scroll.alwaysBounceVertical = false
        scroll.showsHorizontalScrollIndicator = axis == "horizontal" || axis == "both"
        scroll.showsVerticalScrollIndicator = axis == "vertical" || axis == "both"
        stack.translatesAutoresizingMaskIntoConstraints = false
        scroll.addSubview(stack)
        var constraints = [
            stack.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
            stack.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor),
            stack.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor),
        ]
        if axis == "horizontal" {
            stack.axis = .horizontal
            constraints.append(stack.heightAnchor.constraint(equalTo: scroll.frameLayoutGuide.heightAnchor))
        } else if axis == "vertical" {
            stack.axis = .vertical
            constraints.append(stack.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor))
        }
        NSLayoutConstraint.activate(constraints)
        return scroll
    }

    private func displayText(_ spec: HTMLToIOSNodeSpec) -> String {
        let current = stateText(spec)
        if !current.isEmpty { return current }
        return spec.children.lazy.map(\.text).first { !$0.isEmpty } ?? "Action"
    }

    private func flattenedText(_ spec: HTMLToIOSNodeSpec) -> String {
        stateText(spec) + spec.children.map(flattenedText).joined()
    }

    private func stateText(_ spec: HTMLToIOSNodeSpec) -> String {
        if let stateID = spec.selectionCountStateID,
           let initial = spec.selectionCountInitial,
           let total = spec.selectionCountTotal {
            return "\(state.selectionCounts[stateID] ?? initial) / \(total)"
        }
        return spec.text
    }

    private func makeLabel(_ text: String, spec: HTMLToIOSNodeSpec) -> UILabel {
        let label = UILabel()
        label.text = text
        label.numberOfLines = spec.style.textLineLimit ?? 0
        label.lineBreakMode = lineBreakMode(spec)
        label.font = .systemFont(ofSize: spec.style.fontSize ?? 16, weight: fontWeight(spec.style.fontWeight))
        if spec.style.preservesIntrinsicWidth == true {
            label.setContentCompressionResistancePriority(.required, for: .horizontal)
            label.setContentHuggingPriority(.required, for: .horizontal)
        }
        return label
    }

    private func lineBreakMode(_ spec: HTMLToIOSNodeSpec) -> NSLineBreakMode {
        if spec.style.textOverflow == "ellipsis" { return .byTruncatingTail }
        if spec.style.textLineLimit == 1 { return .byClipping }
        return .byWordWrapping
    }

    private func fontWeight(_ raw: String?) -> UIFont.Weight {
        let value = Int(raw ?? "400") ?? 400
        if value >= 700 { return .bold }; if value >= 600 { return .semibold }; if value >= 500 { return .medium }
        return .regular
    }

    private func motionProgress(_ motion: HTMLToIOSMotionSpec, forced: Double) -> Double {
        motion.reverses ? 1 - forced : forced
    }

    private func sampled(_ values: [Double], progress: Double, fallback: Double) -> Double {
        guard values.count >= 3 else { return values.first ?? fallback }
        if progress <= 0.5 {
            return values[0] + (values[1] - values[0]) * progress * 2
        }
        return values[1] + (values[2] - values[1]) * (progress - 0.5) * 2
    }

    private func applyMotion(_ spec: HTMLToIOSNodeSpec, to view: UIView) {
        guard !spec.motions.isEmpty else { return }
        if let forced = HTMLToIOSLaunchConfiguration.motionProgress {
            var transform = CGAffineTransform.identity
            var alpha = view.alpha
            for motion in spec.motions {
                let progress = motionProgress(motion, forced: forced)
                transform = transform
                    .rotated(by: CGFloat(motion.rotationDegrees * progress * .pi / 180))
                    .scaledBy(
                        x: CGFloat(sampled(motion.scaleValues, progress: progress, fallback: 1)),
                        y: CGFloat(sampled(motion.scaleValues, progress: progress, fallback: 1))
                    )
                alpha *= sampled(motion.opacityValues, progress: progress, fallback: 1)
            }
            view.transform = transform
            view.alpha = alpha
            return
        }
        for motion in spec.motions {
            let duration = max(Double(motion.durationMilliseconds) / 1000, 0.001)
            if abs(motion.rotationDegrees) > 0.001 {
                let rotation = CABasicAnimation(keyPath: "transform.rotation")
                rotation.fromValue = 0
                rotation.toValue = motion.rotationDegrees * (motion.reverses ? -1 : 1) * .pi / 180
                rotation.duration = duration
                rotation.beginTime = CACurrentMediaTime() + Double(motion.delayMilliseconds) / 1000
                rotation.repeatCount = motion.repeats ? .infinity : 0
                rotation.autoreverses = motion.autoreverses
                rotation.timingFunction = CAMediaTimingFunction(name: .linear)
                view.layer.add(rotation, forKey: "html-to-ios-\(motion.id)-rotation")
            }
            if motion.scaleValues.count >= 3 && motion.scaleValues.max() != motion.scaleValues.min() {
                let scale = CAKeyframeAnimation(keyPath: "transform.scale")
                scale.values = motion.scaleValues
                scale.keyTimes = [0, 0.5, 1]
                scale.duration = duration
                scale.beginTime = CACurrentMediaTime() + Double(motion.delayMilliseconds) / 1000
                scale.repeatCount = motion.repeats ? .infinity : 0
                scale.autoreverses = motion.autoreverses
                view.layer.add(scale, forKey: "html-to-ios-\(motion.id)-scale")
            }
        }
    }

    private func applyStyle(_ spec: HTMLToIOSNodeSpec, to view: UIView) {
        view.translatesAutoresizingMaskIntoConstraints = false
        let selected = state.isSelected(spec)
        let background = spec.selectionStateID == nil ? spec.style.background : (selected ? spec.selectedBackground : spec.unselectedBackground)
        let foreground = spec.selectionStateID == nil ? spec.style.foreground : (selected ? spec.selectedForeground : spec.unselectedForeground)
        let gradientValues = spec.selectionStateID == nil ? spec.style.gradientColors : (selected ? spec.selectedGradientColors : spec.unselectedGradientColors)
        let gradientFallback = gradientValues?.first
        if let color = UIColor(htmlToIOS: gradientFallback ?? background ?? spec.style.background) { view.backgroundColor = color }
        if let values = gradientValues ?? spec.style.gradientColors, values.count >= 2 {
            let gradient = CAGradientLayer()
            gradient.name = "html-to-ios-gradient"
            gradient.colors = values.compactMap { UIColor(htmlToIOS: $0)?.cgColor }
            if let locations = spec.style.gradientLocations, locations.count == values.count {
                gradient.locations = locations.enumerated().map { index, value in
                    NSNumber(value: value ?? (values.count <= 1 ? 0 : Double(index) / Double(values.count - 1)))
                }
            }
            if spec.style.gradientKind == "radial" {
                gradient.type = .radial
                gradient.startPoint = CGPoint(x: 0.5, y: 0.5)
                gradient.endPoint = CGPoint(x: 1, y: 1)
            } else {
                let radians = (spec.style.gradientAngle ?? 180) * .pi / 180
                let dx = CGFloat(sin(radians)); let dy = CGFloat(-cos(radians))
                gradient.startPoint = CGPoint(x: 0.5 - dx / 2, y: 0.5 - dy / 2)
                gradient.endPoint = CGPoint(x: 0.5 + dx / 2, y: 0.5 + dy / 2)
            }
            view.layer.insertSublayer(gradient, at: 0)
        }
        if let assetName = spec.backgroundAssetName, let backgroundImage = UIImage(named: assetName) {
            let imageView = UIImageView(image: backgroundImage)
            let mode = (spec.style.backgroundContentMode ?? "cover").lowercased()
            imageView.contentMode = mode.contains("contain") ? .scaleAspectFit : .scaleAspectFill
            imageView.clipsToBounds = true
            imageView.translatesAutoresizingMaskIntoConstraints = false
            view.insertSubview(imageView, at: 0)
            NSLayoutConstraint.activate([
                imageView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
                imageView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
                imageView.topAnchor.constraint(equalTo: view.topAnchor),
                imageView.bottomAnchor.constraint(equalTo: view.bottomAnchor)
            ])
        }
        if let color = UIColor(htmlToIOS: foreground ?? spec.style.foreground) { view.tintColor = color; (view as? UILabel)?.textColor = color }
        view.layer.cornerRadius = spec.style.cornerRadius ?? 0
        view.alpha = spec.style.opacity ?? 1
        if let width = spec.style.borderWidth, width > 0, spec.style.borderStyle != "dashed", spec.style.borderStyle != "dotted" {
            view.layer.borderWidth = width
            view.layer.borderColor = UIColor(htmlToIOS: spec.style.borderColor)?.cgColor
        } else if let width = spec.style.borderWidth, width > 0, let color = UIColor(htmlToIOS: spec.style.borderColor) {
            let border = CAShapeLayer()
            border.name = "html-to-ios-border"
            border.fillColor = UIColor.clear.cgColor
            border.strokeColor = color.cgColor
            border.lineWidth = width
            border.lineDashPattern = spec.style.borderStyle == "dotted" ? [1, 3] : [6, 4]
            view.layer.addSublayer(border)
        }
        if let color = UIColor(htmlToIOS: spec.style.shadowColor) {
            view.layer.shadowColor = color.cgColor
            view.layer.shadowOpacity = 1
            view.layer.shadowRadius = spec.style.shadowRadius ?? 0
            view.layer.shadowOffset = CGSize(width: spec.style.shadowOffsetX ?? 0, height: spec.style.shadowOffsetY ?? 0)
        }
        let needsClipping = spec.style.clipsContent == true || (spec.style.cornerRadius ?? 0) > 0
        view.clipsToBounds = needsClipping && spec.style.shadowColor == nil
        if let width = spec.style.fixedWidth, width > 0 {
            view.widthAnchor.constraint(equalToConstant: width).isActive = true
        }
        if let height = spec.style.fixedHeight, height > 0 {
            view.heightAnchor.constraint(equalToConstant: height).isActive = true
        }
        if let ratio = spec.style.aspectRatio,
           ratio > 0,
           spec.style.fixedWidth == nil || spec.style.fixedHeight == nil {
            view.widthAnchor.constraint(equalTo: view.heightAnchor, multiplier: ratio).isActive = true
        }
        if spec.style.preservesIntrinsicWidth == true {
            view.setContentCompressionResistancePriority(.required, for: .horizontal)
            view.setContentHuggingPriority(.required, for: .horizontal)
        }
        if let height = spec.style.minHeight, height > 0, height < 161 {
            view.heightAnchor.constraint(greaterThanOrEqualToConstant: height).isActive = true
        }
        if let padding = spec.style.padding, padding.count == 4, let stack = view as? UIStackView {
            stack.isLayoutMarginsRelativeArrangement = true
            stack.directionalLayoutMargins = NSDirectionalEdgeInsets(top: padding[0], leading: padding[3], bottom: padding[2], trailing: padding[1])
        }
    }
}

private final class HTMLToIOSClosureTapGestureRecognizer: UITapGestureRecognizer {
    private let action: () -> Void
    init(_ action: @escaping () -> Void) {
        self.action = action
        super.init(target: nil, action: nil)
        addTarget(self, action: #selector(invoke))
    }
    @objc func invoke() { action() }
}

class HTMLToIOSGeneratedScreenViewController: UIViewController {
    let screen: HTMLToIOSScreenSpec
    let actionHandler: (HTMLToIOSActionSpec?) -> Void
    private let generatedState = HTMLToIOSUIKitState()
    private var scheduledAutomaticActions = false
    private weak var generatedScrollView: UIScrollView?
    private weak var generatedTopBar: UIView?
    private weak var generatedBottomBar: UIView?

    init(screen: HTMLToIOSScreenSpec, actionHandler: @escaping (HTMLToIOSActionSpec?) -> Void) {
        self.screen = screen; self.actionHandler = actionHandler; super.init(nibName: nil, bundle: nil)
    }
    @available(*, unavailable) required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        updateGeneratedLayers(in: view)
        updateGeneratedScrollInsets()
    }

    private func updateGeneratedScrollInsets() {
        guard let scroll = generatedScrollView else { return }
        let insets = UIEdgeInsets(
            top: generatedTopBar?.bounds.height ?? 0,
            left: 0,
            bottom: generatedBottomBar?.bounds.height ?? 0,
            right: 0
        )
        if scroll.contentInset != insets { scroll.contentInset = insets }
        if scroll.verticalScrollIndicatorInsets != insets { scroll.verticalScrollIndicatorInsets = insets }
        if scroll.horizontalScrollIndicatorInsets != insets { scroll.horizontalScrollIndicatorInsets = insets }
    }

    private func updateGeneratedLayers(in current: UIView) {
        for layer in current.layer.sublayers ?? [] {
            if layer.name == "html-to-ios-gradient" { layer.frame = current.bounds }
            if let border = layer as? CAShapeLayer, layer.name == "html-to-ios-border" {
                border.frame = current.bounds
                border.path = UIBezierPath(
                    roundedRect: current.bounds.insetBy(dx: border.lineWidth / 2, dy: border.lineWidth / 2),
                    cornerRadius: max(current.layer.cornerRadius - border.lineWidth / 2, 0)
                ).cgPath
            }
        }
        current.subviews.forEach { updateGeneratedLayers(in: $0) }
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(htmlToIOS: screen.root.style.background) ?? .systemBackground
        title = screen.title
        renderScreen()
        scheduleAutomaticActions()
    }

    private func renderScreen() {
        let previousOffset = generatedScrollView?.contentOffset ?? .zero
        generatedScrollView = nil; generatedTopBar = nil; generatedBottomBar = nil
        view.subviews.forEach { $0.removeFromSuperview() }
        let renderer = HTMLToIOSNodeRenderer(state: generatedState, actionHandler: { [weak self] action in self?.perform(action) })
        let scroll = UIScrollView(); let content = wrapGeneratedContent(renderer.makeView(screen.root))
        scroll.directionalLockEnabled = true
        scroll.alwaysBounceHorizontal = false
        scroll.showsHorizontalScrollIndicator = false
        scroll.backgroundColor = view.backgroundColor
        scroll.contentInsetAdjustmentBehavior = screen.safeArea.contentInsetAdjustment == "never" ? .never : .automatic
        scroll.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(scroll); scroll.addSubview(content)
        generatedScrollView = scroll
        var constraints = [
            scroll.leadingAnchor.constraint(equalTo: view.leadingAnchor), scroll.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: view.topAnchor), scroll.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            content.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
            content.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
            content.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor),
            content.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor),
            content.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor)
        ]
        if let topBar = screen.topBar {
            let top = renderer.makeView(topBar)
            view.addSubview(top)
            generatedTopBar = top
            constraints.append(contentsOf: [
                top.leadingAnchor.constraint(equalTo: view.leadingAnchor),
                top.trailingAnchor.constraint(equalTo: view.trailingAnchor),
                top.topAnchor.constraint(
                    equalTo: screen.safeArea.owner == "system" ? view.safeAreaLayoutGuide.topAnchor : view.topAnchor,
                    constant: CGFloat(screen.sourceStatusBarHeight ?? 0)
                )
            ])
        }
        if let bottomBar = screen.bottomBar {
            let bottom = renderer.makeView(bottomBar)
            view.addSubview(bottom)
            generatedBottomBar = bottom
            constraints.append(contentsOf: [
                bottom.leadingAnchor.constraint(equalTo: view.leadingAnchor),
                bottom.trailingAnchor.constraint(equalTo: view.trailingAnchor),
                bottom.bottomAnchor.constraint(equalTo: screen.safeArea.owner == "system" ? view.safeAreaLayoutGuide.bottomAnchor : view.bottomAnchor)
            ])
        }
        NSLayoutConstraint.activate(constraints)
        view.layoutIfNeeded()
        scroll.setContentOffset(previousOffset, animated: false)
    }

    func wrapGeneratedContent(_ content: UIView) -> UIView { content }

    private func scheduleAutomaticActions() {
        guard !scheduledAutomaticActions else { return }
        scheduledAutomaticActions = true
        for action in screen.automaticActions {
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(action.delayMilliseconds) / 1000) { [weak self] in self?.perform(action) }
        }
    }

    private func perform(_ spec: HTMLToIOSActionSpec?) {
        guard let spec else { return }
        switch spec.action {
        case "toggle-state", "toggle-selection", "toggle-expanded":
            generatedState.perform(spec)
            UIView.transition(with: view, duration: 0.25, options: [.transitionCrossDissolve, .allowAnimatedContent]) {
                self.renderScreen()
            }
        default:
            actionHandler(spec)
        }
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        navigationController?.setNavigationBarHidden(!screen.showsNavigationBar, animated: false)
        navigationController?.navigationBar.prefersLargeTitles = screen.navigation.titleMode == "large"
        navigationItem.largeTitleDisplayMode = screen.navigation.titleMode == "large" ? .always : .never
        navigationItem.hidesBackButton = screen.navigation.backButton == "hidden"
        if screen.navigation.scrollEdgeAppearance == "transparent" {
            let appearance = UINavigationBarAppearance()
            appearance.configureWithTransparentBackground()
            navigationItem.standardAppearance = appearance
            navigationItem.scrollEdgeAppearance = appearance
        }
        let leading = screen.navigation.toolbarItems.filter { $0.placement == "leading" }.map(makeBarButtonItem)
        let trailing = screen.navigation.toolbarItems.filter { ["trailing", "primary"].contains($0.placement) }.map(makeBarButtonItem)
        navigationItem.leftBarButtonItems = leading
        navigationItem.rightBarButtonItems = trailing
        if let principal = screen.navigation.toolbarItems.first(where: { $0.placement == "principal" }) {
            let label = UILabel()
            label.text = principal.title
            label.font = .preferredFont(forTextStyle: .headline)
            label.accessibilityIdentifier = principal.id
            navigationItem.titleView = label
        }
    }

    private func makeBarButtonItem(_ item: HTMLToIOSToolbarItemSpec) -> UIBarButtonItem {
        let action = UIAction { [weak self] _ in self?.actionHandler(item.action) }
        let barItem: UIBarButtonItem
        if let icon = item.icon, let image = UIImage(systemName: icon) {
            barItem = UIBarButtonItem(image: image, primaryAction: action)
        } else {
            barItem = UIBarButtonItem(title: item.title, primaryAction: action)
        }
        barItem.accessibilityIdentifier = item.id
        return barItem
    }
}
'''


UIKIT_ROOT = r'''// Generated by sky-html-to-ios. App entry surface for UIKit integration.
import UIKit

final class HTMLToIOSGeneratedCustomOverlayController: UIViewController {
    private let presentation: HTMLToIOSPresentationSpec
    private let actionHandler: (HTMLToIOSActionSpec?) -> Void
    private let generatedState = HTMLToIOSUIKitState()

    init(presentation: HTMLToIOSPresentationSpec, actionHandler: @escaping (HTMLToIOSActionSpec?) -> Void) {
        self.presentation = presentation
        self.actionHandler = actionHandler
        super.init(nibName: nil, bundle: nil)
        modalPresentationStyle = .overFullScreen
        modalTransitionStyle = .crossDissolve
    }

    @available(*, unavailable) required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .clear
        let backdrop = UIControl()
        backdrop.translatesAutoresizingMaskIntoConstraints = false
        backdrop.backgroundColor = .clear
        backdrop.addAction(UIAction { [weak self] _ in self?.dismiss(animated: true) }, for: .touchUpInside)
        view.addSubview(backdrop)

        let renderer = HTMLToIOSNodeRenderer(state: generatedState, actionHandler: actionHandler)
        let panel = renderer.makeView(presentation.node)
        panel.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(panel)
        let rect = presentation.sourceRect
        NSLayoutConstraint.activate([
            backdrop.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            backdrop.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            backdrop.topAnchor.constraint(equalTo: view.topAnchor),
            backdrop.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            panel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: CGFloat(rect.indices.contains(0) ? rect[0] : 0)),
            panel.topAnchor.constraint(equalTo: view.topAnchor, constant: CGFloat(rect.indices.contains(1) ? rect[1] : 0)),
        ])
    }
}

final class HTMLToIOSGeneratedCoordinator: NSObject, UITabBarControllerDelegate {
    private weak var hostController: UIViewController?
    private let catalog = HTMLToIOSGeneratedData.catalog
    private var primaryNavigationController: UINavigationController?
    private var tabBarController: UITabBarController?
    private var tabNavigationControllers: [String: UINavigationController] = [:]
    private var lastSelectedTabIndex: Int?

    init(hostController: UIViewController) { self.hostController = hostController }

    func start() {
        if let tabs = catalog.tabContainer {
            let tabController = UITabBarController()
            tabController.delegate = self
            tabController.viewControllers = tabs.items.compactMap { item in
                guard let route = HTMLToIOSGeneratedRoute(rawValue: item.targetScreenId), let controller = makeScreen(route) else { return nil }
                let navigation = UINavigationController(rootViewController: controller)
                navigation.tabBarItem = UITabBarItem(
                    title: item.title,
                    image: UIImage(systemName: item.icon ?? "circle"),
                    selectedImage: UIImage(systemName: item.selectedIcon ?? item.icon ?? "circle.fill")
                )
                navigation.tabBarItem.badgeValue = item.badge
                tabNavigationControllers[item.id] = navigation
                return navigation
            }
            let launchRoute = HTMLToIOSLaunchConfiguration.initialRoute
            tabController.selectedIndex = tabs.items.firstIndex(where: {
                $0.id == launchRoute || $0.targetScreenId == launchRoute
            }) ?? tabs.items.firstIndex(where: { $0.id == tabs.initialTabId }) ?? 0
            lastSelectedTabIndex = tabController.selectedIndex
            tabBarController = tabController
            embed(tabController)
        } else {
            let routeID = HTMLToIOSLaunchConfiguration.initialRoute ?? catalog.initialRoute
            guard let route = HTMLToIOSGeneratedRoute(rawValue: routeID), let controller = makeScreen(route) else { return }
            let navigation = UINavigationController(rootViewController: controller)
            primaryNavigationController = navigation
            embed(navigation)
        }
    }

    private func embed(_ controller: UIViewController) {
        guard let hostController else { return }
        hostController.addChild(controller)
        controller.view.translatesAutoresizingMaskIntoConstraints = false
        hostController.view.addSubview(controller.view)
        NSLayoutConstraint.activate([
            controller.view.leadingAnchor.constraint(equalTo: hostController.view.leadingAnchor),
            controller.view.trailingAnchor.constraint(equalTo: hostController.view.trailingAnchor),
            controller.view.topAnchor.constraint(equalTo: hostController.view.topAnchor),
            controller.view.bottomAnchor.constraint(equalTo: hostController.view.bottomAnchor),
        ])
        controller.didMove(toParent: hostController)
    }

    private func makeScreen(_ route: HTMLToIOSGeneratedRoute) -> UIViewController? {
        guard let controller = HTMLToIOSGeneratedScreenFactory.make(
            route: route,
            catalog: catalog,
            actionHandler: { [weak self] action in self?.perform(action) }
        ) else { return nil }
        if let tabs = catalog.tabContainer, tabs.visibility == "hide-on-push" {
            controller.hidesBottomBarWhenPushed = !tabs.items.contains(where: { $0.targetScreenId == route.rawValue })
        }
        return controller
    }

    private var currentNavigationController: UINavigationController? {
        if let tabBarController,
           let navigation = tabBarController.selectedViewController as? UINavigationController { return navigation }
        return primaryNavigationController
    }

    private func perform(_ spec: HTMLToIOSActionSpec?) {
        guard let spec else { return }
        let routeID = spec.targetScreenID ?? spec.target
        let stateID = spec.targetStateID ?? spec.target
        switch spec.action {
        case "push":
            if let routeID, let route = HTMLToIOSGeneratedRoute(rawValue: routeID), let controller = makeScreen(route) {
                currentNavigationController?.pushViewController(controller, animated: true)
            }
        case "replace-stack", "set-flow-state":
            if let routeID, let route = HTMLToIOSGeneratedRoute(rawValue: routeID), let controller = makeScreen(route) {
                currentNavigationController?.setViewControllers([controller], animated: true)
            }
        case "pop": currentNavigationController?.popViewController(animated: true)
        case "pop-to-root": currentNavigationController?.popToRootViewController(animated: true)
        case "switch-tab", "select-tab":
            guard let tabs = catalog.tabContainer, let routeID else { return }
            if let index = tabs.items.firstIndex(where: { $0.id == routeID || $0.targetScreenId == routeID }) {
                tabBarController?.selectedIndex = index
                lastSelectedTabIndex = index
            }
        case "dismiss", "dismiss-sheet", "dismiss-fullscreen", "dismiss-popover", "dismiss-overlay":
            currentNavigationController?.presentedViewController?.dismiss(animated: true)
        case "present-sheet", "present-fullscreen", "present-full-screen", "present-popover", "present-overlay", "show-dialog":
            guard let stateID, let presentation = catalog.presentation(stateID) else { return }
            if presentation.usesCustomOverlay {
                let controller = HTMLToIOSGeneratedCustomOverlayController(
                    presentation: presentation,
                    actionHandler: { [weak self] action in self?.perform(action) }
                )
                currentNavigationController?.present(controller, animated: true)
                return
            }
            let controller = HTMLToIOSGeneratedScreenViewController(
                screen: HTMLToIOSScreenSpec(
                    id: stateID,
                    swiftCase: stateID,
                    title: "",
                    showsNavigationBar: false,
                    sourceStatusBarHeight: nil,
                    safeArea: HTMLToIOSSafeAreaSpec(owner: "system", contentInsetAdjustment: "automatic", containerWidthPolicy: "full-parent-bounds", containerHeightPolicy: "full-parent-bounds", subtractFromContainerDimensions: false),
                    navigation: HTMLToIOSNavigationSpec(style: "hidden", title: "", titleMode: "inline", scrollEdgeAppearance: "automatic", backButton: "system", toolbarItems: []),
                    root: presentation.node,
                    topBar: nil,
                    bottomBar: nil,
                    presentations: [],
                    automaticActions: []
                ),
                actionHandler: { [weak self] action in self?.perform(action) }
            )
            if spec.action.contains("fullscreen") { controller.modalPresentationStyle = .fullScreen }
            else if spec.action.contains("popover") { controller.modalPresentationStyle = .popover }
            else {
                controller.modalPresentationStyle = .pageSheet
                if let sheet = controller.sheetPresentationController {
                    let values = Set(presentation.detents.map { $0.lowercased() })
                    var detents: [UISheetPresentationController.Detent] = []
                    if values.contains("medium") { detents.append(.medium()) }
                    if values.contains("large") || detents.isEmpty { detents.append(.large()) }
                    sheet.detents = detents
                    sheet.prefersGrabberVisible = presentation.grabberVisible ?? true
                }
                controller.isModalInPresentation = presentation.interactiveDismissDisabled
            }
            currentNavigationController?.present(controller, animated: true)
        default: break
        }
    }

    func tabBarController(_ tabBarController: UITabBarController, didSelect viewController: UIViewController) {
        defer { lastSelectedTabIndex = tabBarController.selectedIndex }
        guard lastSelectedTabIndex == tabBarController.selectedIndex,
              let navigation = viewController as? UINavigationController else { return }
        switch catalog.tabContainer?.reselectBehavior {
        case "pop-to-root":
            navigation.popToRootViewController(animated: true)
        case "scroll-to-top":
            navigation.popToRootViewController(animated: true)
            DispatchQueue.main.async {
                guard let root = navigation.viewControllers.first,
                      let scroll = self.firstScrollView(in: root.view) else { return }
                scroll.setContentOffset(CGPoint(x: 0, y: -scroll.adjustedContentInset.top), animated: true)
            }
        default:
            break
        }
    }

    private func firstScrollView(in view: UIView) -> UIScrollView? {
        if let scroll = view as? UIScrollView { return scroll }
        for child in view.subviews {
            if let scroll = firstScrollView(in: child) { return scroll }
        }
        return nil
    }
}

final class HTMLToIOSGeneratedRootViewController: UIViewController {
    private var generatedCoordinator: HTMLToIOSGeneratedCoordinator?

    override func viewDidLoad() {
        super.viewDidLoad()
        let coordinator = HTMLToIOSGeneratedCoordinator(hostController: self)
        generatedCoordinator = coordinator
        coordinator.start()
    }
}
'''


SWIFTUI_APPLICATION = r'''// Generated by sky-html-to-ios. App-facing SwiftUI entry point.
import SwiftUI

struct HTMLToIOSGeneratedRootView: View {
    var body: some View {
        HTMLToIOSGeneratedNavigationContainer()
    }
}
'''


UIKIT_APPLICATION = r'''// Generated by sky-html-to-ios. App-facing UIKit entry point.
import UIKit

final class HTMLToIOSGeneratedRootViewController: UIViewController {
    private var generatedCoordinator: HTMLToIOSGeneratedCoordinator?

    override func viewDidLoad() {
        super.viewDidLoad()
        let coordinator = HTMLToIOSGeneratedCoordinator(hostController: self)
        generatedCoordinator = coordinator
        coordinator.start()
    }
}
'''


def navigation_source(ui_stack: str) -> str:
    if ui_stack == "swiftui":
        return SWIFTUI_ROOT.replace(
            "struct HTMLToIOSGeneratedRootView: View",
            "struct HTMLToIOSGeneratedNavigationContainer: View",
            1,
        )
    marker = "\nfinal class HTMLToIOSGeneratedRootViewController: UIViewController"
    return UIKIT_ROOT.split(marker, 1)[0].rstrip() + "\n"


def screen_sources(screen: dict[str, Any], ui_stack: str, name_prefix: str) -> dict[str, str]:
    module_type = str(screen["moduleType"])
    screen_type = str(screen["screenType"])
    if ui_stack == "swiftui":
        return {
            f"{module_type}/Screens/{name_prefix}{screen_type}Screen.swift": f'''// Generated by sky-html-to-ios. Native SwiftUI screen for {screen["id"]}.
import SwiftUI

struct {name_prefix}{screen_type}Screen: View {{
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let screen: HTMLToIOSScreenSpec

    var body: some View {{
        {name_prefix}{screen_type}ContentView(store: store, screen: screen)
    }}
}}
''',
            f"{module_type}/Views/{name_prefix}{screen_type}ContentView.swift": f'''// Generated by sky-html-to-ios. Module-owned SwiftUI content for {screen["id"]}.
import SwiftUI

struct {name_prefix}{screen_type}ContentView: View {{
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let screen: HTMLToIOSScreenSpec

    var body: some View {{
        HTMLToIOSGeneratedScreenView(store: store, screen: screen)
    }}
}}
''',
        }
    return {
        f"{module_type}/Controllers/{name_prefix}{screen_type}ViewController.swift": f'''// Generated by sky-html-to-ios. Native UIKit screen for {screen["id"]}.
import UIKit

final class {name_prefix}{screen_type}ViewController: HTMLToIOSGeneratedScreenViewController {{
    override func wrapGeneratedContent(_ content: UIView) -> UIView {{
        {name_prefix}{screen_type}ContentView(content: content)
    }}
}}
''',
        f"{module_type}/Views/{name_prefix}{screen_type}ContentView.swift": f'''// Generated by sky-html-to-ios. Module-owned UIKit content for {screen["id"]}.
import UIKit

final class {name_prefix}{screen_type}ContentView: UIView {{
    init(content: UIView) {{
        super.init(frame: .zero)
        backgroundColor = .clear
        content.translatesAutoresizingMaskIntoConstraints = false
        addSubview(content)
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: leadingAnchor),
            content.trailingAnchor.constraint(equalTo: trailingAnchor),
            content.topAnchor.constraint(equalTo: topAnchor),
            content.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
    }}

    @available(*, unavailable)
    required init?(coder: NSCoder) {{ fatalError("init(coder:) is unavailable") }}
}}
''',
    }


def screen_factory_source(screens: list[dict[str, Any]], ui_stack: str, name_prefix: str) -> str:
    if ui_stack == "swiftui":
        cases = "\n".join(
            f'''        case .{screen["swiftCase"]}:
            {name_prefix}{screen["screenType"]}Screen(store: store, screen: screen)'''
            for screen in screens
        )
        return f'''// Generated by sky-html-to-ios. Resolves routes to module-owned SwiftUI screens.
import SwiftUI

enum HTMLToIOSGeneratedScreenFactory {{
    @ViewBuilder
    static func view(
        route: HTMLToIOSGeneratedRoute,
        store: HTMLToIOSGeneratedStore,
        catalog: HTMLToIOSGeneratedCatalog
    ) -> some View {{
        if let screen = catalog.screen(route) {{
            switch route {{
{cases}
            }}
        }} else {{
            VStack(spacing: 8) {{
                Image(systemName: "exclamationmark.triangle")
                Text("Generated screen unavailable")
            }}
        }}
    }}
}}
'''
    cases = "\n".join(
        f'''        case .{screen["swiftCase"]}:
            return {name_prefix}{screen["screenType"]}ViewController(screen: screen, actionHandler: actionHandler)'''
        for screen in screens
    )
    return f'''// Generated by sky-html-to-ios. Resolves routes to module-owned UIKit controllers.
import UIKit

enum HTMLToIOSGeneratedScreenFactory {{
    static func make(
        route: HTMLToIOSGeneratedRoute,
        catalog: HTMLToIOSGeneratedCatalog,
        actionHandler: @escaping (HTMLToIOSActionSpec?) -> Void
    ) -> UIViewController? {{
        guard let screen = catalog.screen(route) else {{ return nil }}
        switch route {{
{cases}
        }}
    }}
}}
'''


def write_incremental(
    out_dir: Path,
    conflict_dir: Path,
    files: dict[str, str],
    metadata: dict[str, Any],
    overwrite_modified: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_NAME
    previous: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous_files = previous.get("files") or {}
    results: dict[str, Any] = {}
    conflicts = []

    for relative, content in sorted(files.items()):
        destination = out_dir / relative
        encoded = content.encode("utf-8")
        desired_hash = sha256_bytes(encoded)
        previous_entry = previous_files.get(relative) or {}
        previous_hash = previous_entry.get("sha256")
        status = "created"
        if destination.exists():
            current_hash = sha256_file(destination)
            owned_and_clean = bool(previous_entry.get("owned", True)) and previous_hash is not None and current_hash == previous_hash
            if current_hash == desired_hash:
                status = "unchanged"
            elif owned_and_clean or overwrite_modified:
                destination.write_bytes(encoded)
                status = "updated"
            else:
                candidate = conflict_dir / (relative + ".generated")
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(encoded)
                status = "preserved-user-modified"
                conflicts.append({"file": relative, "candidate": str(candidate)})
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(encoded)
        actual_hash = sha256_file(destination) if destination.exists() else desired_hash
        results[relative] = {
            "sha256": actual_hash,
            "desiredSha256": desired_hash,
            "status": status,
            "owned": status != "preserved-user-modified",
        }

    stale_files = []
    for relative, previous_entry in sorted(previous_files.items()):
        if relative in files:
            continue
        destination = out_dir / relative
        if not destination.exists():
            continue
        current_hash = sha256_file(destination)
        if previous_entry.get("owned", True) and current_hash == previous_entry.get("sha256"):
            destination.unlink()
            stale_files.append({"file": relative, "status": "removed-owned-stale"})
        else:
            stale_files.append({"file": relative, "status": "preserved-modified-stale"})
            conflicts.append({"file": relative, "candidate": None, "reason": "stale generated path contains user changes"})

    manifest = {
        "schemaVersion": "html-to-ios-generation-1.0",
        "generatorVersion": GENERATOR_VERSION,
        **metadata,
        "files": results,
        "staleFiles": stale_files,
        "conflicts": conflicts,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def data_uri_payload(value: Any) -> tuple[bytes, str] | None:
    text = str(value or "")
    match = re.match(r"^data:([^;,]+)?(?:;charset=[^;,]+)?(;base64)?,(.*)$", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    mime = (match.group(1) or "application/octet-stream").lower()
    raw = match.group(3)
    payload = base64.b64decode(raw) if match.group(2) else urllib.parse.unquote_to_bytes(raw)
    extension = {
        "image/svg+xml": ".svg",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }.get(mime, "")
    return payload, extension


def ios_asset_payload(asset: dict[str, Any]) -> tuple[bytes, str] | None:
    markup = asset.get("markup")
    if markup:
        normalized_markup = re.sub(
            r"url\(&quot;(#[-A-Za-z0-9_:.]+)&quot;\)",
            r"url(\1)",
            str(markup),
        )
        return normalized_markup.encode("utf-8"), ".svg"

    data_value = next(
        (value for value in (asset.get("localPath"), asset.get("url"), asset.get("source")) if str(value or "").startswith("data:")),
        None,
    )
    decoded = data_uri_payload(data_value) if data_value else None
    source = asset.get("localPath")
    if decoded:
        payload, extension = decoded
    elif source and Path(str(source)).is_file():
        source_path = Path(str(source))
        payload, extension = source_path.read_bytes(), source_path.suffix.lower()
    else:
        return None

    if extension in {".svg", ".pdf", ".png", ".jpg", ".jpeg"}:
        return payload, extension
    if extension in {".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"}:
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(payload))
            if getattr(image, "n_frames", 1) > 1:
                return None
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue(), ".png"
        except (ImportError, OSError, ValueError):
            return None
    return None


def write_asset_catalog(out_dir: Path, irs: list[dict[str, Any]]) -> dict[str, Any] | None:
    assets = [asset for ir in irs for asset in ir.get("assets") or [] if asset.get("iosName")]
    catalog = out_dir / "Resources" / "Assets" / "HTMLToIOSGeneratedAssets.xcassets"
    resolved_assets: list[tuple[str, dict[str, Any], bytes, str]] = []
    seen: set[str] = set()
    for asset in assets:
        name = safe_identifier(str(asset.get("iosName")))
        if name in seen:
            continue
        resolved = ios_asset_payload(asset)
        if not resolved:
            continue
        payload, extension = resolved
        seen.add(name)
        resolved_assets.append((name, asset, payload, extension))

    # This catalog is fully generator-owned. Rebuilding it prevents deleted or
    # renamed HTML assets from surviving as stale Xcode resources.
    if catalog.exists():
        shutil.rmtree(catalog)
    if not resolved_assets:
        return None
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "Contents.json").write_text(
        json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n",
        encoding="utf-8",
    )
    written = []
    for name, asset, payload, extension in resolved_assets:
        imageset = catalog / f"{name}.imageset"
        imageset.mkdir(parents=True, exist_ok=True)
        filename = f"{name}{extension}"
        (imageset / filename).write_bytes(payload)
        contents: dict[str, Any] = {
            "images": [{"filename": filename, "idiom": "universal", "scale": "1x"}],
            "info": {"author": "xcode", "version": 1},
        }
        if extension in {".svg", ".pdf"}:
            contents["properties"] = {"preserves-vector-representation": True}
        (imageset / "Contents.json").write_text(
            json.dumps(contents, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append({"assetName": name, "kind": asset.get("kind"), "source": asset.get("source")})
    return {"path": str(catalog.resolve()), "assets": written}


def main() -> int:
    args = parse_args()
    normalized_parts = args.out_dir.resolve().parts
    if normalized_parts[-2:] != ("Generated", "HTMLToIOS") and not args.allow_nonstandard_output:
        raise ValueError("--out-dir must end with Generated/HTMLToIOS; pass --allow-nonstandard-output only for a confirmed project-specific layout")
    irs = [load_ir(path) for path in args.ir]
    unresolved = [
        interaction.get("id")
        for ir in irs
        for interaction in ir.get("interactions") or []
        if interaction.get("requiresResolution")
    ]
    if unresolved and not args.allow_unresolved:
        raise ValueError("unresolved interactions: " + ", ".join(str(item) for item in unresolved))

    inferred_stacks = {str((ir.get("target") or {}).get("uiStack") or "swiftui").lower() for ir in irs}
    ui_stack = args.ui_stack or (next(iter(inferred_stacks)) if len(inferred_stacks) == 1 else None)
    if ui_stack not in {"swiftui", "uikit"}:
        raise ValueError("--ui-stack is required when UI IR files disagree")

    architecture_by_screen = load_architecture_plan(args.architecture_plan)
    name_prefix, naming_source, existing_type_names = load_naming_prefix(args.naming_plan)
    ir_screen_ids = [str((ir.get("screens") or [{}])[0].get("id") or "screen") for ir in irs]
    unknown_architecture_screens = sorted(set(architecture_by_screen) - set(ir_screen_ids))
    if unknown_architecture_screens:
        raise ValueError("architecture plan contains unknown screens: " + ", ".join(unknown_architecture_screens))
    screens = [build_screen(ir, architecture_by_screen.get(screen_id)) for ir, screen_id in zip(irs, ir_screen_ids)]
    ids = [screen["id"] for screen in screens]
    if len(ids) != len(set(ids)):
        raise ValueError("screen IDs must be unique")
    assign_screen_modules(screens)
    generated_page_types = {
        f"{name_prefix}{screen['screenType']}{suffix}"
        for screen in screens
        for suffix in (("Screen", "ContentView") if ui_stack == "swiftui" else ("ViewController", "ContentView"))
    }
    collisions = sorted(generated_page_types & existing_type_names)
    if collisions:
        raise ValueError("generated page types collide with existing target types: " + ", ".join(collisions))
    tab_candidates = [screen.get("tabContainer") for screen in screens if screen.get("tabContainer")]
    tab_container = None
    if tab_candidates:
        source_tab = tab_candidates[0]
        items = []
        for index, item in enumerate(source_tab.get("items") or []):
            target = str(item.get("targetScreenId") or "")
            if target not in ids:
                raise ValueError(f"tab target screen does not exist: {target!r}")
            items.append({
                "id": str(item.get("id") or target or f"tab-{index + 1}"),
                "title": compact_text(item.get("title") or target, 80),
                "targetScreenId": target,
                "icon": item.get("icon") or "circle",
                "selectedIcon": item.get("selectedIcon"),
                "badge": str(item.get("badge")) if item.get("badge") not in {None, ""} else None,
                "role": str(item.get("role") or "normal"),
            })
        if len(items) < 2:
            raise ValueError("a native tab container requires at least two valid tab items")
        initial_tab_id = str(source_tab.get("initialTabId") or items[0]["id"])
        if initial_tab_id not in {item["id"] for item in items}:
            raise ValueError(f"initial tab does not exist: {initial_tab_id!r}")
        tab_container = {
            "id": str(source_tab.get("id") or "main-tabs"),
            "initialTabId": initial_tab_id,
            "reselectBehavior": str(source_tab.get("reselectBehavior") or "keep"),
            "visibility": str(source_tab.get("visibility") or "automatic"),
            "items": items,
        }
    initial_route = screens[0]["id"]
    if tab_container:
        initial_item = next(item for item in tab_container["items"] if item["id"] == tab_container["initialTabId"])
        initial_route = initial_item["targetScreenId"]
    for screen in screens:
        screen.pop("tabContainer", None)
    payload = {"initialRoute": initial_route, "screens": screens, "tabContainer": tab_container}
    files = {
        "Application/HTMLToIOSGeneratedRoot.swift": SWIFTUI_APPLICATION if ui_stack == "swiftui" else UIKIT_APPLICATION,
        "Core/Models/HTMLToIOSGeneratedModels.swift": models_swift(screens),
        "Core/Data/HTMLToIOSGeneratedData.swift": data_swift(payload),
        "Core/Navigation/HTMLToIOSGeneratedNavigation.swift": navigation_source(ui_stack),
        "Core/Navigation/HTMLToIOSGeneratedScreenFactory.swift": screen_factory_source(screens, ui_stack, name_prefix),
        "Core/Runtime/HTMLToIOSGeneratedRuntime.swift": SWIFTUI_RUNTIME if ui_stack == "swiftui" else UIKIT_RUNTIME,
        "Resources/Payload/HTMLToIOSGeneratedPayload.json": json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
    }
    for screen in screens:
        files.update(screen_sources(screen, ui_stack, name_prefix))
    conflict_dir = args.conflict_dir or args.out_dir.with_name(args.out_dir.name + ".conflicts")
    metadata = {
        "uiStack": ui_stack,
        "moduleName": args.module_name,
        "entrySymbol": "HTMLToIOSGeneratedRootView" if ui_stack == "swiftui" else "HTMLToIOSGeneratedRootViewController",
        "screenIDs": ids,
        "screenModules": {screen["id"]: screen["moduleId"] for screen in screens},
        "inputs": [{"path": str(path.resolve()), "sha256": sha256_file(path)} for path in args.ir],
        "architecturePlan": str(args.architecture_plan.resolve()) if args.architecture_plan else None,
        "namingPlan": str(args.naming_plan.resolve()) if args.naming_plan else None,
        "namePrefix": name_prefix,
        "namingSource": naming_source,
    }
    manifest = write_incremental(args.out_dir, conflict_dir, files, metadata, args.overwrite_modified)
    asset_catalog = write_asset_catalog(args.out_dir, irs)
    asset_migration = None
    legacy_catalog = args.out_dir / "HTMLToIOSGeneratedAssets.xcassets"
    if legacy_catalog.is_dir():
        current_catalog = Path(asset_catalog["path"]) if asset_catalog else None
        if current_catalog and directory_sha256(legacy_catalog) == directory_sha256(current_catalog):
            shutil.rmtree(legacy_catalog)
            asset_migration = {"status": "removed-identical-legacy-catalog", "path": str(legacy_catalog)}
        else:
            preserved = conflict_dir / "Legacy" / legacy_catalog.name
            if preserved.exists():
                shutil.rmtree(preserved)
            preserved.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_catalog), str(preserved))
            asset_migration = {"status": "preserved-legacy-catalog-in-conflicts", "path": str(preserved)}
    print(json.dumps({
        "outDir": str(args.out_dir.resolve()),
        "manifest": str((args.out_dir / MANIFEST_NAME).resolve()),
        "uiStack": ui_stack,
        "entrySymbol": metadata["entrySymbol"],
        "screens": ids,
        "screenModules": metadata["screenModules"],
        "namePrefix": name_prefix,
        "fileStatuses": {name: item["status"] for name, item in manifest["files"].items()},
        "conflicts": manifest["conflicts"],
        "assetCatalog": asset_catalog,
        "assetMigration": asset_migration,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
