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


GENERATOR_VERSION = "1.39.0"
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
    parser.add_argument("--layout-relation-graph", type=Path)
    parser.add_argument("--native-layout-plan", type=Path)
    parser.add_argument("--native-structure-manifest", type=Path)
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


def first_non_none(*values):
    return next((value for value in values if value is not None), None)


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
    schema_version = data.get("schemaVersion")
    if schema_version not in {"native-architecture-plan-1.0", "native-architecture-plan-1.1"}:
        raise ValueError(f"{path}: expected native-architecture-plan-1.0 or native-architecture-plan-1.1")
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
        if schema_version == "native-architecture-plan-1.1":
            layers = screen.get("layers") or {}
            required = {
                "applicationContainer", "screenContainer", "screenRegions",
                "contentContainer", "reusableContent", "leafComponents",
            }
            missing = sorted(required - set(layers))
            if missing:
                raise ValueError(f"{path}: {screen_id} is missing architecture layers: {', '.join(missing)}")
    return result


def load_layout_relation_graph(path: Path | None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if path is None:
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "layout-relation-graph-1.0":
        raise ValueError(f"{path}: expected layout-relation-graph-1.0")
    screens = {
        str(screen.get("screenId") or ""): screen
        for screen in data.get("screens") or []
        if isinstance(screen, dict)
    }
    if not screens or "" in screens:
        raise ValueError(f"{path}: every layout graph screen needs a screenId")
    return data, screens


def load_native_layout_plan(path: Path | None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if path is None:
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "native-layout-plan-1.1":
        raise ValueError(f"{path}: expected native-layout-plan-1.1")
    screens = {
        str(screen.get("screenId") or ""): screen
        for screen in data.get("screens") or []
        if isinstance(screen, dict)
    }
    if not screens or "" in screens:
        raise ValueError(f"{path}: every native layout plan screen needs a screenId")
    return data, screens


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


def font_design(value: Any) -> str:
    family = str(value or "").lower()
    if "monospace" in family:
        return "monospaced"
    if "serif" in family and "sans-serif" not in family:
        return "serif"
    if "rounded" in family:
        return "rounded"
    return "default"


def ios_native_font_name(family: Any, weight: Any, style: Any) -> str | None:
    normalized = re.sub(r"\s+", " ", str(family or "").strip().strip("'\"").lower())
    numeric_weight = int(number(weight, 400))
    italic = str(style or "normal").lower() in {"italic", "oblique"}
    bold = numeric_weight >= 600
    names = {
        "arial": ("ArialMT", "Arial-BoldMT", "Arial-ItalicMT", "Arial-BoldItalicMT"),
        "helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
        "helvetica neue": ("HelveticaNeue", "HelveticaNeue-Bold", "HelveticaNeue-Italic", "HelveticaNeue-BoldItalic"),
        "courier new": ("CourierNewPSMT", "CourierNewPS-BoldMT", "CourierNewPS-ItalicMT", "CourierNewPS-BoldItalicMT"),
        "times new roman": ("TimesNewRomanPSMT", "TimesNewRomanPS-BoldMT", "TimesNewRomanPS-ItalicMT", "TimesNewRomanPS-BoldItalicMT"),
        "georgia": ("Georgia", "Georgia-Bold", "Georgia-Italic", "Georgia-BoldItalic"),
        "menlo": ("Menlo-Regular", "Menlo-Bold", "Menlo-Italic", "Menlo-BoldItalic"),
    }
    variants = names.get(normalized)
    if not variants:
        return None
    return variants[3 if bold and italic else 1 if bold else 2 if italic else 0]


def font_contract(node: dict[str, Any], style: dict[str, Any]) -> dict[str, Any]:
    resolution = (node.get("content") or {}).get("fontResolution") or {}
    resolved_family = str(resolution.get("resolvedFamily") or "").strip()
    resolution_status = str(resolution.get("status") or "legacy-unresolved")
    family_for_design = resolved_family or style.get("fontFamily")
    native_name = (
        ios_native_font_name(resolved_family, style.get("fontWeight"), style.get("fontStyle"))
        if resolution_status == "system-local"
        else None
    )
    return {
        "family": str(style.get("fontFamily") or ""),
        "resolvedFamily": resolved_family or None,
        "resolutionStatus": resolution_status,
        "failedFamilies": resolution.get("failedFamilies") or [],
        "design": font_design(family_for_design),
        "nativeName": native_name,
        "style": str(style.get("fontStyle") or "normal"),
    }


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
    contextual_actions: dict[str, list[dict[str, Any]]]
    detached_root_ids: set[str]
    bottom_bar_placement: str
    native_container_kinds: dict[str, str]
    layout_containers: dict[str, dict[str, Any]]
    layout_nodes: dict[str, dict[str, Any]]
    layout_sizing: dict[str, dict[str, Any]]
    compound_controls: dict[str, dict[str, Any]]


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
        font = font_contract(run_node, style)
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
            "fontFamily": font["family"],
            "fontResolvedFamily": font["resolvedFamily"],
            "fontResolutionStatus": font["resolutionStatus"],
            "fontFailedFamilies": font["failedFamilies"],
            "fontDesign": font["design"],
            "fontNativeName": font["nativeName"],
            "fontStyle": font["style"],
            "foreground": foreground,
            "background": background,
            "lineHeight": scaled_css_value(style.get("lineHeight"), context.design_scale) or None,
            "letterSpacing": scaled_css_value(style.get("letterSpacing"), context.design_scale),
        })
    return result


def rich_text_runs_with_browser_line_breaks(
    runs: list[dict[str, Any]],
    line_texts: list[Any],
) -> list[dict[str, Any]]:
    normalized_lines = [
        re.sub(r"\s+", " ", str(value or "")).strip()
        for value in line_texts
        if str(value or "").strip()
    ]
    if len(runs) < 1 or len(normalized_lines) < 2:
        return runs

    styled_characters: list[tuple[str, int]] = []
    for run_index, run in enumerate(runs):
        for character in re.sub(r"\s+", " ", str(run.get("text") or "")):
            if character == " " and (not styled_characters or styled_characters[-1][0] == " "):
                continue
            styled_characters.append((character, run_index))
    while styled_characters and styled_characters[0][0] == " ":
        styled_characters.pop(0)
    while styled_characters and styled_characters[-1][0] == " ":
        styled_characters.pop()
    full_text = "".join(character for character, _ in styled_characters)
    if not full_text:
        return runs

    boundaries: set[int] = set()
    skipped_spaces: set[int] = set()
    cursor = 0
    for line_index, line in enumerate(normalized_lines):
        index = full_text.find(line, cursor)
        if index < 0 or full_text[cursor:index].strip():
            return runs
        end = index + len(line)
        cursor = end
        if line_index < len(normalized_lines) - 1:
            boundaries.add(end)
            while cursor < len(full_text) and full_text[cursor] == " ":
                skipped_spaces.add(cursor)
                cursor += 1
    if full_text[cursor:].strip() or len(boundaries) != len(normalized_lines) - 1:
        return runs

    result: list[dict[str, Any]] = []
    for character_index, (character, run_index) in enumerate(styled_characters):
        if character_index in boundaries and result:
            result[-1]["text"] += "\n"
        if character_index in skipped_spaces:
            continue
        source = runs[run_index]
        if result and result[-1].get("_sourceRunIndex") == run_index:
            result[-1]["text"] += character
        else:
            result.append({**source, "text": character, "_sourceRunIndex": run_index})
    return [
        {key: value for key, value in run.items() if key != "_sourceRunIndex"}
        for run in result
    ]


def plain_text_with_browser_line_breaks(text: str, line_texts: list[Any]) -> str:
    normalized_lines = [
        re.sub(r"\s+", " ", str(value or "")).strip()
        for value in line_texts
        if str(value or "").strip()
    ]
    if len(normalized_lines) < 2:
        return text
    source_key = re.sub(r"\s+", "", text)
    lines_key = re.sub(r"\s+", "", "".join(normalized_lines))
    return "\n".join(normalized_lines) if source_key and source_key == lines_key else text


def child_rect(context: ScreenBuildContext, child_id: str) -> dict[str, Any]:
    layout = (context.nodes.get(child_id) or {}).get("layout") or {}
    return layout.get("sourceRectCssPx") or layout.get("rect") or {}


def visual_text_line_count(content: dict[str, Any]) -> int:
    reported_count = int(number(content.get("lines"), 0))
    if len(content.get("runs") or []) <= 1:
        return reported_count

    def measured_rect(item: dict[str, Any]) -> dict[str, Any] | None:
        nested = item.get("sourceRectCssPx") or item.get("rect")
        if nested:
            return nested
        if any(key in item for key in ("x", "y", "width", "height")):
            return item
        return None

    rects = [
        rect
        for rect in (
            measured_rect(item)
            for item in (content.get("lineRects") or [])
        )
        if rect and number(rect.get("height")) > 0
    ]
    if not rects:
        rects = [
            rect
            for rect in (
                measured_rect(item)
                for item in (content.get("runs") or [])
            )
            if rect and number(rect.get("height")) > 0
        ]
    if not rects:
        return reported_count

    lines: list[dict[str, float]] = []
    for rect in sorted(rects, key=lambda item: (number(item.get("y")), number(item.get("x")))):
        top = number(rect.get("y"))
        height = number(rect.get("height"))
        bottom = top + height
        center = top + height / 2
        matched_line: dict[str, float] | None = None
        for line in lines:
            overlap = max(min(bottom, line["bottom"]) - max(top, line["top"]), 0)
            overlap_ratio = overlap / max(min(height, line["maxHeight"]), 1)
            center_tolerance = max(min(height, line["maxHeight"]) * 0.35, 2)
            if overlap_ratio >= 0.45 or abs(center - line["center"]) <= center_tolerance:
                matched_line = line
                break
        if matched_line is None:
            lines.append({
                "top": top,
                "bottom": bottom,
                "center": center,
                "maxHeight": height,
                "count": 1,
            })
        else:
            matched_line["top"] = min(matched_line["top"], top)
            matched_line["bottom"] = max(matched_line["bottom"], bottom)
            matched_line["maxHeight"] = max(matched_line["maxHeight"], height)
            matched_line["count"] += 1
            matched_line["center"] += (center - matched_line["center"]) / matched_line["count"]
    return len(lines) or reported_count


def sort_children_by_visual_geometry(
    context: ScreenBuildContext,
    payloads: list[dict[str, Any]],
    axis: str,
    flex_wrap: str,
    flex_direction: str,
) -> list[dict[str, Any]]:
    if len(payloads) < 2:
        return payloads
    indexed = list(enumerate(payloads))

    def key(entry: tuple[int, dict[str, Any]]) -> tuple[float, float, int]:
        index, payload = entry
        rect = child_rect(context, str(payload.get("id") or ""))
        x = number(rect.get("x"))
        y = number(rect.get("y"))
        if axis == "horizontal" and flex_wrap == "nowrap":
            return (x, y, index)
        return (y, x, index)

    sorted_payloads = [payload for _, payload in sorted(indexed, key=key)]
    main_positions = [
        number(child_rect(context, str(payload.get("id") or "")).get("x" if axis == "horizontal" else "y"))
        for payload in payloads
    ]
    if flex_direction.endswith("reverse") and len(set(main_positions)) <= 1:
        return list(reversed(sorted_payloads))
    return sorted_payloads


def plain_inline_text_child(payload: dict[str, Any]) -> bool:
    style = payload.get("style") or {}
    return bool(
        payload.get("semantic") in {"text", "label"}
        and payload.get("text")
        and not payload.get("assetName")
        and not style.get("gradientColors")
        and number(style.get("cornerRadius")) == 0
        and number(style.get("borderWidth")) == 0
        and not style.get("shadowColor")
        and all(abs(number(value)) < 0.01 for value in (style.get("padding") or []))
        and all(abs(number(value)) < 0.01 for value in (style.get("margin") or []))
    )


def ordered_content_items(
    context: ScreenBuildContext,
    node: dict[str, Any],
    child_payloads: list[dict[str, Any]],
    axis: str,
    text: str,
) -> list[dict[str, Any]]:
    content = node.get("content") or {}
    payload_by_id = {str(item.get("id") or ""): item for item in child_payloads}
    items: list[dict[str, Any]] = []
    used_children: set[str] = set()
    text_index = 0

    for run_index, run in enumerate(content.get("runs") or []):
        kind = str(run.get("kind") or "")
        child_id = str(run.get("nodeId") or "")
        rect = run.get("sourceRectCssPx") or run.get("rect")
        dom_index = int(number(run.get("domIndex"), run_index))
        if kind == "node" and child_id in payload_by_id:
            if child_id not in used_children:
                items.append({
                    "id": child_id,
                    "kind": "child",
                    "childID": child_id,
                    "_rect": rect or child_rect(context, child_id),
                    "_domIndex": dom_index,
                })
                used_children.add(child_id)
        elif kind == "text":
            value = re.sub(r"\s+", " ", str(run.get("text") or "")).strip()
            if value:
                fallback_line_rects = content.get("lineRects") or []
                used_fallback_rect = not rect and bool(fallback_line_rects)
                items.append({
                    "id": f"{node.get('id') or 'node'}.__text.{text_index}",
                    "kind": "text",
                    "text": value,
                    "childID": None,
                    "_rect": rect or (
                        fallback_line_rects[min(text_index, len(fallback_line_rects) - 1)]
                        if fallback_line_rects
                        else None
                    ),
                    "_domIndex": dom_index,
                    "_fallbackLineRect": used_fallback_rect,
                })
                text_index += 1

    for child_index, payload in enumerate(child_payloads):
        child_id = str(payload.get("id") or "")
        if child_id and child_id not in used_children:
            items.append({
                "id": child_id,
                "kind": "child",
                "childID": child_id,
                "_rect": child_rect(context, child_id),
                "_domIndex": len(items) + child_index,
            })

    if text and not any(item["kind"] == "text" for item in items):
        line_rects = content.get("lineRects") or []
        items.append({
            "id": f"{node.get('id') or 'node'}.__text.0",
            "kind": "text",
            "text": text,
            "childID": None,
            "_rect": line_rects[0] if line_rects else None,
            "_domIndex": len(items),
        })

    if len(items) > 1 and axis in {"horizontal", "vertical", "grid"} and all(item.get("_rect") for item in items):
        if axis == "horizontal":
            for text_item in (item for item in items if item["kind"] == "text" and item.get("_fallbackLineRect")):
                text_rect = text_item.get("_rect") or {}
                for child_item in (item for item in items if item["kind"] == "child"):
                    child_item_rect = child_item.get("_rect") or {}
                    same_leading_edge = abs(number(text_rect.get("x")) - number(child_item_rect.get("x"))) <= 1
                    contains_child = (
                        number(text_rect.get("width")) > number(child_item_rect.get("width")) + 1
                        and number(text_rect.get("height")) >= number(child_item_rect.get("height")) - 1
                    )
                    if same_leading_edge and contains_child:
                        next_x = (
                            number(child_item_rect.get("x"))
                            + number(child_item_rect.get("width"))
                            + scaled_css_value((node.get("style") or {}).get("gap"), context.design_scale)
                        )
                        consumed_width = max(next_x - number(text_rect.get("x")), 0)
                        text_item["_rect"] = {
                            **text_rect,
                            "x": next_x,
                            "width": max(number(text_rect.get("width")) - consumed_width, 0),
                        }
                        break

        def visual_key(item: dict[str, Any]) -> tuple[float, float, int]:
            rect = item.get("_rect") or {}
            x = number(rect.get("x"))
            y = number(rect.get("y"))
            if axis == "horizontal":
                return (x, y, int(item.get("_domIndex") or 0))
            return (y, x, int(item.get("_domIndex") or 0))

        items.sort(key=visual_key)
    else:
        items.sort(key=lambda item: int(item.get("_domIndex") or 0))

    style = node.get("style") or {}
    node_layout = node.get("layout") or {}
    node_rect = node_layout.get("sourceRectCssPx") or node_layout.get("rect") or {}
    default_gap = scaled_css_value(style.get("gap"), context.design_scale)
    source_font_size = number(style.get("fontSize"), 16)
    source_line_height = number(style.get("lineHeight")) or source_font_size * 1.2
    measured_spacing_axis = axis == "horizontal" or (
        axis == "vertical"
        and len(items) > 1
        and all(item.get("kind") == "text" for item in items)
    )
    if measured_spacing_axis:
        main_origin = "x" if axis == "horizontal" else "y"
        main_extent = "width" if axis == "horizontal" else "height"
        item_span = 0.0
        if items and all(item.get("_rect") for item in items):
            first_rect = items[0]["_rect"]
            last_rect = items[-1]["_rect"]
            item_span = (
                number(last_rect.get(main_origin))
                + number(last_rect.get(main_extent))
                - number(first_rect.get(main_origin))
            )
        parent_extent = number(node_rect.get(main_extent))
        for index in range(1, len(items)):
            previous_rect = items[index - 1].get("_rect") or {}
            current_rect = items[index].get("_rect") or {}
            source_measured_gap = max(
                number(current_rect.get(main_origin))
                - number(previous_rect.get(main_origin))
                - number(previous_rect.get(main_extent)),
                0,
            )
            measured_gap = source_measured_gap * context.design_scale
            current_child = payload_by_id.get(str(items[index].get("childID") or "")) or {}
            child_margin = (current_child.get("style") or {}).get("margin") or []
            child_leading_margin = (
                number(child_margin[3])
                if axis == "horizontal" and len(child_margin) == 4
                else number(child_margin[0]) if axis == "vertical" and len(child_margin) == 4 else 0
            )
            justify_content = str(style.get("justifyContent") or "").lower()
            auto_margin_gap = bool(
                axis == "horizontal"
                and justify_content != "space-between"
                and measured_gap >= max(default_gap * 3, 24)
                and child_leading_margin >= measured_gap * 0.7
                and parent_extent > 0
                and item_span >= parent_extent * 0.75
            )
            if auto_margin_gap:
                items[index]["_gapBefore"] = default_gap
                items[index]["_flexibleGapBefore"] = True
            elif justify_content != "space-between" and measured_gap <= max(default_gap * 2 + 2, 24):
                items[index]["_gapBefore"] = measured_gap
                items[index]["_flexibleGapBefore"] = False

    return [
        {
            "id": item["id"],
            "kind": item["kind"],
            "text": item.get("text"),
            "childID": item.get("childID"),
            "preferredWidth": (
                number((item.get("_rect") or {}).get("width")) * context.design_scale
                if item.get("kind") == "text" and item.get("_rect")
                else None
            ),
            "preferredHeight": (
                number((item.get("_rect") or {}).get("height")) * context.design_scale
                if item.get("kind") == "text" and item.get("_rect")
                else None
            ),
            "singleLine": bool(
                item.get("kind") == "text"
                and item.get("_rect")
                and number((item.get("_rect") or {}).get("height"))
                <= max(source_line_height * 1.35, source_font_size * 1.8)
            ),
            "gapBefore": item.get("_gapBefore"),
            "flexibleGapBefore": bool(item.get("_flexibleGapBefore")),
        }
        for item in items
    ]


