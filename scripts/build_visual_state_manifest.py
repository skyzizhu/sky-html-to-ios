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
    "search-input", "secure-input", "segmented-control", "select", "slider", "stepper", "switch", "text-area", "text-input",
}
ASSET_SEMANTICS = {"icon", "image", "video"}


def build_form_checks(screen: dict) -> list[dict]:
    nodes = {str(node.get("id")): node for node in screen.get("nodes") or []}
    children: dict[str, list[str]] = {}
    dom_ids: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        children.setdefault(str(node.get("parentId") or ""), []).append(node_id)
        dom_id = str((node.get("source") or {}).get("domId") or "").strip()
        if dom_id:
            dom_ids.setdefault(dom_id, []).append(node_id)

    def options_for(node: dict) -> list[dict]:
        state = node.get("state") or {}
        owners = [str(node.get("id") or "")]
        for linked_id in (state.get("listID"), state.get("controlledID")):
            owners.extend(dom_ids.get(str(linked_id or "").strip(), []))
        pending = [child_id for owner in owners for child_id in children.get(owner, [])]
        options = []
        visited = set()
        while pending:
            candidate_id = pending.pop(0)
            if candidate_id in visited:
                continue
            visited.add(candidate_id)
            candidate = nodes.get(candidate_id) or {}
            if candidate.get("semanticType") == "option":
                options.append(candidate)
            else:
                pending.extend(children.get(candidate_id, []))
        return options

    checks = []
    for node_id, node in nodes.items():
        semantic = str(node.get("semanticType") or "")
        state = node.get("state") or {}
        behavior = node.get("textBehavior") or {}
        enabled = state.get("enabled") is not False
        if semantic in {"text-input", "search-input", "secure-input", "number-input", "text-area"}:
            if not enabled:
                checks.append({"id": f"{node_id}.disabled", "type": "disabled", "accessibilityIdentifier": node_id})
            elif behavior.get("editable") is True:
                checks.append({
                    "id": f"{node_id}.input",
                    "type": "input",
                    "accessibilityIdentifier": node_id,
                    "value": "HTMLToIOSTest",
                })
            else:
                checks.append({"id": f"{node_id}.readonly", "type": "readonly", "accessibilityIdentifier": node_id})
        has_linked_options = bool(str(state.get("listID") or "").strip() or str(state.get("controlledID") or "").strip())
        option_nodes = options_for(node) if semantic in {"select", "multi-select", "wheel-picker"} or has_linked_options else []
        if semantic in {"select", "multi-select", "wheel-picker"} or option_nodes:
            candidate = next((
                option for option in option_nodes
                if (option.get("state") or {}).get("enabled") is not False
                and (option.get("state") or {}).get("selected") is not True
            ), next((option for option in option_nodes if (option.get("state") or {}).get("enabled") is not False), None))
            if enabled and candidate:
                title = str((candidate.get("content") or {}).get("text") or "").strip()
                value = str((candidate.get("content") or {}).get("value") or (candidate.get("state") or {}).get("value") or title)
                if title:
                    checks.append({
                        "id": f"{node_id}.select",
                        "type": "select",
                        "accessibilityIdentifier": (
                            f"{node_id}.suggestions"
                            if semantic in {"text-input", "search-input", "number-input"}
                            else node_id
                        ),
                        "resultAccessibilityIdentifier": node_id,
                        "value": title,
                        "expectedValue": value,
                    })
    return checks


