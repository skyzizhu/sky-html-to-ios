#!/usr/bin/env python3
"""Build deterministic HTML and iOS capture steps from UI IR visual states."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


TEXT_SEMANTICS = {"button", "heading", "label", "link", "menu-item", "option", "tab-item", "text"}
CONTROL_SEMANTICS = {
    "button", "checkbox", "date-input", "disclosure", "link", "number-input", "radio",
    "search-input", "segmented-control", "select", "slider", "stepper", "switch", "text-area", "text-input",
}
ASSET_SEMANTICS = {"icon", "image", "video"}


def numeric(value, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def build_validation_regions(screen: dict, target_viewport: dict) -> list[dict]:
    nodes = {str(node["id"]): node for node in screen.get("nodes") or []}
    root = nodes.get(str(screen.get("rootNodeId") or "")) or {}
    root_rect = (root.get("layout") or {}).get("rect") or {}
    root_x = numeric(root_rect.get("x"))
    root_y = numeric(root_rect.get("y"))
    root_width = max(numeric(root_rect.get("width"), numeric(target_viewport.get("width"), 393)), 1)
    root_height = max(numeric(root_rect.get("height"), numeric(target_viewport.get("height"), 852)), 1)
    target_width = max(numeric(target_viewport.get("width"), root_width), 1)
    target_height = max(numeric(target_viewport.get("height"), root_height), 1)

    def initially_visible(node: dict) -> bool:
        visited: set[str] = set()
        current = node
        while current:
            current_id = str(current.get("id") or "")
            if current_id in visited:
                return False
            visited.add(current_id)
            if (current.get("state") or {}).get("initiallyVisible") is False:
                return False
            current = nodes.get(str(current.get("parentId") or ""))
        return True

    def normalized_rect(node: dict, expand: int = 0) -> list[int] | None:
        rect = (node.get("layout") or {}).get("rect") or {}
        width = numeric(rect.get("width"))
        height = numeric(rect.get("height"))
        if width <= 0 or height <= 0:
            return None
        left = round((numeric(rect.get("x")) - root_x) * target_width / root_width) - expand
        top = round((numeric(rect.get("y")) - root_y) * target_height / root_height) - expand
        right = round((numeric(rect.get("x")) - root_x + width) * target_width / root_width) + expand
        bottom = round((numeric(rect.get("y")) - root_y + height) * target_height / root_height) + expand
        left, top = max(0, left), max(0, top)
        right, bottom = min(round(target_width), right), min(round(target_height), bottom)
        return [left, top, right - left, bottom - top] if right > left and bottom > top else None

    regions = [{
        "id": "screen.viewport",
        "nodeId": screen.get("rootNodeId"),
        "category": "viewport",
        "criticality": "critical",
        "toleranceProfile": "structure",
        "rect": [0, 0, round(target_width), round(target_height)],
    }]
    persistent = ((screen.get("regions") or {}).get("topBar"), (screen.get("regions") or {}).get("bottomBar"))
    for name, item in zip(("navigation", "bottom-bar"), persistent):
        node = nodes.get(str((item or {}).get("nodeId") or ""))
        rect = normalized_rect(node or {})
        if rect:
            regions.append({
                "id": f"screen.{name}",
                "nodeId": node.get("id"),
                "category": "system-chrome" if name == "navigation" else "navigation",
                "criticality": "critical",
                "toleranceProfile": "structure",
                "rect": rect,
            })

    for node in screen.get("nodes") or []:
        if not initially_visible(node):
            continue
        semantic = str(node.get("semanticType") or "")
        content = node.get("content") or {}
        has_text = bool(str(content.get("text") or content.get("placeholder") or "").strip())
        if semantic in CONTROL_SEMANTICS:
            category, profile, criticality, expand = "control", "control", "high", 2
        elif semantic in ASSET_SEMANTICS or node.get("assetRef"):
            category, profile, criticality, expand = "asset", "asset", "medium", 1
        elif has_text or semantic in TEXT_SEMANTICS:
            category, profile, criticality, expand = "typography", "text", "high" if semantic == "heading" else "medium", 2
        else:
            continue
        rect = normalized_rect(node, expand)
        if not rect:
            continue
        regions.append({
            "id": f"node.{node['id']}",
            "nodeId": node["id"],
            "category": category,
            "semanticType": semantic,
            "criticality": criticality,
            "toleranceProfile": profile,
            "rect": rect,
        })
    return regions


def build_comparison_masks(screen: dict, target_viewport: dict) -> list[dict]:
    """Exclude native system-owned pixels that cannot deterministically match HTML chrome."""
    chrome = screen.get("systemChrome") or {}
    width = max(round(numeric(target_viewport.get("width"), 393)), 1)
    height = max(round(numeric(target_viewport.get("height"), 852)), 1)
    masks = []
    if chrome.get("statusBar") == "native":
        masks.append({
            "reason": "native-status-bar-is-system-owned-and-time-dependent",
            "rect": [0, 0, width, min(height, max(20, min(64, round(height * 0.07))))],
        })
    if chrome.get("homeIndicator") == "native":
        mask_height = min(height, max(10, min(20, round(height * 0.018))))
        masks.append({
            "reason": "native-home-indicator-is-system-owned",
            "rect": [0, height - mask_height, width, mask_height],
        })
    return masks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ui_ir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--html", type=Path, help="Override the HTML entry from UI IR")
    args = parser.parse_args()

    data = json.loads(args.ui_ir.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "1.2":
        parser.error("UI IR schemaVersion must be 1.2")
    screen = data["screens"][0]
    nodes = {node["id"]: node for node in screen["nodes"]}
    interactions = {item["id"]: item for item in data.get("interactions", [])}
    states_by_id = {str(item.get("id")): item for item in data.get("states", [])}
    target = data.get("target") or {}
    source_viewport = (data.get("source") or {}).get("viewport") or {}
    target_viewport = target.get("viewportPt") or {}
    source_entry = str(args.html.resolve()) if args.html else (data.get("source") or {}).get("entry")
    source_kind = "html" if source_entry and not str(source_entry).startswith(("http://", "https://")) else "url"
    activation = (data.get("source") or {}).get("screenActivation") or {}
    activation_selector = next(iter(activation.get("selectors") or []), None)
    activation_settle_delay = max(int(activation.get("settleDelayMs") or 0), 0)

    states = []
    for state in data.get("visualStates") or []:
        html_actions = []
        ios_actions = []
        if activation_selector:
            html_actions.append({"type": "click", "selector": activation_selector, "purpose": "activate-screen"})
            if activation_settle_delay:
                html_actions.append({
                    "type": "wait",
                    "ms": activation_settle_delay,
                    "purpose": "match-render-tree-capture-checkpoint",
                })
        scroll = state.get("scroll")
        if scroll in {"top", "middle", "bottom"}:
            html_actions.append({"type": "scroll", "selector": screen.get("sourceSelector"), "position": scroll})
            ios_actions.append({"type": "scroll", "accessibilityIdentifier": screen.get("rootNodeId"), "position": scroll})
        sequence = state.get("interactionSequence") or ([state.get("triggerInteractionId")] if state.get("triggerInteractionId") else [])
        for interaction_id in sequence:
            interaction = interactions.get(interaction_id)
            if not interaction:
                continue
            source_node = nodes.get(interaction.get("sourceNodeId")) or {}
            selector = (source_node.get("source") or {}).get("selector") or interaction.get("sourceSelector")
            if selector:
                html_actions.append({"type": "click", "selector": selector, "interactionId": interaction_id})
            if interaction.get("sourceNodeId"):
                ios_action = {"type": "tap", "accessibilityIdentifier": interaction.get("sourceNodeId"), "interactionId": interaction_id}
                target_state = states_by_id.get(str(interaction.get("target") or "")) or {}
                if str(target_state.get("kind") or "") == "local-state":
                    target_ids = {str(item) for item in target_state.get("targetNodeIds") or []}
                    current = str(interaction.get("sourceNodeId") or "")
                    while current:
                        if current in target_ids:
                            if current != interaction.get("sourceNodeId"):
                                ios_action["assertion"] = {
                                    "type": "not-exists",
                                    "accessibilityIdentifier": current,
                                }
                            break
                        current = str((nodes.get(current) or {}).get("parentId") or "")
                ios_actions.append(ios_action)
        states.append({
            "id": state["id"],
            "name": state.get("name") or state["id"],
            "required": state.get("required", True),
            "resetBeforeCapture": True,
            "htmlActions": html_actions,
            "iosActions": ios_actions,
            "animationProgress": state.get("animationProgress"),
            "interactionSequence": sequence,
        })

    manifest = {
        "schemaVersion": "visual-state-manifest-1.0",
        "screenId": screen.get("id"),
        "source": {source_kind: source_entry},
        "viewport": {"width": source_viewport.get("width", target_viewport.get("width", 393)), "height": source_viewport.get("height", target_viewport.get("height", 852))},
        "sourceViewport": {"width": source_viewport.get("width", target_viewport.get("width", 393)), "height": source_viewport.get("height", target_viewport.get("height", 852))},
        "targetViewport": {"width": target_viewport.get("width", 393), "height": target_viewport.get("height", 852)},
        "normalization": {"mode": "cover", "position": "centre", "purpose": "fixed-design-token-normalization-for-visual-comparison"},
        "appearance": target.get("appearance", "light"),
        "locale": (data.get("source") or {}).get("language"),
        "layoutDirection": (data.get("source") or {}).get("direction", "ltr"),
        "rootSelector": screen.get("sourceSelector") or "html",
        "systemChrome": screen.get("systemChrome") or {},
        "comparisonMasks": build_comparison_masks(screen, target_viewport),
        "validationRegions": build_validation_regions(screen, target_viewport),
        "states": states,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "states": len(states)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
