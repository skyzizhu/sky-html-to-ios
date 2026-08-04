#!/usr/bin/env python3
"""Build one executable internal-configuration contract for native system controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from system_control_catalog import SYSTEM_CONTROLS


SCHEMA_VERSION = "native-control-configuration-plan-1.0"
SEMANTICS = {str(item["semantic"]): item for item in SYSTEM_CONTROLS}
SEMANTICS.update({
    "toggle": SEMANTICS["switch"],
    "progress-view": SEMANTICS["progress"],
    "loading": SEMANTICS["activity-indicator"],
    "select": {"uikit": "UIMenu/UIPickerView", "swiftUI": "Picker", "semantic": "select"},
    "picker": {"uikit": "UIMenu/UIPickerView", "swiftUI": "Picker", "semantic": "picker"},
})
ALLOWED_STATES = {"normal", "pressed", "highlighted", "focused", "editing", "selected", "checked", "disabled", "loading"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: Any, default: float = 0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", str(value or ""))
    return float(match.group(0)) if match else default


def color(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if not text or text in {"transparent", "none"} else text


def first_color(*values: Any) -> str | None:
    return next((item for item in (color(value) for value in values) if item), None)


def scaled_quad(value: Any, scale: float) -> list[float]:
    values = list(value) if isinstance(value, list) else []
    values = (values + [0, 0, 0, 0])[:4]
    return [max(number(item) * scale, 0) for item in values]


def state_color(states: dict[str, Any], state: str, key: str) -> str | None:
    payload = states.get(state) or {}
    if key == "foreground":
        return first_color(payload.get("color"), payload.get("foreground"))
    if key == "background":
        return first_color(payload.get("backgroundColor"), payload.get("background"))
    return first_color(payload.get(key))


def preferred_style(semantic: str, node: dict[str, Any]) -> str:
    state = node.get("state") or {}
    hints = node.get("iosHints") or {}
    if semantic == "date-input":
        return str(hints.get("picker-style") or state.get("pickerStyle") or "compact")
    if semantic == "wheel-picker":
        return "wheel"
    if semantic == "search-bar":
        return str(hints.get("search-bar-style") or "minimal")
    if semantic in {"activity-indicator", "loading"}:
        height = number(((node.get("layout") or {}).get("rect") or {}).get("height"))
        return "large" if height >= 28 else "medium"
    return "automatic"


def control_appearance(
    semantic: str,
    foreground: str | None,
    background: str | None,
    border: str | None,
    selected_foreground: str | None,
    selected_background: str | None,
    disabled_foreground: str | None,
    disabled_opacity: float,
    accent: str | None = None,
) -> dict[str, Any]:
    accent = accent or selected_background or border or selected_foreground or foreground
    appearance = {
        "tint": accent,
        "foreground": foreground,
        "background": background,
        "trackTint": background,
        "fillTint": accent,
        "thumbTint": None,
        "selectedTint": selected_background or accent,
        "selectedForeground": selected_foreground or foreground,
        "disabledForeground": disabled_foreground,
        "disabledOpacity": min(max(disabled_opacity, 0), 1),
    }
    if semantic in {"switch", "toggle"}:
        appearance["trackTint"] = background
        appearance["fillTint"] = selected_background or border
        appearance["thumbTint"] = selected_foreground
    elif semantic in {"slider", "progress", "progress-view"}:
        appearance["trackTint"] = background
        appearance["fillTint"] = selected_background or border or foreground
        appearance["thumbTint"] = selected_foreground if semantic == "slider" else None
    elif semantic == "page-control":
        appearance["trackTint"] = background or disabled_foreground
        appearance["fillTint"] = selected_background or selected_foreground or border or foreground
    elif semantic == "segmented-control":
        appearance["selectedTint"] = selected_background or background or border
        appearance["selectedForeground"] = selected_foreground or foreground
    elif semantic in {"activity-indicator", "loading", "refresh-control"}:
        appearance["tint"] = selected_background or border or foreground
    return appearance


def state_appearances(semantic: str, style: dict[str, Any], states: dict[str, Any]) -> dict[str, dict[str, Any]]:
    def slots(raw: dict[str, Any], opacity_default: float = 1) -> dict[str, Any]:
        foreground = first_color(raw.get("color"), raw.get("foreground"), style.get("color"), style.get("foreground"))
        background = first_color(raw.get("backgroundColor"), raw.get("background"), style.get("backgroundColor"), style.get("background"))
        border = first_color(
            raw.get("borderTopColor"), raw.get("borderRightColor"), raw.get("borderBottomColor"), raw.get("borderLeftColor"),
            *(style.get("borderColors") or []), style.get("borderColor"),
        )
        accent = first_color(raw.get("accentColor"), style.get("accentColor"))
        return control_appearance(
            semantic, foreground, background, border, foreground, background, foreground,
            number(raw.get("opacity"), opacity_default), accent,
        )

    result = {"normal": slots(style)}
    for state_name, raw in states.items():
        if state_name in ALLOWED_STATES and isinstance(raw, dict):
            result[state_name] = slots(raw, 0.5 if state_name == "disabled" else 1)
    if "pressed" in result and "highlighted" not in result:
        result["highlighted"] = dict(result["pressed"])
    if "focused" in result and "editing" not in result:
        result["editing"] = dict(result["focused"])
    if "checked" in result and "selected" not in result:
        result["selected"] = dict(result["checked"])
    if "selected" in result and "checked" not in result:
        result["checked"] = dict(result["selected"])
    return result


def node_contract(node: dict[str, Any], scale: float) -> dict[str, Any]:
    semantic = str(node.get("semanticType") or "")
    style = node.get("style") or {}
    states = node.get("controlVisualStates") or {}
    rect = (node.get("layout") or {}).get("rect") or {}
    decision = ((node.get("nativeMapping") or {}).get("nativeControlDecision") or {})
    foreground = first_color(style.get("color"), style.get("foreground"))
    background = first_color(style.get("backgroundColor"), style.get("background"))
    border = first_color(*(style.get("borderColors") or []), style.get("borderColor"))
    selected_foreground = first_color(
        state_color(states, "selected", "foreground"),
        state_color(states, "checked", "foreground"),
        border,
        foreground,
    )
    selected_background = first_color(
        state_color(states, "selected", "background"),
        state_color(states, "checked", "background"),
        border,
        background,
    )
    disabled_foreground = first_color(state_color(states, "disabled", "foreground"), foreground)
    state_names = sorted({str(item) for item in states if str(item) in ALLOWED_STATES})
    padding = scaled_quad(style.get("padding"), scale)
    source_width = number(rect.get("width")) * scale
    source_height = number(rect.get("height")) * scale
    primitive = SEMANTICS[semantic]
    visual_states = state_appearances(semantic, style, states)
    return {
        "nodeId": str(node.get("id") or ""),
        "semantic": semantic,
        "nativePrimitive": {"swiftUI": primitive.get("swiftUI"), "uiKit": primitive.get("uikit")},
        "strategy": "system-control-with-wrapper" if decision.get("decision") == "system-control-with-wrapper" else "system-control",
        "geometry": {
            "sourceWidthPt": source_width if source_width > 0 else None,
            "sourceHeightPt": source_height if source_height > 0 else None,
            "contentInsetsPt": padding,
            "itemSpacingPt": max(number(style.get("gap")) * scale, 0),
            "preservesIntrinsicSize": bool(style.get("preservesIntrinsicWidth")) or semantic in {"switch", "toggle", "stepper", "date-input", "color-picker"},
        },
        "appearance": control_appearance(
            semantic,
            foreground,
            background,
            border,
            selected_foreground,
            selected_background,
            disabled_foreground,
            number((states.get("disabled") or {}).get("opacity"), 0.5),
            first_color(style.get("accentColor")),
        ),
        "stateAppearances": visual_states,
        "behavior": {
            "preferredStyle": preferred_style(semantic, node),
            "stateNames": sorted(visual_states),
            "usesNativeStateMachine": True,
            "requiresWrapper": decision.get("decision") == "system-control-with-wrapper",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    screens = []
    for path in args.ir:
        payload = load(path)
        scale = min(max(number((payload.get("target") or {}).get("scale"), 1), 0.5), 3)
        for screen in payload.get("screens") or []:
            controls = [
                node_contract(node, scale)
                for node in screen.get("nodes") or []
                if str(node.get("semanticType") or "") in SEMANTICS and node.get("id")
            ]
            screens.append({"screenId": str(screen.get("id") or ""), "controls": controls})
    output = {
        "schemaVersion": SCHEMA_VERSION,
        "sources": {"uiIR": [{"path": str(path.resolve()), "sha256": digest(path)} for path in args.ir]},
        "screens": screens,
        "summary": {
            "screenCount": len(screens),
            "controlCount": sum(len(item["controls"]) for item in screens),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **output["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
