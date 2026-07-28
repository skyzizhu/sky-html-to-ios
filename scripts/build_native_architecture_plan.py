#!/usr/bin/env python3
"""Build a deterministic native controller, navigation, and Safe Area plan from UI IR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-architecture-plan-1.1"
VALID_SCROLL_BEHAVIORS = {"fixed", "sticky", "scroll-away", "hide-on-scroll", "collapse", "appearance-change", "unknown"}
CONTENT_SEMANTICS = {"scroll", "list", "sectioned-list", "data-table", "grid", "collection", "carousel"}
REUSABLE_ITEM_SEMANTICS = {"list-item", "table-row", "table-cell"}
NON_LEAF_SEMANTICS = CONTENT_SEMANTICS | REUSABLE_ITEM_SEMANTICS | {
    "container", "header", "footer", "navigation", "navigation-bar", "tab-bar", "modal", "toast",
}

LEAF_COMPONENTS: dict[str, tuple[str, str, str]] = {
    "text": ("text", "Text", "UILabel"),
    "label": ("text", "Text", "UILabel"),
    "heading": ("text", "Text", "UILabel"),
    "icon": ("image", "Image", "UIImageView"),
    "image": ("image", "Image", "UIImageView"),
    "divider": ("decoration", "Divider/Rectangle", "UIView"),
    "separator": ("decoration", "Divider/Rectangle", "UIView"),
    "decoration": ("decoration", "Shape/Canvas", "UIView/CALayer"),
    "spacer": ("layout", "Spacer", "UIView"),
    "button": ("control", "Button", "UIButton"),
    "icon-button": ("control", "Button with Image", "UIButton"),
    "link": ("control", "Button/Link", "UIButton"),
    "menu-item": ("control", "Button/Menu", "UIButton/UIAction"),
    "tab-item": ("control", "Tab item", "UITabBarItem"),
    "text-field": ("input", "TextField", "UITextField"),
    "text-input": ("input", "TextField", "UITextField"),
    "search-field": ("input", "TextField/.searchable", "UISearchTextField"),
    "search-input": ("input", "TextField/.searchable", "UISearchTextField"),
    "secure-field": ("input", "SecureField", "UITextField"),
    "secure-input": ("input", "SecureField", "UITextField"),
    "number-input": ("input", "TextField", "UITextField"),
    "text-area": ("input", "TextEditor", "UITextView"),
    "file-input": ("control", "Button + fileImporter", "UIButton + UIDocumentPickerViewController"),
    "switch": ("control", "Toggle", "UISwitch"),
    "toggle": ("control", "Toggle", "UISwitch"),
    "checkbox": ("control", "ToggleStyle", "UIButton/UIControl"),
    "radio": ("control", "Custom option control", "UIButton/UIControl"),
    "segmented-control": ("control", "Picker(.segmented)", "UISegmentedControl"),
    "select": ("control", "Picker/Menu", "UIButton/UIMenu"),
    "picker": ("control", "Picker", "UIPickerView"),
    "multi-select": ("control", "Multi-select List/Menu", "UITableView/UIMenu"),
    "slider": ("control", "Slider", "UISlider"),
    "stepper": ("control", "Stepper", "UIStepper"),
    "date-input": ("control", "DatePicker", "UIDatePicker"),
    "color-picker": ("control", "ColorPicker", "UIColorWell"),
    "progress": ("status", "ProgressView", "UIProgressView"),
    "meter": ("status", "ProgressView", "UIProgressView"),
    "loading": ("status", "ProgressView", "UIActivityIndicatorView"),
    "disclosure": ("control", "DisclosureGroup", "UIControl"),
    "disclosure-trigger": ("control", "Button", "UIControl"),
    "option": ("control-part", "Picker option", "UIAction/picker row"),
    "table-header": ("text", "Text", "UILabel"),
    "table-cell": ("content", "Cell content View", "UITableViewCell/UICollectionViewCell contentView"),
    "canvas-artwork": ("artwork", "Canvas/Image", "UIView/CALayer/UIImageView"),
    "video": ("media", "VideoPlayer", "AVPlayerViewController"),
    "audio": ("media", "Project audio view", "Project AVFoundation view"),
    "map": ("media", "Map", "MKMapView"),
    "embedded-content": ("unsupported", "Unsupported native fallback", "Unsupported native fallback"),
    "unsupported-web-content": ("unsupported", "Unsupported", "Unsupported"),
    "custom": ("view", "Custom View", "UIView"),
}


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


def node_index(screen: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    nodes = {str(node.get("id") or ""): node for node in screen.get("nodes") or [] if node.get("id")}
    children: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        parent_id = str(node.get("parentId") or "")
        children.setdefault(parent_id, []).append(node_id)
    return nodes, children


def descendants(root_id: str, children: dict[str, list[str]]) -> list[str]:
    result: list[str] = []
    pending = list(children.get(root_id) or [])
    while pending:
        node_id = pending.pop(0)
        result.append(node_id)
        pending[0:0] = children.get(node_id) or []
    return result


def node_signature(node_id: str, nodes: dict[str, dict[str, Any]], children: dict[str, list[str]]) -> tuple[Any, ...]:
    node = nodes[node_id]
    child_semantics = tuple(str(nodes[child].get("semanticType") or "container") for child in children.get(node_id) or [])
    return str(node.get("semanticType") or "container"), child_semantics


def repeated_groups(
    parent_id: str,
    nodes: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[str]] = {}
    for child_id in children.get(parent_id) or []:
        groups.setdefault(node_signature(child_id, nodes, children), []).append(child_id)
    return [
        {"templateNodeId": ids[0], "itemNodeIds": ids, "itemCount": len(ids)}
        for ids in groups.values()
        if len(ids) >= 3
    ]


def scroll_axis(node: dict[str, Any]) -> str:
    layout = node.get("layout") or {}
    axis = str(layout.get("scrollAxis") or "none")
    if axis != "none":
        return axis
    metrics = layout.get("scrollMetrics") or {}
    horizontal = bool(metrics.get("overflowsHorizontally"))
    vertical = bool(metrics.get("overflowsVertically"))
    if horizontal and vertical:
        return "both"
    if horizontal:
        return "horizontal"
    if vertical:
        return "vertical"
    return "none"


def content_container_plan(
    screen: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root_id = str(screen.get("rootNodeId") or "")
    root_descendants = [root_id] + descendants(root_id, children)
    candidates = [nodes[node_id] for node_id in root_descendants if node_id in nodes]
    semantics = {str(node.get("semanticType") or "container") for node in candidates}
    content_nodes = [node for node in candidates if str(node.get("semanticType") or "") in CONTENT_SEMANTICS]
    data_tables = [node for node in content_nodes if node.get("semanticType") == "data-table"]
    carousels = [node for node in content_nodes if node.get("semanticType") == "carousel" or scroll_axis(node) == "horizontal"]
    grids = [node for node in content_nodes if node.get("semanticType") in {"grid", "collection"}]
    lists = [node for node in content_nodes if node.get("semanticType") in {"list", "sectioned-list"}]
    structured_collections = data_tables + carousels + grids + lists
    vertical_scroll = [node for node in candidates if scroll_axis(node) in {"vertical", "both"}]
    primary_vertical_scrolls = [
        node for node in vertical_scroll
        if str(node.get("semanticType") or "") == "scroll"
    ]
    direct_repeated = repeated_groups(root_id, nodes, children)

    kind = "static-view"
    swiftui = "VStack/HStack/ZStack"
    uikit = "UIView/UIStackView"
    selected_node_id = root_id
    confidence = 0.86
    reasons = ["No native collection evidence; use intrinsic layout."]
    if primary_vertical_scrolls:
        selected_node_id = str(primary_vertical_scrolls[0].get("id") or root_id)
        kind, swiftui, uikit = "scroll-view", "ScrollView", "UIScrollView"
        confidence = 0.96
        reasons = ["An explicit measured vertical scroll node owns the screen content axis."]
    elif len(structured_collections) >= 2:
        selected_node_id = root_id
        kind, swiftui, uikit = "compositional-collection", "Lazy stacks/grids", "UICollectionViewCompositionalLayout"
        confidence = 0.92
        reasons = ["Multiple independently structured sections require compositional layout and reuse."]
    elif data_tables:
        selected_node_id = str(data_tables[0].get("id"))
        kind, swiftui, uikit = "collection-view", "Grid/custom table", "UICollectionView"
        confidence = 0.94
        reasons = ["Data-table semantics require multi-column layout and reusable rows."]
    elif carousels:
        selected_node_id = str(carousels[0].get("id"))
        kind, swiftui, uikit = "collection-view", "ScrollView/LazyHStack", "UICollectionView"
        confidence = 0.92
        reasons = ["Horizontal repeated content is owned by a collection container."]
    elif grids:
        selected_node_id = str(grids[0].get("id"))
        item_count = len(children.get(selected_node_id) or [])
        if item_count >= 4 or repeated_groups(selected_node_id, nodes, children):
            kind, swiftui, uikit = "collection-view", "LazyVGrid", "UICollectionView"
            confidence = 0.91
            reasons = ["Grid has enough repeated items to require lazy/reusable layout."]
        else:
            kind, swiftui, uikit = "static-grid", "Grid", "UIStackView grid"
            confidence = 0.82
            reasons = ["Small fixed grid is cheaper and clearer as static native layout."]
    elif lists:
        selected_node_id = str(lists[0].get("id"))
        item_count = len(children.get(selected_node_id) or [])
        if item_count >= 5 or repeated_groups(selected_node_id, nodes, children):
            kind, swiftui, uikit = "table-view", "List/LazyVStack", "UITableView"
            confidence = 0.9
            reasons = ["Single-column repeated rows require lazy layout and cell reuse."]
        else:
            kind, swiftui, uikit = "static-list", "VStack", "UIStackView"
            confidence = 0.82
            reasons = ["Small fixed list does not justify table lifecycle and reuse."]
    elif len(direct_repeated) >= 2:
        kind, swiftui, uikit = "compositional-collection", "LazyVStack/LazyVGrid", "UICollectionViewCompositionalLayout"
        confidence = 0.78
        reasons = ["Multiple repeated root groups suggest a heterogeneous feed."]
    elif direct_repeated:
        kind, swiftui, uikit = "table-view", "LazyVStack", "UITableView"
        confidence = 0.76
        reasons = ["Repeated homogeneous root children suggest reusable rows."]
    elif vertical_scroll:
        selected_node_id = str(vertical_scroll[0].get("id") or root_id)
        kind, swiftui, uikit = "scroll-view", "ScrollView", "UIScrollView"
        confidence = 0.94
        reasons = ["Measured vertical overflow requires a scroll container."]

    sections: list[dict[str, Any]] = []
    section_sources = structured_collections if kind == "compositional-collection" else [nodes.get(selected_node_id) or nodes.get(root_id) or {}]
    for index, source in enumerate(section_sources):
        source_id = str(source.get("id") or root_id)
        source_semantic = str(source.get("semanticType") or "container")
        groups = repeated_groups(source_id, nodes, children)
        item_ids = list(children.get(source_id) or [])
        template_id = groups[0]["templateNodeId"] if groups else (item_ids[0] if item_ids else None)
        section_kind = (
            "horizontal-carousel" if source_semantic == "carousel" or scroll_axis(source) == "horizontal"
            else "grid" if source_semantic in {"grid", "collection", "data-table"}
            else "list"
        )
        sections.append({
            "id": f"{screen.get('id')}.section.{index}",
            "sourceNodeId": source_id,
            "kind": section_kind,
            "scrollAxis": "horizontal" if section_kind == "horizontal-carousel" else "vertical",
            "itemNodeIds": item_ids,
            "itemCount": len(item_ids),
            "itemTemplateNodeId": template_id,
            "usesReuse": kind in {"table-view", "collection-view", "compositional-collection"},
            "headerNodeId": next((item for item in item_ids if nodes[item].get("semanticType") in {"header", "table-header"}), None),
            "footerNodeId": next((item for item in item_ids if nodes[item].get("semanticType") == "footer"), None),
        })

    node_strategies = []
    for node in content_nodes:
        node_id = str(node.get("id") or "")
        semantic = str(node.get("semanticType") or "")
        item_count = len(children.get(node_id) or [])
        has_repeated_items = bool(repeated_groups(node_id, nodes, children))
        current_parent = str(node.get("parentId") or "")
        has_vertical_scroll_ancestor = False
        while current_parent:
            parent = nodes.get(current_parent) or {}
            if parent.get("semanticType") == "scroll" and scroll_axis(parent) in {"vertical", "both"}:
                has_vertical_scroll_ancestor = True
                break
            current_parent = str(parent.get("parentId") or "")
        if semantic == "data-table":
            node_kind = "static-grid" if has_vertical_scroll_ancestor else "collection-view"
        elif semantic in {"carousel", "collection"}:
            node_kind = "collection-view" if semantic == "carousel" or scroll_axis(node) == "horizontal" or not has_vertical_scroll_ancestor else "static-grid"
        elif semantic == "grid":
            node_kind = "collection-view" if not has_vertical_scroll_ancestor and (item_count >= 4 or has_repeated_items) else "static-grid"
        elif semantic in {"list", "sectioned-list"}:
            node_kind = "table-view" if not has_vertical_scroll_ancestor and (semantic == "sectioned-list" or item_count >= 5 or has_repeated_items) else "static-list"
        elif semantic == "scroll":
            node_kind = "scroll-view"
        else:
            node_kind = "static-view"
        node_strategies.append({
            "nodeId": node_id,
            "kind": node_kind,
            "itemCount": item_count,
            "usesReuse": node_kind in {"table-view", "collection-view"},
        })
    if kind == "compositional-collection" and not any(item["nodeId"] == root_id for item in node_strategies):
        node_strategies.insert(0, {
            "nodeId": root_id,
            "kind": "compositional-collection",
            "itemCount": len(children.get(root_id) or []),
            "usesReuse": True,
        })

    return {
        "nodeId": selected_node_id,
        "kind": kind,
        "swiftUIType": swiftui,
        "uiKitType": uikit,
        "scrollAxis": "horizontal" if kind == "collection-view" and carousels else "vertical" if kind not in {"static-view", "static-grid", "static-list"} else "none",
        "usesCellReuse": kind in {"table-view", "collection-view", "compositional-collection"},
        "confidence": confidence,
        "reasons": reasons,
        "detectedSemantics": sorted(semantics & CONTENT_SEMANTICS),
        "nodeStrategies": node_strategies,
    }, sections


def leaf_component_plan(
    screen: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        semantic = str(node.get("semanticType") or "container")
        child_ids = children.get(node_id) or []
        if semantic in NON_LEAF_SEMANTICS and child_ids:
            continue
        category, swiftui, uikit = LEAF_COMPONENTS.get(
            semantic,
            ("view", "Custom View" if child_ids else "Color/Custom View", "UIView"),
        )
        text_behavior = node.get("textBehavior") or {}
        if text_behavior.get("nativeControl") == "text-view":
            category, swiftui, uikit = "text", "Text/TextEditor", "UITextView"
        elif text_behavior.get("nativeControl") == "text-field":
            category, swiftui, uikit = "input", "TextField/SecureField", "UITextField"
        mapping = node.get("nativeMapping") or {}
        confidence = float(mapping.get("confidence") or 0.75)
        style_strategy = str(mapping.get("styleStrategy") or "custom-native-view")
        if style_strategy == "project-component":
            swiftui = str(mapping.get("swiftUI") or swiftui)
            uikit = str(mapping.get("uiKit") or uikit)
        result.append({
            "nodeId": node_id,
            "semanticType": semantic,
            "category": category,
            "swiftUIType": swiftui,
            "uiKitType": uikit,
            "styleStrategy": style_strategy,
            "interactive": bool((node.get("interactionRefs") or []) or category in {"control", "input"}),
            "accessibilityIdentifier": node_id,
            "confidence": confidence,
            "reasons": list(mapping.get("rationale") or [f"semantic:{semantic}"]),
        })
    return result


def screen_plan(ir: dict[str, Any], report: dict[str, Any] | None, ui_stack: str) -> dict[str, Any]:
    screen = (ir.get("screens") or [])[0]
    screen_id = str(screen.get("id") or "screen")
    navigation = screen.get("navigation") or {}
    navigation_style = str(navigation.get("style") or (screen.get("systemChrome") or {}).get("navigationBar") or "hidden")
    tab = screen.get("tabContainer") if isinstance(screen.get("tabContainer"), dict) else None
    top = region(screen, "topBar")
    bottom = region(screen, "bottomBar")
    nodes, children = node_index(screen)

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
    input_node_ids = [
        str(node.get("id") or "")
        for node in screen.get("nodes") or []
        if ((node.get("textBehavior") or {}).get("role") == "input")
    ]

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
    content_container, sections = content_container_plan(screen, nodes, children)
    leaf_components = leaf_component_plan(screen, nodes, children)
    app_container_kind = "tab-navigation" if tab else "navigation"
    top_region = {
        "kind": (top or {}).get("kind") if top else ("system-navigation-bar" if navigation_style == "native" else "none"),
        "nodeId": top.get("nodeId") if top and navigation_style == "custom" else None,
        "behavior": top_behavior,
        "ownership": "screen-custom" if top and navigation_style == "custom" else "system",
    }
    bottom_region = {
        "kind": (bottom or {}).get("kind") if bottom else ("native-tab-bar" if tab else "none"),
        "nodeId": bottom.get("nodeId") if bottom and not tab else None,
        "behavior": bottom_behavior if bottom and not tab else ("system-managed" if tab else "none"),
        "ownership": "app-container" if tab else "screen-custom" if bottom else "none",
    }
    six_layers = {
        "applicationContainer": {
            "kind": app_container_kind,
            "swiftUIType": "TabView + NavigationStack per tab" if tab else "NavigationStack",
            "uiKitType": "UITabBarController + UINavigationController per tab" if tab else "UINavigationController",
            "ownership": "generated-or-existing-project-router",
        },
        "screenContainer": {
            "kind": "screen",
            "swiftUIType": "SwiftUI.View",
            "uiKitType": "UIViewController",
            "screenId": screen_id,
            "containment": containment,
        },
        "screenRegions": {
            "top": top_region,
            "content": {"nodeId": content_container["nodeId"], "kind": "content"},
            "bottom": bottom_region,
            "presentations": presentation_mechanisms,
        },
        "contentContainer": content_container,
        "reusableContent": {
            "sections": sections,
            "usesReuse": content_container["usesCellReuse"],
            "cellStrategy": (
                "UITableViewCell" if content_container["kind"] == "table-view"
                else "UICollectionViewCell" if content_container["usesCellReuse"]
                else "none"
            ),
        },
        "leafComponents": leaf_components,
    }

    return {
        "screenId": screen_id,
        "layers": six_layers,
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
            "kind": bottom_region["kind"],
            "nodeId": bottom_region["nodeId"],
            "behavior": bottom_region["behavior"],
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
        "keyboard": {
            "present": bool(input_node_ids),
            "fieldNodeIds": input_node_ids,
            "avoidanceOwner": "system" if ui_stack == "swiftui" else "scroll-view-controller",
            "scrollDismissMode": "interactive",
            "bottomRegionPolicy": "system-keyboard-safe-area" if bottom else "none",
            "subtractKeyboardFromContainerDimensions": False,
            "nestedTextViewScrollOwnership": "text-view-when-source-scrollable",
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
            "sixLayerArchitectureComplete": True,
            "singleSafeAreaOwner": True,
            "scrollContainersUseFullParentBounds": True,
            "safeAreaNeverSubtractedFromWidthOrHeight": True,
            "systemAndCustomNavigationBarsNeverRenderTogether": True,
            "onePresentationOwnerPerState": True,
            "keyboardAndSafeAreaNeverDoubleCounted": True,
        },
        "screens": screens,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "screens": ids, "uiStack": ui_stack}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
