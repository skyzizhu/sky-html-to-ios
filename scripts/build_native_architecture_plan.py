#!/usr/bin/env python3
"""Build a deterministic native controller, navigation, and Safe Area plan from UI IR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-architecture-plan-1.0"
VALID_SCROLL_BEHAVIORS = {"fixed", "sticky", "scroll-away", "hide-on-scroll", "collapse", "appearance-change", "unknown"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--scroll-behavior", action="append", type=Path, default=[])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ui-stack", choices=("swiftui", "uikit"))
    parser.add_argument("--minimum-ios", default="16.0")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def region(screen: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = (screen.get("regions") or {}).get(key)
    return value if isinstance(value, dict) and value.get("nodeId") else None


def scroll_report_by_screen(paths: list[Path]) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for path in paths:
        report = load_json(path)
        screen_id = str(report.get("screenId") or "")
        if screen_id:
            reports[screen_id] = report
    return reports


def observed_bar_behavior(
    report: dict[str, Any] | None,
    edge: str,
    source_ids: set[str],
) -> tuple[str, list[str]]:
    if not report:
        return "unknown", []
    candidates = [item for item in report.get("regions") or [] if item.get("edge") == edge]
    if not candidates:
        return "unknown", []
    exact = [item for item in candidates if str(item.get("nodeId") or "") in source_ids]
    candidate = max(exact or candidates, key=lambda item: float(item.get("confidence") or 0))
    behavior = str(candidate.get("behavior") or "unknown")
    if behavior not in VALID_SCROLL_BEHAVIORS:
        behavior = "unknown"
    return behavior, [str(item) for item in candidate.get("evidence") or []]


def screen_plan(ir: dict[str, Any], report: dict[str, Any] | None, ui_stack: str) -> dict[str, Any]:
    screen = (ir.get("screens") or [])[0]
    screen_id = str(screen.get("id") or "screen")
    navigation = screen.get("navigation") or {}
    navigation_style = str(navigation.get("style") or (screen.get("systemChrome") or {}).get("navigationBar") or "hidden")
    tab = screen.get("tabContainer") if isinstance(screen.get("tabContainer"), dict) else None
    top = region(screen, "topBar")
    bottom = region(screen, "bottomBar")
    nodes = {str(node.get("id") or ""): node for node in screen.get("nodes") or []}

    def source_ids(value: dict[str, Any] | None) -> set[str]:
        if not value:
            return set()
        node_id = str(value.get("nodeId") or "")
        source = (nodes.get(node_id) or {}).get("source") or {}
        return {item for item in (node_id, str(source.get("domId") or ""), str(source.get("runtimeId") or "")) if item}

    top_behavior, top_evidence = observed_bar_behavior(report, "top", source_ids(top))
    bottom_behavior, bottom_evidence = observed_bar_behavior(report, "bottom", source_ids(bottom))

    if top_behavior == "unknown":
        top_behavior = "appearance-change" if navigation.get("scrollEdgeAppearance") not in {None, "", "automatic"} else "fixed"
    if bottom_behavior == "unknown" and bottom:
        bottom_behavior = "fixed"

    immersive = navigation_style == "immersive"
    safe_area_owner = "immersive-content" if immersive else "system"
    scroll_roots = [
        str(node.get("id"))
        for node in screen.get("nodes") or []
        if node.get("semanticType") in {"scroll", "list", "table", "collection"}
    ]
    root_id = str(screen.get("rootNodeId") or "")
    if not scroll_roots and root_id:
        scroll_roots = [root_id]

    actions = {str(item.get("action") or "") for item in ir.get("interactions") or []}
    navigation_mechanisms = sorted(actions & {"push", "pop", "pop-to-root", "replace-stack", "back"})
    presentation_mechanisms = sorted(actions & {
        "present", "present-sheet", "present-fullscreen", "present-popover", "present-alert",
        "present-confirmation", "present-menu", "dismiss", "overlay",
    })
    containment = sorted(actions & {"add-child", "remove-child"})

    warnings: list[str] = []
    if safe_area_owner == "system" and immersive:
        warnings.append("Conflicting Safe Area ownership was normalized to immersive-content.")
    if tab and bottom:
        warnings.append("Native tab ownership suppresses the page bottomBar to avoid duplicate bottom insets.")

    return {
        "screenId": screen_id,
        "controller": {
            "content": "UIViewController" if ui_stack == "uikit" else "SwiftUI.View",
            "navigationContainer": "UINavigationController" if ui_stack == "uikit" else "NavigationStack",
            "tabContainer": ("UITabBarController" if ui_stack == "uikit" else "TabView") if tab else None,
            "containment": containment,
        },
        "navigation": {
            "mechanisms": navigation_mechanisms,
            "barRendering": navigation_style,
            "barBehavior": top_behavior,
            "barNodeId": top.get("nodeId") if top else None,
            "evidence": top_evidence,
        },
        "bottomRegion": {
            "kind": (bottom or {}).get("kind") if bottom else ("native-tab-bar" if tab else "none"),
            "nodeId": bottom.get("nodeId") if bottom and not tab else None,
            "behavior": bottom_behavior if bottom and not tab else ("system-managed" if tab else "none"),
            "evidence": bottom_evidence,
        },
        "scroll": {
            "rootNodeIds": scroll_roots,
            "containerWidthPolicy": "full-parent-bounds",
            "containerHeightPolicy": "full-parent-bounds",
            "contentInsetAdjustment": "never" if immersive else "automatic",
            "customBarInsets": "safe-area-inset-once",
            "subtractSafeAreaFromFrame": False,
        },
        "safeArea": {
            "owner": safe_area_owner,
            "systemInsetsAppliedBy": "SwiftUI" if ui_stack == "swiftui" else "UIScrollView.adjustedContentInset",
            "backgroundMayExtendUnderChrome": True,
            "contentAvoidsSystemChrome": not immersive,
            "subtractFromContainerDimensions": False,
        },
        "presentations": presentation_mechanisms,
        "requiresResolution": False,
        "warnings": warnings,
    }


def main() -> int:
    args = parse_args()
    irs = [load_json(path) for path in args.ir]
    for path, ir in zip(args.ir, irs):
        if ir.get("schemaVersion") != "1.2" or not ir.get("screens"):
            raise ValueError(f"{path}: expected a validated UI IR 1.2 document")
    inferred = {str((ir.get("target") or {}).get("uiStack") or "swiftui").lower() for ir in irs}
    ui_stack = args.ui_stack or (next(iter(inferred)) if len(inferred) == 1 else None)
    if ui_stack not in {"swiftui", "uikit"}:
        raise ValueError("--ui-stack is required when UI IR targets disagree")
    behavior_reports = scroll_report_by_screen(args.scroll_behavior)
    screens = [screen_plan(ir, behavior_reports.get(str(ir["screens"][0].get("id") or "")), ui_stack) for ir in irs]
    ids = [screen["screenId"] for screen in screens]
    if len(ids) != len(set(ids)):
        raise ValueError("screen IDs must be unique")
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "uiStack": ui_stack,
        "minimumIOS": str(args.minimum_ios),
        "invariants": {
            "singleSafeAreaOwner": True,
            "scrollContainersUseFullParentBounds": True,
            "safeAreaNeverSubtractedFromWidthOrHeight": True,
            "systemAndCustomNavigationBarsNeverRenderTogether": True,
            "onePresentationOwnerPerState": True,
        },
        "screens": screens,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "screens": ids, "uiStack": ui_stack}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
