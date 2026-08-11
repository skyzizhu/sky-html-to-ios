#!/usr/bin/env python3
"""Build the executable scroll, coordinate-space, Safe Area, and bar attachment contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "scroll-and-attachment-plan-1.0"
SCROLL_AXES = {"none", "horizontal", "vertical", "both"}
INPUT_SEMANTICS = {
    "text-field", "text-input", "search-field", "search-input", "secure-field",
    "secure-input", "number-input", "text-area", "date-input",
}
NATIVE_INTERNAL_SCROLL_SEMANTICS = {
    "text-area", "select", "multi-select", "picker", "wheel-picker",
    "date-input", "calendar-view", "search-bar",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--architecture-plan", required=True, type=Path)
    parser.add_argument("--scroll-behavior", action="append", type=Path, default=[])
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(-?(?:\d+(?:\.\d*)?|\.\d+))(?:px|pt)?\s*", value, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return 0.0


def bottom_padding(node: dict[str, Any]) -> float:
    padding = (node.get("style") or {}).get("padding")
    if isinstance(padding, list) and len(padding) >= 3:
        return max(number(padding[2]), 0)
    if isinstance(padding, dict):
        return max(number(padding.get("bottom")), 0)
    return 0.0


def screen_map(paths: list[Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in paths:
        for screen in load(path).get("screens") or []:
            result[str(screen.get("id") or "")] = screen
    return result


def report_map(paths: list[Path]) -> dict[str, dict[str, Any]]:
    return {
        str(report.get("screenId")): report
        for report in (load(path) for path in paths)
        if report.get("screenId")
    }


def node_index(screen: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    nodes = {str(node["id"]): node for node in screen.get("nodes") or [] if node.get("id")}
    children: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        children.setdefault(str(node.get("parentId") or ""), []).append(node_id)
    return nodes, children


def descendants(node_id: str, children: dict[str, list[str]]) -> set[str]:
    found: set[str] = set()
    pending = list(children.get(node_id) or [])
    while pending:
        child = pending.pop()
        if child in found:
            continue
        found.add(child)
        pending.extend(children.get(child) or [])
    return found


def axis(node: dict[str, Any]) -> str:
    if str(node.get("semanticType") or "") in NATIVE_INTERNAL_SCROLL_SEMANTICS:
        return "none"
    layout = node.get("layout") or {}
    value = str(layout.get("scrollAxis") or "none")
    return value if value in SCROLL_AXES else "none"


def nearest_scroll_owner(
    node_id: str,
    nodes: dict[str, dict[str, Any]],
    root_owner_id: str | None,
) -> str | None:
    if node_id == root_owner_id:
        return None
    parent_id = str((nodes.get(node_id) or {}).get("parentId") or "")
    while parent_id:
        parent = nodes.get(parent_id) or {}
        if axis(parent) != "none":
            return parent_id
        parent_id = str(parent.get("parentId") or "")
    return root_owner_id


def observed_behavior(report: dict[str, Any], node: dict[str, Any], edge: str) -> tuple[str, list[str]]:
    source = node.get("source") or {}
    candidates = [item for item in report.get("regions") or [] if item.get("edge") == edge]
    identities = {
        str(node.get("id") or ""), str(source.get("runtimeId") or ""),
        str(source.get("domId") or ""), str(source.get("selector") or ""),
    }
    exact = [
        item for item in candidates
        if str(item.get("nodeId") or "") in identities or str(item.get("selector") or "") in identities
    ]
    # A same-edge candidate may belong to another header/footer in a state board.
    # Only exact identity evidence is strong enough to override the architecture plan.
    selected = max(exact, key=lambda item: float(item.get("confidence") or 0), default=None)
    if not selected:
        return "unknown", []
    return str(selected.get("behavior") or "unknown"), [str(item) for item in selected.get("evidence") or []]


def region_contract(
    edge: str,
    region: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
    report: dict[str, Any],
    safe_owner: str,
    root_scroll_owner_id: str | None,
) -> dict[str, Any]:
    node_id = str(region.get("nodeId") or "") or None
    node = nodes.get(node_id or "") or {}
    declared = str(region.get("behavior") or "unknown")
    observed, evidence = observed_behavior(report, node, edge) if node_id else ("none", [])
    behavior = observed if observed != "unknown" else declared
    if not node_id:
        behavior = "none"
    if behavior in {"fixed", "hide-on-scroll", "collapse", "appearance-change"}:
        attachment = "viewport-overlay"
        coordinate_space = "safe-area" if safe_owner == "system" else "app-root"
        lifted = True
    elif behavior == "sticky":
        attachment = "scroll-sticky"
        coordinate_space = "scroll-frame"
        lifted = False
    else:
        attachment = "scroll-content"
        coordinate_space = "scroll-content"
        lifted = False
    subtree = descendants(node_id, children) | ({node_id} if node_id else set())
    has_input = any(str((nodes.get(item) or {}).get("semanticType") or "") in INPUT_SEMANTICS for item in subtree)
    return {
        "edge": edge,
        "nodeId": node_id,
        "behavior": behavior,
        "attachment": attachment,
        "coordinateSpace": coordinate_space,
        "scrollOwnerNodeId": None if lifted else root_scroll_owner_id,
        "safeAreaPolicy": "system-managed" if safe_owner == "system" else "source-immersive",
        "subtractSafeAreaFromDimensions": False,
        "liftedFromContent": lifted,
        "keyboardAvoidance": "keyboard-layout-guide" if edge == "bottom" and has_input else "none",
        "nativePrimitive": {
            "swiftUI": "safeAreaInset/overlay" if lifted else "scroll-content/pinned-view",
            "uiKit": "viewport sibling view" if lifted else "scroll content/supplementary view",
        },
        "evidence": [f"architecture:{declared}", f"probe:{observed}", *evidence],
    }


def build_screen(
    screen: dict[str, Any],
    architecture: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    screen_id = str(screen.get("id") or "")
    nodes, children = node_index(screen)
    layers = architecture.get("layers") or {}
    content = layers.get("contentContainer") or {}
    regions = layers.get("screenRegions") or {}
    safe_area = architecture.get("safeArea") or {}
    safe_owner = str(safe_area.get("owner") or "system")
    root_scroll_owner_id = str(content.get("nodeId") or "") or None
    root_axis = str(content.get("scrollAxis") or axis(nodes.get(root_scroll_owner_id or "") or {}) or "none")
    if root_axis not in SCROLL_AXES:
        root_axis = "none"
    root_node = nodes.get(root_scroll_owner_id or "") or {}
    root_hints = root_node.get("iosHints") or {}
    root_semantic = str(root_node.get("semanticType") or "")
    explicit_bidirectional = (
        root_axis == "both"
        and (
            root_semantic in {"data-table", "canvas", "diagram", "map"}
            or str(root_hints.get("scroll-root") or "").lower() in {"both", "bidirectional"}
        )
    )
    top = region_contract("top", regions.get("top") or {}, nodes, children, report, safe_owner, root_scroll_owner_id)
    bottom = region_contract("bottom", regions.get("bottom") or {}, nodes, children, report, safe_owner, root_scroll_owner_id)
    bottom_node = nodes.get(str(bottom.get("nodeId") or "")) or {}
    bottom_rect = (bottom_node.get("layout") or {}).get("rect") or {}
    bottom_height = max(number(bottom_rect.get("height")), 0)
    source_bottom_padding = bottom_padding(root_node)
    bottom_relationship = "overlay" if bottom.get("attachment") == "viewport-overlay" else "docked" if bottom.get("nodeId") else "none"
    if bottom_relationship == "overlay" and source_bottom_padding > 0:
        reservation_owner = "source-padding"
        additional_bottom_inset = 0.0
    elif bottom_relationship == "overlay":
        reservation_owner = "native-content-inset"
        additional_bottom_inset = bottom_height
    elif bottom_relationship == "docked":
        reservation_owner = "native-safe-area-inset"
        additional_bottom_inset = 0.0
    else:
        reservation_owner = "none"
        additional_bottom_inset = 0.0
    node_contracts = []
    for node_id, node in nodes.items():
        position = str((node.get("layout") or {}).get("position") or (node.get("style") or {}).get("position") or "static")
        node_axis = axis(node)
        owner = nearest_scroll_owner(node_id, nodes, root_scroll_owner_id)
        if position == "fixed":
            coordinate_space, attachment, owner = "app-root", "viewport-fixed", None
        elif position == "sticky":
            coordinate_space, attachment = "scroll-frame", "scroll-sticky"
        elif position == "absolute":
            coordinate_space, attachment = "positioned-ancestor", "positioned"
        else:
            coordinate_space, attachment = "scroll-content", "scroll-content"
        node_contracts.append({
            "nodeId": node_id,
            "scrollAxis": node_axis,
            "scrollOwnerNodeId": owner,
            "coordinateSpace": coordinate_space,
            "attachment": attachment,
            "safeAreaPolicy": "inherited",
            "directionalLockEnabled": node_axis in {"horizontal", "vertical"},
            "allowsSameAxisNestedScroll": False,
        })
    return {
        "screenId": screen_id,
        "rootScrollOwnerNodeId": root_scroll_owner_id,
        "rootScrollAxis": root_axis,
        "rootSemantic": root_semantic,
        "rootBidirectionalScrollExplicit": explicit_bidirectional,
        "safeArea": {
            "owner": safe_owner,
            "contentInsetAdjustment": str((architecture.get("scroll") or {}).get("contentInsetAdjustment") or "automatic"),
            "subtractFromContainerDimensions": False,
        },
        "viewportOccupancy": {
            "framePolicy": "fill-available-bounds",
            "widthOwner": "screen-container",
            "heightOwner": "screen-container",
            "bottomBarRelationship": bottom_relationship,
            "bottomReservationOwner": reservation_owner,
            "sourceBottomPaddingPt": round(source_bottom_padding, 4),
            "bottomBarHeightPt": round(bottom_height, 4),
            "additionalBottomContentInsetPt": round(additional_bottom_inset, 4),
            "subtractBottomBarFromFrame": bottom_relationship == "docked",
        },
        "regions": {"top": top, "bottom": bottom},
        "nodes": node_contracts,
    }


def main() -> int:
    args = parse_args()
    architecture_payload = load(args.architecture_plan)
    architectures = {str(item.get("screenId") or ""): item for item in architecture_payload.get("screens") or []}
    screens = screen_map(args.ir)
    reports = report_map(args.scroll_behavior)
    output = {
        "schemaVersion": SCHEMA_VERSION,
        "sources": {
            "uiIR": [{"path": str(path.resolve()), "sha256": digest(path)} for path in args.ir],
            "architecturePlan": {"path": str(args.architecture_plan.resolve()), "sha256": digest(args.architecture_plan)},
            "scrollBehavior": [{"path": str(path.resolve()), "sha256": digest(path)} for path in args.scroll_behavior],
        },
        "screens": [
            build_screen(screen, architectures.get(screen_id) or {}, reports.get(screen_id) or {})
            for screen_id, screen in screens.items()
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "screens": len(output["screens"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