def numeric(value, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def build_geometry_nodes(screen: dict, active_state: dict | None = None) -> list[dict]:
    nodes = {str(node["id"]): node for node in screen.get("nodes") or []}
    active_state_id = str((active_state or {}).get("id") or "")
    removed_node_ids = {
        str(item.get("targetNodeId"))
        for item in ((active_state or {}).get("stateDelta") or {}).get("operations") or []
        if item.get("kind") in {"remove-subtree", "replace-subtree"} and item.get("targetNodeId")
    }
    child_counts: dict[str, int] = {}
    for node in screen.get("nodes") or []:
        parent_id = str(node.get("parentId") or "")
        if parent_id:
            child_counts[parent_id] = child_counts.get(parent_id, 0) + 1

    def initially_visible(node: dict) -> bool:
        visited: set[str] = set()
        current = node
        state_owned = False
        while current:
            current_id = str(current.get("id") or "")
            if current_id in visited:
                return False
            visited.add(current_id)
            if current_id in removed_node_ids:
                return False
            if active_state_id and str(
                (current.get("iosHints") or {}).get("state-owner") or ""
            ) == active_state_id:
                state_owned = True
            if (current.get("state") or {}).get("initiallyVisible") is False and not state_owned:
                return False
            current = nodes.get(str(current.get("parentId") or ""))
        return True

    return [
        {
            "nodeId": node["id"],
            "parentNodeId": node.get("parentId"),
            "semanticType": node.get("semanticType") or "container",
            "scrollAxis": (node.get("layout") or {}).get("scrollAxis") or "none",
            "position": (node.get("layout") or {}).get("position") or "static",
            "hasChildren": child_counts.get(str(node["id"]), 0) > 0,
            "isDecorative": bool((node.get("content") or {}).get("isDecorative"))
                or str(node.get("semanticType") or "") == "decoration",
        }
        for node in screen.get("nodes") or []
        if initially_visible(node)
    ]


def build_validation_regions(
    screen: dict,
    target_viewport: dict,
    design_scale: float = 1.0,
    active_state: dict | None = None,
    screen_context: dict | None = None,
) -> list[dict]:
    nodes = {str(node["id"]): node for node in screen.get("nodes") or []}
    active_state_id = str((active_state or {}).get("id") or "")
    removed_node_ids = {
        str(item.get("targetNodeId"))
        for item in ((active_state or {}).get("stateDelta") or {}).get("operations") or []
        if item.get("kind") in {"remove-subtree", "replace-subtree"} and item.get("targetNodeId")
    }
    screen_context = screen_context or {}
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
        state_owned = False
        while current:
            current_id = str(current.get("id") or "")
            if current_id in visited:
                return False
            visited.add(current_id)
            if current_id in removed_node_ids:
                return False
            if active_state_id and str(
                (current.get("iosHints") or {}).get("state-owner") or ""
            ) == active_state_id:
                state_owned = True
            if (current.get("state") or {}).get("initiallyVisible") is False and not state_owned:
                return False
            current = nodes.get(str(current.get("parentId") or ""))
        return True

    visual_root_rect = screen_context.get("visualRootRect") or {}
    visual_root_width = numeric(visual_root_rect.get("width"))
    visual_root_height = numeric(visual_root_rect.get("height"))
    uses_browser_visual_root = visual_root_width > 0 and visual_root_height > 0
    comparison_root_width = visual_root_width if uses_browser_visual_root else root_width
    comparison_root_height = visual_root_height if uses_browser_visual_root else root_height
    uniform_scale = target_width / comparison_root_width
    scaled_root_height = comparison_root_height * uniform_scale
    cover_crop_top = (
        max((scaled_root_height - target_height) / 2, 0)
        if abs(design_scale - 1) > 0.001 and scaled_root_height > target_height
        else 0
    )

    def normalized_rect(node: dict, expand: int = 0) -> list[int] | None:
        layout = node.get("layout") or {}
        source_rect = layout.get("sourceRectCssPx") or {}
        uses_source_rect = uses_browser_visual_root and numeric(source_rect.get("width")) > 0 and numeric(source_rect.get("height")) > 0
        rect = source_rect if uses_source_rect else (layout.get("rect") or {})
        width = numeric(rect.get("width"))
        height = numeric(rect.get("height"))
        if width <= 0 or height <= 0:
            return None
        origin_x = numeric(visual_root_rect.get("x")) if uses_source_rect else root_x
        origin_y = numeric(visual_root_rect.get("y")) if uses_source_rect else root_y
        scale = uniform_scale if uses_source_rect else target_width / root_width
        crop_top = cover_crop_top if uses_source_rect else (
            max((root_height * scale - target_height) / 2, 0)
            if abs(design_scale - 1) > 0.001 and root_height * scale > target_height
            else 0
        )
        left = round((numeric(rect.get("x")) - origin_x) * scale) - expand
        top = round((numeric(rect.get("y")) - origin_y) * scale - crop_top) - expand
        right = round((numeric(rect.get("x")) - origin_x + width) * scale) + expand
        bottom = round((numeric(rect.get("y")) - origin_y + height) * scale - crop_top) + expand
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
        text_behavior = node.get("textBehavior") or {}
        has_text = bool(str(content.get("text") or content.get("placeholder") or "").strip())
        if semantic in CONTROL_SEMANTICS or text_behavior.get("role") == "input":
            category, profile, criticality, expand = "control", "control", "high", 2
        elif semantic in ASSET_SEMANTICS or node.get("assetRef"):
            category, profile, criticality, expand = "asset", "asset", "medium", 1
        elif has_text or semantic in TEXT_SEMANTICS:
            category, profile, criticality, expand = "typography", "text", "high" if semantic == "heading" else "medium", 2
        else:
            continue
        rect = normalized_rect(node, expand)
        geometry_rect = normalized_rect(node)
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
            "geometryRect": geometry_rect,
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
    screen_context = (data.get("source") or {}).get("screenContext") or {}
    visual_root_selector = str(screen_context.get("visualRootSelector") or "").strip()
    comparison_root_selector = visual_root_selector or screen.get("sourceSelector") or "html"
    scroll_root_selector = next((
        str(item.get("selector") or "").strip()
        for item in screen_context.get("ancestorChain") or []
        if str(((item.get("style") or {}).get("overflowY") or "")).lower() in {"auto", "scroll"}
        and str(item.get("selector") or "").strip()
    ), screen.get("sourceSelector") or comparison_root_selector)
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
            html_actions.append({"type": "scroll", "selector": scroll_root_selector, "position": scroll})
            # Every state launches a fresh app at the initial offset. Requiring a
            # root accessibility element for "top" can skip an otherwise valid
            # initial capture when the native root is intentionally grouped.
            if scroll != "top":
                ios_actions.append({"type": "scroll", "accessibilityIdentifier": screen.get("rootNodeId"), "position": scroll})
        sequence = state.get("interactionSequence") or ([state.get("triggerInteractionId")] if state.get("triggerInteractionId") else [])
        active_state = None
        html_root_selector = None
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
                if target_state:
                    active_state = target_state
                    html_root_selector = (
                        (target_state.get("visualRepresentation") or {}).get("sourceSelector")
                        or html_root_selector
                    )
                target_kind = str(target_state.get("kind") or "")
                state_delta = target_state.get("stateDelta") or {}
                if state_delta.get("nativeStrategy") == "contextual-item-actions":
                    contextual_target = str(state_delta.get("contextualTargetNodeId") or "")
                    contextual_actions = [
                        str(item)
                        for item in state_delta.get("contextualActionRootNodeIds") or []
                        if str(item)
                    ]
                    if contextual_target:
                        ios_action = {
                            "type": "swipe-left",
                            "accessibilityIdentifier": contextual_target,
                            "interactionId": interaction_id,
                        }
                        if contextual_actions:
                            ios_action["assertion"] = {
                                "type": "exists",
                                "accessibilityIdentifier": f"{target_state.get('id')}.contextual.1",
                            }
                if any(token in target_kind for token in ("sheet", "modal", "popover", "overlay", "alert", "dialog")):
                    target_ids = [str(item) for item in target_state.get("targetNodeIds") or [] if str(item)]
                    if target_ids:
                        ios_action["assertion"] = {
                            "type": "exists",
                            "accessibilityIdentifier": target_ids[0],
                        }
                elif target_kind == "local-state":
                    target_ids = {str(item) for item in target_state.get("targetNodeIds") or []}
                    runtime_after_targets = (
                        (((interaction.get("evidence") or {}).get("runtime") or {}).get("after") or {}).get("targets")
                        or {}
                    )
                    target_selector = str(target_state.get("targetSelector") or "")
                    runtime_target = runtime_after_targets.get(target_selector) if target_selector else None
                    runtime_confirms_visible = (
                        isinstance(runtime_target, dict) and runtime_target.get("visible") is True
                    )
                    current = str(interaction.get("sourceNodeId") or "")
                    while current:
                        if current in target_ids:
                            if current != interaction.get("sourceNodeId") and not runtime_confirms_visible:
                                ios_action["assertion"] = {
                                    "type": "not-exists",
                                    "accessibilityIdentifier": current,
                                }
                            break
                        current = str((nodes.get(current) or {}).get("parentId") or "")
                ios_actions.append(ios_action)
        if html_root_selector:
            html_actions = [
                item for item in html_actions
                if not item.get("interactionId")
            ]
        state_payload = {
            "id": state["id"],
            "name": state.get("name") or state["id"],
            "required": state.get("required", True),
            "resetBeforeCapture": True,
            "htmlActions": html_actions,
            "iosActions": ios_actions,
            "animationProgress": state.get("animationProgress"),
            "interactionSequence": sequence,
            "htmlRootSelector": html_root_selector,
            "activeStateId": (active_state or {}).get("id"),
            "geometryNodes": build_geometry_nodes(screen, active_state),
            "validationRegions": build_validation_regions(
                screen,
                target_viewport,
                numeric(target.get("scale"), 1),
                active_state,
                screen_context,
            ),
        }
        states.append(state_payload)

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
        "rootSelector": comparison_root_selector,
        "systemChrome": screen.get("systemChrome") or {},
        "comparisonMasks": build_comparison_masks(screen, target_viewport),
        "geometryNodes": build_geometry_nodes(screen),
        "validationRegions": build_validation_regions(
            screen,
            target_viewport,
            numeric(target.get("scale"), 1),
            screen_context=screen_context,
        ),
        "states": states,
        "formChecks": build_form_checks(screen),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "states": len(states)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