def control_visual_state_payload(raw: Any, scale: float) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    gradient = gradient_spec(raw.get("backgroundImage"))
    border_widths = [
        scaled_css_value(raw.get(key), scale)
        for key in ("borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth")
    ]
    border_colors = [
        color_string(raw.get(key))
        for key in ("borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor")
    ]
    border_index = max(range(4), key=lambda index: border_widths[index])
    transform = str(raw.get("transform") or "none")
    state_scale = 1.0
    matrix = re.search(r"matrix\(\s*([-+.\deE]+)\s*,\s*([-+.\deE]+)", transform)
    explicit_scale = re.search(r"scale\(\s*([-+.\deE]+)", transform)
    if matrix:
        state_scale = max((float(matrix.group(1)) ** 2 + float(matrix.group(2)) ** 2) ** 0.5, 0)
    elif explicit_scale:
        state_scale = max(float(explicit_scale.group(1)), 0)
    shadow = shadow_spec(raw.get("boxShadow"), scale)
    return {
        "foreground": color_string(raw.get("color")),
        "background": color_string(raw.get("backgroundColor")),
        "gradientColors": gradient["colors"],
        "borderWidth": max(border_widths),
        "borderColor": border_colors[border_index],
        "cornerRadius": scaled_css_value(raw.get("borderRadius"), scale) or None,
        "opacity": min(max(number(raw.get("opacity"), 1), 0), 1),
        "scale": state_scale,
        "shadowColor": shadow["color"],
        "shadowOffsetX": shadow["x"],
        "shadowOffsetY": shadow["y"],
        "shadowRadius": shadow["radius"],
    }


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
        not presentation
        and parent_state_id
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
    if semantic in {"button", "file-input"} and not text:
        text = compact_text(content.get("value"), 240)
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
    layout_container = context.layout_containers.get(node_id) or {}
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
    if layout_container.get("axis") in {"horizontal", "vertical", "grid", "overlay"}:
        axis = str(layout_container["axis"])
    if (
        axis == "vertical"
        and semantic in {"text", "label", "heading"}
        and len(content.get("runs") or []) > 1
        and visual_text_line_count(content) == 1
    ):
        axis = "horizontal"
    child_payloads = sort_children_by_visual_geometry(
        context,
        child_payloads,
        axis,
        str(style.get("flexWrap") or "nowrap").lower(),
        flex_direction,
    )
    planned_child_order = [str(item) for item in layout_container.get("orderedChildNodeIds") or []]
    if planned_child_order:
        planned_index = {child_id: index for index, child_id in enumerate(planned_child_order)}
        child_payloads.sort(
            key=lambda child: planned_index.get(str(child.get("id") or ""), len(planned_index))
        )
    inline_runs = rich_text_runs(context, node, allow_block_children=True)
    inline_text_container = bool(
        semantic == "container"
        and axis == "horizontal"
        and inline_runs
        and child_payloads
        and str(style.get("justifyContent") or "normal") not in {"space-between", "space-around", "space-evenly"}
        and all(child.get("action") is None and plain_inline_text_child(child) for child in child_payloads)
    )
    if inline_text_container:
        semantic = "text"
        text = "".join(str(run.get("text") or "") for run in inline_runs)
        child_payloads = []
        content_items = []
    else:
        content_items = ordered_content_items(context, node, child_payloads, axis, text)
        compound_layout = context.compound_controls.get(node_id) or {}
        planned_slot_ids = [str(item) for item in compound_layout.get("orderedSlotIds") or []]
        if planned_slot_ids:
            planned_slot_index = {slot_id: index for index, slot_id in enumerate(planned_slot_ids)}
            content_items.sort(
                key=lambda item: planned_slot_index.get(str(item.get("id") or ""), len(planned_slot_index))
            )
        child_by_id = {str(child.get("id") or ""): child for child in child_payloads}
        for item in content_items:
            child = child_by_id.get(str(item.get("childID") or ""))
            child_margin = ((child or {}).get("style") or {}).get("margin")
            if not child or not isinstance(child_margin, list) or len(child_margin) != 4:
                continue
            if axis == "horizontal" and item.get("gapBefore") is not None:
                child_margin[3] = 0
            elif axis == "vertical" and item.get("gapBefore") is not None:
                child_margin[0] = 0
    layout_node = context.layout_nodes.get(node_id) or {}
    box_model = layout_node.get("boxModel") or {}
    positioning = layout_node.get("positioning") or {}
    grid_plan = layout_container.get("grid") or {}
    grid_column_widths = []
    for track in grid_plan.get("columnTracks") or []:
        length = track.get("length") if isinstance(track, dict) and track.get("kind") == "length" else None
        fixed_value = length.get("fixedValuePt") if isinstance(length, dict) else None
        grid_column_widths.append(number(fixed_value) * context.design_scale if fixed_value is not None else None)
    padding = [number(item) * context.design_scale for item in box_model.get("paddingPt") or []]
    margin = [number(item) * context.design_scale for item in box_model.get("marginPt") or []]
    border_widths = [number(item) * context.design_scale for item in box_model.get("borderWidthsPt") or []]
    if len(padding) != 4:
        padding = scaled_edges(style.get("padding"), context.design_scale)
    if len(margin) != 4:
        margin = scaled_edges(style.get("margin"), context.design_scale)
    if len(border_widths) != 4:
        border_widths = scaled_edges(style.get("borderWidths"), context.design_scale)
    border_index = max(range(4), key=lambda index: border_widths[index])
    border_colors = style.get("borderColors") or []
    border_styles = style.get("borderStyles") or []
    border_color = color_string(border_colors[border_index]) if border_index < len(border_colors) else None
    border_style = str(border_styles[border_index] or "solid") if border_index < len(border_styles) else "solid"
    gradient = gradient_spec(style.get("backgroundImage"))
    shadow = shadow_spec(style.get("boxShadow"), context.design_scale)
    font = font_contract(node, style)
    if context.bottom_bar_placement == "safe-area-inset" and semantic == "scroll":
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
    measured_height = max(number(rect.get("height")), 0.0)
    control_min_height = min(measured_height, 160.0) if semantic in {
        "button", "input", "text-field", "secure-field", "toggle", "switch", "progress", "progress-view"
    } else 0
    bordered_flow_min_height = measured_height if (
        max(border_widths) > 0
        and measured_height <= max(context.root_width * 3, 1200)
        and semantic not in {"text", "label", "heading", "icon", "image", "scroll"}
        and parent_id is not None
        and str(layout.get("position") or "static") not in {"absolute", "fixed"}
    ) else 0
    min_height = max(control_min_height, bordered_flow_min_height)
    if box_model.get("minHeightPt") is not None:
        min_height = max(min_height, number(box_model.get("minHeightPt")) * context.design_scale)
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
    line_count = visual_text_line_count(content)
    if semantic in {"text", "label", "heading"} and line_count > 1 and measured_height > 0:
        min_height = max(min_height, measured_height)
    node_rich_text_runs = inline_runs if inline_text_container else rich_text_runs(context, node)
    browser_line_texts = content.get("lineTexts") or []
    browser_broken_text = plain_text_with_browser_line_breaks(
        text,
        browser_line_texts if len(browser_line_texts) == line_count else [],
    )
    if browser_broken_text != text:
        text = browser_broken_text
        textual_items = [item for item in content_items if item.get("kind") == "text"]
        if len(textual_items) == 1:
            textual_items[0]["text"] = browser_broken_text
    node_rich_text_runs = rich_text_runs_with_browser_line_breaks(
        node_rich_text_runs,
        browser_line_texts
        if len(browser_line_texts) == visual_text_line_count(content)
        else [],
    )
    explicit_line_clamp = int(number(style.get("webkitLineClamp"), 0))
    explicit_no_wrap = str(style.get("whiteSpace") or "").lower() == "nowrap"
    rich_text_visual_single_line = bool(
        node_rich_text_runs
        and line_count == 1
        and not content.get("clippedHorizontally")
    )
    inferred_compact_single_line = bool(
        parent_horizontal
        and line_count == 1
        and semantic in {"text", "label", "heading", "button", "link", "menu-item", "tab-item"}
        and not content.get("clippedHorizontally")
    )
    text_line_limit = explicit_line_clamp if explicit_line_clamp > 0 else (
        1 if explicit_no_wrap or inferred_compact_single_line or rich_text_visual_single_line else None
    )
    child_by_id_for_baseline = {str(child.get("id") or ""): child for child in child_payloads}

    def is_textual_content_item(item: dict[str, Any]) -> bool:
        if item.get("kind") == "text":
            return bool(compact_text(item.get("text")))
        child = child_by_id_for_baseline.get(str(item.get("childID") or "")) or {}
        return bool(
            str(child.get("semantic") or "") in {"text", "label", "heading", "link"}
            and compact_text(child.get("text"))
        )

    baseline_aligned = bool(
        axis == "horizontal"
        and line_count == 1
        and len(content_items) > 1
        and all(is_textual_content_item(item) for item in content_items)
    )
    source_rect = layout.get("sourceRectCssPx") or rect
    first_baseline_y = number(content.get("firstBaselineY"), None)
    last_baseline_y = number(content.get("lastBaselineY"), None)
    source_top = number(source_rect.get("y"), 0)
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
        and semantic not in {"image", "icon"}
        and has_visual_style
        and not child_payloads
        and not text
        and not action
    )
    compact_styled_inline_geometry = bool(
        width > 0
        and height > 0
        and width <= 120
        and height <= 56
        and width_fraction < 0.35
        and semantic in {"text", "label", "container", "menu-item", "tab-item"}
        and has_visual_style
        and line_count <= 1
        and (text or child_payloads)
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
        or compact_styled_inline_geometry
        or compact_overlay_geometry
    )
    layout_sizing = context.layout_sizing.get(node_id) or {}
    layout_width_policy = str(layout_sizing.get("widthPolicy") or "")
    layout_height_policy = str(layout_sizing.get("heightPolicy") or "")
    fixed_width = width if (
        compact_visual_container
        or measured_visual_leaf
        or compact_styled_inline_geometry
        or compact_overlay_geometry
        or (horizontal_scroll_item and semantic not in {"image", "icon"})
    ) else None
    fixed_height = height if (
        compact_visual_container
        or measured_visual_leaf
        or compact_styled_inline_geometry
        or compact_overlay_geometry
        or (horizontal_scroll_item and semantic not in {"image", "icon"})
        or (semantic == "carousel" and scroll_axis == "horizontal")
    ) else None
    if layout_width_policy == "fixed" and number(layout_sizing.get("measuredWidth")) > 0:
        fixed_width = number(layout_sizing.get("measuredWidth"))
    if layout_height_policy == "fixed" and number(layout_sizing.get("measuredHeight")) > 0:
        fixed_height = number(layout_sizing.get("measuredHeight"))
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
    node_state = node.get("state") or {}
    source_text_behavior = node.get("textBehavior")
    text_behavior = dict(source_text_behavior) if isinstance(source_text_behavior, dict) else None
    if text_behavior is not None:
        text_behavior.update({
            "initialValue": str(first_non_none(content.get("value"), node_state.get("value"), text) or ""),
            "enabled": bool(first_non_none(node_state.get("enabled"), True)),
            "keyboardType": node_state.get("keyboardType"),
            "contentType": node_state.get("contentType"),
            "submitLabel": node_state.get("submitLabel"),
        })
        source_placeholder_style = text_behavior.get("placeholderStyle")
        if isinstance(source_placeholder_style, dict):
            text_behavior["placeholderStyle"] = {
                "fontSize": scaled_css_value(source_placeholder_style.get("fontSize"), context.design_scale) or None,
                "fontWeight": str(source_placeholder_style.get("fontWeight") or style.get("fontWeight") or "400"),
                "foreground": color_string(source_placeholder_style.get("foreground")),
                "lineHeight": scaled_css_value(source_placeholder_style.get("lineHeight"), context.design_scale) or None,
                "letterSpacing": scaled_css_value(source_placeholder_style.get("letterSpacing"), context.design_scale),
                "opacity": min(max(number(source_placeholder_style.get("opacity"), 1), 0), 1),
            }
    control_options = []
    for child in child_payloads:
        title = compact_text(child.get("text"))
        if not title:
            title = next((
                compact_text(grandchild.get("text"))
                for grandchild in child.get("children") or []
                if compact_text(grandchild.get("text"))
            ), "")
        if title:
            control_options.append({
                "id": str(child.get("id") or f"{node_id}.option-{len(control_options) + 1}"),
                "title": title,
                "selected": bool((context.nodes.get(str(child.get("id") or "")) or {}).get("state", {}).get("selected")),
            })
    control_config = None
    if semantic in {
        "slider", "stepper", "select", "multi-select", "segmented-control",
        "date-input", "radio", "checkbox", "switch", "color-picker", "file-input",
        "progress", "progress-view", "meter",
    }:
        control_config = {
            "minimum": number(node_state.get("min"), 0),
            "maximum": number(node_state.get("max"), 100),
            "step": max(number(node_state.get("step"), 1), 0.0001),
            "value": str(first_non_none(node_state.get("value"), content.get("value"), "") or ""),
            "inputType": str(node_state.get("inputType") or ""),
            "options": control_options,
            "allowsMultipleSelection": semantic == "multi-select",
        }
    layout_spacing = (
        number(layout_container.get("gapPt")) * context.design_scale
        if layout_container.get("gapPt") is not None
        else scaled_css_value(style.get("gap"), context.design_scale)
    )
    payload = {
        "id": node_id,
        "semantic": semantic,
        "nativeContainerKind": context.native_container_kinds.get(node_id),
        "text": text,
        "placeholder": placeholder,
        "textBehavior": text_behavior,
        "dataBinding": node.get("dataBinding") if isinstance(node.get("dataBinding"), dict) else None,
        "isEnabled": bool((node.get("state") or {}).get("enabled", True)),
        "controlVisualStates": {
            key: value
            for key, value in (
                (
                    state_name,
                    control_visual_state_payload(state_style, context.design_scale),
                )
                for state_name, state_style in (node.get("controlVisualStates") or {}).items()
            )
            if value is not None
        },
        "controlConfig": control_config,
        "axis": axis,
        "children": child_payloads,
        "overlayChildren": overlay_child_payloads,
        "contentItems": content_items,
        "compoundLayout": (
            {
                "axis": str((context.compound_controls.get(node_id) or {}).get("axis") or axis),
                "orderedSlotIds": [
                    str(item)
                    for item in (context.compound_controls.get(node_id) or {}).get("orderedSlotIds") or []
                ],
                "singleLine": bool((context.compound_controls.get(node_id) or {}).get("singleLine")),
            }
            if node_id in context.compound_controls
            else None
        ),
        "action": action,
        "style": {
            "fontSize": min(max(number(style.get("fontSize"), 16) * context.design_scale, 8), 72),
            "fontWeight": str(style.get("fontWeight") or "400"),
            "fontFamily": font["family"],
            "fontResolvedFamily": font["resolvedFamily"],
            "fontResolutionStatus": font["resolutionStatus"],
            "fontFailedFamilies": font["failedFamilies"],
            "fontDesign": font["design"],
            "fontNativeName": font["nativeName"],
            "fontStyle": font["style"],
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
            "spacing": (
                max(layout_spacing, 0)
                if layout_container.get("gapPt") is not None
                else min(max(layout_spacing, 0), 40)
            ),
            "rowSpacing": number(layout_container.get("rowGapPt"), layout_spacing) * context.design_scale,
            "columnSpacing": number(layout_container.get("columnGapPt"), layout_spacing) * context.design_scale,
            "layoutAlgorithm": str(layout_container.get("layoutAlgorithm") or "stack"),
            "wraps": bool(layout_container.get("wraps")),
            "reversesChildren": bool(layout_container.get("reverse")),
            "flexGrow": max(number(layout_sizing.get("flexGrow"), number(style.get("flexGrow"))), 0),
            "widthFraction": width_fraction,
            "minHeight": min_height,
            "minWidth": number(box_model.get("minWidthPt")) * context.design_scale if box_model.get("minWidthPt") is not None else None,
            "maxWidth": number(box_model.get("maxWidthPt")) * context.design_scale if box_model.get("maxWidthPt") is not None else None,
            "maxHeight": number(box_model.get("maxHeightPt")) * context.design_scale if box_model.get("maxHeightPt") is not None else None,
            "boxSizing": str(box_model.get("boxSizing") or style.get("boxSizing") or "content-box"),
            "contentWidth": number(box_model.get("contentWidthPt")) * context.design_scale if box_model.get("contentWidthPt") is not None else None,
            "contentHeight": number(box_model.get("contentHeightPt")) * context.design_scale if box_model.get("contentHeightPt") is not None else None,
            "preferredWidth": min(max(width, 0), context.root_width),
            "preferredHeight": min(max(number(rect.get("height")), 0), max(context.root_width * 3, 1200)),
            "textMeasureWidth": (
                min(max(width, 0), context.root_width)
                if semantic in {"text", "label", "heading"}
                and line_count > 1
                and width > 0
                else None
            ),
            "expectedTextLines": line_count if line_count > 0 else None,
            "firstBaselineOffset": (
                max(first_baseline_y - source_top, 0) * context.design_scale
                if first_baseline_y is not None else None
            ),
            "lastBaselineOffset": (
                max(last_baseline_y - source_top, 0) * context.design_scale
                if last_baseline_y is not None else None
            ),
            "baselineAligned": baseline_aligned,
            "resistsCompression": str(style.get("flexShrink") or "1") == "0" or bool(layout_sizing.get("resistsHorizontalCompression")),
            "preservesIntrinsicWidth": preserves_intrinsic_width or layout_width_policy == "intrinsic",
            "fixedWidth": min(max(fixed_width, 0), context.root_width) if fixed_width is not None else None,
            "fixedHeight": min(max(fixed_height, 0), 240) if fixed_height is not None else None,
            "aspectRatio": ratio if preserves_aspect_ratio else None,
            "scrollAxis": scroll_axis,
            "textLineLimit": text_line_limit,
            "textOverflow": str(style.get("textOverflow") or "clip"),
            "textAlignment": str(style.get("textAlign") or "start"),
            "justifyContent": str(layout_container.get("distribution") or style.get("justifyContent") or "normal"),
            "alignItems": str(layout_container.get("alignment") or style.get("alignItems") or "normal"),
            "gridColumnCount": grid_column_count(style.get("gridTemplateColumns")) if axis == "grid" else None,
            "gridColumnWidths": grid_column_widths if grid_column_widths else None,
            "positioningScheme": str(positioning.get("scheme") or style.get("position") or "static"),
            "positioningOwnerNodeID": positioning.get("containingBlockNodeId"),
            "coordinateSpace": positioning.get("coordinateSpace"),
            "mediaContentMode": str(asset.get("renderMode") or style.get("objectFit") or "contain"),
            "mediaPosition": str(asset.get("position") or style.get("objectPosition") or "50% 50%"),
            "backgroundContentMode": str(asset.get("renderMode") or "cover") if asset_kind == "css-background" else None,
            "backgroundPosition": str(asset.get("position") or "50% 50%") if asset_kind == "css-background" else None,
            "backgroundRepeat": str(asset.get("repeat") or "no-repeat") if asset_kind == "css-background" else None,
        },
        "systemImage": system_image_name(node, context.nodes.get(str(node.get("parentId") or ""))),
        "assetName": asset.get("iosName") if is_foreground_asset else None,
        "backgroundAssetName": asset.get("iosName") if asset_kind == "css-background" else None,
        "layoutContract": {
            "nodeID": node_id,
            "containerAlgorithm": str(layout_container.get("layoutAlgorithm") or "stack"),
            "widthKind": str((box_model.get("widthContract") or {}).get("kind") or "automatic"),
            "heightKind": str((box_model.get("heightContract") or {}).get("kind") or "automatic"),
            "positioningScheme": str(positioning.get("scheme") or "static"),
            "positioningOwnerNodeID": positioning.get("containingBlockNodeId"),
            "gridColumnStart": ((layout_node.get("gridItem") or {}).get("columnStart") or {}).get("index"),
            "gridColumnSpan": ((layout_node.get("gridItem") or {}).get("columnEnd") or {}).get("span"),
            "gridRowStart": ((layout_node.get("gridItem") or {}).get("rowStart") or {}).get("index"),
            "gridRowSpan": ((layout_node.get("gridItem") or {}).get("rowEnd") or {}).get("span"),
        },
        "accessibilityLabel": compact_text(content.get("accessibilityLabel"), 120) or None,
        "contextualActions": context.contextual_actions.get(node_id) or [],
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
        "richTextRuns": node_rich_text_runs,
        "motions": context.motions.get(node_id) or [],
    }
    if expansion_content:
        payload["visibleWhenStateID"] = parent_state_id
    return payload


def build_screen(
    ir: dict[str, Any],
    architecture: dict[str, Any] | None = None,
    native_layout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    screen = ir["screens"][0]
    screen_id = str(screen.get("id") or "screen")
    architecture = architecture or {}
    layers = architecture.get("layers") if isinstance(architecture.get("layers"), dict) else {}
    content_container = layers.get("contentContainer") if isinstance(layers.get("contentContainer"), dict) else {}
    node_strategies = content_container.get("nodeStrategies") if isinstance(content_container.get("nodeStrategies"), list) else []
    native_container_kinds = {
        str(item.get("nodeId") or ""): str(item.get("kind") or "")
        for item in node_strategies
        if isinstance(item, dict) and item.get("nodeId") and item.get("kind")
    }
    native_layout = native_layout or {}
    layout_containers = {
        str(item.get("containerNodeId") or ""): item
        for item in native_layout.get("containers") or []
        if isinstance(item, dict) and item.get("containerNodeId")
    }
    layout_nodes = {
        str(item.get("nodeId") or ""): item
        for item in native_layout.get("nodes") or []
        if isinstance(item, dict) and item.get("nodeId")
    }
    layout_sizing = {
        str(item.get("nodeId") or ""): item
        for container in layout_containers.values()
        for item in container.get("childSizing") or []
        if isinstance(item, dict) and item.get("nodeId")
    }
    compound_controls = {
        str(item.get("nodeId") or ""): item
        for item in native_layout.get("compoundControls") or []
        if isinstance(item, dict) and item.get("nodeId")
    }
    nodes_list = screen.get("nodes") or []
    nodes = {str(node["id"]): node for node in nodes_list}
    children: dict[str | None, list[str]] = {}
    for node in nodes_list:
        children.setdefault(node.get("parentId"), []).append(str(node["id"]))

    presentation_root_ids = {
        str(target_id)
        for state in ir.get("states") or []
        if str(state.get("kind") or "") in PRESENTATION_KINDS
        for target_id in state.get("targetNodeIds") or []
    }

    def text_leaf_ids(node_id: str) -> list[str]:
        child_ids = children.get(node_id) or []
        result = [leaf_id for child_id in child_ids for leaf_id in text_leaf_ids(child_id)]
        if result:
            return result
        content = (nodes.get(node_id) or {}).get("content") or {}
        return [node_id] if compact_text(content.get("text")) or content.get("runs") else []

    def native_content_variant(raw: dict[str, Any]) -> dict[str, Any] | None:
        target_node_id = str(raw.get("targetNodeId") or "")
        template_ids = children.get(target_node_id) or []
        if not target_node_id or not template_ids:
            return None
        items = []
        for index, item in enumerate(raw.get("items") or []):
            if not isinstance(item, dict):
                continue
            template_id = template_ids[index] if index < len(template_ids) else template_ids[0]
            leaf_ids = text_leaf_ids(template_id)
            values = [compact_text(value, 480) for value in (item.get("textLeaves") or []) if compact_text(value, 480)]
            if not values and compact_text(item.get("text"), 480):
                values = [compact_text(item.get("text"), 480)]
            if not leaf_ids or not values:
                continue
            items.append({
                "id": f"{target_node_id}.dynamic.{index}",
                "templateNodeID": template_id,
                "textByNodeID": {leaf_id: values[value_index] for value_index, leaf_id in enumerate(leaf_ids) if value_index < len(values)},
                "textValues": values,
            })
        if not items:
            return None
        size_overrides = []
        before_rect = raw.get("targetRectBeforeCssPx") or {}
        after_rect = raw.get("targetRectAfterCssPx") or {}
        measured_rect = ((nodes.get(target_node_id) or {}).get("layout") or {}).get("rect") or {}
        before_height = number(before_rect.get("height"))
        after_height = number(after_rect.get("height"))
        measured_height = number(measured_rect.get("height"))
        if before_height > 0 and after_height > 0 and abs(after_height - before_height) > 0.5:
            scale = measured_height / before_height if measured_height > 0 else 1
            size_overrides.append({"nodeID": target_node_id, "width": None, "height": after_height * scale})
            current = target_node_id
            while current:
                if current in presentation_root_ids:
                    presentation_rect = ((nodes.get(current) or {}).get("layout") or {}).get("rect") or {}
                    presentation_height = number(presentation_rect.get("height"))
                    if presentation_height > 0 and current != target_node_id:
                        size_overrides.append({
                            "nodeID": current,
                            "width": None,
                            "height": presentation_height + (after_height - before_height) * scale,
                        })
                    break
                current = str((nodes.get(current) or {}).get("parentId") or "")
        return {
            "targetNodeID": target_node_id,
            "items": items,
            "sizeOverrides": size_overrides,
            "scrollAxisOverride": str(raw.get("scrollAxisAfter") or "none"),
        }

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
        delta_operations = (target_state.get("stateDelta") or {}).get("operations") or []
        action["deltaRemoveNodeIDs"] = [
            str(item.get("targetNodeId"))
            for item in delta_operations
            if isinstance(item, dict)
            and item.get("kind") in {"remove-subtree", "replace-subtree"}
            and item.get("targetNodeId")
        ]
        action["selectionMode"] = "exclusive" if state_kind == "selection" and duplicate_state_transitions > 1 else "multiple"
        if action.get("targetStateID") and action.get("presentation"):
            presentation_by_state[str(action["targetStateID"])] = dict(action["presentation"])
        if interaction.get("automatic"):
            automatic_actions.append(action)
        source_ids = [str(item) for item in (interaction.get("sourceNodeIds") or [interaction.get("sourceNodeId")]) if item]
        content_variants = {
            str(item.get("sourceNodeId") or ""): item
            for item in ((interaction.get("payload") or {}).get("contentVariants") or [])
            if isinstance(item, dict)
        }
        target_ids = [str(item) for item in target_state.get("targetNodeIds") or []]
        for index, source_id in enumerate(source_ids):
            if source_id:
                node_action = dict(action)
                node_action["sourceNodeID"] = source_id
                content_variant = native_content_variant(content_variants.get(source_id) or {})
                if content_variant:
                    node_action["contentVariant"] = content_variant
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

    contextual_actions: dict[str, list[dict[str, Any]]] = {}
    contextual_root_ids: set[str] = set()
    for state in states_by_id.values():
        delta = state.get("stateDelta") or {}
        if delta.get("nativeStrategy") != "contextual-item-actions":
            continue
        target_node_id = str(delta.get("contextualTargetNodeId") or "")
        root_ids = [
            str(item)
            for item in delta.get("contextualActionRootNodeIds") or []
            if str(item) in nodes
        ]
        if not target_node_id or target_node_id not in nodes or not root_ids:
            continue
        contextual_root_ids.update(root_ids)
        action_node_ids = []
        for contextual_root_id in root_ids:
            pending = [contextual_root_id]
            candidates = []
            visited: set[str] = set()
            while pending:
                candidate_id = pending.pop(0)
                if candidate_id in visited:
                    continue
                visited.add(candidate_id)
                candidate = nodes.get(candidate_id) or {}
                if str(candidate.get("semanticType") or "") in {
                    "button",
                    "icon-button",
                    "link",
                    "menu-item",
                }:
                    candidates.append(candidate_id)
                pending.extend(children.get(candidate_id) or [])
            action_node_ids.extend(candidates or [contextual_root_id])
        contextual_items = []
        for index, action_node_id in enumerate(dict.fromkeys(action_node_ids)):
            action_node = nodes[action_node_id]
            action_text = compact_text((action_node.get("content") or {}).get("text"), 80)
            destructive = bool(re.search(
                r"delete|remove|trash|destructive|删除|移除|清空",
                " ".join([
                    action_text,
                    str((action_node.get("source") or {}).get("selector") or ""),
                    str((action_node.get("source") or {}).get("domId") or ""),
                ]),
                re.IGNORECASE,
            ))
            contextual_items.append({
                "id": f"{state.get('id')}.contextual.{index + 1}",
                "title": action_text or "Action",
                "systemImage": system_image_name(action_node, nodes.get(str(action_node.get("parentId") or ""))),
                "tint": color_string((action_node.get("style") or {}).get("backgroundColor")),
                "role": "destructive" if destructive else "normal",
                "edge": "trailing",
                "allowsFullSwipe": destructive and len(action_node_ids) == 1,
                "action": actions.get(action_node_id) or {
                    "interactionID": None,
                    "action": "none",
                    "target": None,
                    "targetScreenID": None,
                    "targetStateID": None,
                    "delayMilliseconds": 0,
                    "sourceNodeID": action_node_id,
                    "targetNodeID": target_node_id,
                    "stateKind": "contextual-item-actions",
                    "selectionMode": "multiple",
                    "localEffect": None,
                    "deltaRemoveNodeIDs": [],
                    "feedbackText": None,
                    "feedbackDurationMilliseconds": None,
                    "initiallySelected": None,
                    "selectionCountInitial": None,
                    "selectionCountTotal": None,
                    "contentVariant": None,
                },
            })
        contextual_actions[target_node_id] = contextual_items

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
    presentation_root_ids = set()
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
    target_viewport = (ir.get("target") or {}).get("viewportPt") or {}
    target_height = number(target_viewport.get("height"), root_height)
    design_scale = number((ir.get("target") or {}).get("scale"), 1)
    fixed_artboard_cover_crop_top = (
        max((root_height - target_height) / 2, 0)
        if abs(design_scale - 1) > 0.001 and root_height > target_height
        else 0
    )
    visible_source_status_bar_height = max(
        source_status_bar_height - fixed_artboard_cover_crop_top,
        0,
    )
    aligns_to_source_status_bar = bool(
        visible_source_status_bar_height
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
            if re.search(r"nav|header|top.?bar|app.?bar|toolbar", hint):
                top_score += 1.5
            if re.search(r"bottom|footer|tab.?bar|actions?|toolbar|dock", hint):
                bottom_score += 1.5
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
    bottom_bar_placement = (
        str(((regions.get("bottomBar") or {}).get("placement")) or "safe-area-inset")
        if bottom_bar_id
        else "none"
    )
    detached_root_ids = set(presentation_root_ids) | contextual_root_ids
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
        contextual_actions=contextual_actions,
        detached_root_ids=detached_root_ids,
        bottom_bar_placement=bottom_bar_placement,
        native_container_kinds=native_container_kinds,
        layout_containers=layout_containers,
        layout_nodes=layout_nodes,
        layout_sizing=layout_sizing,
        compound_controls=compound_controls,
    )
    root = node_payload(context, root_id) or {
        "id": root_id,
        "semantic": "container",
        "text": "",
        "placeholder": "",
        "axis": "vertical",
        "children": [],
        "overlayChildren": [],
        "contentItems": [],
        "action": None,
        "style": {},
        "accessibilityLabel": None,
        "contextualActions": [],
        "motions": [],
    }
    root["style"]["cornerRadius"] = 0
    top_bar = node_payload(context, top_bar_id, presentation=True) if top_bar_id else None
    bottom_bar = node_payload(context, bottom_bar_id, presentation=True) if bottom_bar_id else None

    def normalize_viewport_bar_geometry(bar: dict[str, Any] | None) -> None:
        if not bar:
            return
        bar_style = bar.get("style") or {}
        bar_style.update({
            "fixedWidth": None,
            "preferredWidth": None,
            "widthFraction": 1.0,
            "preservesIntrinsicWidth": False,
            "resistsCompression": False,
            "offsetX": 0,
        })
        if str(bar.get("axis") or "") != "horizontal":
            return
        # A viewport bar is pinned to the native parent width. Large direct
        # children participate in that width allocation, while their icons,
        # labels, badges, and other descendants retain measured geometry.
        for child in bar.get("children") or []:
            child_style = child.get("style") or {}
            if number(child_style.get("widthFraction")) < 0.2:
                continue
            child_style["fixedWidth"] = None
            child_style["preservesIntrinsicWidth"] = False
            child_style["resistsCompression"] = False

    normalize_viewport_bar_geometry(top_bar)
    normalize_viewport_bar_geometry(bottom_bar)
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
                    number(presentation_source_rect.get("x")) - number(root_rect.get("x")),
                    number(presentation_source_rect.get("y")) - number(root_rect.get("y")),
                    number(presentation_source_rect.get("width")),
                    number(presentation_source_rect.get("height")),
                ]
                presentation_contract = presentation_by_state.get(str(state.get("id"))) or {}
                presentation_style = str(presentation_contract.get("style") or "page-sheet")
                uses_custom_overlay = (
                    str(state.get("kind") or "") in {"overlay", "popover-overlay"}
                    or presentation_style in {"in-place-overlay", "menu"}
                )
                if uses_custom_overlay:
                    presentation_node["style"]["fixedWidth"] = source_rect[2]
                    presentation_node["style"]["fixedHeight"] = source_rect[3]
                presentations.append({
                    "stateID": str(state.get("id")),
                    "kind": str(state.get("kind") or "sheet"),
                    "node": presentation_node,
                    "style": presentation_style,
                    "detents": presentation_contract.get("detents") or [],
                    "grabberVisible": presentation_contract.get("grabberVisible"),
                    "interactiveDismissDisabled": bool(presentation_contract.get("interactiveDismissDisabled", False)),
                    "usesCustomOverlay": uses_custom_overlay,
                    "coordinateSpace": "app-root",
                    "sourceRect": source_rect,
                })
                break

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
        "sourceStatusBarHeight": visible_source_status_bar_height if aligns_to_source_status_bar else None,
        "safeArea": safe_area_payload,
        "contentContainer": {
            "nodeId": str(content_container.get("nodeId") or root_id),
            "kind": str(content_container.get("kind") or "scroll-view"),
            "scrollAxis": str(content_container.get("scrollAxis") or "vertical"),
            "usesCellReuse": bool(content_container.get("usesCellReuse")),
        },
        "navigation": navigation,
        "tabContainer": tab_container,
        "root": root,
        "topBar": top_bar,
        "bottomBar": bottom_bar,
        "bottomBarPlacement": bottom_bar_placement,
        "presentations": presentations,
        "automaticActions": automatic_actions,
        "stateLayouts": native_layout.get("stateLayouts") or [],
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
    let contentContainer: HTMLToIOSContentContainerSpec
    let navigation: HTMLToIOSNavigationSpec
    let root: HTMLToIOSNodeSpec
    let topBar: HTMLToIOSNodeSpec?
    let bottomBar: HTMLToIOSNodeSpec?
    let bottomBarPlacement: String
    let presentations: [HTMLToIOSPresentationSpec]
    let automaticActions: [HTMLToIOSActionSpec]
    let stateLayouts: [HTMLToIOSStateLayoutSpec]
}}

struct HTMLToIOSStateLayoutSpec: Codable, Identifiable {{
    var id: String {{ stateId }}
    let stateId: String
    let ownerScreenId: String
    let nativeStrategy: String
    let operations: [HTMLToIOSStateLayoutOperationSpec]
    let affectedContainerNodeIds: [String]
}}

struct HTMLToIOSStateLayoutOperationSpec: Codable {{
    let kind: String
    let targetNodeId: String?
    let generatedRootNodeId: String?
    let targetParentNodeId: String?
    let generatedLayoutNodeId: String?
    let targetBaselineLayoutNodeId: String?
    let changesLayout: Bool
}}

struct HTMLToIOSContentContainerSpec: Codable {{
    let nodeId: String
    let kind: String
    let scrollAxis: String
    let usesCellReuse: Bool
}}

struct HTMLToIOSSafeAreaSpec: Codable {{
    let owner: String
    let contentInsetAdjustment: String
    let containerWidthPolicy: String
    let containerHeightPolicy: String
    let subtractFromContainerDimensions: Bool
}}

enum HTMLToIOSLaunchConfiguration {{
    static var geometryCaptureEnabled: Bool {{
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "-HTMLToIOSGeometryCapture"), arguments.indices.contains(index + 1) else {{
            return false
        }}
        return ["1", "true", "yes"].contains(arguments[index + 1].lowercased())
    }}

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
    let coordinateSpace: String
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
    let deltaRemoveNodeIDs: [String]
    let feedbackText: String?
    let feedbackDurationMilliseconds: Int?
    let initiallySelected: Bool?
    let selectionCountInitial: Int?
    let selectionCountTotal: Int?
    let contentVariant: HTMLToIOSContentVariantSpec?
}}

struct HTMLToIOSContextualActionSpec: Codable, Identifiable {{
    let id: String
    let title: String
    let systemImage: String?
    let tint: String?
    let role: String
    let edge: String
    let allowsFullSwipe: Bool
    let action: HTMLToIOSActionSpec
}}

struct HTMLToIOSContentVariantSpec: Codable {{
    let targetNodeID: String
    let items: [HTMLToIOSDynamicContentItemSpec]
    let sizeOverrides: [HTMLToIOSSizeOverrideSpec]
    let scrollAxisOverride: String
}}

struct HTMLToIOSSizeOverrideSpec: Codable {{
    let nodeID: String
    let width: Double?
    let height: Double?
}}

struct HTMLToIOSDynamicContentItemSpec: Codable, Identifiable {{
    let id: String
    let templateNodeID: String
    let textByNodeID: [String: String]
    let textValues: [String]
}}

struct HTMLToIOSNodeLayoutContractSpec: Codable {{
    let nodeID: String
    let containerAlgorithm: String
    let widthKind: String
    let heightKind: String
    let positioningScheme: String
    let positioningOwnerNodeID: String?
    let gridColumnStart: Int?
    let gridColumnSpan: Int?
    let gridRowStart: Int?
    let gridRowSpan: Int?
}}

struct HTMLToIOSNodeSpec: Codable, Identifiable {{
    let id: String
    let semantic: String
    let nativeContainerKind: String?
    let text: String
    let placeholder: String
    let textBehavior: HTMLToIOSTextBehaviorSpec?
    let dataBinding: HTMLToIOSDataBindingSpec?
    let isEnabled: Bool
    let controlVisualStates: [String: HTMLToIOSControlVisualStateSpec]
    let controlConfig: HTMLToIOSControlConfigSpec?
    let axis: String
    let children: [HTMLToIOSNodeSpec]
    let overlayChildren: [HTMLToIOSNodeSpec]
    let contentItems: [HTMLToIOSContentItemSpec]
    let compoundLayout: HTMLToIOSCompoundLayoutSpec?
    let action: HTMLToIOSActionSpec?
    let style: HTMLToIOSStyleSpec
    let systemImage: String?
    let assetName: String?
    let backgroundAssetName: String?
    let layoutContract: HTMLToIOSNodeLayoutContractSpec
    let accessibilityLabel: String?
    let contextualActions: [HTMLToIOSContextualActionSpec]
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

struct HTMLToIOSTextBehaviorSpec: Codable {{
    let role: String?
    let nativeControl: String?
    let editable: Bool?
    let readOnly: Bool?
    let selectable: Bool?
    let multiline: Bool?
    let scrollable: Bool?
    let secure: Bool?
    let maxLength: Int?
    let initialValue: String?
    let enabled: Bool?
    let keyboardType: String?
    let contentType: String?
    let submitLabel: String?
    let fieldID: String?
    let autofocus: Bool?
    let returnKey: String?
    let autocapitalization: String?
    let autocorrection: Bool?
    let validation: String?
    let placeholderStyle: HTMLToIOSPlaceholderStyleSpec?
}}

struct HTMLToIOSPlaceholderStyleSpec: Codable {{
    let fontSize: Double?
    let fontWeight: String?
    let foreground: String?
    let lineHeight: Double?
    let letterSpacing: Double?
    let opacity: Double?
}}

struct HTMLToIOSDataBindingSpec: Codable {{
    let sourceID: String?
    let itemIDKey: String?
    let stateRole: String?
    let pagination: String
    let ownership: String
    let requiresViewModel: Bool
    let snapshotIsSampleData: Bool
}}

struct HTMLToIOSControlVisualStateSpec: Codable {{
    let foreground: String?
    let background: String?
    let gradientColors: [String]?
    let borderWidth: Double?
    let borderColor: String?
    let cornerRadius: Double?
    let opacity: Double
    let scale: Double
    let shadowColor: String?
    let shadowOffsetX: Double
    let shadowOffsetY: Double
    let shadowRadius: Double
}}

struct HTMLToIOSControlConfigSpec: Codable {{
    let minimum: Double
    let maximum: Double
    let step: Double
    let value: String
    let inputType: String
    let options: [HTMLToIOSControlOptionSpec]
    let allowsMultipleSelection: Bool
}}

struct HTMLToIOSControlOptionSpec: Codable, Identifiable {{
    let id: String
    let title: String
    let selected: Bool
}}

struct HTMLToIOSContentItemSpec: Codable, Identifiable {{
    let id: String
    let kind: String
    let text: String?
    let childID: String?
    let preferredWidth: Double?
    let preferredHeight: Double?
    let singleLine: Bool?
    let gapBefore: Double?
    let flexibleGapBefore: Bool?
}}

struct HTMLToIOSCompoundLayoutSpec: Codable {{
    let axis: String
    let orderedSlotIds: [String]
    let singleLine: Bool
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
    let fontFamily: String?
    let fontResolvedFamily: String?
    let fontResolutionStatus: String?
    let fontFailedFamilies: [String]?
    let fontDesign: String?
    let fontNativeName: String?
    let fontStyle: String?
    let foreground: String?
    let background: String?
    let lineHeight: Double?
    let letterSpacing: Double?
}}

struct HTMLToIOSStyleSpec: Codable {{
    let fontSize: Double?
    let fontWeight: String?
    let fontFamily: String?
    let fontResolvedFamily: String?
    let fontResolutionStatus: String?
    let fontFailedFamilies: [String]?
    let fontDesign: String?
    let fontNativeName: String?
    let fontStyle: String?
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
    let rowSpacing: Double?
    let columnSpacing: Double?
    let layoutAlgorithm: String?
    let wraps: Bool?
    let reversesChildren: Bool?
    let flexGrow: Double?
    let widthFraction: Double?
    let minHeight: Double?
    let minWidth: Double?
    let maxWidth: Double?
    let maxHeight: Double?
    let boxSizing: String?
    let contentWidth: Double?
    let contentHeight: Double?
    let preferredWidth: Double?
    let preferredHeight: Double?
    let textMeasureWidth: Double?
    let expectedTextLines: Int?
    let firstBaselineOffset: Double?
    let lastBaselineOffset: Double?
    let baselineAligned: Bool?
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
    let gridColumnWidths: [Double?]?
    let positioningScheme: String?
    let positioningOwnerNodeID: String?
    let coordinateSpace: String?
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
import UIKit

private func htmlToIOSKeyboardType(_ raw: String?) -> UIKeyboardType {
    switch raw {
    case "emailAddress": return .emailAddress
    case "URL": return .URL
    case "phonePad": return .phonePad
    case "numberPad": return .numberPad
    case "decimalPad": return .decimalPad
    default: return .default
    }
}

private func htmlToIOSTextContentType(_ raw: String?) -> UITextContentType? {
    switch raw {
    case "emailAddress": return .emailAddress
    case "URL": return .URL
    case "telephoneNumber": return .telephoneNumber
    case "password": return .password
    case "username": return .username
    default: return nil
    }
}

private func htmlToIOSSubmitLabel(_ raw: String?) -> SubmitLabel {
    switch raw?.lowercased() {
    case "done": return .done
    case "go": return .go
    case "join": return .join
    case "next": return .next
    case "route": return .route
    case "search": return .search
    case "send": return .send
    case "continue": return .continue
    case "return": return .return
    default: return .return
    }
}

private struct HTMLToIOSInputPolicyModifier: ViewModifier {
    let behavior: HTMLToIOSTextBehaviorSpec?

    @ViewBuilder func body(content: Content) -> some View {
        switch behavior?.autocapitalization?.lowercased() {
        case "none", "off":
            content.textInputAutocapitalization(.never)
                .autocorrectionDisabled(behavior?.autocorrection == false)
        case "words":
            content.textInputAutocapitalization(.words)
                .autocorrectionDisabled(behavior?.autocorrection == false)
        case "characters":
            content.textInputAutocapitalization(.characters)
                .autocorrectionDisabled(behavior?.autocorrection == false)
        default:
            content.textInputAutocapitalization(.sentences)
                .autocorrectionDisabled(behavior?.autocorrection == false)
        }
    }
}

private func htmlToIOSFontWeight(_ raw: String?) -> Font.Weight {
    let value = Int(raw ?? "400") ?? 400
    if value >= 900 { return .black }
    if value >= 800 { return .heavy }
    if value >= 700 { return .bold }
    if value >= 600 { return .semibold }
    if value >= 500 { return .medium }
    if value >= 400 { return .regular }
    if value >= 300 { return .light }
    if value >= 200 { return .thin }
    return .ultraLight
}

private func htmlToIOSFontDesign(_ raw: String?) -> Font.Design {
    switch raw {
    case "monospaced": return .monospaced
    case "serif": return .serif
    case "rounded": return .rounded
    default: return .default
    }
}

private func htmlToIOSFont(size: Double, weight: String?, design: String?, nativeName: String?, style: String?) -> Font {
    if let nativeName, !nativeName.isEmpty {
        let font = Font.custom(nativeName, fixedSize: size)
        return style == "italic" || style == "oblique" ? font.italic() : font
    }
    let font = Font.system(size: size, weight: htmlToIOSFontWeight(weight), design: htmlToIOSFontDesign(design))
    return style == "italic" || style == "oblique" ? font.italic() : font
}

private func htmlToIOSUIFontLineHeight(size: Double, weight: String?, design: String?, nativeName: String?, style: String?) -> CGFloat {
    htmlToIOSUIFont(size: size, weight: weight, design: design, nativeName: nativeName, style: style).lineHeight
}

private func htmlToIOSUIFont(size: Double, weight: String?, design: String?, nativeName: String?, style: String?) -> UIFont {
    if let nativeName, let font = UIFont(name: nativeName, size: size) { return font }
    let value = Int(weight ?? "400") ?? 400
    let uiWeight: UIFont.Weight
    if value >= 900 { uiWeight = .black }
    else if value >= 800 { uiWeight = .heavy }
    else if value >= 700 { uiWeight = .bold }
    else if value >= 600 { uiWeight = .semibold }
    else if value >= 500 { uiWeight = .medium }
    else if value >= 400 { uiWeight = .regular }
    else if value >= 300 { uiWeight = .light }
    else if value >= 200 { uiWeight = .thin }
    else { uiWeight = .ultraLight }
    var descriptor = UIFont.systemFont(ofSize: size, weight: uiWeight).fontDescriptor
    let systemDesign: UIFontDescriptor.SystemDesign? = design == "monospaced" ? .monospaced : (design == "serif" ? .serif : (design == "rounded" ? .rounded : nil))
    if let systemDesign, let designed = descriptor.withDesign(systemDesign) { descriptor = designed }
    if style == "italic" || style == "oblique", let italic = descriptor.withSymbolicTraits(.traitItalic) { descriptor = italic }
    return UIFont(descriptor: descriptor, size: size)
}

@MainActor
final class HTMLToIOSGeneratedStore: ObservableObject {
    struct PresentedState: Identifiable { let id: String }

    @Published var path: [HTMLToIOSGeneratedRoute] = []
    @Published var selectedTab: String?
    @Published var tabPaths: [String: [HTMLToIOSGeneratedRoute]] = [:]
    @Published var tabScrollToTopID: String?
    @Published var tabScrollToTopNonce = 0
    @Published var values: [String: String] = [:]
    @Published var numericValues: [String: Double] = [:]
    @Published var dateValues: [String: Date] = [:]
    @Published var colorValues: [String: Color] = [:]
    @Published var flags: Set<String> = []
    @Published var selectedByState: [String: String] = [:]
    @Published var selectionOverrides: [String: Bool] = [:]
    @Published var selectionCounts: [String: Int] = [:]
    @Published var hiddenNodeIDs: Set<String> = []
    @Published var feedbackText: [String: String] = [:]
    @Published var contentOverrides: [String: [HTMLToIOSDynamicContentItemSpec]] = [:]
    @Published var sizeOverrides: [String: HTMLToIOSSizeOverrideSpec] = [:]
    @Published var scrollAxisOverrides: [String: String] = [:]
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

    func binding(for nodeID: String, initialValue: String = "", maxLength: Int? = nil) -> Binding<String> {
        Binding(
            get: { self.values[nodeID] ?? initialValue },
            set: { value in
                if let maxLength, maxLength >= 0 {
                    self.values[nodeID] = String(value.prefix(maxLength))
                } else {
                    self.values[nodeID] = value
                }
            }
        )
    }

    func flagBinding(for nodeID: String) -> Binding<Bool> {
        Binding(get: { self.flags.contains(nodeID) }, set: { enabled in
            if enabled { self.flags.insert(nodeID) } else { self.flags.remove(nodeID) }
        })
    }

    func numericBinding(for nodeID: String, initialValue: Double) -> Binding<Double> {
        Binding(
            get: { self.numericValues[nodeID] ?? initialValue },
            set: { self.numericValues[nodeID] = $0 }
        )
    }

    func selectionBinding(for nodeID: String, initialValue: String) -> Binding<String> {
        Binding(
            get: { self.values[nodeID] ?? initialValue },
            set: { self.values[nodeID] = $0 }
        )
    }

    func dateBinding(for nodeID: String, initialValue: String) -> Binding<Date> {
        let fallback = HTMLToIOSDateParser.date(from: initialValue)
        return Binding(
            get: { self.dateValues[nodeID] ?? fallback },
            set: { self.dateValues[nodeID] = $0 }
        )
    }

    func colorBinding(for nodeID: String, initialValue: String?) -> Binding<Color> {
        Binding(
            get: { self.colorValues[nodeID] ?? Color(htmlToIOS: initialValue) },
            set: { self.colorValues[nodeID] = $0 }
        )
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
        if let variant = spec.contentVariant {
            contentOverrides[variant.targetNodeID] = variant.items
            for override in variant.sizeOverrides { sizeOverrides[override.nodeID] = override }
            scrollAxisOverrides[variant.targetNodeID] = variant.scrollAxisOverride
        }
        let routeID = spec.targetScreenID ?? spec.target
        let stateID = spec.targetStateID ?? spec.target
        if !spec.deltaRemoveNodeIDs.isEmpty {
            var nextHiddenNodeIDs = hiddenNodeIDs
            if spec.deltaRemoveNodeIDs.allSatisfy({ nextHiddenNodeIDs.contains($0) }) {
                spec.deltaRemoveNodeIDs.forEach { nextHiddenNodeIDs.remove($0) }
            } else {
                spec.deltaRemoveNodeIDs.forEach { nextHiddenNodeIDs.insert($0) }
            }
            hiddenNodeIDs = nextHiddenNodeIDs
        }
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
        case "overlay", "present-overlay", "show-dialog":
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

private enum HTMLToIOSDateParser {
    static func date(from value: String) -> Date {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if let date = ISO8601DateFormatter().date(from: trimmed) { return date }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        for format in ["yyyy-MM-dd", "yyyy-MM-dd'T'HH:mm", "HH:mm"] {
            formatter.dateFormat = format
            if let date = formatter.date(from: trimmed) { return date }
        }
        return Date(timeIntervalSinceReferenceDate: 0)
    }
}

private struct HTMLToIOSRichTextView: View {
    let runs: [HTMLToIOSRichTextRunSpec]
    let style: HTMLToIOSStyleSpec

    @ViewBuilder var body: some View {
        if style.expectedTextLines == 1 {
            HStack(alignment: .firstTextBaseline, spacing: 0) {
                ForEach(Array(runs.enumerated()), id: \.offset) { _, run in
                    Text(attributedText(for: run))
                }
            }
        } else {
            Text(attributedText)
        }
    }

    private var attributedText: AttributedString {
        var result = AttributedString()
        for run in runs {
            result.append(attributedText(for: run))
        }
        return result
    }

    private func attributedText(for run: HTMLToIOSRichTextRunSpec) -> AttributedString {
        var result = AttributedString(run.text)
        result.font = htmlToIOSFont(
            size: run.fontSize ?? style.fontSize ?? 16,
            weight: run.fontWeight ?? style.fontWeight,
            design: run.fontDesign ?? style.fontDesign,
            nativeName: run.fontNativeName ?? style.fontNativeName,
            style: run.fontStyle ?? style.fontStyle
        )
        result.foregroundColor = Color(htmlToIOS: run.foreground ?? style.foreground)
        if let background = run.background {
            result.backgroundColor = Color(htmlToIOS: background)
        }
        result.kern = run.letterSpacing ?? style.letterSpacing ?? 0
        return result
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
                        GeometryReader { proxy in
                            RadialGradient(
                                gradient: Gradient(stops: stops),
                                center: .center,
                                startRadius: 0,
                                endRadius: radialEndRadius(proxy.size)
                            )
                        }
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

    private func radialEndRadius(_ size: CGSize) -> CGFloat {
        max(sqrt(size.width * size.width + size.height * size.height) / 2, 1)
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

private struct HTMLToIOSMarginModifier: ViewModifier {
    let style: HTMLToIOSStyleSpec

    func body(content: Content) -> some View {
        let margin = style.margin ?? [0, 0, 0, 0]
        return content
            .padding(.top, margin.indices.contains(0) ? margin[0] : 0)
            .padding(.trailing, margin.indices.contains(1) ? margin[1] : 0)
            .padding(.bottom, margin.indices.contains(2) ? margin[2] : 0)
            .padding(.leading, margin.indices.contains(3) ? margin[3] : 0)
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
    let maxHeight: CGFloat?

    @ViewBuilder func body(content: Content) -> some View {
        if fixedWidth != nil || fixedHeight != nil {
            content.frame(width: fixedWidth, height: fixedHeight)
        } else if minWidth != nil || idealWidth != nil {
            firstFrame(content)
        } else if maxWidth != nil || minHeight != nil || maxHeight != nil {
            secondFrame(content)
        } else {
            content
        }
    }

    @ViewBuilder private func firstFrame(_ content: Content) -> some View {
        let framed = content.frame(minWidth: minWidth, idealWidth: idealWidth)
        if maxWidth != nil || minHeight != nil || maxHeight != nil {
            framed.frame(maxWidth: maxWidth, minHeight: minHeight, maxHeight: maxHeight)
        } else {
            framed
        }
    }

    private func secondFrame(_ content: Content) -> some View {
        content.frame(maxWidth: maxWidth, minHeight: minHeight, maxHeight: maxHeight)
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
    let sizeOverride: HTMLToIOSSizeOverrideSpec?
    let assetName: String?
    let foregroundOverride: String?
    let backgroundOverride: String?
    let gradientOverride: [String]?
    let constrainsPreferredWidth: Bool
    let enforcesPreferredWidth: Bool
    let calibratesTextLineBox: Bool
    let calibratesFirstBaseline: Bool

    func body(content: Content) -> some View {
        let padding = style.padding ?? [0, 0, 0, 0]
        let foregroundValue = foregroundOverride ?? style.foreground
        let foreground = foregroundValue == nil ? Color.primary : Color(htmlToIOS: foregroundValue)
        let alignment: TextAlignment = style.textAlignment == "center" ? .center : (style.textAlignment == "end" ? .trailing : .leading)
        let preferredWidth = constrainsPreferredWidth ? CGFloat(style.preferredWidth ?? 0) : 0
        let measuredTextWidth = style.textMeasureWidth.map { CGFloat($0) }
        let inferredMaxWidth: CGFloat? = measuredTextWidth
            ?? ((style.flexGrow ?? 0) > 0 || (constrainsPreferredWidth && (style.widthFraction ?? 0) > 0.88)
                ? .infinity
                : nil)
        let maxWidth: CGFloat? = style.maxWidth.map { CGFloat($0) } ?? inferredMaxWidth
        let idealWidth: CGFloat? = preferredWidth > 0 && maxWidth == nil ? preferredWidth : nil
        let inferredMinWidth: CGFloat? = (enforcesPreferredWidth || style.resistsCompression == true) && preferredWidth > 0 ? preferredWidth : nil
        let minWidth: CGFloat? = style.minWidth.map { CGFloat($0) } ?? inferredMinWidth
        let rawMinHeight = style.minHeight ?? 0
        let minHeight: CGFloat? = rawMinHeight > 0 ? CGFloat(rawMinHeight) : nil
        let maxHeight: CGFloat? = style.maxHeight.map { CGFloat($0) }
        let sourceFixedWidth: CGFloat? = style.fixedWidth.map { CGFloat($0) }
        let sourceFixedHeight: CGFloat? = style.fixedHeight.map { CGFloat($0) }
        let fixedWidth: CGFloat? = sizeOverride?.width.map { CGFloat($0) } ?? sourceFixedWidth
        let fixedHeight: CGFloat? = sizeOverride?.height.map { CGFloat($0) } ?? sourceFixedHeight
        let fontSize = style.fontSize ?? 16
        let nativeLineHeight = htmlToIOSUIFontLineHeight(
            size: fontSize,
            weight: style.fontWeight,
            design: style.fontDesign,
            nativeName: style.fontNativeName,
            style: style.fontStyle
        )
        let lineBoxLeading = calibratesTextLineBox ? max((style.lineHeight ?? Double(nativeLineHeight)) - Double(nativeLineHeight), 0) : 0
        let nativeFont = htmlToIOSUIFont(
            size: fontSize,
            weight: style.fontWeight,
            design: style.fontDesign,
            nativeName: style.fontNativeName,
            style: style.fontStyle
        )
        let nativeFirstBaseline = nativeFont.ascender + max(nativeFont.leading, 0) / 2
        let rawBaselineAdjustment = calibratesFirstBaseline
            ? CGFloat(style.firstBaselineOffset ?? Double(nativeFirstBaseline)) - nativeFirstBaseline
            : 0
        let baselineAdjustment = min(max(rawBaselineAdjustment, -CGFloat(fontSize) * 0.25), CGFloat(fontSize) * 0.25)
        let typography = content
            .font(htmlToIOSFont(size: fontSize, weight: style.fontWeight, design: style.fontDesign, nativeName: style.fontNativeName, style: style.fontStyle))
            .foregroundStyle(foreground)
            .multilineTextAlignment(alignment)
            .lineLimit(style.textLineLimit)
            .lineSpacing(lineBoxLeading)
            .tracking(style.letterSpacing ?? 0)
            .fixedSize(
                horizontal: style.preservesIntrinsicWidth == true,
                vertical: (style.expectedTextLines ?? 1) > 1
            )
            .layoutPriority(
                style.resistsCompression == true ? 2 : (style.preservesIntrinsicWidth == true ? 1 : 0)
            )
            .offset(y: baselineAdjustment)
        let insetContent = typography
            .padding(.top, (padding.indices.contains(0) ? padding[0] : 0) + lineBoxLeading / 2)
            .padding(.trailing, padding.indices.contains(1) ? padding[1] : 0)
            .padding(.bottom, (padding.indices.contains(2) ? padding[2] : 0) + lineBoxLeading / 2)
            .padding(.leading, padding.indices.contains(3) ? padding[3] : 0)
            .modifier(HTMLToIOSAspectRatioModifier(ratio: style.aspectRatio.map { CGFloat($0) }))
        let framedContent = insetContent
            .modifier(HTMLToIOSFrameModifier(
                fixedWidth: fixedWidth,
                fixedHeight: fixedHeight,
                minWidth: minWidth,
                idealWidth: idealWidth,
                maxWidth: maxWidth,
                minHeight: minHeight,
                maxHeight: maxHeight
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
    }

}

private struct HTMLToIOSAccessibilityModifier: ViewModifier {
    let spec: HTMLToIOSNodeSpec

    @ViewBuilder func body(content: Content) -> some View {
        if HTMLToIOSLaunchConfiguration.geometryCaptureEnabled {
            content
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier(spec.id)
        } else if spec.action != nil || (spec.children.isEmpty && spec.overlayChildren.isEmpty) {
            content
                .accessibilityIdentifier(spec.id)
                .accessibilityLabel(spec.accessibilityLabel ?? spec.text)
                .accessibilityAddTraits(spec.action == nil ? [] : .isButton)
        } else {
            content
        }
    }
}

private struct HTMLToIOSControlVisualStateKey: EnvironmentKey {
    static let defaultValue = "normal"
}

private extension EnvironmentValues {
    var htmlToIOSControlVisualState: String {
        get { self[HTMLToIOSControlVisualStateKey.self] }
        set { self[HTMLToIOSControlVisualStateKey.self] = newValue }
    }
}

private struct HTMLToIOSControlButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label.environment(
            \.htmlToIOSControlVisualState,
            !isEnabled ? "disabled" : (configuration.isPressed ? "pressed" : "normal")
        )
    }
}

private struct HTMLToIOSCheckboxToggleStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button {
            configuration.isOn.toggle()
        } label: {
            HStack(spacing: 8) {
                Image(systemName: configuration.isOn ? "checkmark.square.fill" : "square")
                configuration.label
            }
        }
        .buttonStyle(HTMLToIOSControlButtonStyle())
    }
}

private struct HTMLToIOSRadioToggleStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button {
            configuration.isOn = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: configuration.isOn ? "circle.inset.filled" : "circle")
                configuration.label
            }
        }
        .buttonStyle(HTMLToIOSControlButtonStyle())
    }
}

struct HTMLToIOSTypedViewRegistry {
    let build: (
        _ nodeID: String,
        _ store: HTMLToIOSGeneratedStore,
        _ spec: HTMLToIOSNodeSpec,
        _ registry: HTMLToIOSTypedViewRegistry
    ) -> AnyView?
}

private struct HTMLToIOSContextualActionsModifier: ViewModifier {
    let actions: [HTMLToIOSContextualActionSpec]
    @ObservedObject var store: HTMLToIOSGeneratedStore
    @State private var fallbackRevealed = false

    @ViewBuilder func body(content: Content) -> some View {
        if actions.isEmpty {
            content
        } else {
            contextualBody(content)
        }
    }

    private func contextualBody(_ content: Content) -> some View {
        ZStack(alignment: .trailing) {
            if fallbackRevealed {
                HStack(spacing: 0) {
                    ForEach(actions.filter { $0.edge != "leading" }) { item in
                        contextualButton(item)
                            .frame(minWidth: 72, maxHeight: .infinity)
                    }
                }
                .transition(.move(edge: .trailing))
            }
            content
                .offset(x: fallbackRevealed ? -CGFloat(actions.filter { $0.edge != "leading" }.count * 72) : 0)
                .swipeActions(
                    edge: .trailing,
                    allowsFullSwipe: actions.filter { $0.edge != "leading" }.contains { $0.allowsFullSwipe }
                ) {
                    ForEach(actions.filter { $0.edge != "leading" }) { item in
                        contextualButton(item)
                    }
                }
                .swipeActions(
                    edge: .leading,
                    allowsFullSwipe: actions.filter { $0.edge == "leading" }.contains { $0.allowsFullSwipe }
                ) {
                    ForEach(actions.filter { $0.edge == "leading" }) { item in
                        contextualButton(item)
                    }
                }
        }
        .clipped()
        .contentShape(Rectangle())
        .simultaneousGesture(
            DragGesture(minimumDistance: 18)
                .onEnded { value in
                    if value.translation.width < -18, !actions.isEmpty {
                        withAnimation(.easeOut(duration: 0.2)) { fallbackRevealed = true }
                    } else if value.translation.width > 18 {
                        withAnimation(.easeOut(duration: 0.2)) { fallbackRevealed = false }
                    }
                }
        )
    }

    @ViewBuilder private func contextualButton(_ item: HTMLToIOSContextualActionSpec) -> some View {
        Button(role: item.role == "destructive" ? .destructive : nil) {
            store.perform(item.action)
        } label: {
            if let systemImage = item.systemImage {
                Label(item.title, systemImage: systemImage)
            } else {
                Text(item.title)
            }
        }
        .tint(item.tint.map { Color(htmlToIOS: $0) } ?? (item.role == "destructive" ? .red : .accentColor))
        .accessibilityIdentifier(item.id)
    }
}

private struct HTMLToIOSWrappingLayout: Layout {
    let horizontalSpacing: CGFloat
    let verticalSpacing: CGFloat

    private func rows(proposal: ProposedViewSize, subviews: Subviews) -> [[(Int, CGSize)]] {
        let availableWidth = proposal.width ?? .greatestFiniteMagnitude
        var result: [[(Int, CGSize)]] = [[]]
        var currentWidth: CGFloat = 0
        for (index, subview) in subviews.enumerated() {
            let size = subview.sizeThatFits(.unspecified)
            let proposedWidth = currentWidth + (result.last?.isEmpty == false ? horizontalSpacing : 0) + size.width
            if proposedWidth > availableWidth, result.last?.isEmpty == false {
                result.append([])
                currentWidth = 0
            }
            result[result.count - 1].append((index, size))
            currentWidth += (result.last?.count ?? 0) > 1 ? horizontalSpacing + size.width : size.width
        }
        return result
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let measuredRows = rows(proposal: proposal, subviews: subviews)
        let width = measuredRows.map { row in
            row.reduce(0) { $0 + $1.1.width } + CGFloat(max(row.count - 1, 0)) * horizontalSpacing
        }.max() ?? 0
        let height = measuredRows.reduce(0) { $0 + ($1.map { $0.1.height }.max() ?? 0) }
            + CGFloat(max(measuredRows.count - 1, 0)) * verticalSpacing
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in rows(proposal: ProposedViewSize(width: bounds.width, height: proposal.height), subviews: subviews) {
            var x = bounds.minX
            let rowHeight = row.map { $0.1.height }.max() ?? 0
            for (index, size) in row {
                subviews[index].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + horizontalSpacing
            }
            y += rowHeight + verticalSpacing
        }
    }
}

struct HTMLToIOSNativeNodeView: View {
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let spec: HTMLToIOSNodeSpec
    let textOverrides: [String: String]
    let typedRegistry: HTMLToIOSTypedViewRegistry?
    let bypassTypedNodeID: String?
    @FocusState private var isInputFocused: Bool
    @Environment(\.htmlToIOSControlVisualState) private var inheritedControlVisualState

    init(
        store: HTMLToIOSGeneratedStore,
        spec: HTMLToIOSNodeSpec,
        textOverrides: [String: String] = [:],
        typedRegistry: HTMLToIOSTypedViewRegistry? = nil,
        bypassTypedNodeID: String? = nil
    ) {
        self.store = store
        self.spec = spec
        self.textOverrides = textOverrides
        self.typedRegistry = typedRegistry
        self.bypassTypedNodeID = bypassTypedNodeID
    }

    @ViewBuilder var body: some View {
        if !store.hiddenNodeIDs.contains(spec.id) && (spec.visibleWhenStateID == nil || store.flags.contains(spec.visibleWhenStateID!)) {
            if spec.id != bypassTypedNodeID,
               let registry = typedRegistry,
               let typed = registry.build(spec.id, store, spec, registry) {
                typed.modifier(HTMLToIOSContextualActionsModifier(
                    actions: spec.contextualActions,
                    store: store
                ))
            } else {
                interactiveContent
                    .modifier(HTMLToIOSContextualActionsModifier(
                        actions: spec.contextualActions,
                        store: store
                    ))
                    .transition(.asymmetric(insertion: .opacity, removal: .move(edge: .trailing).combined(with: .opacity)))
            }
        }
    }

    @ViewBuilder private var interactiveContent: some View {
        if spec.action != nil && !isNativeControl {
            Button(action: { store.perform(spec.action) }) { styledContent }
                .buttonStyle(HTMLToIOSControlButtonStyle())
                .disabled(!spec.isEnabled)
                .modifier(HTMLToIOSAccessibilityModifier(spec: spec))
        } else {
            styledContent
                .disabled(isNativeControl && !spec.isEnabled)
                .modifier(HTMLToIOSAccessibilityModifier(spec: spec))
        }
    }

    private var styledContent: some View {
        content.modifier(HTMLToIOSStyleModifier(
            style: spec.style,
            sizeOverride: store.sizeOverrides[spec.id],
            assetName: spec.backgroundAssetName,
            foregroundOverride: selectionForeground,
            backgroundOverride: selectionBackground,
            gradientOverride: selectionGradient,
            constrainsPreferredWidth: isMeasuredText || spec.children.isEmpty || isNativeControl,
            enforcesPreferredWidth: isNativeControl && spec.style.preservesIntrinsicWidth == true,
            calibratesTextLineBox: isTextBearingNode,
            calibratesFirstBaseline: isPureTextNode && hasReliableFontMetrics
        ))
        .overlay {
            ZStack {
                ForEach(spec.overlayChildren) { child in
                    HTMLToIOSNativeNodeView(
                        store: store,
                        spec: child,
                        textOverrides: textOverrides,
                        typedRegistry: typedRegistry,
                        bypassTypedNodeID: bypassTypedNodeID
                    )
                }
            }
        }
        .modifier(HTMLToIOSOverlayClipModifier(style: spec.style))
        .modifier(HTMLToIOSMotionModifier(motions: spec.motions))
        .overlay {
            if let state = activeControlVisualStyle,
               let borderColor = state.borderColor,
               (state.borderWidth ?? 0) > 0 {
                RoundedRectangle(cornerRadius: state.cornerRadius ?? spec.style.cornerRadius ?? 0)
                    .stroke(Color(htmlToIOS: borderColor), lineWidth: state.borderWidth ?? 0)
                    .allowsHitTesting(false)
            }
        }
        .scaleEffect(activeControlVisualStyle?.scale ?? 1)
        .opacity(activeControlVisualStyle?.opacity ?? 1)
        .shadow(
            color: Color(htmlToIOS: activeControlVisualStyle?.shadowColor)
                .opacity(activeControlVisualStyle?.shadowColor == nil ? 0 : 1),
            radius: activeControlVisualStyle?.shadowRadius ?? 0,
            x: activeControlVisualStyle?.shadowOffsetX ?? 0,
            y: activeControlVisualStyle?.shadowOffsetY ?? 0
        )
        .modifier(HTMLToIOSMarginModifier(style: spec.style))
    }

    private var selectionForeground: String? {
        if let value = activeControlVisualStyle?.foreground { return value }
        guard spec.selectionStateID != nil else { return nil }
        return store.isSelected(spec) ? spec.selectedForeground : spec.unselectedForeground
    }
    private var selectionBackground: String? {
        if let value = activeControlVisualStyle?.background { return value }
        guard spec.selectionStateID != nil else { return nil }
        return store.isSelected(spec) ? spec.selectedBackground : spec.unselectedBackground
    }
    private var selectionGradient: [String]? {
        if let value = activeControlVisualStyle?.gradientColors, !value.isEmpty { return value }
        guard spec.selectionStateID != nil else { return nil }
        return store.isSelected(spec) ? spec.selectedGradientColors : spec.unselectedGradientColors
    }
    private var activeControlVisualStyle: HTMLToIOSControlVisualStateSpec? {
        let stateName: String
        if !spec.isEnabled {
            stateName = "disabled"
        } else if isInputFocused {
            stateName = "focused"
        } else if inheritedControlVisualState != "normal" {
            stateName = inheritedControlVisualState
        } else if spec.selectionStateID != nil && store.isSelected(spec) {
            stateName = "selected"
        } else {
            return nil
        }
        return spec.controlVisualStates[stateName]
    }
    private var isNativeControl: Bool {
        ["button", "link", "menu-item", "tab-item", "toggle", "switch", "checkbox",
         "radio", "slider", "stepper", "segmented-control", "select", "picker", "multi-select",
         "date-input", "color-picker", "file-input", "progress", "progress-view", "meter",
         "text-input", "search-input", "number-input", "secure-input", "text-area"].contains(spec.semantic)
    }
    private var isMeasuredText: Bool {
        ["text", "label", "heading"].contains(spec.semantic) && spec.style.textMeasureWidth != nil
    }
    private var isTextBearingNode: Bool {
        ["text", "label", "heading", "button", "link", "menu-item", "tab-item"].contains(spec.semantic)
            && (!spec.text.isEmpty || !(spec.richTextRuns ?? []).isEmpty || spec.contentItems.contains { $0.kind == "text" })
    }
    private var isPureTextNode: Bool {
        ["text", "label", "heading"].contains(spec.semantic)
            && spec.children.isEmpty
            && spec.contentItems.allSatisfy { $0.kind == "text" }
    }
    private var hasReliableFontMetrics: Bool {
        ["loaded-web-font", "system-local"].contains(spec.style.fontResolutionStatus)
    }

    private var inputPrompt: Text {
        let placeholder = spec.textBehavior?.placeholderStyle
        return Text(spec.placeholder)
            .font(htmlToIOSFont(
                size: placeholder?.fontSize ?? spec.style.fontSize ?? 16,
                weight: placeholder?.fontWeight ?? spec.style.fontWeight,
                design: spec.style.fontDesign,
                nativeName: spec.style.fontNativeName,
                style: spec.style.fontStyle
            ))
            .foregroundColor(
                Color(htmlToIOS: placeholder?.foreground ?? spec.style.foreground)
                    .opacity(placeholder?.opacity ?? (placeholder?.foreground == nil ? 0.5 : 1))
            )
            .tracking(placeholder?.letterSpacing ?? spec.style.letterSpacing ?? 0)
    }

    @ViewBuilder private var content: some View {
        if effectiveScrollAxis != "none" && spec.semantic != "carousel" && spec.semantic != "scroll" {
            scrollContainer
        } else {
          switch spec.semantic {
        case "button", "link", "menu-item", "tab-item":
            if spec.action != nil {
                Button(action: { store.perform(spec.action) }) { buttonContent }
                    .buttonStyle(HTMLToIOSControlButtonStyle())
                    .disabled(!spec.isEnabled)
            } else {
                buttonContent
            }
        case "text-field", "input", "search-field", "text-input", "search-input", "number-input":
            TextField(
                "",
                text: store.binding(
                    for: spec.id,
                    initialValue: spec.textBehavior?.initialValue ?? spec.text,
                    maxLength: spec.textBehavior?.maxLength
                ),
                prompt: inputPrompt
            )
            .textFieldStyle(.plain)
            .keyboardType(htmlToIOSKeyboardType(spec.textBehavior?.keyboardType))
            .textContentType(htmlToIOSTextContentType(spec.textBehavior?.contentType))
            .submitLabel(htmlToIOSSubmitLabel(spec.textBehavior?.returnKey ?? spec.textBehavior?.submitLabel))
            .modifier(HTMLToIOSInputPolicyModifier(behavior: spec.textBehavior))
            .focused($isInputFocused)
            .onSubmit { store.perform(spec.action) }
            .onAppear {
                if spec.textBehavior?.autofocus == true { isInputFocused = true }
            }
            .disabled(!spec.isEnabled || spec.textBehavior?.editable == false || spec.textBehavior?.enabled == false)
        case "secure-field", "secure-input":
            SecureField(
                "",
                text: store.binding(
                    for: spec.id,
                    initialValue: spec.textBehavior?.initialValue ?? "",
                    maxLength: spec.textBehavior?.maxLength
                ),
                prompt: inputPrompt
            )
            .textFieldStyle(.plain)
            .keyboardType(htmlToIOSKeyboardType(spec.textBehavior?.keyboardType))
            .textContentType(htmlToIOSTextContentType(spec.textBehavior?.contentType))
            .submitLabel(htmlToIOSSubmitLabel(spec.textBehavior?.returnKey ?? spec.textBehavior?.submitLabel))
            .modifier(HTMLToIOSInputPolicyModifier(behavior: spec.textBehavior))
            .focused($isInputFocused)
            .onSubmit { store.perform(spec.action) }
            .onAppear {
                if spec.textBehavior?.autofocus == true { isInputFocused = true }
            }
            .disabled(!spec.isEnabled || spec.textBehavior?.editable == false || spec.textBehavior?.enabled == false)
        case "text-area":
            let value = store.binding(
                for: spec.id,
                initialValue: spec.textBehavior?.initialValue ?? spec.text,
                maxLength: spec.textBehavior?.maxLength
            )
            ZStack(alignment: .topLeading) {
                if value.wrappedValue.isEmpty && !spec.placeholder.isEmpty {
                    inputPrompt
                        .allowsHitTesting(false)
                }
                TextEditor(text: value)
                    .scrollContentBackground(.hidden)
                    .padding(.horizontal, -5)
                    .padding(.vertical, -8)
                    .keyboardType(htmlToIOSKeyboardType(spec.textBehavior?.keyboardType))
                    .modifier(HTMLToIOSInputPolicyModifier(behavior: spec.textBehavior))
                    .focused($isInputFocused)
                    .onAppear {
                        if spec.textBehavior?.autofocus == true { isInputFocused = true }
                    }
                    .disabled(!spec.isEnabled || spec.textBehavior?.editable == false || spec.textBehavior?.enabled == false)
            }
        case "switch", "toggle":
            Toggle(spec.text, isOn: store.flagBinding(for: spec.id))
        case "checkbox":
            Toggle(spec.text, isOn: store.flagBinding(for: spec.id))
                .toggleStyle(HTMLToIOSCheckboxToggleStyle())
        case "radio":
            Toggle(spec.text, isOn: store.flagBinding(for: spec.id))
                .toggleStyle(HTMLToIOSRadioToggleStyle())
        case "slider":
            let config = spec.controlConfig
            Slider(
                value: store.numericBinding(
                    for: spec.id,
                    initialValue: Double(config?.value ?? "") ?? config?.minimum ?? 0
                ),
                in: (config?.minimum ?? 0)...max(config?.maximum ?? 100, config?.minimum ?? 0),
                step: config?.step ?? 1
            )
        case "stepper":
            let config = spec.controlConfig
            Stepper(
                spec.text,
                value: store.numericBinding(
                    for: spec.id,
                    initialValue: Double(config?.value ?? "") ?? config?.minimum ?? 0
                ),
                in: (config?.minimum ?? 0)...max(config?.maximum ?? 100, config?.minimum ?? 0),
                step: config?.step ?? 1
            )
        case "segmented-control":
            let options = spec.controlConfig?.options ?? []
            let initial = options.first(where: \.selected)?.id ?? options.first?.id ?? ""
            Picker(
                spec.text,
                selection: store.selectionBinding(for: spec.id, initialValue: initial)
            ) {
                ForEach(options) { option in Text(option.title).tag(option.id) }
            }
            .pickerStyle(.segmented)
        case "select", "picker":
            let options = spec.controlConfig?.options ?? []
            let initial = options.first(where: \.selected)?.id ?? options.first?.id ?? ""
            Picker(
                spec.text,
                selection: store.selectionBinding(for: spec.id, initialValue: initial)
            ) {
                ForEach(options) { option in Text(option.title).tag(option.id) }
            }
            .pickerStyle(.menu)
        case "multi-select":
            let options = spec.controlConfig?.options ?? []
            Menu {
                ForEach(options) { option in
                    Button(option.title) {
                        let key = spec.id + "|" + option.id
                        if store.flags.contains(key) { store.flags.remove(key) } else { store.flags.insert(key) }
                    }
                }
            } label: {
                Text(spec.text.isEmpty ? (options.first?.title ?? "") : spec.text)
            }
        case "date-input":
            let value = spec.controlConfig?.value ?? ""
            let components: DatePickerComponents = {
                switch spec.controlConfig?.inputType {
                case "time": return [.hourAndMinute]
                case "datetime-local": return [.date, .hourAndMinute]
                default: return [.date]
                }
            }()
            DatePicker(
                spec.text,
                selection: store.dateBinding(for: spec.id, initialValue: value),
                displayedComponents: components
            )
            .labelsHidden()
        case "color-picker":
            ColorPicker(
                spec.text,
                selection: store.colorBinding(
                    for: spec.id,
                    initialValue: spec.controlConfig?.value.isEmpty == false
                        ? spec.controlConfig?.value
                        : spec.style.foreground
                )
            )
        case "file-input":
            Button(action: { store.perform(spec.action) }) {
                buttonContent
            }
            .buttonStyle(HTMLToIOSControlButtonStyle())
            .disabled(!spec.isEnabled)
        case "progress", "progress-view", "meter":
            let config = spec.controlConfig
            let minimum = config?.minimum ?? 0
            let maximum = config?.maximum ?? 1
            let value = Double(config?.value ?? "") ?? minimum
            ProgressView(
                value: max(value - minimum, 0),
                total: max(maximum - minimum, 0.0001)
            )
        case "carousel":
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: verticalAlignment, spacing: contentSpacing) { dynamicOrOrderedContent }
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
            if spec.textBehavior?.nativeControl == "text-view", spec.textBehavior?.selectable == true {
                styledText(displayValue).textSelection(.enabled)
            } else if let runs = spec.richTextRuns, !runs.isEmpty {
                HTMLToIOSRichTextView(runs: runs, style: spec.style)
            } else if spec.contentItems.isEmpty {
                styledText(displayValue)
            } else if spec.axis == "vertical" {
                VStack(alignment: horizontalAlignment, spacing: contentSpacing) {
                    dynamicOrOrderedContent
                }
            } else {
                HStack(alignment: verticalAlignment, spacing: contentSpacing) {
                    dynamicOrOrderedContent
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
        switch effectiveScrollAxis {
        case "horizontal":
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: verticalAlignment, spacing: contentSpacing) { dynamicOrOrderedContent }
                    .fixedSize(horizontal: true, vertical: false)
            }
            .clipped()
        case "both":
            ScrollView([.horizontal, .vertical]) { childContent }
                .scrollDismissesKeyboard(.interactively)
                .clipped()
        case "none":
            childContent
        default:
            ScrollView(.vertical, showsIndicators: true) {
                childContent.frame(maxWidth: .infinity, alignment: .topLeading)
            }
            .scrollDismissesKeyboard(.interactively)
            .clipped()
        }
    }

    private var effectiveScrollAxis: String {
        store.scrollAxisOverrides[spec.id] ?? spec.style.scrollAxis ?? (spec.semantic == "scroll" ? "vertical" : "none")
    }

    @ViewBuilder private var buttonContent: some View {
        if spec.axis == "grid" {
            LazyVGrid(columns: gridColumns, spacing: spec.style.rowSpacing ?? spec.style.spacing ?? 0) { dynamicOrOrderedContent }
        } else if spec.axis == "vertical" {
            VStack(alignment: .center, spacing: contentSpacing) { dynamicOrOrderedContent }
        } else {
            HStack(alignment: .center, spacing: contentSpacing) { dynamicOrOrderedContent }
        }
    }

    private var displayValue: String {
        if let value = textOverrides[spec.id] { return value }
        if let stateID = spec.selectionCountStateID,
           let initial = spec.selectionCountInitial,
           let total = spec.selectionCountTotal {
            return "\(store.selectionCounts[stateID] ?? initial) / \(total)"
        }
        return store.feedbackText[spec.id] ?? spec.text
    }

    @ViewBuilder private var childContent: some View {
        if spec.nativeContainerKind == "compositional-collection" {
            LazyVStack(alignment: horizontalAlignment, spacing: contentSpacing) {
                dynamicOrDistributedContent
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
        } else if spec.nativeContainerKind == "table-view" {
            LazyVStack(alignment: horizontalAlignment, spacing: contentSpacing) {
                dynamicOrDistributedContent
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
        } else if spec.nativeContainerKind == "collection-view", effectiveScrollAxis == "horizontal" {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: verticalAlignment, spacing: contentSpacing) {
                    dynamicOrOrderedContent
                }
            }
        } else if spec.nativeContainerKind == "collection-view" {
            LazyVGrid(columns: gridColumns, spacing: spec.style.rowSpacing ?? spec.style.spacing ?? 0) {
                dynamicOrOrderedContent
            }
        } else if spec.style.layoutAlgorithm == "wrapping-stack" {
            HTMLToIOSWrappingLayout(
                horizontalSpacing: spec.style.columnSpacing ?? spec.style.spacing ?? 0,
                verticalSpacing: spec.style.rowSpacing ?? spec.style.spacing ?? 0
            ) {
                dynamicOrOrderedContent
            }
        } else if spec.axis == "horizontal" {
            HStack(alignment: verticalAlignment, spacing: contentSpacing) { dynamicOrDistributedContent }
                .frame(maxWidth: fillsAvailableWidth ? .infinity : nil, alignment: horizontalFrameAlignment)
        } else if spec.axis == "grid" {
            LazyVGrid(columns: gridColumns, spacing: spec.style.rowSpacing ?? spec.style.spacing ?? 0) { dynamicOrOrderedContent }
        } else if spec.axis == "overlay" {
            ZStack(alignment: .center) {
                ForEach(spec.children) { child in
                    HTMLToIOSNativeNodeView(
                        store: store,
                        spec: child,
                        textOverrides: textOverrides,
                        typedRegistry: typedRegistry,
                        bypassTypedNodeID: bypassTypedNodeID
                    )
                }
            }
                .frame(width: overlayWidth, height: overlayHeight)
        } else {
            VStack(alignment: horizontalAlignment, spacing: contentSpacing) { dynamicOrDistributedContent }
                .frame(maxWidth: fillsAvailableWidth ? .infinity : nil, alignment: verticalFrameAlignment)
        }
    }

    @ViewBuilder private var dynamicOrOrderedContent: some View {
        if let items = store.contentOverrides[spec.id], !items.isEmpty {
            ForEach(items) { item in dynamicContentItem(item) }
        } else {
            orderedContentItems
        }
    }

    @ViewBuilder private var dynamicOrDistributedContent: some View {
        if let items = store.contentOverrides[spec.id], !items.isEmpty {
            ForEach(items) { item in dynamicContentItem(item) }
        } else {
            distributedContentItems
        }
    }

    @ViewBuilder private func dynamicContentItem(_ item: HTMLToIOSDynamicContentItemSpec) -> some View {
        if let template = spec.children.first(where: { $0.id == item.templateNodeID }) ?? spec.children.first {
            HTMLToIOSNativeNodeView(
                store: store,
                spec: template,
                textOverrides: item.textByNodeID,
                typedRegistry: typedRegistry,
                bypassTypedNodeID: bypassTypedNodeID
            )
        }
    }

    private var fillsAvailableWidth: Bool { (spec.style.widthFraction ?? 0) > 0.88 }
    private var gridColumns: [GridItem] {
        let spacing = spec.style.columnSpacing ?? spec.style.spacing ?? 0
        if let widths = spec.style.gridColumnWidths, !widths.isEmpty {
            return widths.map { width in
                width.map { GridItem(.fixed($0), spacing: spacing) }
                    ?? GridItem(.flexible(), spacing: spacing)
            }
        }
        return Array(
            repeating: GridItem(.flexible(), spacing: spacing),
            count: max(spec.style.gridColumnCount ?? 2, 1)
        )
    }
    private var overlayWidth: CGFloat? { spec.style.preferredWidth.map { CGFloat($0) } }
    private var overlayHeight: CGFloat? { spec.style.preferredHeight.map { CGFloat($0) } }
    private var distributesChildren: Bool { spec.style.justifyContent == "space-between" }
    private var usesMeasuredContentSpacing: Bool {
        spec.contentItems.dropFirst().contains { $0.gapBefore != nil || $0.flexibleGapBefore == true }
    }
    private var contentSpacing: Double {
        usesMeasuredContentSpacing ? 0 : (spec.style.spacing ?? 0)
    }
    private var horizontalAlignment: HorizontalAlignment {
        switch spec.style.alignItems {
        case "center": return .center
        case "flex-end", "end": return .trailing
        default: return .leading
        }
    }
    private var verticalAlignment: VerticalAlignment {
        if spec.style.baselineAligned == true { return .firstTextBaseline }
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

    @ViewBuilder private var distributedContentItems: some View {
        let indexed = Array(spec.contentItems.enumerated())
        ForEach(indexed, id: \.element.id) { index, item in
            contentItemGap(item)
            contentItem(item)
            if distributesChildren && !usesMeasuredContentSpacing && index < indexed.count - 1 {
                Spacer(minLength: spec.style.spacing ?? 0)
            }
        }
    }

    @ViewBuilder private var orderedContentItems: some View {
        ForEach(spec.contentItems) { item in
            contentItemGap(item)
            contentItem(item)
        }
    }

    @ViewBuilder private func contentItem(_ item: HTMLToIOSContentItemSpec) -> some View {
        if item.kind == "text" {
            if item.singleLine == true, let width = item.preferredWidth, width > 0 {
                styledText(contentItemText(item))
                    .lineLimit(1)
                    .allowsTightening(true)
                    .minimumScaleFactor(0.7)
                    .frame(width: width, alignment: textItemFrameAlignment)
            } else {
                styledText(contentItemText(item))
            }
        } else if let childID = item.childID,
                  let child = spec.children.first(where: { $0.id == childID }) {
            HTMLToIOSNativeNodeView(
                store: store,
                spec: child,
                textOverrides: textOverrides,
                typedRegistry: typedRegistry,
                bypassTypedNodeID: bypassTypedNodeID
            )
                .frame(maxWidth: (child.style.flexGrow ?? 0) > 0 ? .infinity : nil)
        }
    }

    @ViewBuilder private func contentItemGap(_ item: HTMLToIOSContentItemSpec) -> some View {
        let gap = item.gapBefore ?? 0
        if item.flexibleGapBefore == true {
            Spacer(minLength: gap)
        } else if gap > 0 {
            if spec.axis == "vertical" {
                Color.clear.frame(width: 0, height: gap)
            } else {
                Color.clear.frame(width: gap, height: 0)
            }
        }
    }

    private var textItemFrameAlignment: Alignment {
        switch spec.style.textAlignment {
        case "center": return .center
        case "end", "right": return .trailing
        default: return .leading
        }
    }

    private func contentItemText(_ item: HTMLToIOSContentItemSpec) -> String {
        let textItemCount = spec.contentItems.filter { $0.kind == "text" }.count
        if textItemCount == 1, (item.text ?? "") == spec.text {
            return displayValue
        }
        return item.text ?? ""
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
    let typedRegistry: HTMLToIOSTypedViewRegistry?

    @ViewBuilder var body: some View {
        if ["static-view", "static-grid", "static-list"].contains(screen.contentContainer.kind) {
            HTMLToIOSNativeNodeView(store: store, spec: screen.root, typedRegistry: typedRegistry)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .accessibilityIdentifier(screen.root.id)
                .background {
                    Color(htmlToIOS: screen.root.style.background)
                        .ignoresSafeArea()
                }
        } else {
            ScrollViewReader { proxy in
                ScrollView(.vertical) {
                    HTMLToIOSNativeNodeView(store: store, spec: scrollRoot, typedRegistry: typedRegistry)
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
    let typedRegistry: HTMLToIOSTypedViewRegistry?

    var body: some View {
        insetContent
            .task(id: screen.id) { await performAutomaticActions() }
    }

    private var scrollContent: some View {
        HTMLToIOSGeneratedScrollContent(store: store, screen: screen, typedRegistry: typedRegistry)
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
        if let sourceStatusBarHeight = screen.sourceStatusBarHeight, sourceStatusBarHeight > 0 {
            navigationContent
                .padding(.top, sourceStatusBarHeight)
                .ignoresSafeArea(.container, edges: .top)
        } else {
            navigationContent
        }
    }

    @ViewBuilder private var insetContent: some View {
        if screen.safeArea.owner == "system" {
            if screen.bottomBarPlacement == "viewport-overlay" {
                GeometryReader { proxy in
                    chromeAlignedNavigationContent
                        .safeAreaInset(edge: .top, spacing: 0) { topBarContent }
                        .frame(width: proxy.size.width, height: proxy.size.height)
                        .overlay(alignment: .bottom) {
                            bottomBarContent
                                .offset(y: proxy.safeAreaInsets.bottom)
                        }
                }
            } else {
                chromeAlignedNavigationContent
                    .safeAreaInset(edge: .top, spacing: 0) { topBarContent }
                    .safeAreaInset(edge: .bottom, spacing: 0) { bottomBarContent }
            }
        } else {
            chromeAlignedNavigationContent
                .ignoresSafeArea(.container)
                .overlay(alignment: .top) { topBarContent }
                .overlay(alignment: .bottom) { bottomBarContent }
        }
    }

    @ViewBuilder private var topBarContent: some View {
        if let topBar = screen.topBar {
            HTMLToIOSNativeNodeView(store: store, spec: topBar, typedRegistry: typedRegistry)
                .frame(maxWidth: .infinity)
        }
    }

    @ViewBuilder private var bottomBarContent: some View {
        if let bottomBar = screen.bottomBar {
            HTMLToIOSNativeNodeView(store: store, spec: bottomBar, typedRegistry: typedRegistry)
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
                let globalFrame = proxy.frame(in: .global)
                let width = min(CGFloat(rect.indices.contains(2) ? rect[2] : 0), proxy.size.width)
                let measuredHeight = store.sizeOverrides[presentation.node.id]?.height ?? (rect.indices.contains(3) ? rect[3] : 0)
                let height = min(CGFloat(measuredHeight), proxy.size.height)
                let localX = CGFloat(rect.indices.contains(0) ? rect[0] : 0) - globalFrame.minX
                let localY = CGFloat(rect.indices.contains(1) ? rect[1] : 0) - globalFrame.minY
                let centerX = min(max(localX + width / 2, width / 2), proxy.size.width - width / 2)
                let centerY = min(max(localY + height / 2, height / 2), proxy.size.height - height / 2)
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

private final class HTMLToIOSWrappingView: UIView {
    let horizontalSpacing: CGFloat
    let verticalSpacing: CGFloat

    init(views: [UIView], horizontalSpacing: CGFloat, verticalSpacing: CGFloat) {
        self.horizontalSpacing = horizontalSpacing
        self.verticalSpacing = verticalSpacing
        super.init(frame: .zero)
        views.forEach {
            $0.translatesAutoresizingMaskIntoConstraints = true
            addSubview($0)
        }
    }

    required init?(coder: NSCoder) { nil }

    private func measuredSize(for view: UIView) -> CGSize {
        let intrinsic = view.systemLayoutSizeFitting(UIView.layoutFittingCompressedSize)
        return CGSize(
            width: max(intrinsic.width, view.intrinsicContentSize.width > 0 ? view.intrinsicContentSize.width : 0),
            height: max(intrinsic.height, view.intrinsicContentSize.height > 0 ? view.intrinsicContentSize.height : 0)
        )
    }

    private func arrange(width: CGFloat, applyFrames: Bool) -> CGSize {
        let availableWidth = width > 0 ? width : .greatestFiniteMagnitude
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var measuredWidth: CGFloat = 0
        for view in subviews {
            let size = measuredSize(for: view)
            if x > 0, x + size.width > availableWidth {
                x = 0
                y += rowHeight + verticalSpacing
                rowHeight = 0
            }
            if applyFrames { view.frame = CGRect(origin: CGPoint(x: x, y: y), size: size) }
            x += size.width + horizontalSpacing
            measuredWidth = max(measuredWidth, max(x - horizontalSpacing, 0))
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: min(measuredWidth, availableWidth), height: y + rowHeight)
    }

    override func sizeThatFits(_ size: CGSize) -> CGSize { arrange(width: size.width, applyFrames: false) }
    override var intrinsicContentSize: CGSize { arrange(width: bounds.width, applyFrames: false) }
    override func layoutSubviews() {
        super.layoutSubviews()
        _ = arrange(width: bounds.width, applyFrames: true)
    }
}

private extension UIColor {
    convenience init?(htmlToIOS value: String?) {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("#") {
            let hex = String(trimmed.dropFirst())
            guard hex.count == 6 || hex.count == 8,
                  let number = UInt64(hex, radix: 16) else { return nil }
            let hasAlpha = hex.count == 8
            let red = hasAlpha ? (number >> 24) & 0xFF : (number >> 16) & 0xFF
            let green = hasAlpha ? (number >> 16) & 0xFF : (number >> 8) & 0xFF
            let blue = hasAlpha ? (number >> 8) & 0xFF : number & 0xFF
            let alpha = hasAlpha ? number & 0xFF : 0xFF
            self.init(
                red: CGFloat(red) / 255,
                green: CGFloat(green) / 255,
                blue: CGFloat(blue) / 255,
                alpha: CGFloat(alpha) / 255
            )
            return
        }
        let parts = value.split(whereSeparator: { !$0.isNumber && $0 != "." }).compactMap { Double($0) }
        guard parts.count >= 3 else { return nil }
        self.init(red: parts[0] / 255, green: parts[1] / 255, blue: parts[2] / 255,
                  alpha: parts.count > 3 ? parts[3] : 1)
    }
}

private enum HTMLToIOSUIKitDateParser {
    static func date(from value: String) -> Date {
        if let date = ISO8601DateFormatter().date(from: value) { return date }
        for format in ["yyyy-MM-dd", "yyyy-MM-dd'T'HH:mm", "HH:mm"] {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(secondsFromGMT: 0)
            formatter.dateFormat = format
            if let date = formatter.date(from: value) { return date }
        }
        return Date(timeIntervalSinceReferenceDate: 0)
    }
}

final class HTMLToIOSUIKitState {
    var values: [String: String] = [:]
    var flags = Set<String>()
    var hiddenNodeIDs = Set<String>()
    var selectedByState: [String: String] = [:]
    var selectionOverrides: [String: Bool] = [:]
    var selectionCounts: [String: Int] = [:]
    var contentOverrides: [String: [HTMLToIOSDynamicContentItemSpec]] = [:]
    var sizeOverrides: [String: HTMLToIOSSizeOverrideSpec] = [:]
    var scrollAxisOverrides: [String: String] = [:]

    func isSelected(_ spec: HTMLToIOSNodeSpec) -> Bool {
        guard let stateID = spec.selectionStateID else { return false }
        if let selected = selectedByState[stateID] { return selected == spec.id }
        return selectionOverrides[stateID + "|" + spec.id] ?? spec.isInitiallySelected ?? false
    }

    func perform(_ spec: HTMLToIOSActionSpec) {
        if let variant = spec.contentVariant {
            contentOverrides[variant.targetNodeID] = variant.items
            for override in variant.sizeOverrides { sizeOverrides[override.nodeID] = override }
            scrollAxisOverrides[variant.targetNodeID] = variant.scrollAxisOverride
        }
        let stateID = spec.targetStateID ?? spec.target
        if !spec.deltaRemoveNodeIDs.isEmpty {
            if spec.deltaRemoveNodeIDs.allSatisfy({ hiddenNodeIDs.contains($0) }) {
                spec.deltaRemoveNodeIDs.forEach { hiddenNodeIDs.remove($0) }
            } else {
                spec.deltaRemoveNodeIDs.forEach { hiddenNodeIDs.insert($0) }
            }
        }
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

final class HTMLToIOSInsetTextField: UITextField {
    var contentInsets = UIEdgeInsets.zero {
        didSet { setNeedsLayout() }
    }

    override func textRect(forBounds bounds: CGRect) -> CGRect {
        bounds.inset(by: contentInsets)
    }

    override func editingRect(forBounds bounds: CGRect) -> CGRect {
        bounds.inset(by: contentInsets)
    }

    override func placeholderRect(forBounds bounds: CGRect) -> CGRect {
        bounds.inset(by: contentInsets)
    }
}

final class HTMLToIOSManagedTextView: UITextView, UITextViewDelegate {
    var maxLength: Int?
    var onValueChanged: ((String) -> Void)?
    var visualStateDidChange: ((String) -> Void)?
    private let placeholderLabel = UILabel()
    private var placeholderTopConstraint: NSLayoutConstraint?
    private var placeholderLeadingConstraint: NSLayoutConstraint?
    private var placeholderTrailingConstraint: NSLayoutConstraint?

    var placeholderAttributedText: NSAttributedString? {
        didSet { placeholderLabel.attributedText = placeholderAttributedText }
    }

    var contentInsets = UIEdgeInsets.zero {
        didSet {
            textContainerInset = contentInsets
            placeholderTopConstraint?.constant = contentInsets.top
            placeholderLeadingConstraint?.constant = contentInsets.left + textContainer.lineFragmentPadding
            placeholderTrailingConstraint?.constant = -(contentInsets.right + textContainer.lineFragmentPadding)
        }
    }

    override init(frame: CGRect, textContainer: NSTextContainer?) {
        super.init(frame: frame, textContainer: textContainer)
        configurePlaceholder()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        configurePlaceholder()
    }

    private func configurePlaceholder() {
        delegate = self
        placeholderLabel.numberOfLines = 0
        placeholderLabel.translatesAutoresizingMaskIntoConstraints = false
        addSubview(placeholderLabel)
        placeholderTopConstraint = placeholderLabel.topAnchor.constraint(equalTo: topAnchor)
        placeholderLeadingConstraint = placeholderLabel.leadingAnchor.constraint(equalTo: leadingAnchor)
        placeholderTrailingConstraint = placeholderLabel.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor)
        NSLayoutConstraint.activate([
            placeholderTopConstraint!,
            placeholderLeadingConstraint!,
            placeholderTrailingConstraint!,
        ])
    }

    func refreshPlaceholder() {
        placeholderLabel.isHidden = !text.isEmpty
    }

    func textViewDidChange(_ textView: UITextView) {
        if markedTextRange == nil, let maxLength, maxLength >= 0, text.count > maxLength {
            text = String(text.prefix(maxLength))
        }
        refreshPlaceholder()
        onValueChanged?(text)
    }

    func textViewDidBeginEditing(_ textView: UITextView) {
        visualStateDidChange?("focused")
    }

    func textViewDidEndEditing(_ textView: UITextView) {
        visualStateDidChange?("normal")
    }
}

final class HTMLToIOSStatefulButton: UIButton {
    var visualStateDidChange: ((String) -> Void)?

    override var isHighlighted: Bool {
        didSet { notifyVisualState() }
    }
    override var isSelected: Bool {
        didSet { notifyVisualState() }
    }
    override var isEnabled: Bool {
        didSet { notifyVisualState() }
    }

    private func notifyVisualState() {
        visualStateDidChange?(!isEnabled ? "disabled" : (isHighlighted ? "pressed" : (isSelected ? "selected" : "normal")))
    }
}

final class HTMLToIOSStatefulControl: UIControl {
    var visualStateDidChange: ((String) -> Void)?

    override var isHighlighted: Bool {
        didSet { notifyVisualState() }
    }
    override var isSelected: Bool {
        didSet { notifyVisualState() }
    }
    override var isEnabled: Bool {
        didSet { notifyVisualState() }
    }

    private func notifyVisualState() {
        visualStateDidChange?(!isEnabled ? "disabled" : (isHighlighted ? "pressed" : (isSelected ? "selected" : "normal")))
    }
}

class HTMLToIOSGeneratedTableCell: UITableViewCell {
    static let reuseIdentifier = "HTMLToIOSGeneratedTableCell"

    func install(_ generatedView: UIView) {
        contentView.subviews.forEach { $0.removeFromSuperview() }
        generatedView.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(generatedView)
        NSLayoutConstraint.activate([
            generatedView.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            generatedView.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            generatedView.topAnchor.constraint(equalTo: contentView.topAnchor),
            generatedView.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
        ])
    }
}

private final class HTMLToIOSGeneratedTableView: UITableView, UITableViewDataSource {
    private let itemSpecs: [HTMLToIOSNodeSpec]
    private let render: (HTMLToIOSNodeSpec) -> UIView
    private let cellType: (HTMLToIOSNodeSpec) -> HTMLToIOSGeneratedTableCell.Type

    init(
        itemSpecs: [HTMLToIOSNodeSpec],
        render: @escaping (HTMLToIOSNodeSpec) -> UIView,
        cellType: @escaping (HTMLToIOSNodeSpec) -> HTMLToIOSGeneratedTableCell.Type
    ) {
        self.itemSpecs = itemSpecs
        self.render = render
        self.cellType = cellType
        super.init(frame: .zero, style: .plain)
        dataSource = self
        separatorStyle = .none
        rowHeight = UITableView.automaticDimension
        estimatedRowHeight = 72
        backgroundColor = .clear
        for spec in itemSpecs {
            let type = cellType(spec)
            register(type, forCellReuseIdentifier: String(reflecting: type))
        }
    }

    @available(*, unavailable) required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }
    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int { itemSpecs.count }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let type = cellType(itemSpecs[indexPath.row])
        let cell = tableView.dequeueReusableCell(withIdentifier: String(reflecting: type), for: indexPath)
            as! HTMLToIOSGeneratedTableCell
        cell.selectionStyle = .none
        cell.backgroundColor = .clear
        cell.install(render(itemSpecs[indexPath.row]))
        return cell
    }
}

class HTMLToIOSGeneratedCollectionCell: UICollectionViewCell {
    static let reuseIdentifier = "HTMLToIOSGeneratedCollectionCell"

    func install(_ generatedView: UIView) {
        contentView.subviews.forEach { $0.removeFromSuperview() }
        generatedView.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(generatedView)
        NSLayoutConstraint.activate([
            generatedView.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            generatedView.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            generatedView.topAnchor.constraint(equalTo: contentView.topAnchor),
            generatedView.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
        ])
    }
}

private final class HTMLToIOSGeneratedCollectionView: UICollectionView, UICollectionViewDataSource {
    private let itemSpecs: [HTMLToIOSNodeSpec]
    private let render: (HTMLToIOSNodeSpec) -> UIView
    private let cellType: (HTMLToIOSNodeSpec) -> HTMLToIOSGeneratedCollectionCell.Type

    init(
        spec: HTMLToIOSNodeSpec,
        render: @escaping (HTMLToIOSNodeSpec) -> UIView,
        cellType: @escaping (HTMLToIOSNodeSpec) -> HTMLToIOSGeneratedCollectionCell.Type
    ) {
        self.itemSpecs = spec.children
        self.render = render
        self.cellType = cellType
        let layout = UICollectionViewFlowLayout()
        layout.scrollDirection = (spec.style.scrollAxis == "horizontal" || spec.semantic == "carousel") ? .horizontal : .vertical
        layout.minimumLineSpacing = spec.style.spacing ?? 0
        layout.minimumInteritemSpacing = spec.style.spacing ?? 0
        layout.estimatedItemSize = UICollectionViewFlowLayout.automaticSize
        super.init(frame: .zero, collectionViewLayout: layout)
        dataSource = self
        backgroundColor = .clear
        showsHorizontalScrollIndicator = layout.scrollDirection == .horizontal
        showsVerticalScrollIndicator = layout.scrollDirection == .vertical
        for item in itemSpecs {
            let type = cellType(item)
            register(type, forCellWithReuseIdentifier: String(reflecting: type))
        }
    }

    @available(*, unavailable) required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }
    func collectionView(_ collectionView: UICollectionView, numberOfItemsInSection section: Int) -> Int { itemSpecs.count }

    func collectionView(_ collectionView: UICollectionView, cellForItemAt indexPath: IndexPath) -> UICollectionViewCell {
        let type = cellType(itemSpecs[indexPath.item])
        let cell = collectionView.dequeueReusableCell(
            withReuseIdentifier: String(reflecting: type),
            for: indexPath
        ) as! HTMLToIOSGeneratedCollectionCell
        cell.install(render(itemSpecs[indexPath.item]))
        return cell
    }
}

private final class HTMLToIOSGeneratedCompositionalCollectionView: UICollectionView, UICollectionViewDataSource {
    private let sectionSpecs: [HTMLToIOSNodeSpec]
    private let render: (HTMLToIOSNodeSpec) -> UIView
    private let cellType: (HTMLToIOSNodeSpec) -> HTMLToIOSGeneratedCollectionCell.Type

    init(
        spec: HTMLToIOSNodeSpec,
        render: @escaping (HTMLToIOSNodeSpec) -> UIView,
        cellType: @escaping (HTMLToIOSNodeSpec) -> HTMLToIOSGeneratedCollectionCell.Type
    ) {
        self.sectionSpecs = spec.children
        self.render = render
        self.cellType = cellType
        let sections = spec.children
        let layout = UICollectionViewCompositionalLayout { sectionIndex, _ in
            guard sections.indices.contains(sectionIndex) else { return nil }
            let sectionSpec = sections[sectionIndex]
            let horizontal = sectionSpec.semantic == "carousel" || sectionSpec.style.scrollAxis == "horizontal"
            let columns = horizontal ? 1 : max(sectionSpec.style.gridColumnCount ?? 1, 1)
            let estimatedWidth = CGFloat(max(sectionSpec.style.preferredWidth ?? 160, 44))
            let estimatedHeight = CGFloat(max(sectionSpec.style.preferredHeight ?? 72, 44))
            let spacing = CGFloat(sectionSpec.style.spacing ?? 0)
            let itemWidth: NSCollectionLayoutDimension = horizontal
                ? .estimated(estimatedWidth)
                : .fractionalWidth(1.0 / CGFloat(columns))
            let itemSize = NSCollectionLayoutSize(
                widthDimension: itemWidth,
                heightDimension: .estimated(estimatedHeight)
            )
            let item = NSCollectionLayoutItem(layoutSize: itemSize)
            let groupSize = NSCollectionLayoutSize(
                widthDimension: horizontal ? .estimated(estimatedWidth) : .fractionalWidth(1),
                heightDimension: .estimated(estimatedHeight)
            )
            let group = NSCollectionLayoutGroup.horizontal(layoutSize: groupSize, subitems: [item])
            group.interItemSpacing = .fixed(spacing)
            let section = NSCollectionLayoutSection(group: group)
            section.interGroupSpacing = spacing
            if horizontal { section.orthogonalScrollingBehavior = .continuous }
            return section
        }
        super.init(frame: .zero, collectionViewLayout: layout)
        dataSource = self
        backgroundColor = .clear
        for section in sectionSpecs {
            for item in section.children.isEmpty ? [section] : section.children {
                let type = cellType(item)
                register(type, forCellWithReuseIdentifier: String(reflecting: type))
            }
        }
    }

    @available(*, unavailable) required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }
    func numberOfSections(in collectionView: UICollectionView) -> Int { sectionSpecs.count }

    func collectionView(_ collectionView: UICollectionView, numberOfItemsInSection section: Int) -> Int {
        guard sectionSpecs.indices.contains(section) else { return 0 }
        return max(sectionSpecs[section].children.count, 1)
    }

    func collectionView(_ collectionView: UICollectionView, cellForItemAt indexPath: IndexPath) -> UICollectionViewCell {
        let section = sectionSpecs[indexPath.section]
        let item = section.children.indices.contains(indexPath.item) ? section.children[indexPath.item] : section
        let type = cellType(item)
        let cell = collectionView.dequeueReusableCell(
            withReuseIdentifier: String(reflecting: type),
            for: indexPath
        ) as! HTMLToIOSGeneratedCollectionCell
        cell.install(render(item))
        return cell
    }
}

final class HTMLToIOSNodeRenderer {
    typealias ActionHandler = (HTMLToIOSActionSpec?) -> Void
    typealias TypedViewBuilder = (HTMLToIOSNodeSpec, HTMLToIOSNodeRenderer) -> UIView
    private let actionHandler: ActionHandler
    private let state: HTMLToIOSUIKitState
    private var typedViewBuilders: [String: TypedViewBuilder] = [:]
    private var typedTableCellTypes: [String: HTMLToIOSGeneratedTableCell.Type] = [:]
    private var typedCollectionCellTypes: [String: HTMLToIOSGeneratedCollectionCell.Type] = [:]

    init(state: HTMLToIOSUIKitState, actionHandler: @escaping ActionHandler) {
        self.state = state
        self.actionHandler = actionHandler
    }

    func registerView(nodeID: String, builder: @escaping TypedViewBuilder) {
        typedViewBuilders[nodeID] = builder
    }

    func registerTableCell(nodeID: String, type: HTMLToIOSGeneratedTableCell.Type) {
        typedTableCellTypes[nodeID] = type
    }

    func registerCollectionCell(nodeID: String, type: HTMLToIOSGeneratedCollectionCell.Type) {
        typedCollectionCellTypes[nodeID] = type
    }

    func makeView(_ spec: HTMLToIOSNodeSpec, bypassingTypedNodeID: String? = nil) -> UIView {
        if spec.id != bypassingTypedNodeID, let builder = typedViewBuilders[spec.id] {
            let typedView = builder(spec, self)
            installContextualActions(spec.contextualActions, on: typedView)
            return typedView
        }
        let view: UIView
        let effectiveScrollAxis = state.scrollAxisOverrides[spec.id] ?? spec.style.scrollAxis ?? "none"
        if spec.nativeContainerKind == "compositional-collection" {
            view = HTMLToIOSGeneratedCompositionalCollectionView(
                spec: spec,
                render: { [weak self] item in self?.makeView(item) ?? UIView() },
                cellType: { [weak self] item in self?.typedCollectionCellTypes[item.id] ?? HTMLToIOSGeneratedCollectionCell.self }
            )
        } else if spec.nativeContainerKind == "table-view" {
            view = HTMLToIOSGeneratedTableView(
                itemSpecs: spec.children,
                render: { [weak self] item in self?.makeView(item) ?? UIView() },
                cellType: { [weak self] item in self?.typedTableCellTypes[item.id] ?? HTMLToIOSGeneratedTableCell.self }
            )
        } else if spec.nativeContainerKind == "collection-view" {
            view = HTMLToIOSGeneratedCollectionView(
                spec: spec,
                render: { [weak self] item in self?.makeView(item) ?? UIView() },
                cellType: { [weak self] item in self?.typedCollectionCellTypes[item.id] ?? HTMLToIOSGeneratedCollectionCell.self }
            )
        } else if effectiveScrollAxis != "none" && spec.semantic != "carousel" && spec.semantic != "scroll" {
            view = makeScrollContainer(spec)
        } else {
          switch spec.semantic {
        case "button", "link", "menu-item", "tab-item":
            if spec.children.isEmpty && spec.overlayChildren.isEmpty {
                let button = HTMLToIOSStatefulButton(type: .system)
                button.setAttributedTitle(attributedText(displayText(spec), spec: spec), for: .normal)
                button.titleLabel?.numberOfLines = spec.style.textLineLimit ?? 0
                button.titleLabel?.lineBreakMode = lineBreakMode(spec)
                button.contentHorizontalAlignment = .leading
                button.isEnabled = spec.isEnabled
                button.addAction(UIAction { [actionHandler] _ in actionHandler(spec.action) }, for: .touchUpInside)
                view = button
            } else {
                let control = HTMLToIOSStatefulControl()
                control.isEnabled = spec.isEnabled
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
        case "text-field", "input", "search-field", "secure-field", "text-input", "search-input", "number-input", "secure-input":
            let field = HTMLToIOSInsetTextField()
            field.borderStyle = .none
            field.attributedPlaceholder = attributedPlaceholder(spec)
            field.contentInsets = contentInsets(spec)
            let initialValue = state.values[spec.id] ?? spec.textBehavior?.initialValue ?? spec.text
            field.attributedText = attributedText(initialValue, spec: spec)
            field.isSecureTextEntry = spec.semantic == "secure-field" || spec.semantic == "secure-input" || spec.textBehavior?.secure == true
            field.isEnabled = spec.isEnabled && spec.textBehavior?.enabled != false
            field.isUserInteractionEnabled = spec.textBehavior?.editable != false
            field.keyboardType = keyboardType(spec.textBehavior?.keyboardType)
            field.textContentType = textContentType(spec.textBehavior?.contentType)
            field.returnKeyType = returnKeyType(spec.textBehavior?.returnKey ?? spec.textBehavior?.submitLabel)
            field.autocapitalizationType = autocapitalizationType(spec.textBehavior?.autocapitalization)
            field.autocorrectionType = autocorrectionType(spec.textBehavior?.autocorrection)
            field.addAction(UIAction { [weak field, state] _ in
                guard let field else { return }
                if field.markedTextRange == nil, let maxLength = spec.textBehavior?.maxLength, maxLength >= 0 {
                    if field.text?.count ?? 0 > maxLength { field.text = String((field.text ?? "").prefix(maxLength)) }
                }
                state.values[spec.id] = field.text ?? ""
            }, for: .editingChanged)
            if spec.action != nil {
                field.addAction(UIAction { [actionHandler] _ in actionHandler(spec.action) }, for: .primaryActionTriggered)
            }
            if spec.textBehavior?.autofocus == true {
                DispatchQueue.main.async { [weak field] in field?.becomeFirstResponder() }
            }
            view = field
        case "text-area":
            let textView = HTMLToIOSManagedTextView()
            let initialValue = state.values[spec.id] ?? spec.textBehavior?.initialValue ?? spec.text
            textView.attributedText = attributedText(initialValue, spec: spec)
            textView.textContainer.lineFragmentPadding = 0
            textView.contentInsets = contentInsets(spec)
            textView.backgroundColor = .clear
            textView.isEditable = spec.textBehavior?.editable == true
            textView.isSelectable = spec.textBehavior?.selectable != false
            textView.isScrollEnabled = spec.textBehavior?.scrollable != false
            textView.isUserInteractionEnabled = spec.isEnabled && spec.textBehavior?.enabled != false
            textView.keyboardType = keyboardType(spec.textBehavior?.keyboardType)
            textView.textContentType = textContentType(spec.textBehavior?.contentType)
            textView.returnKeyType = returnKeyType(spec.textBehavior?.returnKey ?? spec.textBehavior?.submitLabel)
            textView.autocapitalizationType = autocapitalizationType(spec.textBehavior?.autocapitalization)
            textView.autocorrectionType = autocorrectionType(spec.textBehavior?.autocorrection)
            textView.maxLength = spec.textBehavior?.maxLength
            textView.placeholderAttributedText = attributedPlaceholder(spec)
            textView.onValueChanged = { [state] value in state.values[spec.id] = value }
            textView.refreshPlaceholder()
            if spec.textBehavior?.autofocus == true {
                DispatchQueue.main.async { [weak textView] in textView?.becomeFirstResponder() }
            }
            view = textView
        case "switch", "toggle":
            let row = UIStackView()
            row.axis = .horizontal; row.spacing = 8
            let label = makeLabel(spec.text, spec: spec)
            let toggle = UISwitch()
            toggle.isOn = spec.selectionStateID == nil ? (spec.isInitiallySelected ?? false) : state.isSelected(spec)
            toggle.isEnabled = spec.isEnabled
            if spec.action != nil {
                toggle.addAction(UIAction { [actionHandler] _ in actionHandler(spec.action) }, for: .valueChanged)
            }
            row.addArrangedSubview(label); row.addArrangedSubview(toggle)
            view = row
        case "checkbox", "radio":
            let button = HTMLToIOSStatefulButton(type: .system)
            let selected = spec.isInitiallySelected ?? false
            button.isSelected = selected
            button.isEnabled = spec.isEnabled
            let offImage = spec.semantic == "radio" ? "circle" : "square"
            let onImage = spec.semantic == "radio" ? "circle.inset.filled" : "checkmark.square.fill"
            button.setImage(UIImage(systemName: offImage), for: .normal)
            button.setImage(UIImage(systemName: onImage), for: .selected)
            button.setTitle(spec.text, for: .normal)
            button.contentHorizontalAlignment = .leading
            button.addAction(UIAction { [weak button, actionHandler] _ in
                guard let button else { return }
                button.isSelected = spec.semantic == "radio" ? true : !button.isSelected
                actionHandler(spec.action)
            }, for: .touchUpInside)
            view = button
        case "slider":
            let slider = UISlider()
            let config = spec.controlConfig
            slider.minimumValue = Float(config?.minimum ?? 0)
            slider.maximumValue = Float(max(config?.maximum ?? 100, config?.minimum ?? 0))
            slider.value = Float(Double(config?.value ?? "") ?? config?.minimum ?? 0)
            slider.isEnabled = spec.isEnabled
            slider.addAction(UIAction { [state, weak slider] _ in
                state.values[spec.id] = String(slider?.value ?? 0)
            }, for: .valueChanged)
            view = slider
        case "stepper":
            let row = UIStackView()
            row.axis = .horizontal; row.spacing = 8
            if !spec.text.isEmpty { row.addArrangedSubview(makeLabel(spec.text, spec: spec)) }
            let stepper = UIStepper()
            let config = spec.controlConfig
            stepper.minimumValue = config?.minimum ?? 0
            stepper.maximumValue = max(config?.maximum ?? 100, config?.minimum ?? 0)
            stepper.stepValue = config?.step ?? 1
            stepper.value = Double(config?.value ?? "") ?? config?.minimum ?? 0
            stepper.isEnabled = spec.isEnabled
            stepper.addAction(UIAction { [state, weak stepper] _ in
                state.values[spec.id] = String(stepper?.value ?? 0)
            }, for: .valueChanged)
            row.addArrangedSubview(stepper)
            view = row
        case "segmented-control":
            let options = spec.controlConfig?.options ?? []
            let segmented = UISegmentedControl(items: options.map(\.title))
            segmented.selectedSegmentIndex = options.isEmpty
                ? UISegmentedControl.noSegment
                : (options.firstIndex(where: \.selected) ?? 0)
            segmented.isEnabled = spec.isEnabled
            segmented.addAction(UIAction { [state, weak segmented] _ in
                guard let segmented, segmented.selectedSegmentIndex >= 0,
                      segmented.selectedSegmentIndex < options.count else { return }
                state.values[spec.id] = options[segmented.selectedSegmentIndex].id
            }, for: .valueChanged)
            view = segmented
        case "select", "picker", "multi-select":
            let options = spec.controlConfig?.options ?? []
            let selected = options.first(where: \.selected) ?? options.first
            let button = HTMLToIOSStatefulButton(type: .system)
            button.setTitle(selected?.title ?? spec.text, for: .normal)
            button.contentHorizontalAlignment = .leading
            button.showsMenuAsPrimaryAction = true
            button.menu = UIMenu(children: options.map { option in
                UIAction(
                    title: option.title,
                    state: option.selected ? .on : .off
                ) { [state, weak button] _ in
                    state.values[spec.id] = option.id
                    button?.setTitle(option.title, for: .normal)
                }
            })
            button.isEnabled = spec.isEnabled
            view = button
        case "date-input":
            let picker = UIDatePicker()
            picker.preferredDatePickerStyle = .compact
            switch spec.controlConfig?.inputType {
            case "time": picker.datePickerMode = .time
            case "datetime-local": picker.datePickerMode = .dateAndTime
            default: picker.datePickerMode = .date
            }
            picker.date = HTMLToIOSUIKitDateParser.date(from: spec.controlConfig?.value ?? "")
            picker.isEnabled = spec.isEnabled
            picker.addAction(UIAction { [state, weak picker] _ in
                guard let picker else { return }
                state.values[spec.id] = ISO8601DateFormatter().string(from: picker.date)
            }, for: .valueChanged)
            view = picker
        case "color-picker":
            let colorWell = UIColorWell()
            colorWell.selectedColor = UIColor(htmlToIOS: spec.controlConfig?.value) ?? UIColor(htmlToIOS: spec.style.foreground)
            colorWell.isEnabled = spec.isEnabled
            view = colorWell
        case "file-input":
            let button = HTMLToIOSStatefulButton(type: .system)
            button.setTitle(displayText(spec), for: .normal)
            button.contentHorizontalAlignment = .leading
            button.isEnabled = spec.isEnabled
            if spec.action != nil {
                button.addAction(UIAction { [actionHandler] _ in actionHandler(spec.action) }, for: .touchUpInside)
            }
            view = button
        case "progress", "progress-view", "meter":
            let progress = UIProgressView(progressViewStyle: .default)
            let minimum = spec.controlConfig?.minimum ?? 0
            let maximum = spec.controlConfig?.maximum ?? 1
            let value = Double(spec.controlConfig?.value ?? "") ?? minimum
            progress.progress = Float(min(max((value - minimum) / max(maximum - minimum, 0.0001), 0), 1))
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
            if spec.textBehavior?.nativeControl == "text-view" {
                view = makeReadOnlyTextView(flattenedText(spec), spec: spec)
            } else {
                view = spec.contentItems.contains(where: { $0.kind == "child" })
                    ? makeStack(spec)
                    : makeLabel(flattenedText(spec), spec: spec)
            }
        default:
            let stack: UIView
            if spec.axis == "grid" {
                stack = makeGrid(spec)
            } else if spec.axis == "overlay" {
                stack = makeOverlay(spec)
            } else if spec.style.layoutAlgorithm == "wrapping-stack" {
                stack = makeWrappingStack(spec)
            } else {
                stack = makeStack(spec)
            }
            if spec.action != nil {
                stack.isUserInteractionEnabled = true
                stack.addGestureRecognizer(HTMLToIOSClosureTapGestureRecognizer { [actionHandler] in actionHandler(spec.action) })
            }
            view = stack
          }
        }
        applyStyle(spec, to: view)
        installControlVisualStates(spec, on: view)
        attachOverlayChildren(spec, to: view)
        applyMotion(spec, to: view)
        let renderedView = wrapInMargins(view, spec: spec)
        renderedView.isHidden = state.hiddenNodeIDs.contains(spec.id)
            || (spec.visibleWhenStateID != nil && !state.flags.contains(spec.visibleWhenStateID!))
        renderedView.accessibilityIdentifier = spec.id
        renderedView.accessibilityLabel = spec.accessibilityLabel ?? (spec.text.isEmpty ? nil : spec.text)
        installContextualActions(spec.contextualActions, on: renderedView)
        return renderedView
    }

    private func installContextualActions(_ actions: [HTMLToIOSContextualActionSpec], on view: UIView) {
        guard !actions.isEmpty else { return }
        view.isUserInteractionEnabled = true
        let actionStack = UIStackView()
        actionStack.axis = .horizontal
        actionStack.spacing = 0
        actionStack.translatesAutoresizingMaskIntoConstraints = false
        actionStack.isHidden = true
        actionStack.accessibilityIdentifier = "\(view.accessibilityIdentifier ?? "node").contextual-actions"
        for item in actions {
            var configuration = UIButton.Configuration.filled()
            configuration.title = item.title
            if let systemImage = item.systemImage {
                configuration.image = UIImage(systemName: systemImage)
                configuration.imagePadding = 6
            }
            configuration.baseBackgroundColor = UIColor(htmlToIOS: item.tint)
                ?? (item.role == "destructive" ? .systemRed : .systemBlue)
            configuration.baseForegroundColor = .white
            let button = UIButton(configuration: configuration)
            button.accessibilityIdentifier = item.id
            button.addAction(UIAction { [actionHandler] _ in
                actionHandler(item.action)
            }, for: .touchUpInside)
            button.widthAnchor.constraint(greaterThanOrEqualToConstant: 72).isActive = true
            actionStack.addArrangedSubview(button)
        }
        view.addSubview(actionStack)
        NSLayoutConstraint.activate([
            actionStack.topAnchor.constraint(equalTo: view.topAnchor),
            actionStack.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            actionStack.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        let reveal = HTMLToIOSClosureSwipeGestureRecognizer(direction: .left) { [weak actionStack, weak view] in
            actionStack?.isHidden = false
            if let actionStack { view?.bringSubviewToFront(actionStack) }
        }
        let hide = HTMLToIOSClosureSwipeGestureRecognizer(direction: .right) { [weak actionStack] in
            actionStack?.isHidden = true
        }
        view.addGestureRecognizer(reveal)
        view.addGestureRecognizer(hide)
    }

    private func wrapInMargins(_ view: UIView, spec: HTMLToIOSNodeSpec) -> UIView {
        let margin = spec.style.margin ?? [0, 0, 0, 0]
        guard margin.count == 4, margin.contains(where: { abs($0) > 0.01 }) else { return view }
        let wrapper = UIView()
        wrapper.translatesAutoresizingMaskIntoConstraints = false
        wrapper.backgroundColor = .clear
        wrapper.addSubview(view)
        NSLayoutConstraint.activate([
            view.topAnchor.constraint(equalTo: wrapper.topAnchor, constant: margin[0]),
            view.trailingAnchor.constraint(equalTo: wrapper.trailingAnchor, constant: -margin[1]),
            view.bottomAnchor.constraint(equalTo: wrapper.bottomAnchor, constant: -margin[2]),
            view.leadingAnchor.constraint(equalTo: wrapper.leadingAnchor, constant: margin[3]),
        ])
        return wrapper
    }

    private func makeStack(_ spec: HTMLToIOSNodeSpec) -> UIStackView {
        let stack = UIStackView()
        stack.axis = spec.axis == "horizontal" ? .horizontal : .vertical
        stack.alignment = spec.axis == "horizontal"
            ? (spec.style.baselineAligned == true ? .firstBaseline : .center)
            : .fill
        let usesMeasuredSpacing = spec.contentItems.dropFirst().contains {
            $0.gapBefore != nil || $0.flexibleGapBefore == true
        }
        stack.spacing = usesMeasuredSpacing ? 0 : (spec.style.spacing ?? 8)
        let directChildren = spec.contentItems.compactMap { item in
            item.childID.flatMap { childID in spec.children.first(where: { $0.id == childID }) }
        }
        if spec.axis == "horizontal",
           directChildren.count >= 2,
           directChildren.allSatisfy({ ($0.style.flexGrow ?? 0) > 0 }) {
            let firstGrow = directChildren.first?.style.flexGrow ?? 0
            stack.distribution = directChildren.allSatisfy {
                abs(($0.style.flexGrow ?? 0) - firstGrow) < 0.001
            } ? .fillEqually : .fillProportionally
        }
        if let dynamicItems = state.contentOverrides[spec.id], !dynamicItems.isEmpty {
            dynamicItems.forEach { stack.addArrangedSubview(makeDynamicView($0, in: spec)) }
        } else if spec.contentItems.isEmpty {
            if !stateText(spec).isEmpty && spec.children.isEmpty {
                stack.addArrangedSubview(makeLabel(stateText(spec), spec: spec))
            }
            spec.children.forEach { stack.addArrangedSubview(makeView($0)) }
        } else {
            spec.contentItems.forEach { item in
                addContentGap(item, to: stack, axis: spec.axis)
                if item.kind == "text" {
                    stack.addArrangedSubview(makeContentItemLabel(item, spec: spec))
                } else if let childID = item.childID,
                          let child = spec.children.first(where: { $0.id == childID }) {
                    stack.addArrangedSubview(makeView(child))
                }
            }
        }
        return stack
    }

    private func makeWrappingStack(_ spec: HTMLToIOSNodeSpec) -> HTMLToIOSWrappingView {
        let views: [UIView]
        if let dynamicItems = state.contentOverrides[spec.id], !dynamicItems.isEmpty {
            views = dynamicItems.map { makeDynamicView($0, in: spec) }
        } else {
            views = spec.children.map { makeView($0) }
        }
        return HTMLToIOSWrappingView(
            views: views,
            horizontalSpacing: spec.style.columnSpacing ?? spec.style.spacing ?? 0,
            verticalSpacing: spec.style.rowSpacing ?? spec.style.spacing ?? 0
        )
    }

    private func addContentGap(_ item: HTMLToIOSContentItemSpec, to stack: UIStackView, axis: String) {
        guard let gap = item.gapBefore, gap > 0 else { return }
        let spacer = UIView()
        spacer.isUserInteractionEnabled = false
        if item.flexibleGapBefore == true {
            spacer.setContentHuggingPriority(.defaultLow, for: axis == "vertical" ? .vertical : .horizontal)
            if axis == "vertical" {
                spacer.heightAnchor.constraint(greaterThanOrEqualToConstant: gap).isActive = true
            } else {
                spacer.widthAnchor.constraint(greaterThanOrEqualToConstant: gap).isActive = true
            }
        } else if axis == "vertical" {
            spacer.heightAnchor.constraint(equalToConstant: gap).isActive = true
        } else {
            spacer.widthAnchor.constraint(equalToConstant: gap).isActive = true
        }
        stack.addArrangedSubview(spacer)
    }

    private func makeContentItemLabel(_ item: HTMLToIOSContentItemSpec, spec: HTMLToIOSNodeSpec) -> UILabel {
        let label = makeLabel(contentItemText(item, spec: spec), spec: spec)
        if item.singleLine == true {
            label.numberOfLines = 1
            label.adjustsFontSizeToFitWidth = true
            label.minimumScaleFactor = 0.7
            label.allowsDefaultTighteningForTruncation = true
            if let width = item.preferredWidth, width > 0 {
                label.widthAnchor.constraint(equalToConstant: width).isActive = true
            }
        }
        return label
    }

    private func makeGrid(_ spec: HTMLToIOSNodeSpec) -> UIStackView {
        let grid = UIStackView()
        grid.axis = .vertical
        grid.alignment = .fill
        grid.spacing = spec.style.rowSpacing ?? spec.style.spacing ?? 0
        let columns = max(spec.style.gridColumnCount ?? 2, 1)
        let trackWidths = spec.style.gridColumnWidths ?? []
        let dynamicItems = state.contentOverrides[spec.id] ?? []
        let itemCount = dynamicItems.isEmpty ? spec.children.count : dynamicItems.count
        for start in stride(from: 0, to: itemCount, by: columns) {
            let row = UIStackView()
            row.axis = .horizontal
            row.alignment = .fill
            row.distribution = .fillEqually
            if trackWidths.contains(where: { $0 != nil }) { row.distribution = .fill }
            row.spacing = spec.style.columnSpacing ?? spec.style.spacing ?? 0
            let end = min(start + columns, itemCount)
            for index in start..<end {
                if dynamicItems.isEmpty {
                    let child = makeView(spec.children[index])
                    row.addArrangedSubview(child)
                    let column = index - start
                    if trackWidths.indices.contains(column), let width = trackWidths[column], width > 0 {
                        child.widthAnchor.constraint(equalToConstant: width).isActive = true
                    }
                } else {
                    let child = makeDynamicView(dynamicItems[index], in: spec)
                    row.addArrangedSubview(child)
                    let column = index - start
                    if trackWidths.indices.contains(column), let width = trackWidths[column], width > 0 {
                        child.widthAnchor.constraint(equalToConstant: width).isActive = true
                    }
                }
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

    private func makeDynamicView(_ item: HTMLToIOSDynamicContentItemSpec, in container: HTMLToIOSNodeSpec) -> UIView {
        guard let template = container.children.first(where: { $0.id == item.templateNodeID }) ?? container.children.first else {
            return UIView()
        }
        let view = makeView(template)
        var labels: [UILabel] = []
        func collectLabels(_ current: UIView) {
            if let label = current as? UILabel { labels.append(label) }
            current.subviews.forEach(collectLabels)
        }
        collectLabels(view)
        for (label, value) in zip(labels, item.textValues) {
            label.text = value
        }
        return view
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
        let axis = spec.semantic == "carousel" ? "horizontal" : (state.scrollAxisOverrides[spec.id] ?? spec.style.scrollAxis ?? "vertical")
        if axis == "none" { return makeStack(spec) }
        let scroll = UIScrollView()
        let stack = spec.axis == "grid" ? makeGrid(spec) : makeStack(spec)
        scroll.isDirectionalLockEnabled = axis != "both"
        scroll.alwaysBounceHorizontal = false
        scroll.alwaysBounceVertical = false
        scroll.keyboardDismissMode = .interactive
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

    private func contentInsets(_ spec: HTMLToIOSNodeSpec) -> UIEdgeInsets {
        let padding = spec.style.padding ?? [0, 0, 0, 0]
        return UIEdgeInsets(
            top: padding.indices.contains(0) ? padding[0] : 0,
            left: padding.indices.contains(3) ? padding[3] : 0,
            bottom: padding.indices.contains(2) ? padding[2] : 0,
            right: padding.indices.contains(1) ? padding[1] : 0
        )
    }

    private func attributedPlaceholder(_ spec: HTMLToIOSNodeSpec) -> NSAttributedString {
        let placeholder = spec.textBehavior?.placeholderStyle
        let font = nativeFont(
            size: placeholder?.fontSize ?? spec.style.fontSize ?? 16,
            weight: placeholder?.fontWeight ?? spec.style.fontWeight,
            design: spec.style.fontDesign,
            nativeName: spec.style.fontNativeName,
            style: spec.style.fontStyle
        )
        let paragraph = NSMutableParagraphStyle()
        if let lineHeight = placeholder?.lineHeight, lineHeight > 0 {
            paragraph.minimumLineHeight = lineHeight
            paragraph.maximumLineHeight = lineHeight
        }
        let baseColor = UIColor(htmlToIOS: placeholder?.foreground ?? spec.style.foreground) ?? .placeholderText
        let opacity = placeholder?.opacity ?? (placeholder?.foreground == nil ? 0.5 : 1)
        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: baseColor.withAlphaComponent(baseColor.cgColor.alpha * opacity),
            .paragraphStyle: paragraph,
        ]
        if let spacing = placeholder?.letterSpacing ?? spec.style.letterSpacing {
            attributes[.kern] = spacing
        }
        return NSAttributedString(string: spec.placeholder, attributes: attributes)
    }

    private func keyboardType(_ raw: String?) -> UIKeyboardType {
        switch raw {
        case "emailAddress": return .emailAddress
        case "URL": return .URL
        case "phonePad": return .phonePad
        case "numberPad": return .numberPad
        case "decimalPad": return .decimalPad
        default: return .default
        }
    }

    private func textContentType(_ raw: String?) -> UITextContentType? {
        switch raw {
        case "emailAddress": return .emailAddress
        case "URL": return .URL
        case "telephoneNumber": return .telephoneNumber
        case "password": return .password
        case "username": return .username
        default: return nil
        }
    }

    private func returnKeyType(_ raw: String?) -> UIReturnKeyType {
        switch raw?.lowercased() {
        case "done": return .done
        case "go": return .go
        case "google": return .google
        case "join": return .join
        case "next": return .next
        case "route": return .route
        case "search": return .search
        case "send": return .send
        case "continue": return .continue
        default: return .default
        }
    }

    private func autocapitalizationType(_ raw: String?) -> UITextAutocapitalizationType {
        switch raw?.lowercased() {
        case "none", "off": return .none
        case "words": return .words
        case "characters": return .allCharacters
        default: return .sentences
        }
    }

    private func autocorrectionType(_ value: Bool?) -> UITextAutocorrectionType {
        guard let value else { return .default }
        return value ? .yes : .no
    }

    private func flattenedText(_ spec: HTMLToIOSNodeSpec) -> String {
        if let runs = spec.richTextRuns, !runs.isEmpty {
            return runs.map(\.text).joined()
        }
        if spec.contentItems.isEmpty {
            return stateText(spec) + spec.children.map(flattenedText).joined()
        }
        return spec.contentItems.map { item in
            if item.kind == "text" { return contentItemText(item, spec: spec) }
            guard let childID = item.childID,
                  let child = spec.children.first(where: { $0.id == childID }) else { return "" }
            return flattenedText(child)
        }.joined()
    }

    private func contentItemText(_ item: HTMLToIOSContentItemSpec, spec: HTMLToIOSNodeSpec) -> String {
        let textItemCount = spec.contentItems.filter { $0.kind == "text" }.count
        if textItemCount == 1, (item.text ?? "") == spec.text {
            return stateText(spec)
        }
        return item.text ?? ""
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
        label.attributedText = attributedText(text, spec: spec)
        label.numberOfLines = spec.style.textLineLimit ?? 0
        label.lineBreakMode = lineBreakMode(spec)
        switch spec.style.textAlignment {
        case "center": label.textAlignment = .center
        case "end", "right": label.textAlignment = .right
        default: label.textAlignment = .left
        }
        if spec.style.preservesIntrinsicWidth == true || spec.style.resistsCompression == true {
            label.setContentCompressionResistancePriority(.required, for: .horizontal)
            label.setContentHuggingPriority(.required, for: .horizontal)
        }
        if (spec.style.expectedTextLines ?? 1) > 1 {
            label.setContentCompressionResistancePriority(.required, for: .vertical)
        }
        return label
    }

    private func makeReadOnlyTextView(_ text: String, spec: HTMLToIOSNodeSpec) -> UITextView {
        let textView = UITextView()
        textView.attributedText = attributedText(text, spec: spec)
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.backgroundColor = .clear
        textView.isEditable = false
        textView.isSelectable = spec.textBehavior?.selectable == true
        textView.isScrollEnabled = spec.textBehavior?.scrollable == true
        return textView
    }

    private func attributedText(_ text: String, spec: HTMLToIOSNodeSpec) -> NSAttributedString {
        let runs = (spec.richTextRuns?.isEmpty == false ? spec.richTextRuns : nil) ?? [
            HTMLToIOSRichTextRunSpec(
                text: text,
                fontSize: spec.style.fontSize,
                fontWeight: spec.style.fontWeight,
                fontFamily: spec.style.fontFamily,
                fontResolvedFamily: spec.style.fontResolvedFamily,
                fontResolutionStatus: spec.style.fontResolutionStatus,
                fontFailedFamilies: spec.style.fontFailedFamilies,
                fontDesign: spec.style.fontDesign,
                fontNativeName: spec.style.fontNativeName,
                fontStyle: spec.style.fontStyle,
                foreground: spec.style.foreground,
                background: nil,
                lineHeight: spec.style.lineHeight,
                letterSpacing: spec.style.letterSpacing
            )
        ]
        let result = NSMutableAttributedString()
        for run in runs {
            let size = run.fontSize ?? spec.style.fontSize ?? 16
            let font = nativeFont(
                size: size,
                weight: run.fontWeight ?? spec.style.fontWeight,
                design: run.fontDesign ?? spec.style.fontDesign,
                nativeName: run.fontNativeName ?? spec.style.fontNativeName,
                style: run.fontStyle ?? spec.style.fontStyle
            )
            let targetLineHeight = run.lineHeight ?? spec.style.lineHeight
            let paragraph = NSMutableParagraphStyle()
            paragraph.alignment = spec.style.textAlignment == "center" ? .center : ((spec.style.textAlignment == "end" || spec.style.textAlignment == "right") ? .right : .left)
            paragraph.lineBreakMode = lineBreakMode(spec)
            var attributes: [NSAttributedString.Key: Any] = [
                .font: font,
                .paragraphStyle: paragraph,
                .kern: run.letterSpacing ?? spec.style.letterSpacing ?? 0,
            ]
            var baselineOffset: CGFloat = 0
            if let targetLineHeight, targetLineHeight > 0 {
                paragraph.minimumLineHeight = targetLineHeight
                paragraph.maximumLineHeight = targetLineHeight
                baselineOffset += (targetLineHeight - font.lineHeight) / 2
            }
            if isPureTextSpec(spec),
               hasReliableFontMetrics(spec),
               let expectedFirstBaseline = spec.style.firstBaselineOffset {
                let nativeFirstBaseline = font.ascender + max(font.leading, 0) / 2
                let rawAdjustment = CGFloat(expectedFirstBaseline) - nativeFirstBaseline
                baselineOffset += min(max(rawAdjustment, -font.pointSize * 0.25), font.pointSize * 0.25)
            }
            if abs(baselineOffset) > 0.001 {
                attributes[.baselineOffset] = baselineOffset
            }
            if let foreground = UIColor(htmlToIOS: run.foreground ?? spec.style.foreground) {
                attributes[.foregroundColor] = foreground
            }
            if let background = UIColor(htmlToIOS: run.background) {
                attributes[.backgroundColor] = background
            }
            result.append(NSAttributedString(string: run.text, attributes: attributes))
        }
        return result
    }

    private func isPureTextSpec(_ spec: HTMLToIOSNodeSpec) -> Bool {
        ["text", "label", "heading"].contains(spec.semantic)
            && spec.children.isEmpty
            && spec.contentItems.allSatisfy { $0.kind == "text" }
    }

    private func hasReliableFontMetrics(_ spec: HTMLToIOSNodeSpec) -> Bool {
        ["loaded-web-font", "system-local"].contains(spec.style.fontResolutionStatus)
    }

    private func lineBreakMode(_ spec: HTMLToIOSNodeSpec) -> NSLineBreakMode {
        if spec.style.textOverflow == "ellipsis" { return .byTruncatingTail }
        if spec.style.textLineLimit == 1 { return .byClipping }
        return .byWordWrapping
    }

    private func fontWeight(_ raw: String?) -> UIFont.Weight {
        let value = Int(raw ?? "400") ?? 400
        if value >= 900 { return .black }
        if value >= 800 { return .heavy }
        if value >= 700 { return .bold }
        if value >= 600 { return .semibold }
        if value >= 500 { return .medium }
        if value >= 400 { return .regular }
        if value >= 300 { return .light }
        if value >= 200 { return .thin }
        return .ultraLight
    }

    private func nativeFont(size: Double, weight: String?, design: String?, nativeName: String?, style: String?) -> UIFont {
        if let nativeName, let font = UIFont(name: nativeName, size: size) { return font }
        var descriptor = UIFont.systemFont(ofSize: size, weight: fontWeight(weight)).fontDescriptor
        let systemDesign: UIFontDescriptor.SystemDesign? = design == "monospaced" ? .monospaced : (design == "serif" ? .serif : (design == "rounded" ? .rounded : nil))
        if let systemDesign, let designed = descriptor.withDesign(systemDesign) { descriptor = designed }
        if style == "italic" || style == "oblique", let italic = descriptor.withSymbolicTraits(.traitItalic) { descriptor = italic }
        return UIFont(descriptor: descriptor, size: size)
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

    private func installControlVisualStates(_ spec: HTMLToIOSNodeSpec, on view: UIView) {
        guard !spec.controlVisualStates.isEmpty else { return }
        let update: (String) -> Void = { [weak self, weak view] stateName in
            guard let self, let view else { return }
            self.applyControlVisualState(stateName, spec: spec, to: view)
        }
        if let button = view as? HTMLToIOSStatefulButton {
            button.visualStateDidChange = update
        } else if let control = view as? HTMLToIOSStatefulControl {
            control.visualStateDidChange = update
        } else if let field = view as? UITextField {
            field.addAction(UIAction { _ in update("focused") }, for: .editingDidBegin)
            field.addAction(UIAction { _ in update("normal") }, for: .editingDidEnd)
        } else if let textView = view as? HTMLToIOSManagedTextView {
            textView.visualStateDidChange = update
        }
        update(spec.isEnabled ? (state.isSelected(spec) ? "selected" : "normal") : "disabled")
    }

    private func applyControlVisualState(_ stateName: String, spec: HTMLToIOSNodeSpec, to view: UIView) {
        let visual = spec.controlVisualStates[stateName]
        let selected = state.isSelected(spec)
        let baseBackground = spec.selectionStateID == nil
            ? spec.style.background
            : (selected ? spec.selectedBackground : spec.unselectedBackground)
        let baseForeground = spec.selectionStateID == nil
            ? spec.style.foreground
            : (selected ? spec.selectedForeground : spec.unselectedForeground)
        let baseGradient = spec.selectionStateID == nil
            ? spec.style.gradientColors
            : (selected ? spec.selectedGradientColors : spec.unselectedGradientColors)
        let background = visual?.background ?? baseBackground
        let foreground = visual?.foreground ?? baseForeground
        let gradients = (visual?.gradientColors?.isEmpty == false ? visual?.gradientColors : nil) ?? baseGradient

        view.layer.sublayers?
            .filter { $0.name == "html-to-ios-control-state-gradient" }
            .forEach { $0.removeFromSuperlayer() }
        view.layer.sublayers?.first(where: { $0.name == "html-to-ios-gradient" })?.isHidden = visual != nil
        if let color = UIColor(htmlToIOS: gradients?.first ?? background) {
            view.backgroundColor = color
        }
        if let gradients, gradients.count >= 2 {
            let layer = CAGradientLayer()
            layer.name = "html-to-ios-control-state-gradient"
            layer.colors = gradients.compactMap { UIColor(htmlToIOS: $0)?.cgColor }
            layer.startPoint = CGPoint(x: 0, y: 0.5)
            layer.endPoint = CGPoint(x: 1, y: 0.5)
            view.layer.insertSublayer(layer, at: 0)
        }
        if let color = UIColor(htmlToIOS: foreground) {
            applyControlForeground(color, to: view)
        }
        view.layer.borderWidth = visual?.borderWidth ?? spec.style.borderWidth ?? 0
        view.layer.borderColor = UIColor(htmlToIOS: visual?.borderColor ?? spec.style.borderColor)?.cgColor
        view.layer.cornerRadius = visual?.cornerRadius ?? spec.style.cornerRadius ?? 0
        view.alpha = visual?.opacity ?? spec.style.opacity ?? 1
        let scale = visual?.scale ?? 1
        view.transform = CGAffineTransform(scaleX: scale, y: scale)
        if let color = UIColor(htmlToIOS: visual?.shadowColor ?? spec.style.shadowColor) {
            view.layer.shadowColor = color.cgColor
            view.layer.shadowOpacity = 1
            view.layer.shadowRadius = visual?.shadowRadius ?? spec.style.shadowRadius ?? 0
            view.layer.shadowOffset = CGSize(
                width: visual?.shadowOffsetX ?? spec.style.shadowOffsetX ?? 0,
                height: visual?.shadowOffsetY ?? spec.style.shadowOffsetY ?? 0
            )
        } else {
            view.layer.shadowOpacity = 0
        }
    }

    private func applyControlForeground(_ color: UIColor, to view: UIView) {
        view.tintColor = color
        (view as? UILabel)?.textColor = color
        (view as? UITextField)?.textColor = color
        (view as? UITextView)?.textColor = color
        if let button = view as? UIButton,
           let title = button.attributedTitle(for: .normal)?.mutableCopy() as? NSMutableAttributedString {
            title.addAttribute(.foregroundColor, value: color, range: NSRange(location: 0, length: title.length))
            button.setAttributedTitle(title, for: .normal)
        }
        view.subviews.forEach { applyControlForeground(color, to: $0) }
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
        if let color = UIColor(htmlToIOS: foreground ?? spec.style.foreground) {
            view.tintColor = color
            (view as? UILabel)?.textColor = color
            (view as? UITextField)?.textColor = color
            (view as? UITextView)?.textColor = color
        }
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
        let sizeOverride = state.sizeOverrides[spec.id]
        if let width = sizeOverride?.width ?? spec.style.fixedWidth, width > 0 {
            view.widthAnchor.constraint(equalToConstant: width).isActive = true
        } else if let width = spec.style.textMeasureWidth, width > 0 {
            view.widthAnchor.constraint(lessThanOrEqualToConstant: width).isActive = true
        }
        if let height = sizeOverride?.height ?? spec.style.fixedHeight, height > 0 {
            view.heightAnchor.constraint(equalToConstant: height).isActive = true
        }
        if let ratio = spec.style.aspectRatio,
           ratio > 0,
           spec.style.fixedWidth == nil || spec.style.fixedHeight == nil {
            view.widthAnchor.constraint(equalTo: view.heightAnchor, multiplier: ratio).isActive = true
        }
        if spec.style.preservesIntrinsicWidth == true || spec.style.resistsCompression == true {
            view.setContentCompressionResistancePriority(.required, for: .horizontal)
            view.setContentHuggingPriority(.required, for: .horizontal)
        }
        if let height = spec.style.minHeight, height > 0, height < 161 {
            view.heightAnchor.constraint(greaterThanOrEqualToConstant: height).isActive = true
        }
        if let width = spec.style.minWidth, width > 0 {
            view.widthAnchor.constraint(greaterThanOrEqualToConstant: width).isActive = true
        }
        if let width = spec.style.maxWidth, width > 0 {
            view.widthAnchor.constraint(lessThanOrEqualToConstant: width).isActive = true
        }
        if let height = spec.style.maxHeight, height > 0 {
            view.heightAnchor.constraint(lessThanOrEqualToConstant: height).isActive = true
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

private final class HTMLToIOSClosureSwipeGestureRecognizer: UISwipeGestureRecognizer {
    private let action: () -> Void
    init(direction: UISwipeGestureRecognizer.Direction, _ action: @escaping () -> Void) {
        self.action = action
        super.init(target: nil, action: nil)
        self.direction = direction
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
            bottom: screen.bottomBarPlacement == "safe-area-inset" ? (generatedBottomBar?.bounds.height ?? 0) : 0,
            right: 0
        )
        if scroll.contentInset != insets { scroll.contentInset = insets }
        if scroll.verticalScrollIndicatorInsets != insets { scroll.verticalScrollIndicatorInsets = insets }
        if scroll.horizontalScrollIndicatorInsets != insets { scroll.horizontalScrollIndicatorInsets = insets }
    }

    private func updateGeneratedLayers(in current: UIView) {
        for layer in current.layer.sublayers ?? [] {
            if layer.name == "html-to-ios-gradient" || layer.name == "html-to-ios-control-state-gradient" {
                layer.frame = current.bounds
            }
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
        let previousOffset = generatedScrollView?.contentOffset
        generatedScrollView = nil; generatedTopBar = nil; generatedBottomBar = nil
        view.subviews.forEach { $0.removeFromSuperview() }
        let renderer = HTMLToIOSNodeRenderer(state: generatedState, actionHandler: { [weak self] action in self?.perform(action) })
        configureTypedComponents(renderer)
        let content = wrapGeneratedContent(renderer.makeView(screen.root))
        let usesOuterScroll = screen.contentContainer.kind == "scroll-view"
        var constraints: [NSLayoutConstraint] = []
        if !usesOuterScroll {
            view.addSubview(content)
            constraints.append(contentsOf: [
                content.leadingAnchor.constraint(equalTo: view.leadingAnchor),
                content.trailingAnchor.constraint(equalTo: view.trailingAnchor),
                content.topAnchor.constraint(equalTo: view.topAnchor),
                content.bottomAnchor.constraint(equalTo: view.bottomAnchor)
            ])
        } else {
            let scroll = UIScrollView()
            scroll.isDirectionalLockEnabled = true
            scroll.alwaysBounceHorizontal = false
            scroll.showsHorizontalScrollIndicator = false
            scroll.backgroundColor = view.backgroundColor
            scroll.contentInsetAdjustmentBehavior = screen.safeArea.contentInsetAdjustment == "never" ? .never : .automatic
            scroll.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(scroll)
            scroll.addSubview(content)
            generatedScrollView = scroll
            constraints.append(contentsOf: [
                scroll.leadingAnchor.constraint(equalTo: view.leadingAnchor), scroll.trailingAnchor.constraint(equalTo: view.trailingAnchor),
                scroll.topAnchor.constraint(equalTo: view.topAnchor), scroll.bottomAnchor.constraint(equalTo: view.bottomAnchor),
                content.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
                content.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
                content.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor),
                content.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor),
                content.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor)
            ])
        }
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
                bottom.bottomAnchor.constraint(
                    equalTo: screen.bottomBarPlacement == "viewport-overlay"
                        ? view.bottomAnchor
                        : (screen.safeArea.owner == "system" ? view.safeAreaLayoutGuide.bottomAnchor : view.bottomAnchor)
                )
            ])
        }
        NSLayoutConstraint.activate(constraints)
        view.layoutIfNeeded()
        if let scroll = generatedScrollView,
           let sourceStatusBarHeight = screen.sourceStatusBarHeight, sourceStatusBarHeight > 0 {
            let topCalibration = CGFloat(sourceStatusBarHeight) - view.safeAreaInsets.top
            scroll.contentInset.top = topCalibration
            scroll.verticalScrollIndicatorInsets.top = max(CGFloat(sourceStatusBarHeight), 0)
        }
        if let scroll = generatedScrollView, let previousOffset {
            scroll.setContentOffset(previousOffset, animated: false)
        } else if let scroll = generatedScrollView {
            scroll.setContentOffset(
                CGPoint(x: 0, y: -scroll.adjustedContentInset.top),
                animated: false
            )
        }
    }

    func wrapGeneratedContent(_ content: UIView) -> UIView { content }
    func configureTypedComponents(_ renderer: HTMLToIOSNodeRenderer) {}

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
    private let backdrop = UIControl()
    private weak var panel: UIView?

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
        backdrop.translatesAutoresizingMaskIntoConstraints = false
        backdrop.backgroundColor = .clear
        backdrop.addAction(UIAction { [weak self] _ in self?.dismiss(animated: true) }, for: .touchUpInside)
        view.addSubview(backdrop)
        NSLayoutConstraint.activate([
            backdrop.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            backdrop.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            backdrop.topAnchor.constraint(equalTo: view.topAnchor),
            backdrop.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        renderPanel(animated: false)
    }

    private func renderPanel(animated: Bool) {
        panel?.removeFromSuperview()
        let renderer = HTMLToIOSNodeRenderer(state: generatedState, actionHandler: { [weak self] action in
            self?.perform(action)
        })
        let panel = renderer.makeView(presentation.node)
        self.panel = panel
        panel.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(panel)
        let rect = presentation.sourceRect
        let width = CGFloat(rect.indices.contains(2) ? rect[2] : 0)
        let height = CGFloat(generatedState.sizeOverrides[presentation.node.id]?.height ?? (rect.indices.contains(3) ? rect[3] : 0))
        let sourceLeading = panel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: CGFloat(rect.indices.contains(0) ? rect[0] : 0))
        let sourceTop = panel.topAnchor.constraint(equalTo: view.topAnchor, constant: CGFloat(rect.indices.contains(1) ? rect[1] : 0))
        sourceLeading.priority = .defaultHigh
        sourceTop.priority = .defaultHigh
        NSLayoutConstraint.activate([
            sourceLeading,
            sourceTop,
            panel.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor),
            panel.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor),
            panel.topAnchor.constraint(greaterThanOrEqualTo: view.topAnchor),
            panel.bottomAnchor.constraint(lessThanOrEqualTo: view.bottomAnchor),
            panel.widthAnchor.constraint(equalToConstant: width),
            panel.heightAnchor.constraint(equalToConstant: height),
        ])
        if animated {
            panel.alpha = 0
            UIView.animate(withDuration: 0.2) { panel.alpha = 1; self.view.layoutIfNeeded() }
        }
    }

    private func perform(_ action: HTMLToIOSActionSpec?) {
        guard let action else { return }
        if action.contentVariant != nil || ["toggle-state", "toggle-selection", "toggle-expanded", "update-value"].contains(action.action) {
            generatedState.perform(action)
            renderPanel(animated: true)
        } else {
            actionHandler(action)
        }
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
        case "present-sheet", "present-fullscreen", "present-full-screen", "present-popover", "overlay", "present-overlay", "show-dialog":
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
                    contentContainer: HTMLToIOSContentContainerSpec(nodeId: presentation.node.id, kind: "static-view", scrollAxis: "none", usesCellReuse: false),
                    navigation: HTMLToIOSNavigationSpec(style: "hidden", title: "", titleMode: "inline", scrollEdgeAppearance: "automatic", backButton: "system", toolbarItems: []),
                    root: presentation.node,
                    topBar: nil,
                    bottomBar: nil,
                    bottomBarPlacement: "none",
                    presentations: [],
                    automaticActions: [],
                    stateLayouts: []
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


def typed_section_descriptors(architecture: dict[str, Any] | None) -> list[dict[str, Any]]:
    architecture = architecture or {}
    layers = architecture.get("layers") if isinstance(architecture.get("layers"), dict) else {}
    reusable = layers.get("reusableContent") if isinstance(layers.get("reusableContent"), dict) else {}
    content = layers.get("contentContainer") if isinstance(layers.get("contentContainer"), dict) else {}
    strategies = {
        str(item.get("nodeId") or ""): str(item.get("kind") or "")
        for item in content.get("nodeStrategies") or []
        if isinstance(item, dict)
    }
    result = []
    for index, section in enumerate(reusable.get("sections") or []):
        if not isinstance(section, dict) or not section.get("sourceNodeId"):
            continue
        source_id = str(section["sourceNodeId"])
        item_ids = [str(item) for item in section.get("itemNodeIds") or [] if item]
        uses_reuse = bool(section.get("usesReuse"))
        strategy = strategies.get(source_id, "")
        cell_kind = "table" if strategy == "table-view" else "collection"
        result.append({
            "index": index + 1,
            "sourceNodeId": source_id,
            "itemNodeIds": item_ids,
            "itemTemplateNodeId": str(section.get("itemTemplateNodeId") or "") or None,
            "kind": str(section.get("kind") or "list"),
            "usesReuse": uses_reuse,
            "cellKind": cell_kind,
        })
    return result


def typed_leaf_descriptors(
    architecture: dict[str, Any] | None,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    architecture = architecture or {}
    layers = architecture.get("layers") if isinstance(architecture.get("layers"), dict) else {}
    leaves = layers.get("leafComponents") if isinstance(layers.get("leafComponents"), list) else []
    reserved_node_ids = {
        node_id
        for section in sections
        for node_id in [section["sourceNodeId"], *section["itemNodeIds"]]
    }
    sizing_by_node_id = {
        str(child.get("nodeId") or ""): child
        for relation in layout_relation_descriptors(architecture)
        for child in relation.get("childSizing") or []
        if isinstance(child, dict) and child.get("nodeId")
    }
    result = []
    used_type_stems: set[str] = set()
    for index, leaf in enumerate(leaves):
        if not isinstance(leaf, dict) or not leaf.get("generateType"):
            continue
        node_id = str(leaf.get("nodeId") or "")
        if not node_id or node_id in reserved_node_ids:
            continue
        raw_stem = str(leaf.get("sourceName") or node_id.rsplit(".", 1)[-1])
        normalized_stem = re.sub(r"(?i)btn$", "Button", raw_stem)
        camel_aware_stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", normalized_stem)
        type_stem = swift_type_name(camel_aware_stem, f"Component{index + 1}")
        candidate = type_stem
        duplicate_index = 2
        while candidate in used_type_stems:
            candidate = f"{type_stem}{duplicate_index}"
            duplicate_index += 1
        used_type_stems.add(candidate)
        result.append({
            "index": len(result) + 1,
            "nodeId": node_id,
            "typeStem": candidate,
            "category": str(leaf.get("category") or "view"),
            "semanticType": str(leaf.get("semanticType") or "custom"),
            "generationReasons": [str(item) for item in leaf.get("generationReasons") or []],
            "systemControlPreferred": bool(leaf.get("systemControlPreferred")),
            "requiresCustomControl": bool(leaf.get("requiresCustomControl")),
            "layoutSizing": sizing_by_node_id.get(node_id) or {},
        })
    return result


def layout_relation_descriptors(architecture: dict[str, Any] | None) -> list[dict[str, Any]]:
    architecture = architecture or {}
    layers = architecture.get("layers") if isinstance(architecture.get("layers"), dict) else {}
    content = layers.get("contentContainer") if isinstance(layers.get("contentContainer"), dict) else {}
    return [item for item in content.get("layoutRelations") or [] if isinstance(item, dict)]


def swift_optional_number(value: Any) -> str:
    return "nil" if value is None else repr(float(value))


def layout_contract_source(base_type: str, screen_id: str, relations: list[dict[str, Any]]) -> str:
    relation_rows = []
    for relation in relations:
        child_rows = []
        for child in relation.get("childSizing") or []:
            child_rows.append(
                f'''            ChildSizing(
                nodeID: {json.dumps(str(child.get("nodeId") or ""))},
                widthPolicy: {json.dumps(str(child.get("widthPolicy") or "intrinsic"))},
                heightPolicy: {json.dumps(str(child.get("heightPolicy") or "intrinsic"))},
                measuredWidth: {swift_optional_number(child.get("measuredWidth"))},
                measuredHeight: {swift_optional_number(child.get("measuredHeight"))},
                aspectRatio: {swift_optional_number(child.get("aspectRatio"))},
                flexGrow: {float(child.get("flexGrow") or 0)!r},
                flexShrink: {float(child.get("flexShrink") if child.get("flexShrink") is not None else 1)!r},
                resistsHorizontalCompression: {str(bool(child.get("resistsHorizontalCompression"))).lower()}
            )'''
            )
        child_block = ",\n".join(child_rows)
        relation_rows.append(
            f'''        Relation(
            containerNodeID: {json.dumps(str(relation.get("containerNodeId") or ""))},
            axis: {json.dumps(str(relation.get("axis") or "vertical"))},
            sourceChildNodeIDs: [{", ".join(json.dumps(str(item)) for item in relation.get("sourceChildNodeIds") or [])}],
            orderedChildNodeIDs: [{", ".join(json.dumps(str(item)) for item in relation.get("orderedChildNodeIds") or [])}],
            reordersSourceChildren: {str(bool(relation.get("reordersSourceChildren"))).lower()},
            alignment: {json.dumps(str(relation.get("alignment") or "normal"))},
            distribution: {json.dumps(str(relation.get("distribution") or "normal"))},
            wraps: {str(bool(relation.get("wraps"))).lower()},
            gap: {float(relation.get("gap") or 0)!r},
            childSizing: [
{child_block}
            ]
        )'''
        )
    relations_block = ",\n".join(relation_rows)
    return f'''// Generated by sky-html-to-ios. Measured layout relationships for {screen_id}.
import Foundation

enum {base_type}LayoutContract {{
    struct ChildSizing {{
        let nodeID: String
        let widthPolicy: String
        let heightPolicy: String
        let measuredWidth: Double?
        let measuredHeight: Double?
        let aspectRatio: Double?
        let flexGrow: Double
        let flexShrink: Double
        let resistsHorizontalCompression: Bool
    }}

    struct Relation {{
        let containerNodeID: String
        let axis: String
        let sourceChildNodeIDs: [String]
        let orderedChildNodeIDs: [String]
        let reordersSourceChildren: Bool
        let alignment: String
        let distribution: String
        let wraps: Bool
        let gap: Double
        let childSizing: [ChildSizing]
    }}

    static let relations: [Relation] = [
{relations_block}
    ]

    static func relation(for nodeID: String) -> Relation? {{
        relations.first {{ $0.containerNodeID == nodeID }}
    }}

    static func sizing(for nodeID: String) -> ChildSizing? {{
        relations.lazy.compactMap {{ relation in
            relation.childSizing.first {{ $0.nodeID == nodeID }}
        }}.first
    }}
}}
'''


def screen_sources(
    screen: dict[str, Any],
    ui_stack: str,
    name_prefix: str,
    architecture: dict[str, Any] | None,
) -> dict[str, str]:
    module_type = str(screen["moduleType"])
    screen_type = str(screen["screenType"])
    base_type = f"{name_prefix}{screen_type}"
    sections = typed_section_descriptors(architecture)
    leaves = typed_leaf_descriptors(architecture, sections)
    layout_relations = layout_relation_descriptors(architecture)
    contract_lines = [
        f'    static let screenID: String = {json.dumps(screen["id"])}',
        *[
            f'    static let section{item["index"]}NodeID: String = {json.dumps(item["sourceNodeId"])}'
            for item in sections
        ],
        *[
            f'    static let section{item["index"]}ItemNodeIDs: [String] = [{", ".join(json.dumps(node_id) for node_id in item["itemNodeIds"])}]'
            for item in sections
        ],
        *[
            f'    static let component{item["index"]}NodeID: String = {json.dumps(item["nodeId"])}'
            for item in leaves
        ],
    ]
    sources = {
        f"{module_type}/Models/{base_type}UIContract.swift": f'''// Generated by sky-html-to-ios. Stable node ownership for {screen["id"]}.
import Foundation

enum {base_type}UIContract {{
{chr(10).join(contract_lines)}
}}
''',
        f"{module_type}/Models/{base_type}LayoutContract.swift": layout_contract_source(
            base_type,
            str(screen["id"]),
            layout_relations,
        ),
    }
    if ui_stack == "swiftui":
        section_cases = []
        item_cases = []
        leaf_cases = []
        for item in sections:
            section_type = f"{base_type}Section{item['index']}View"
            sources[f"{module_type}/Sections/{section_type}.swift"] = f'''// Generated by sky-html-to-ios. Typed SwiftUI section for {screen["id"]}.
import SwiftUI

struct {section_type}: View {{
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let spec: HTMLToIOSNodeSpec
    let registry: HTMLToIOSTypedViewRegistry

    var body: some View {{
        HTMLToIOSNativeNodeView(
            store: store,
            spec: spec,
            typedRegistry: registry,
            bypassTypedNodeID: spec.id
        )
    }}
}}
'''
            section_cases.append(
                f'''            case {base_type}UIContract.section{item["index"]}NodeID:
                return AnyView({section_type}(store: store, spec: spec, registry: registry))'''
            )
            if item["usesReuse"] and item["itemNodeIds"]:
                cell_type = f"{base_type}Section{item['index']}ItemView"
                sources[f"{module_type}/Cells/{cell_type}.swift"] = f'''// Generated by sky-html-to-ios. Typed SwiftUI reusable item for {screen["id"]}.
import SwiftUI

struct {cell_type}: View {{
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let spec: HTMLToIOSNodeSpec
    let registry: HTMLToIOSTypedViewRegistry

    var body: some View {{
        HTMLToIOSNativeNodeView(
            store: store,
            spec: spec,
            typedRegistry: registry,
            bypassTypedNodeID: spec.id
        )
    }}
}}
'''
                item_cases.append(
                    f'''            case let nodeID where {base_type}UIContract.section{item["index"]}ItemNodeIDs.contains(nodeID):
                return AnyView({cell_type}(store: store, spec: spec, registry: registry))'''
                )
        for item in leaves:
            leaf_type = f"{base_type}Leaf{item['typeStem']}View"
            sizing = item["layoutSizing"]
            fixed_width = (
                f"CGFloat({float(sizing['measuredWidth'])!r})"
                if sizing.get("widthPolicy") == "fixed"
                and sizing.get("measuredWidth") is not None
                else "nil"
            )
            fixed_height = (
                f"CGFloat({float(sizing['measuredHeight'])!r})"
                if sizing.get("heightPolicy") == "fixed"
                and sizing.get("measuredHeight") is not None
                else "nil"
            )
            resists_compression = str(
                bool(sizing.get("resistsHorizontalCompression"))
                and sizing.get("widthPolicy") != "fixed"
            ).lower()
            layout_priority = (
                2
                if sizing.get("resistsHorizontalCompression")
                else 1 if float(sizing.get("flexGrow") or 0) > 0 else 0
            )
            sources[f"{module_type}/Views/{leaf_type}.swift"] = f'''// Generated by sky-html-to-ios. Typed SwiftUI component for {item["nodeId"]}.
import SwiftUI

struct {leaf_type}: View {{
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let spec: HTMLToIOSNodeSpec
    let registry: HTMLToIOSTypedViewRegistry

    var body: some View {{
        HTMLToIOSNativeNodeView(
            store: store,
            spec: spec,
            typedRegistry: registry,
            bypassTypedNodeID: spec.id
        )
        .frame(
            width: {fixed_width},
            height: {fixed_height}
        )
        .fixedSize(horizontal: {resists_compression}, vertical: false)
        .layoutPriority({layout_priority})
    }}
}}
'''
            leaf_cases.append(
                f'''            case {base_type}UIContract.component{item["index"]}NodeID:
                return AnyView({leaf_type}(store: store, spec: spec, registry: registry))'''
            )
        registry_cases = "\n".join(section_cases + item_cases + leaf_cases)
        sources.update({
            f"{module_type}/Screens/{base_type}Screen.swift": f'''// Generated by sky-html-to-ios. Native SwiftUI screen for {screen["id"]}.
import SwiftUI

struct {base_type}Screen: View {{
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let screen: HTMLToIOSScreenSpec

    var body: some View {{
        {base_type}ContentView(store: store, screen: screen)
    }}
}}
''',
            f"{module_type}/Views/{base_type}ContentView.swift": f'''// Generated by sky-html-to-ios. Module-owned SwiftUI content for {screen["id"]}.
import SwiftUI

struct {base_type}ContentView: View {{
    @ObservedObject var store: HTMLToIOSGeneratedStore
    let screen: HTMLToIOSScreenSpec

    var body: some View {{
        HTMLToIOSGeneratedScreenView(store: store, screen: screen, typedRegistry: typedRegistry)
    }}

    private var typedRegistry: HTMLToIOSTypedViewRegistry {{
        HTMLToIOSTypedViewRegistry {{ nodeID, store, spec, registry in
            switch nodeID {{
{registry_cases}
            default:
                return nil
            }}
        }}
    }}
}}
''',
        })
        return sources

    register_lines = []
    for item in sections:
        section_type = f"{base_type}Section{item['index']}View"
        sources[f"{module_type}/Sections/{section_type}.swift"] = f'''// Generated by sky-html-to-ios. Typed UIKit section for {screen["id"]}.
import UIKit

final class {section_type}: UIView {{
    init(spec: HTMLToIOSNodeSpec, renderer: HTMLToIOSNodeRenderer) {{
        super.init(frame: .zero)
        backgroundColor = .clear
        let content = renderer.makeView(spec, bypassingTypedNodeID: spec.id)
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
'''
        register_lines.append(
            f'''        renderer.registerView(nodeID: {base_type}UIContract.section{item["index"]}NodeID) {{ spec, renderer in
            {section_type}(spec: spec, renderer: renderer)
        }}'''
        )
        if item["usesReuse"] and item["itemNodeIds"]:
            cell_suffix = "TableViewCell" if item["cellKind"] == "table" else "CollectionViewCell"
            cell_type = f"{base_type}Section{item['index']}{cell_suffix}"
            base_cell = "HTMLToIOSGeneratedTableCell" if item["cellKind"] == "table" else "HTMLToIOSGeneratedCollectionCell"
            register_method = "registerTableCell" if item["cellKind"] == "table" else "registerCollectionCell"
            sources[f"{module_type}/Cells/{cell_type}.swift"] = f'''// Generated by sky-html-to-ios. Typed reusable UIKit cell for {screen["id"]}.
import UIKit

final class {cell_type}: {base_cell} {{}}
'''
            register_lines.append(
                f'''        for nodeID in {base_type}UIContract.section{item["index"]}ItemNodeIDs {{
            renderer.{register_method}(nodeID: nodeID, type: {cell_type}.self)
        }}'''
            )
    for item in leaves:
        leaf_type = f"{base_type}Leaf{item['typeStem']}View"
        leaf_base_type = (
            "UIControl"
            if item["category"] == "control"
            and item["requiresCustomControl"]
            and not item["systemControlPreferred"]
            else "UIView"
        )
        sizing = item["layoutSizing"]
        width_policy = str(sizing.get("widthPolicy") or "intrinsic")
        fixed_width = sizing.get("measuredWidth") if width_policy == "fixed" else None
        fixed_height = sizing.get("measuredHeight") if sizing.get("heightPolicy") == "fixed" else None
        aspect_ratio = sizing.get("aspectRatio")
        horizontal_hugging = ".defaultLow" if width_policy == "flexible" else ".defaultHigh"
        horizontal_compression = (
            ".required" if sizing.get("resistsHorizontalCompression") else ".defaultHigh"
        )
        sizing_lines = [
            f"        content.setContentHuggingPriority({horizontal_hugging}, for: .horizontal)",
            f"        content.setContentCompressionResistancePriority({horizontal_compression}, for: .horizontal)",
            "        content.setContentCompressionResistancePriority(.required, for: .vertical)",
        ]
        if fixed_width is not None:
            sizing_lines.append(
                f"        widthAnchor.constraint(equalToConstant: {float(fixed_width)!r}).isActive = true"
            )
        if fixed_height is not None:
            sizing_lines.append(
                f"        heightAnchor.constraint(equalToConstant: {float(fixed_height)!r}).isActive = true"
            )
        if aspect_ratio is not None and fixed_width is None and fixed_height is None:
            sizing_lines.extend([
                f"        let aspectConstraint = widthAnchor.constraint(equalTo: heightAnchor, multiplier: {float(aspect_ratio)!r})",
                "        aspectConstraint.priority = .defaultHigh",
                "        aspectConstraint.isActive = true",
            ])
        sizing_block = "\n".join(sizing_lines)
        sources[f"{module_type}/Views/{leaf_type}.swift"] = f'''// Generated by sky-html-to-ios. Typed UIKit component for {item["nodeId"]}.
import UIKit

final class {leaf_type}: {leaf_base_type} {{
    init(spec: HTMLToIOSNodeSpec, renderer: HTMLToIOSNodeRenderer) {{
        super.init(frame: .zero)
        backgroundColor = .clear
        let content = renderer.makeView(spec, bypassingTypedNodeID: spec.id)
        content.translatesAutoresizingMaskIntoConstraints = false
        addSubview(content)
{sizing_block}
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
'''
        register_lines.append(
            f'''        renderer.registerView(nodeID: {base_type}UIContract.component{item["index"]}NodeID) {{ spec, renderer in
            {leaf_type}(spec: spec, renderer: renderer)
        }}'''
        )
    sources.update({
        f"{module_type}/Controllers/{base_type}ViewController.swift": f'''// Generated by sky-html-to-ios. Native UIKit screen for {screen["id"]}.
import UIKit

final class {base_type}ViewController: HTMLToIOSGeneratedScreenViewController {{
    override func configureTypedComponents(_ renderer: HTMLToIOSNodeRenderer) {{
{chr(10).join(register_lines)}
    }}

    override func wrapGeneratedContent(_ content: UIView) -> UIView {{
        {base_type}ContentView(content: content)
    }}
}}
''',
        f"{module_type}/Views/{base_type}ContentView.swift": f'''// Generated by sky-html-to-ios. Module-owned UIKit content for {screen["id"]}.
import UIKit

final class {base_type}ContentView: UIView {{
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
    })
    return sources


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


def recursive_payload_evidence(value: Any) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
    nodes: dict[str, dict[str, Any]] = {}
    identifiers: set[str] = set()
    order: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            node_id = item.get("id")
            if isinstance(node_id, str) and node_id:
                identifiers.add(node_id)
                order.append(node_id)
                if "semantic" in item and "style" in item:
                    nodes[node_id] = item
            for key in ("childID", "nodeId", "sourceNodeID", "targetNodeID"):
                identifier = item.get(key)
                if isinstance(identifier, str) and identifier:
                    identifiers.add(identifier)
                    order.append(identifier)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return nodes, identifiers, order


def direct_payload_child_ids(node: dict[str, Any] | None) -> list[str]:
    if not node:
        return []
    result = [
        str(child.get("id"))
        for key in ("children", "overlayChildren")
        for child in node.get(key) or []
        if isinstance(child, dict) and child.get("id")
    ]
    result.extend(
        str(item.get("childID"))
        for item in node.get("contentItems") or []
        if isinstance(item, dict) and item.get("childID")
    )
    return list(dict.fromkeys(result))


def native_optimization_reason(node: dict[str, Any], payload_nodes: dict[str, dict[str, Any]]) -> str | None:
    semantic = str(node.get("semanticType") or "")
    parent_id = str(node.get("parentId") or "")
    parent = payload_nodes.get(parent_id) or {}
    content = node.get("content") or {}
    style = node.get("style") or {}
    interactive = bool(node.get("interactionRef") or node.get("interactionRefs"))
    if interactive or node.get("assetRef"):
        return None
    if semantic in {"text", "label", "heading"} and parent.get("semantic") == "text":
        source_text = compact_text(content.get("text"))
        if source_text and source_text in str(parent.get("text") or ""):
            return "flattened-into-native-rich-text"
    has_visual_style = bool(
        color_string(style.get("backgroundColor"))
        or str(style.get("backgroundImage") or "none") != "none"
        or max(scaled_edges(style.get("borderWidths"), 1.0)) > 0
        or max(number(value) for value in style.get("cornerRadii") or [0]) > 0
        or str(style.get("boxShadow") or "none") != "none"
    )
    if semantic in {"container", "decoration", "spacer"} and not has_visual_style and not compact_text(content.get("text")):
        return "empty-structural-wrapper-elided"
    return None


def relation_native_consumption(
    relation: dict[str, Any],
    payload_screen: dict[str, Any],
    payload_nodes: dict[str, dict[str, Any]],
    represented_ids: set[str],
    payload_order: list[str],
    ir_nodes: dict[str, dict[str, Any]],
    architecture_relations: dict[str, dict[str, Any]],
    detached_native_ids: set[str],
) -> dict[str, Any]:
    relation_id = str(relation.get("id") or "")
    kind = str(relation.get("kind") or "")
    node_ids = [str(item) for item in relation.get("nodeIds") or []]
    missing = [node_id for node_id in node_ids if node_id not in represented_ids]
    optimized = {
        node_id: native_optimization_reason(ir_nodes.get(node_id) or {}, payload_nodes)
        for node_id in missing
    }
    unresolved = [node_id for node_id, reason in optimized.items() if not reason]
    checks: list[dict[str, Any]] = []
    strategy = "payload-and-native-runtime"

    def check(name: str, passed: bool, evidence: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    check("node-representation", not unresolved, {
        "represented": [node_id for node_id in node_ids if node_id in represented_ids],
        "optimized": optimized,
        "unresolved": unresolved,
    })

    if kind == "containment":
        parent_id = str(relation.get("parentNodeId") or "")
        child_id = str(relation.get("childNodeId") or "")
        direct_children = direct_payload_child_ids(payload_nodes.get(parent_id))
        presentation_ids = {
            str(item.get("node", {}).get("id") or item.get("id") or "")
            for item in payload_screen.get("presentations") or []
            if isinstance(item, dict) and isinstance(item.get("node") or {}, dict)
        }
        detached_ids = detached_native_ids | presentation_ids
        consumed = child_id in direct_children or child_id in detached_ids or bool(optimized.get(child_id))
        strategy = (
            "native-layer-detachment" if child_id in detached_ids
            else "native-child-tree"
        )
        check("native-parent-child-ownership", consumed, {
            "parentNodeId": parent_id,
            "childNodeId": child_id,
            "directChildNodeIds": direct_children,
            "detachedPresentationNodeIds": sorted(detached_ids),
        })
    elif kind == "visual-sequence":
        before_id = str(relation.get("beforeNodeId") or "")
        after_id = str(relation.get("afterNodeId") or "")
        container_id = str(relation.get("containerNodeId") or "")
        direct_order = direct_payload_child_ids(payload_nodes.get(container_id))
        if before_id in detached_native_ids or after_id in detached_native_ids:
            consumed = before_id in represented_ids and after_id in represented_ids
            strategy = "native-layer-detachment"
        elif before_id in direct_order and after_id in direct_order:
            consumed = direct_order.index(before_id) < direct_order.index(after_id)
        elif before_id in payload_order and after_id in payload_order:
            consumed = payload_order.index(before_id) < payload_order.index(after_id)
        else:
            consumed = bool(optimized.get(before_id) or optimized.get(after_id)) and not unresolved
        if strategy != "native-layer-detachment":
            strategy = "ordered-native-children"
        check("rendered-child-order", consumed, {
            "containerNodeId": container_id,
            "beforeNodeId": before_id,
            "afterNodeId": after_id,
            "payloadChildOrder": direct_order,
        })
    elif kind in {"equal-width", "equal-height"}:
        dimension = "preferredWidth" if kind == "equal-width" else "preferredHeight"
        measured = {
            node_id: value
            for node_id in node_ids
            if (
                value := number(
                    (payload_nodes.get(node_id) or {}).get("style", {}).get(dimension),
                    None,
                )
            ) is not None
        }
        values = list(measured.values())
        tolerance = number(relation.get("tolerancePt"), 1.5)
        optimized_ids = {node_id for node_id, reason in optimized.items() if reason}
        detached_unmeasured_ids = {
            node_id
            for node_id in node_ids
            if node_id in detached_native_ids and node_id not in measured
        }
        covered_ids = set(measured) | optimized_ids | detached_unmeasured_ids
        consumed = set(node_ids) <= covered_ids and (
            len(values) < 2 or max(values) - min(values) <= tolerance
        )
        strategy = (
            "measured-native-size-contract-with-layer-detachment"
            if detached_unmeasured_ids
            else "measured-native-size-contract"
        )
        check("equal-dimension-contract", consumed, {
            "dimension": dimension,
            "measuredNodeValues": measured,
            "values": values,
            "detachedNodeIds": sorted(detached_unmeasured_ids),
            "optimizedNodeIds": sorted(optimized_ids),
            "uncoveredNodeIds": sorted(set(node_ids) - covered_ids),
            "tolerancePt": tolerance,
        })
    elif kind == "square-aspect":
        node_id = node_ids[0] if node_ids else ""
        style = (payload_nodes.get(node_id) or {}).get("style") or {}
        ratio = number(style.get("aspectRatio"), None)
        fixed_width = number(style.get("fixedWidth"), None)
        fixed_height = number(style.get("fixedHeight"), None)
        consumed = bool(
            ratio is not None and abs(ratio - 1) <= 0.05
            or fixed_width is not None and fixed_height is not None and abs(fixed_width - fixed_height) <= 1.5
            or optimized.get(node_id)
        )
        strategy = "native-aspect-ratio-constraint"
        check("square-aspect-contract", consumed, {"aspectRatio": ratio, "fixedWidth": fixed_width, "fixedHeight": fixed_height})
    elif kind == "scroll-axis-ownership":
        node_id = str(relation.get("ownerNodeId") or "")
        axis = str(relation.get("axis") or "none")
        payload_axis = str(((payload_nodes.get(node_id) or {}).get("style") or {}).get("scrollAxis") or "none")
        content = payload_screen.get("contentContainer") or {}
        content_match = str(content.get("nodeId") or "") == node_id and str(content.get("scrollAxis") or "none") == axis
        consumed = payload_axis == axis or content_match
        strategy = "native-scroll-owner"
        check("scroll-axis-contract", consumed, {"sourceAxis": axis, "payloadAxis": payload_axis, "contentContainer": content})
    elif kind == "alignment":
        container_id = str(relation.get("containerNodeId") or "")
        container = payload_nodes.get(container_id) or {}
        architecture_relation = architecture_relations.get(container_id) or {}
        consumed = bool(container) and not unresolved and bool(architecture_relation)
        strategy = "native-container-alignment-with-layer-detachment" if any(
            node_id in detached_native_ids for node_id in node_ids
        ) else "native-container-alignment"
        check("alignment-contract", consumed, {
            "sourceAlignment": relation.get("alignment"),
            "payloadAlignItems": (container.get("style") or {}).get("alignItems"),
            "architectureAlignment": architecture_relation.get("alignment"),
        })
    elif kind == "overlap-order":
        container_id = str(relation.get("containerNodeId") or "")
        container = payload_nodes.get(container_id) or {}
        overlay_order = [str(item.get("id")) for item in container.get("overlayChildren") or [] if item.get("id")]
        back_id = str(relation.get("backNodeId") or "")
        front_id = str(relation.get("frontNodeId") or "")
        if back_id in overlay_order and front_id in overlay_order:
            consumed = overlay_order.index(back_id) < overlay_order.index(front_id)
        else:
            back_z = number(((payload_nodes.get(back_id) or {}).get("style") or {}).get("zIndex"), 0)
            front_z = number(((payload_nodes.get(front_id) or {}).get("style") or {}).get("zIndex"), 0)
            consumed = back_id in represented_ids and front_id in represented_ids and back_z <= front_z
        strategy = "native-overlay-z-order"
        check("overlap-order-contract", consumed, {"overlayOrder": overlay_order, "backNodeId": back_id, "frontNodeId": front_id})

    passed = all(item["passed"] for item in checks)
    return {
        "relationId": relation_id,
        "kind": kind,
        "nodeIds": node_ids,
        "status": "optimized-equivalent" if passed and optimized else "consumed" if passed else "not-consumed",
        "strategy": strategy,
        "checks": checks,
    }


def build_native_structure_manifest(
    irs: list[dict[str, Any]],
    screens: list[dict[str, Any]],
    architecture_by_screen: dict[str, dict[str, Any]],
    layout_graph: dict[str, Any],
    graph_by_screen: dict[str, dict[str, Any]],
    native_layout_by_screen: dict[str, dict[str, Any]],
    screen_source_files: dict[str, list[str]],
    generation_manifest: dict[str, Any],
    out_dir: Path,
    architecture_path: Path | None,
    graph_path: Path,
    native_layout_path: Path | None,
    ui_stack: str,
) -> dict[str, Any]:
    ir_by_screen = {
        str((ir.get("screens") or [{}])[0].get("id") or ""): ir
        for ir in irs
    }
    payload_by_screen = {str(screen.get("id") or ""): screen for screen in screens}
    manifest_screens = []
    for screen_id, graph_screen in graph_by_screen.items():
        ir_screen = (ir_by_screen.get(screen_id) or {}).get("screens", [{}])[0]
        design_scale = min(max(number(((ir_by_screen.get(screen_id) or {}).get("target") or {}).get("scale"), 1), 0.5), 3.0)
        ir_nodes = {str(node.get("id") or ""): node for node in ir_screen.get("nodes") or [] if node.get("id")}
        payload_screen = payload_by_screen.get(screen_id) or {}
        payload_nodes, represented_ids, payload_order = recursive_payload_evidence(payload_screen)
        detached_native_ids = {
            str((payload_screen.get(key) or {}).get("id") or "")
            for key in ("topBar", "bottomBar")
            if isinstance(payload_screen.get(key), dict)
        }
        detached_native_ids.update(
            str(item.get("node", {}).get("id") or item.get("id") or "")
            for item in payload_screen.get("presentations") or []
            if isinstance(item, dict) and isinstance(item.get("node") or {}, dict)
        )
        detached_native_ids.update(
            node_id for node_id in represented_ids
            if node_id in ir_nodes and node_id not in payload_nodes
        )
        detached_native_ids.discard("")
        architecture = architecture_by_screen.get(screen_id) or {}
        native_layout = native_layout_by_screen.get(screen_id) or {}
        architecture_relations = {
            str(item.get("containerNodeId") or ""): item
            for item in (((architecture.get("layers") or {}).get("contentContainer") or {}).get("layoutRelations") or [])
            if isinstance(item, dict) and item.get("containerNodeId")
        }
        node_records = []
        for graph_node in graph_screen.get("nodes") or []:
            node_id = str(graph_node.get("nodeId") or "")
            reason = None if node_id in represented_ids else native_optimization_reason(ir_nodes.get(node_id) or {}, payload_nodes)
            node_records.append({
                "nodeId": node_id,
                "status": "represented" if node_id in represented_ids else "optimized-equivalent" if reason else "missing",
                "strategy": reason or "generated-native-payload",
            })
        relation_records = [
            relation_native_consumption(
                relation,
                payload_screen,
                payload_nodes,
                represented_ids,
                payload_order,
                ir_nodes,
                architecture_relations,
                detached_native_ids,
            )
            for relation in graph_screen.get("relations") or []
        ]
        container_consumption = []
        for container_plan in native_layout.get("containers") or []:
            container_id = str(container_plan.get("containerNodeId") or "")
            payload_container = payload_nodes.get(container_id) or {}
            actual_children = direct_payload_child_ids(payload_container)
            expected_children = [
                str(item)
                for item in container_plan.get("orderedChildNodeIds") or []
                if str(item) in actual_children
            ]
            actual_expected_order = [item for item in actual_children if item in expected_children]
            style = payload_container.get("style") or {}
            checks = {
                "axis": str(payload_container.get("axis") or "") == str(container_plan.get("axis") or ""),
                "visualOrder": actual_expected_order == expected_children,
                "gap": abs(number(style.get("spacing")) - number(container_plan.get("gapPt")) * design_scale) <= 0.01,
                "rowGap": abs(number(style.get("rowSpacing")) - number(container_plan.get("rowGapPt")) * design_scale) <= 0.01,
                "columnGap": abs(number(style.get("columnSpacing")) - number(container_plan.get("columnGapPt")) * design_scale) <= 0.01,
                "algorithm": str(style.get("layoutAlgorithm") or "") == str(container_plan.get("layoutAlgorithm") or ""),
                "wrap": bool(style.get("wraps")) == bool(container_plan.get("wraps")),
                "alignment": str(style.get("alignItems") or "normal") == str(container_plan.get("alignment") or "normal"),
                "distribution": str(style.get("justifyContent") or "normal") == str(container_plan.get("distribution") or "normal"),
            }
            container_consumption.append({
                "containerNodeId": container_id,
                "status": "consumed" if payload_container and all(checks.values()) else "not-consumed",
                "checks": checks,
                "expectedChildNodeIds": expected_children,
                "actualChildNodeIds": actual_children,
                "relationIds": container_plan.get("relationIds") or [],
            })
        compound_consumption = []
        for compound_plan in native_layout.get("compoundControls") or []:
            node_id = str(compound_plan.get("nodeId") or "")
            payload_node = payload_nodes.get(node_id) or {}
            generated_compound = payload_node.get("compoundLayout") or {}
            expected_slots = [str(item) for item in compound_plan.get("orderedSlotIds") or []]
            generated_slots = [str(item) for item in generated_compound.get("orderedSlotIds") or []]
            content_slots = [str(item.get("id") or "") for item in payload_node.get("contentItems") or []]
            checks = {
                "slotContract": generated_slots == expected_slots,
                "contentOrder": [item for item in content_slots if item in expected_slots] == [
                    item for item in expected_slots if item in content_slots
                ],
                "axis": str(payload_node.get("axis") or "") == str(compound_plan.get("axis") or ""),
            }
            compound_consumption.append({
                "nodeId": node_id,
                "status": "consumed" if payload_node and all(checks.values()) else "not-consumed",
                "checks": checks,
                "expectedSlotIds": expected_slots,
                "actualSlotIds": content_slots,
            })
        node_layout_consumption = []
        for node_plan in native_layout.get("nodes") or []:
            node_id = str(node_plan.get("nodeId") or "")
            payload_node = payload_nodes.get(node_id) or {}
            contract = payload_node.get("layoutContract") or {}
            positioning = node_plan.get("positioning") or {}
            box = node_plan.get("boxModel") or {}
            checks = {
                "node": str(contract.get("nodeID") or "") == node_id,
                "widthKind": str(contract.get("widthKind") or "") == str((box.get("widthContract") or {}).get("kind") or ""),
                "heightKind": str(contract.get("heightKind") or "") == str((box.get("heightContract") or {}).get("kind") or ""),
                "positioning": str(contract.get("positioningScheme") or "") == str(positioning.get("scheme") or ""),
                "owner": contract.get("positioningOwnerNodeID") == positioning.get("containingBlockNodeId"),
            }
            optimized_reason = (
                native_optimization_reason(ir_nodes.get(node_id) or {}, payload_nodes)
                or ("detached-native-owner" if node_id in detached_native_ids and node_id in represented_ids else None)
            )
            node_layout_consumption.append({
                "nodeId": node_id,
                "status": (
                    "consumed" if payload_node and all(checks.values())
                    else "optimized-equivalent" if optimized_reason
                    else "not-consumed"
                ),
                "strategy": optimized_reason or "native-node-layout-contract",
                "checks": checks,
            })
        state_layout_consumption = []
        payload_state_layouts = {
            str(item.get("stateId") or ""): item
            for item in payload_screen.get("stateLayouts") or []
        }
        for state_plan in native_layout.get("stateLayouts") or []:
            state_id = str(state_plan.get("stateId") or "")
            payload_state = payload_state_layouts.get(state_id) or {}
            expected_operations = state_plan.get("operations") or []
            actual_operations = payload_state.get("operations") or []
            checks = {
                "strategy": payload_state.get("nativeStrategy") == state_plan.get("nativeStrategy"),
                "operations": actual_operations == expected_operations,
                "generatedNodes": all(
                    not item.get("generatedLayoutNodeId")
                    or str(item.get("generatedLayoutNodeId")) in represented_ids
                    for item in expected_operations
                ),
            }
            state_layout_consumption.append({
                "stateId": state_id,
                "status": "consumed" if payload_state and all(checks.values()) else "not-consumed",
                "checks": checks,
            })
        source_paths = [
            "Resources/Payload/HTMLToIOSGeneratedPayload.json",
            "Core/Runtime/HTMLToIOSGeneratedRuntime.swift",
            *screen_source_files.get(screen_id, []),
        ]
        source_paths = list(dict.fromkeys(source_paths))
        consumer_files = []
        for relative in source_paths:
            file_entry = (generation_manifest.get("files") or {}).get(relative) or {}
            path = out_dir / relative
            consumer_files.append({
                "relativePath": relative,
                "status": file_entry.get("status"),
                "sha256": sha256_file(path) if path.is_file() else None,
                "exists": path.is_file(),
            })
        regions = ir_screen.get("regions") or {}
        manifest_screens.append({
            "screenId": screen_id,
            "nodes": node_records,
            "relations": relation_records,
            "layoutPlanConsumption": {
                "containers": container_consumption,
                "compoundControls": compound_consumption,
                "nodes": node_layout_consumption,
                "stateLayouts": state_layout_consumption,
                "summary": {
                    "containerCount": len(container_consumption),
                    "unconsumedContainerCount": sum(item["status"] == "not-consumed" for item in container_consumption),
                    "compoundControlCount": len(compound_consumption),
                    "unconsumedCompoundControlCount": sum(item["status"] == "not-consumed" for item in compound_consumption),
                    "nodeLayoutCount": len(node_layout_consumption),
                    "unconsumedNodeLayoutCount": sum(item["status"] not in {"consumed", "optimized-equivalent"} for item in node_layout_consumption),
                    "stateLayoutCount": len(state_layout_consumption),
                    "unconsumedStateLayoutCount": sum(item["status"] == "not-consumed" for item in state_layout_consumption),
                },
            },
            "contentContainer": payload_screen.get("contentContainer"),
            "regions": {
                "top": {
                    "sourceNodeId": (regions.get("topBar") or {}).get("nodeId"),
                    "generatedNodeId": (payload_screen.get("topBar") or {}).get("id"),
                },
                "bottom": {
                    "sourceNodeId": (regions.get("bottomBar") or {}).get("nodeId"),
                    "generatedNodeId": (payload_screen.get("bottomBar") or {}).get("id"),
                },
            },
            "consumerFiles": consumer_files,
            "summary": {
                "nodeCount": len(node_records),
                "missingNodeCount": sum(item["status"] == "missing" for item in node_records),
                "relationCount": len(relation_records),
                "unconsumedRelationCount": sum(item["status"] == "not-consumed" for item in relation_records),
            },
        })
    return {
        "schemaVersion": "native-structure-manifest-1.0",
        "generatorVersion": GENERATOR_VERSION,
        "uiStack": ui_stack,
        "architecturePlan": str(architecture_path.resolve()) if architecture_path else None,
        "architecturePlanSha256": sha256_file(architecture_path) if architecture_path else None,
        "layoutRelationGraph": str(graph_path.resolve()),
        "layoutRelationGraphSha256": sha256_file(graph_path),
        "layoutRelationGraphSchemaVersion": layout_graph.get("schemaVersion"),
        "nativeLayoutPlan": str(native_layout_path.resolve()) if native_layout_path else None,
        "nativeLayoutPlanSha256": sha256_file(native_layout_path) if native_layout_path else None,
        "generationManifest": str((out_dir / MANIFEST_NAME).resolve()),
        "generationManifestSha256": sha256_file(out_dir / MANIFEST_NAME),
        "screens": manifest_screens,
        "summary": {
            "screenCount": len(manifest_screens),
            "nodeCount": sum(item["summary"]["nodeCount"] for item in manifest_screens),
            "missingNodeCount": sum(item["summary"]["missingNodeCount"] for item in manifest_screens),
            "relationCount": sum(item["summary"]["relationCount"] for item in manifest_screens),
            "unconsumedRelationCount": sum(item["summary"]["unconsumedRelationCount"] for item in manifest_screens),
        },
    }


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
    layout_graph, graph_by_screen = load_layout_relation_graph(args.layout_relation_graph)
    native_layout_plan, native_layout_by_screen = load_native_layout_plan(args.native_layout_plan)
    if args.native_structure_manifest and not args.layout_relation_graph:
        raise ValueError("--native-structure-manifest requires --layout-relation-graph")
    name_prefix, naming_source, existing_type_names = load_naming_prefix(args.naming_plan)
    ir_screen_ids = [str((ir.get("screens") or [{}])[0].get("id") or "screen") for ir in irs]
    unknown_architecture_screens = sorted(set(architecture_by_screen) - set(ir_screen_ids))
    if unknown_architecture_screens:
        raise ValueError("architecture plan contains unknown screens: " + ", ".join(unknown_architecture_screens))
    if graph_by_screen and set(graph_by_screen) != set(ir_screen_ids):
        missing = sorted(set(ir_screen_ids) - set(graph_by_screen))
        extra = sorted(set(graph_by_screen) - set(ir_screen_ids))
        raise ValueError(f"layout relation graph screen mismatch; missing={missing}, extra={extra}")
    if native_layout_by_screen and set(native_layout_by_screen) != set(ir_screen_ids):
        missing = sorted(set(ir_screen_ids) - set(native_layout_by_screen))
        extra = sorted(set(native_layout_by_screen) - set(ir_screen_ids))
        raise ValueError(f"native layout plan screen mismatch; missing={missing}, extra={extra}")
    screens = [
        build_screen(
            ir,
            architecture_by_screen.get(screen_id),
            native_layout_by_screen.get(screen_id),
        )
        for ir, screen_id in zip(irs, ir_screen_ids)
    ]
    ids = [screen["id"] for screen in screens]
    if len(ids) != len(set(ids)):
        raise ValueError("screen IDs must be unique")
    assign_screen_modules(screens)
    generated_page_types = {
        f"{name_prefix}{screen['screenType']}{suffix}"
        for screen in screens
        for suffix in (
            ("Screen", "ContentView", "UIContract", "LayoutContract")
            if ui_stack == "swiftui"
            else ("ViewController", "ContentView", "UIContract", "LayoutContract")
        )
    }
    for screen in screens:
        base_type = f"{name_prefix}{screen['screenType']}"
        sections = typed_section_descriptors(architecture_by_screen.get(screen["id"]))
        for section in sections:
            generated_page_types.add(f"{base_type}Section{section['index']}View")
            if section["usesReuse"] and section["itemNodeIds"]:
                suffix = "ItemView" if ui_stack == "swiftui" else (
                    "TableViewCell" if section["cellKind"] == "table" else "CollectionViewCell"
                )
                generated_page_types.add(f"{base_type}Section{section['index']}{suffix}")
        for leaf in typed_leaf_descriptors(architecture_by_screen.get(screen["id"]), sections):
            generated_page_types.add(f"{base_type}Leaf{leaf['typeStem']}View")
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
    screen_source_files: dict[str, list[str]] = {}
    for screen in screens:
        generated_screen_sources = screen_sources(
            screen, ui_stack, name_prefix, architecture_by_screen.get(screen["id"])
        )
        screen_source_files[screen["id"]] = sorted(generated_screen_sources)
        files.update(generated_screen_sources)
    conflict_dir = args.conflict_dir or args.out_dir.with_name(args.out_dir.name + ".conflicts")
    metadata = {
        "uiStack": ui_stack,
        "moduleName": args.module_name,
        "entrySymbol": "HTMLToIOSGeneratedRootView" if ui_stack == "swiftui" else "HTMLToIOSGeneratedRootViewController",
        "screenIDs": ids,
        "screenModules": {screen["id"]: screen["moduleId"] for screen in screens},
        "inputs": [{"path": str(path.resolve()), "sha256": sha256_file(path)} for path in args.ir],
        "architecturePlan": str(args.architecture_plan.resolve()) if args.architecture_plan else None,
        "layoutRelationGraph": str(args.layout_relation_graph.resolve()) if args.layout_relation_graph else None,
        "nativeLayoutPlan": str(args.native_layout_plan.resolve()) if args.native_layout_plan else None,
        "nativeLayoutPlanSha256": sha256_file(args.native_layout_plan) if args.native_layout_plan else None,
        "namingPlan": str(args.naming_plan.resolve()) if args.naming_plan else None,
        "namePrefix": name_prefix,
        "namingSource": naming_source,
    }
    manifest = write_incremental(args.out_dir, conflict_dir, files, metadata, args.overwrite_modified)
    native_structure_path = None
    native_structure = None
    if graph_by_screen:
        native_structure_path = args.native_structure_manifest or args.out_dir / "native-structure-manifest.json"
        native_structure = build_native_structure_manifest(
            irs,
            screens,
            architecture_by_screen,
            layout_graph,
            graph_by_screen,
            native_layout_by_screen,
            screen_source_files,
            manifest,
            args.out_dir,
            args.architecture_plan,
            args.layout_relation_graph,
            args.native_layout_plan,
            ui_stack,
        )
        native_structure_path.parent.mkdir(parents=True, exist_ok=True)
        native_structure_path.write_text(
            json.dumps(native_structure, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
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
        "nativeStructureManifest": str(native_structure_path.resolve()) if native_structure_path else None,
        "nativeStructureSummary": native_structure.get("summary") if native_structure else None,
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
