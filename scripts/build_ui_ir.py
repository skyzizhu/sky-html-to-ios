#!/usr/bin/env python3
"""Build a validated draft UI IR from extract_render_tree.cjs output."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


SKIP_TAGS = {"head", "meta", "title", "link", "style", "script", "noscript"}

EXPLICIT_COMPONENTS = {
    "activity-indicator": ("activity-indicator", "ProgressView", "UIActivityIndicatorView"),
    "alert": ("alert", "alert", "UIAlertController"),
    "button": ("button", "Button", "UIButton"),
    "checkbox": ("checkbox", "Toggle with checkbox style", "Custom UIControl"),
    "collection": ("collection", "LazyVGrid/LazyHGrid", "UICollectionView"),
    "color-picker": ("color-picker", "ColorPicker", "UIColorWell"),
    "context-menu": ("context-menu", "contextMenu", "UIContextMenuInteraction"),
    "date-picker": ("date-input", "DatePicker", "UIDatePicker"),
    "disclosure": ("disclosure", "DisclosureGroup", "Expandable UIControl"),
    "file-picker": ("file-input", "fileImporter", "UIDocumentPickerViewController"),
    "form": ("form", "Form", "UIView/form controller"),
    "gauge": ("gauge", "Gauge", "Custom UIView"),
    "image": ("image", "Image", "UIImageView"),
    "label": ("label", "Text", "UILabel"),
    "link": ("link", "Link/Button", "UIButton/UIControl"),
    "list": ("list", "List/LazyVStack", "UITableView/UICollectionView"),
    "map": ("map", "Map", "MKMapView"),
    "menu": ("menu", "Menu", "UIMenu"),
    "navigation-bar": ("navigation-bar", "toolbar/navigationTitle", "UINavigationBar"),
    "page-control": ("page-control", "custom page indicator", "UIPageControl"),
    "picker": ("select", "Picker", "UIPickerView/UIMenu"),
    "popover": ("popover", "popover", "UIPopoverPresentationController"),
    "progress": ("progress", "ProgressView", "UIProgressView"),
    "radio": ("radio", "Custom radio option", "Custom UIControl"),
    "scroll-view": ("scroll-container", "ScrollView", "UIScrollView"),
    "search-field": ("search-input", "TextField/.searchable", "UISearchTextField"),
    "segmented-control": ("segmented-control", "Picker segmented style", "UISegmentedControl"),
    "sheet": ("sheet", "sheet", "Presented UIViewController"),
    "slider": ("slider", "Slider", "UISlider"),
    "split-view": ("split-view", "NavigationSplitView", "UISplitViewController"),
    "stepper": ("stepper", "Stepper", "UIStepper"),
    "switch": ("switch", "Toggle", "UISwitch"),
    "tab-bar": ("tab-bar", "TabView", "UITabBarController/UITabBar"),
    "table": ("data-table", "Grid/custom table", "UITableView/UICollectionView"),
    "text": ("text", "Text", "UILabel"),
    "text-editor": ("text-area", "TextEditor", "UITextView"),
    "text-field": ("text-input", "TextField", "UITextField"),
    "toolbar": ("toolbar", "toolbar", "UIToolbar"),
    "video": ("video", "VideoPlayer", "AVPlayerViewController"),
    "web-content": ("web-content", "UIViewRepresentable", "WKWebView"),
}


def slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.").lower()
    return normalized or "node"


def attr_present(attributes: dict, name: str) -> bool:
    return name in attributes


def attr_bool(attributes: dict, name: str):
    if name not in attributes:
        return None
    return str(attributes.get(name, "")).lower() not in {"false", "0", "none"}


def first_known(*values):
    return next((value for value in values if value is not None), None)


def local_path_from_asset(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    if not parsed.scheme and not value.startswith("data:"):
        return unquote(parsed.path or value)
    return None


def css_background_url(value: str | None) -> str | None:
    match = re.search(r"url\((?:['\"])?(.*?)(?:['\"])?\)", str(value or ""))
    return match.group(1).strip() if match else None


def keyboard_metadata(input_type: str) -> tuple[str | None, str | None]:
    return {
        "email": ("emailAddress", "emailAddress"),
        "url": ("URL", "URL"),
        "tel": ("phonePad", "telephoneNumber"),
        "number": ("decimalPad", None),
        "search": ("default", None),
        "password": ("default", "password"),
    }.get(input_type, (None, None))


def semantic_mapping(node: dict, has_interaction: bool) -> dict:
    tag = str(node.get("tag") or "").lower()
    attrs = node.get("attributes") or {}
    role = str(attrs.get("role") or "").lower()
    input_type = str(attrs.get("type") or "text").lower()
    style = node.get("style") or {}
    rect = node.get("rect") or {}
    name_blob = " ".join([str(node.get("domId") or ""), *node.get("classNames", [])]).lower()
    explicit_component = str(attrs.get("data-ios-component") or "").strip()
    project_component = str(attrs.get("data-ios-project-component") or "").strip()
    reasons: list[str] = []

    def result(semantic, swiftui, uikit, confidence, strategy="plain-native-semantics-custom-appearance", support="native"):
        selected_swiftui = swiftui
        selected_uikit = uikit
        selected_strategy = strategy
        concrete_component = project_component or (explicit_component if explicit_component not in EXPLICIT_COMPONENTS else "")
        if concrete_component and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", concrete_component):
            reasons.append(f"explicit-project-component:{concrete_component}")
            if concrete_component.startswith(("UI", "AV", "MK", "QL", "SF", "PHPicker", "CN", "MF", "EK")):
                selected_uikit = concrete_component
            else:
                selected_swiftui = concrete_component
            selected_strategy = "project-component"
        return {
            "semanticType": semantic,
            "nativeMapping": {
                "swiftUI": selected_swiftui,
                "uiKit": selected_uikit,
                "styleStrategy": selected_strategy,
                "confidence": confidence,
                "rationale": reasons.copy(),
            },
            "support": support,
        }

    if (node.get("synthetic") or {}).get("kind") == "pseudo-element":
        reasons.extend(["synthetic:pseudo-element", f"pseudo:{node['synthetic'].get('pseudo')}"])
        if node.get("text"):
            return result("decoration", "Text/Shape overlay", "UILabel/CALayer overlay", 0.72, "native-fallback", "native-fallback")
        return result("decoration", "Shape/Image overlay", "CALayer/UIImageView overlay", 0.68, "native-fallback", "native-fallback")

    if explicit_component in EXPLICIT_COMPONENTS:
        reasons.append(f"explicit-ios-component:{explicit_component}")
        semantic, swiftui, uikit = EXPLICIT_COMPONENTS[explicit_component]
        return result(semantic, swiftui, uikit, 1.0)

    role_map = {
        "button": ("button", "Button", "UIButton"),
        "link": ("link", "Button/Link", "UIButton/UIControl"),
        "checkbox": ("checkbox", "Toggle with checkbox style", "Custom UIControl"),
        "switch": ("switch", "Toggle", "UISwitch"),
        "radio": ("radio", "Custom radio option", "Custom radio UIControl"),
        "radiogroup": ("radio-group", "Picker/custom radio group", "Custom UIControl group"),
        "slider": ("slider", "Slider", "UISlider"),
        "spinbutton": ("number-input", "TextField/Stepper", "UITextField/UIStepper"),
        "textbox": ("text-input", "TextField/TextEditor", "UITextField/UITextView"),
        "searchbox": ("search-input", "TextField/.searchable", "UISearchTextField/UISearchController"),
        "combobox": ("select", "Picker/Menu", "UIMenu/UIPickerView"),
        "listbox": ("select", "Picker/List", "UIPickerView/UITableView"),
        "option": ("option", "Picker option", "UIAction/picker row"),
        "tab": ("tab-item", "Tab Button", "Tab UIControl"),
        "tablist": ("tab-control", "Tab container", "Tab container UIView"),
        "menuitem": ("menu-item", "Menu item Button", "UIAction/UIMenu"),
        "dialog": ("modal", "sheet/fullScreenCover", "Presented UIViewController"),
        "alertdialog": ("alert", "alert", "UIAlertController"),
        "status": ("toast", "Overlay/status View", "Overlay/status UIView"),
        "progressbar": ("progress", "ProgressView", "UIProgressView"),
        "heading": ("heading", "Text", "UILabel"),
        "img": ("image", "Image", "UIImageView"),
    }
    if role in role_map:
        reasons.append(f"aria-role:{role}")
        semantic, swiftui, uikit = role_map[role]
        return result(semantic, swiftui, uikit, 0.96)

    if tag == "input":
        reasons.extend(["html-tag:input", f"input-type:{input_type}"])
        input_map = {
            "button": ("button", "Button", "UIButton"),
            "submit": ("button", "Button", "UIButton"),
            "reset": ("button", "Button", "UIButton"),
            "image": ("button", "Button with Image", "UIButton"),
            "text": ("text-input", "TextField", "UITextField"),
            "email": ("text-input", "TextField", "UITextField"),
            "url": ("text-input", "TextField", "UITextField"),
            "tel": ("text-input", "TextField", "UITextField"),
            "password": ("secure-input", "SecureField", "UITextField secure entry"),
            "search": ("search-input", "TextField/.searchable", "UISearchTextField/UISearchController"),
            "number": ("number-input", "TextField with numeric parsing", "UITextField numeric keyboard"),
            "date": ("date-input", "DatePicker", "UIDatePicker"),
            "time": ("date-input", "DatePicker", "UIDatePicker"),
            "datetime-local": ("date-input", "DatePicker", "UIDatePicker"),
            "month": ("date-input", "DatePicker/custom month picker", "UIDatePicker/custom picker"),
            "week": ("date-input", "DatePicker/custom week picker", "UIDatePicker/custom picker"),
            "checkbox": ("checkbox", "Toggle with checkbox style", "Custom UIControl"),
            "radio": ("radio", "Custom radio option", "Custom radio UIControl"),
            "range": ("slider", "Slider", "UISlider"),
            "color": ("color-picker", "ColorPicker", "UIColorWell"),
            "file": ("file-input", "Button + fileImporter", "UIDocumentPickerViewController"),
        }
        semantic, swiftui, uikit = input_map.get(input_type, ("text-input", "TextField", "UITextField"))
        return result(semantic, swiftui, uikit, 0.99)

    tag_map = {
        "button": ("button", "Button", "UIButton"),
        "a": ("link", "Button/Link", "UIButton/UIControl"),
        "textarea": ("text-area", "TextEditor", "UITextView"),
        "form": ("form", "Layout container + submit action", "UIView + submit action"),
        "label": ("label", "Text", "UILabel"),
        "ul": ("list", "VStack/LazyVStack/List", "UIStackView/UITableView/UICollectionView"),
        "ol": ("list", "VStack/LazyVStack/List", "UIStackView/UITableView/UICollectionView"),
        "li": ("list-item", "Row View", "Reusable cell/content view"),
        "table": ("data-table", "Grid/custom table", "UICollectionView/custom table"),
        "tr": ("table-row", "Grid row", "Collection/table row view"),
        "th": ("table-header", "Text/header cell", "UILabel/header cell"),
        "td": ("table-cell", "Text/content cell", "UILabel/content cell"),
        "progress": ("progress", "ProgressView", "UIProgressView"),
        "meter": ("meter", "Gauge/custom ProgressView", "Custom UIView"),
        "details": ("disclosure", "DisclosureGroup", "Expandable UIControl"),
        "summary": ("disclosure-trigger", "Disclosure Button", "Disclosure UIControl"),
        "option": ("option", "Picker option", "UIAction/picker row"),
        "optgroup": ("option-group", "Picker section", "Picker/table section"),
        "fieldset": ("form-group", "Form section container", "Form section UIView"),
        "legend": ("label", "Text", "UILabel"),
        "output": ("text", "Text", "UILabel"),
        "dialog": ("modal", "sheet/fullScreenCover", "Presented UIViewController"),
        "nav": ("navigation", "Toolbar/custom navigation", "UINavigationBar/custom UIView"),
        "header": ("header", "Header View", "UIView"),
        "footer": ("footer", "Footer View", "UIView"),
        "hr": ("divider", "Divider/Rectangle", "UIView"),
        "video": ("video", "VideoPlayer", "AVPlayerViewController"),
        "audio": ("audio", "Project audio controls", "AVPlayer/AVAudioPlayer controls"),
        "iframe": ("embedded-content", "Unsupported without content-specific mapping", "Unsupported without content-specific mapping"),
        "embed": ("embedded-content", "Unsupported without content-specific mapping", "Unsupported without content-specific mapping"),
        "object": ("embedded-content", "Unsupported without content-specific mapping", "Unsupported without content-specific mapping"),
    }
    if tag == "select":
        reasons.append("html-tag:select")
        if attr_present(attrs, "multiple"):
            reasons.append("attribute:multiple")
            return result("multi-select", "Multi-select list", "UITableView/UICollectionView multi-select", 0.99)
        return result("select", "Picker/Menu", "UIMenu/UIPickerView", 0.99)
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        reasons.append(f"html-tag:{tag}")
        return result("heading", "Text", "UILabel", 0.99, "native-default")
    if tag in {"p", "span", "strong", "em", "small", "time", "blockquote", "code"}:
        reasons.append(f"html-tag:{tag}")
        return result("text", "Text/AttributedString", "UILabel/NSAttributedString", 0.96, "native-default")
    if tag == "img":
        reasons.append("html-tag:img")
        return result("image", "Image/project image component", "UIImageView/project image component", 0.99, "project-component")
    if tag == "svg":
        is_icon = float(rect.get("width") or 0) <= 96 and float(rect.get("height") or 0) <= 96
        reasons.extend(["html-tag:svg", "size-icon" if is_icon else "size-artwork"])
        return result("icon" if is_icon else "image", "Image/Shape", "UIImage/CAShapeLayer", 0.9, "native-fallback", "native-fallback")
    if tag == "canvas":
        reasons.append("html-tag:canvas")
        if has_interaction:
            reasons.append("interactive-canvas")
            return result("unsupported-web-content", "Unsupported", "Unsupported", 0.95, "unsupported", "unsupported")
        return result("canvas-artwork", "Canvas/image asset", "Core Graphics/image asset", 0.75, "native-fallback", "native-fallback")
    if tag in tag_map:
        reasons.append(f"html-tag:{tag}")
        semantic, swiftui, uikit = tag_map[tag]
        unsupported = tag in {"iframe", "embed", "object"}
        media = tag in {"video", "audio"}
        return result(
            semantic, swiftui, uikit, 0.98,
            "unsupported" if unsupported else "native-fallback" if media else "native-default",
            "unsupported" if unsupported else "native-fallback" if media else "native",
        )

    if attrs.get("contenteditable") == "true":
        reasons.append("attribute:contenteditable")
        return result("text-area", "TextEditor", "UITextView", 0.94)
    if has_interaction:
        reasons.append("observable-click-interaction")
        return result("button", "Button", "UIControl/UIButton", 0.78)
    scroll = scroll_contract(node)
    if scroll["axis"] != "none":
        reasons.extend([
            f"computed-overflow:{scroll['axis']}",
            f"measured-overflow:x={scroll['overflowsHorizontally']},y={scroll['overflowsVertically']}",
        ])
        if scroll["axis"] == "horizontal" and style.get("display") in {"flex", "inline-flex"}:
            return result("carousel", "Horizontal ScrollView/LazyHStack", "UICollectionView", 0.9, "custom-native-view")
        return result("scroll", "ScrollView", "UIScrollView", 0.92, "custom-native-view")
    if style.get("display") in {"grid", "inline-grid"}:
        reasons.append("computed-display:grid")
        return result("grid", "Grid/LazyVGrid", "UICollectionView", 0.9, "custom-native-view")
    if "tab" in name_blob and ("bar" in name_blob or "nav" in name_blob):
        reasons.append("name-pattern:tab-bar")
        return result("tab-bar", "TabView/custom tab bar", "UITabBarController/custom tab bar", 0.62, "custom-native-view")
    if re.search(r"modal|dialog", name_blob):
        reasons.append("name-pattern:modal-dialog")
        return result("modal", "sheet/fullScreenCover/custom overlay", "present/custom overlay", 0.62, "custom-native-view")
    if re.search(r"toast|snackbar", name_blob):
        reasons.append("name-pattern:toast")
        return result("toast", "Overlay", "Overlay UIView", 0.62, "custom-native-view")
    if node.get("text"):
        reasons.append("direct-text-content")
        return result("text", "Text", "UILabel", 0.72, "native-default")
    reasons.append(f"layout-container:{style.get('display', 'unknown')}")
    return result("container", "VStack/HStack/ZStack", "UIView + Auto Layout", 0.75, "custom-native-view")


def layout_mode(style: dict) -> str:
    position = style.get("position")
    display = style.get("display")
    if position == "fixed":
        return "fixed"
    if position == "absolute":
        return "absolute"
    if display in {"grid", "inline-grid"}:
        return "grid"
    if display in {"flex", "inline-flex"}:
        return "flex-row" if style.get("flexDirection") in {"row", "row-reverse"} else "flex-column"
    return "flow"


def scroll_contract(node: dict) -> dict:
    style = node.get("style") or {}
    metrics = node.get("scroll") or {}
    scroll_width = float(metrics.get("scrollWidth") or 0)
    scroll_height = float(metrics.get("scrollHeight") or 0)
    client_width = float(metrics.get("clientWidth") or 0)
    client_height = float(metrics.get("clientHeight") or 0)
    horizontal_overflow = scroll_width > client_width + 1
    vertical_overflow = scroll_height > client_height + 1
    horizontal_allowed = str(style.get("overflowX") or "visible") in {"auto", "scroll"}
    vertical_allowed = str(style.get("overflowY") or "visible") in {"auto", "scroll"}

    active_horizontal = horizontal_allowed and horizontal_overflow
    active_vertical = vertical_allowed and vertical_overflow
    if active_horizontal and active_vertical:
        axis = "both"
    elif active_horizontal:
        axis = "horizontal"
    elif active_vertical:
        axis = "vertical"
    elif horizontal_allowed and not vertical_allowed:
        axis = "horizontal"
    elif vertical_allowed and not horizontal_allowed:
        axis = "vertical"
    else:
        axis = "none"

    return {
        "axis": axis,
        "horizontalAllowed": horizontal_allowed,
        "verticalAllowed": vertical_allowed,
        "overflowsHorizontally": horizontal_overflow,
        "overflowsVertically": vertical_overflow,
        "scrollWidth": scroll_width,
        "scrollHeight": scroll_height,
        "clientWidth": client_width,
        "clientHeight": client_height,
    }


ALLOWED_EXPLICIT_ACTIONS = {
    "push", "pop", "pop-to-root", "replace-stack", "back", "show-primary", "show-detail",
    "present", "present-sheet", "present-fullscreen", "present-popover", "present-alert",
    "present-confirmation", "present-menu", "dismiss", "overlay", "add-child", "remove-child",
    "switch-tab", "select-tab", "page-next", "page-previous", "toggle-state", "update-value", "scroll-to",
    "open-url", "submit", "set-flow-state", "unknown",
}


def normalize_interaction(raw: dict, screen_id: str, index: int) -> dict:
    href = raw.get("href")
    inline = raw.get("inlineHandler") or ""
    source_tag = str(raw.get("sourceTag") or "").lower()
    source_role = str(raw.get("sourceRole") or "").lower()
    source_type = str(raw.get("sourceType") or "").lower()
    action = "unknown"
    target = None
    confidence = 0.55
    trigger = raw.get("trigger") or "tap"
    explicit_action = str(raw.get("iosAction") or "").strip().lower()
    explicit_target = raw.get("iosTarget")
    if explicit_action in ALLOWED_EXPLICIT_ACTIONS:
        action, target, confidence = explicit_action, explicit_target, 1.0
        if action == "select-tab":
            action = "switch-tab"
    elif source_tag == "summary":
        action, confidence = "toggle-state", 0.98
    elif source_tag == "form" or (source_tag in {"button", "input"} and source_type == "submit"):
        action, target, confidence = "submit", raw.get("action"), 0.95
        trigger = "submit"
    elif source_role in {"checkbox", "switch", "radio"} or (source_tag == "input" and source_type in {"checkbox", "radio"}):
        action, confidence = "toggle-state", 0.98
        trigger = "change"
    elif source_role == "tab":
        action, confidence = "switch-tab", 0.95
    elif source_tag in {"input", "select", "textarea"} or source_role in {"textbox", "searchbox", "slider", "spinbutton", "combobox", "listbox"}:
        action, confidence = "update-value", 0.98
        trigger = "change"
    elif href:
        parsed = urlparse(href)
        if parsed.scheme in {"http", "https", "mailto", "tel"}:
            action, target, confidence = "open-url", href, 0.98
        elif href.startswith("#"):
            action, target, confidence = "scroll-to", href[1:], 0.92
        else:
            action, target, confidence = "push", slug(Path(parsed.path).stem or href), 0.9
    elif re.search(r"history\.(back|go)|location\.back", inline):
        action, confidence = "back", 0.95
    else:
        location_match = re.search(r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)", inline)
        if location_match:
            target_value = location_match.group(1)
            parsed = urlparse(target_value)
            if parsed.scheme in {"http", "https"}:
                action, target = "open-url", target_value
            else:
                action, target = "push", slug(Path(parsed.path).stem or target_value)
            confidence = 0.9
        elif re.search(r"classList\.(toggle|add|remove)|hidden\s*=", inline):
            action, confidence = "toggle-state", 0.78
        elif re.search(r"\.showModal\s*\(", inline):
            action, confidence = "present", 0.86
        elif re.search(r"\.close\s*\(", inline):
            action, confidence = "dismiss", 0.86
        elif re.search(r"alert\s*\(", inline):
            action, confidence = "present-alert", 0.9
        elif re.search(r"confirm\s*\(|prompt\s*\(", inline):
            action, confidence = "present-confirmation", 0.9
    detents = [item.strip() for item in str(raw.get("iosDetents") or "").split(",") if item.strip()]
    presentation = None
    if action.startswith("present"):
        presentation = {
            "style": raw.get("iosPresentationStyle") or {
                "present-sheet": "page-sheet",
                "present-fullscreen": "full-screen",
                "present-popover": "popover",
                "present-alert": "alert",
                "present-confirmation": "action-sheet",
                "present-menu": "menu",
            }.get(action, "automatic"),
            "detents": detents,
            "transition": "system",
        }
    containment = None
    if action in {"add-child", "remove-child"}:
        containment = {
            "containerNodeId": raw.get("iosContainer"),
            "childScreenId": explicit_target,
            "lifecycleOwner": None,
        }
    return {
        "id": f"interaction.{screen_id}.{index + 1}",
        "sourceRuntimeId": raw.get("sourceRuntimeId"),
        "trigger": trigger,
        "action": action,
        "target": target,
        "payload": {},
        "confidence": confidence,
        "presentation": presentation,
        "containment": containment,
    }


def native_action(transition: dict, state_by_id: dict[str, dict], resolved_action: str | None) -> str:
    action = resolved_action or transition.get("recommendedNativeAction") or ""
    state = state_by_id.get(transition.get("targetStateId")) or {}
    state_kind = state.get("kind")
    if transition.get("webAction") == "remove" and state_kind in {"sheet", "full-screen-overlay", "popover-overlay", "overlay"}:
        return "dismiss"
    if state_kind in {"transient-feedback", "progress-animation"}:
        return "update-value"
    mapping = {
        "replace-flow-state": "set-flow-state",
        "push": "push",
        "pop": "pop",
        "pop-to-root": "pop-to-root",
        "replace-root": "replace-stack",
        "replace": "replace-stack",
        "sheet": "present-sheet",
        "full-screen-cover": "present-fullscreen",
        "popover-or-overlay": "present-popover",
        "overlay": "overlay",
        "dismiss": "dismiss",
        "toggle-expanded": "toggle-state",
        "update-selection": "toggle-state",
        "update-local-state": "toggle-state",
        "open-url": "open-url",
    }
    if action in mapping:
        return mapping[action]
    if state_kind == "sheet":
        return "dismiss" if transition.get("webAction") == "remove" else "present-sheet"
    if state_kind == "full-screen-overlay":
        return "dismiss" if transition.get("webAction") == "remove" else "present-fullscreen"
    if state_kind == "popover-overlay":
        return "dismiss" if transition.get("webAction") == "remove" else "present-popover"
    if state_kind == "overlay":
        return "dismiss" if transition.get("webAction") == "remove" else "overlay"
    if state_kind in {"transient-feedback", "progress-animation"}:
        return "update-value"
    if transition.get("targetStateId"):
        return "toggle-state"
    return "unknown"


def presentation_for_action(action: str) -> dict | None:
    styles = {
        "present": "automatic",
        "present-sheet": "page-sheet",
        "present-fullscreen": "full-screen",
        "present-popover": "popover",
        "present-alert": "alert",
        "present-confirmation": "action-sheet",
        "present-menu": "menu",
        "overlay": "in-place-overlay",
    }
    if action not in styles:
        return None
    return {"style": styles[action], "detents": [], "transition": "source-derived"}


def override_resolutions(graph: dict, overrides: dict | None) -> tuple[dict[str, str], dict[str, str]]:
    if not overrides:
        return {}, {}
    graph_fingerprint = (graph.get("source") or {}).get("fingerprint")
    if graph_fingerprint and overrides.get("sourceFingerprint") != graph_fingerprint:
        raise ValueError("--interaction-overrides sourceFingerprint does not match --interaction-graph")

    by_ambiguity: dict[str, str] = {}
    for item in [*(overrides.get("resolutions") or []), *(overrides.get("unresolved") or [])]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        resolution = item.get("resolution") or item.get("nativeAction")
        if isinstance(resolution, dict):
            resolution = resolution.get("nativeAction") or resolution.get("action")
        if isinstance(resolution, str) and resolution:
            by_ambiguity[item["id"]] = resolution
    ambiguity_by_transition = {
        item.get("transitionId"): item.get("id")
        for item in graph.get("unresolved") or []
        if isinstance(item, dict) and item.get("transitionId")
    }
    return by_ambiguity, ambiguity_by_transition


def normalize_dynamic_contracts(
    graph: dict | None,
    overrides: dict | None,
    screen_id: str,
    selector_to_node_ids: dict[str, list[str]],
    selector_to_runtime_ids: dict[str, list[str]],
    selector_visibility: dict[str, list[bool]],
) -> tuple[list[dict], list[dict], list[dict]]:
    if not graph:
        return [], [], []
    state_by_id = {item.get("id"): item for item in graph.get("states") or [] if isinstance(item, dict)}
    transition_by_interaction: dict[str, list[dict]] = {}
    automatic_transitions = []
    for transition in graph.get("transitions") or []:
        if not isinstance(transition, dict) or transition.get("sourceScreenId") != screen_id:
            continue
        if transition.get("interactionId"):
            transition_by_interaction.setdefault(transition["interactionId"], []).append(transition)
        else:
            automatic_transitions.append(transition)
    by_ambiguity, ambiguity_by_transition = override_resolutions(graph, overrides)
    warnings: list[dict] = []

    def normalized_transition(transition: dict) -> dict:
        ambiguity_id = ambiguity_by_transition.get(transition.get("id"))
        resolution = by_ambiguity.get(ambiguity_id)
        action = native_action(transition, state_by_id, resolution)
        target = transition.get("targetScreenId") or transition.get("targetStateId") or transition.get("externalURL")
        unresolved = bool(transition.get("requiresOverride") and not resolution)
        if unresolved:
            warnings.append({
                "code": "UNRESOLVED_NATIVE_OWNERSHIP",
                "severity": "warning",
                "nodeId": None,
                "message": f"{transition.get('id')} uses recommended action {action!r}; confirm it before code generation.",
                "fallback": f"Resolve {ambiguity_id or transition.get('id')} in html-to-ios.overrides.json.",
            })
        return {
            "sourceTransitionId": transition.get("id"),
            "kind": transition.get("kind"),
            "trigger": transition.get("trigger"),
            "action": action,
            "target": target,
            "targetScreenId": transition.get("targetScreenId"),
            "targetStateId": transition.get("targetStateId"),
            "targetStateOwnerScreenId": (state_by_id.get(transition.get("targetStateId")) or {}).get("ownerScreenId"),
            "schedule": transition.get("schedule"),
            "confidence": transition.get("confidence"),
            "resolution": {
                "ambiguityId": ambiguity_id,
                "status": "resolved" if resolution else "recommended-unresolved" if unresolved else "not-required",
                "nativeAction": resolution or transition.get("recommendedNativeAction"),
            },
            "evidence": transition.get("evidence"),
        }

    interactions = []
    for source in graph.get("interactions") or []:
        if not isinstance(source, dict) or source.get("sourceScreenId") != screen_id:
            continue
        transitions = [normalized_transition(item) for item in transition_by_interaction.get(source.get("id"), [])]
        transitions.sort(key=lambda item: 0 if "navigation" in str(item.get("kind")) else 1 if item.get("kind") == "presentation" else 2)
        primary = transitions[0] if transitions else {"action": "unknown", "target": None}
        selector = source.get("sourceSelector")
        node_ids = selector_to_node_ids.get(selector, []) if selector else []
        runtime_ids = selector_to_runtime_ids.get(selector, []) if selector else []
        action = primary.get("action") or "unknown"
        interactions.append({
            "id": f"interaction.dynamic.{source.get('id')}",
            "sourceInteractionId": source.get("id"),
            "sourceNodeId": node_ids[0] if node_ids else None,
            "sourceNodeIds": node_ids,
            "sourceRuntimeIds": runtime_ids,
            "sourceSelector": selector,
            "sourceScope": source.get("sourceScope"),
            "sourceVisibleInitially": any(selector_visibility.get(selector, [])) if selector else False,
            "trigger": source.get("trigger") or "tap",
            "action": action,
            "target": primary.get("target"),
            "payload": {"transitions": transitions},
            "confidence": source.get("confidence", 0.5),
            "presentation": presentation_for_action(action),
            "containment": None,
            "automatic": False,
            "requiresResolution": any(item.get("resolution", {}).get("status") == "recommended-unresolved" for item in transitions),
            "evidence": {"ast": source.get("astEvidence"), "runtime": source.get("runtimeEvidence")},
        })
        if not node_ids and not source.get("sourceScope"):
            warnings.append({
                "code": "DYNAMIC_SOURCE_NODE_UNMAPPED",
                "severity": "warning",
                "nodeId": None,
                "message": f"Unable to map dynamic interaction {source.get('id')} selector {selector!r} into this render tree.",
                "fallback": "Re-extract the screen with the shared control inside the selected app root.",
            })

    for transition in automatic_transitions:
        normalized = normalized_transition(transition)
        action = normalized["action"]
        interactions.append({
            "id": f"interaction.dynamic.{transition.get('id')}",
            "sourceInteractionId": None,
            "sourceNodeId": None,
            "sourceNodeIds": [],
            "sourceRuntimeIds": [],
            "sourceSelector": None,
            "sourceScope": "screen",
            "sourceVisibleInitially": False,
            "trigger": "timer-complete" if transition.get("schedule") else "appear",
            "action": action,
            "target": normalized.get("target"),
            "payload": {"transitions": [normalized]},
            "confidence": transition.get("confidence", 0.5),
            "presentation": presentation_for_action(action),
            "containment": None,
            "automatic": True,
            "requiresResolution": normalized.get("resolution", {}).get("status") == "recommended-unresolved",
            "evidence": {"ast": transition.get("evidence"), "runtime": None},
        })

    states = []
    for state in graph.get("states") or []:
        if not isinstance(state, dict) or state.get("ownerScreenId") != screen_id:
            continue
        selector = state.get("targetSelector")
        states.append({
            "id": state.get("id"),
            "sourceStateId": state.get("id"),
            "ownerScreenId": screen_id,
            "kind": state.get("kind"),
            "targetSelector": selector,
            "targetNodeIds": selector_to_node_ids.get(selector, []) if selector else [],
            "classes": state.get("classes") or [],
            "confidence": state.get("confidence", 0.5),
        })
    return interactions, states, warnings


def select_root(data: dict, root_runtime_id: str | None, root_selector: str | None) -> dict:
    nodes = data.get("nodes") or []
    if root_runtime_id:
        match = next((node for node in nodes if node.get("runtimeId") == root_runtime_id), None)
    elif root_selector:
        match = next((node for node in nodes if node.get("selector") == root_selector), None)
    else:
        candidates = sorted(
            (candidate for candidate in data.get("phoneCandidates", []) if candidate.get("isPrimary", True)),
            key=lambda item: item.get("score", 0), reverse=True,
        )
        recommended = candidates[0].get("recommendedRootRuntimeId") if candidates else None
        match = next((node for node in nodes if node.get("runtimeId") == recommended), None)
        if match is None:
            match = next((node for node in nodes if node.get("tag") == "body"), None)
        if match is None and nodes:
            match = nodes[0]
    if match is None:
        raise ValueError("Unable to select a root node. Pass --root-runtime-id or --root-selector.")
    return match


def descendants(nodes: list[dict], root_id: str) -> list[dict]:
    node_by_id = {node.get("runtimeId"): node for node in nodes}
    selected = []
    for node in nodes:
        current = node.get("runtimeId")
        while current:
            if current == root_id:
                selected.append(node)
                break
            current = (node_by_id.get(current) or {}).get("parentRuntimeId")
    return selected


def mapping_availability(native_mapping: dict, args) -> dict:
    report = getattr(args, "sdk_report_data", None) or {}
    sdk = report.get("sdk") or {}
    verified_sdk = args.verified_sdk or (f"iphoneos-{sdk.get('version')}" if sdk.get("version") else None)
    result = {
        "verifiedSDK": verified_sdk,
        "minimumIOS": args.minimum_ios,
        "swiftUI": {"status": "pending-verification", "symbol": None, "introduced": None, "fallback": None},
        "uiKit": {"status": "pending-verification", "symbol": None, "introduced": None, "fallback": None},
    }
    for output_key, report_key, mapping_key in (("swiftUI", "swiftui", "swiftUI"), ("uiKit", "uikit", "uiKit")):
        candidates = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", native_mapping.get(mapping_key, ""))
        symbol_report = (report.get("symbols") or {}).get(report_key) or {}
        symbol = next((candidate for candidate in candidates if candidate in symbol_report), None)
        if symbol:
            item = symbol_report[symbol]
            result[output_key] = {
                "status": item.get("status") or "review-required",
                "symbol": symbol,
                "introduced": item.get("introduced"),
                "fallback": None,
            }
    return result


def normalize_motion(raw: dict, screen_id: str, index: int, selector_to_node_id: dict[str, str]) -> dict:
    properties = [str(item) for item in raw.get("properties") or []]
    direct_properties = {"opacity", "transform", "background-color", "color", "cornerRadius", "border-radius"}
    complex_properties = {"filter", "backdrop-filter", "clip-path", "box-shadow", "mask", "perspective"}
    property_set = set(properties)
    if property_set & complex_properties:
        support = "native-fallback"
        swiftui = "Canvas/visualEffect/UIKit bridge"
        uikit = "Core Animation/Core Image/custom animator"
    elif not property_set or property_set <= direct_properties:
        support = "native"
        swiftui = "Animation/withAnimation"
        uikit = "UIViewPropertyAnimator/Core Animation"
    else:
        support = "native-fallback"
        swiftui = "Animation/custom animatable View"
        uikit = "UIViewPropertyAnimator/CAKeyframeAnimation"
    source = raw.get("source") or "unknown"
    return {
        "id": f"motion.{screen_id}.{index + 1}",
        "sourceNodeId": selector_to_node_id.get(raw.get("sourceSelector")),
        "sourceSelector": raw.get("sourceSelector"),
        "kind": "transition" if source == "css-transition" else "keyframe-animation",
        "source": source,
        "name": raw.get("name"),
        "properties": properties,
        "durationMs": raw.get("durationMs") or 0,
        "delayMs": raw.get("delayMs") or 0,
        "timingFunction": raw.get("timingFunction") or "linear",
        "iterationCount": raw.get("iterationCount") or "1",
        "direction": raw.get("direction") or "normal",
        "fillMode": raw.get("fillMode") or "none",
        "playState": raw.get("playState"),
        "keyframes": raw.get("keyframes") or [],
        "sampleProgress": [0, 0.5, 1],
        "nativeMapping": {"swiftUI": swiftui, "uiKit": uikit},
        "support": support,
        "confidence": 0.9 if source == "web-animation" and raw.get("keyframes") else 0.76,
    }


def visual_states(root: dict, interactions: list[dict], motions: list[dict]) -> list[dict]:
    states = [{
        "id": "initial",
        "name": "Initial",
        "source": "automatic",
        "triggerInteractionId": None,
        "scroll": "top",
        "required": True,
    }]
    root_scroll = root.get("scroll") or {}
    scroll_height = float(root_scroll.get("scrollHeight") or 0)
    client_height = float(root_scroll.get("clientHeight") or 0)
    overflow = max(0.0, scroll_height - client_height)
    meaningful_overflow = max(24.0, client_height * 0.05)
    if overflow > meaningful_overflow:
        positions = ["bottom"]
        if scroll_height > client_height * 1.5:
            positions.insert(0, "middle")
        for position in positions:
            states.append({
                "id": f"scroll-{position}",
                "name": f"Scroll {position.title()}",
                "source": "automatic",
                "triggerInteractionId": None,
                "scroll": position,
                "required": True,
            })
    state_actions = {
        "present", "present-sheet", "present-fullscreen", "present-popover", "present-alert",
        "present-confirmation", "present-menu", "overlay", "switch-tab", "toggle-state",
    }
    for interaction in interactions:
        sequence = [*(interaction.get("prerequisiteInteractionIds") or []), interaction.get("id")]
        if interaction.get("automatic") or not interaction.get("sourceNodeId") or (interaction.get("sourceVisibleInitially") is False and len(sequence) == 1):
            continue
        if interaction.get("action") not in state_actions:
            continue
        states.append({
            "id": slug(f"after-{interaction['id']}"),
            "name": f"After {interaction.get('action')}",
            "source": "automatic",
            "triggerInteractionId": interaction.get("id"),
            "interactionSequence": sequence,
            "scroll": "preserve",
            "required": True,
        })
    if motions:
        for progress in (0, 0.5, 1):
            percent = int(progress * 100)
            states.append({
                "id": f"motion-{percent}",
                "name": f"Motion {percent}%",
                "source": "automatic",
                "triggerInteractionId": None,
                "scroll": "preserve",
                "animationProgress": progress,
                "required": False,
                "captureSupport": "advisory-until-native-motion-hook-exists",
            })
    return states


def filter_other_route_screens(nodes: list[dict], selected: list[dict], route_graph: dict | None, screen_id: str) -> list[dict]:
    if not route_graph:
        return selected
    node_by_id = {node.get("runtimeId"): node for node in nodes}
    runtime_by_selector = {node.get("selector"): node.get("runtimeId") for node in nodes if node.get("selector")}
    excluded_roots = {
        runtime_by_selector.get(screen.get("rootSelector"))
        for screen in route_graph.get("screens") or []
        if isinstance(screen, dict)
        and screen.get("includeInNativeConversion") is not False
        and screen.get("id") != screen_id
        and screen.get("rootSelector")
    }
    excluded_roots.discard(None)

    def belongs_to_other_screen(node: dict) -> bool:
        current = node.get("runtimeId")
        while current:
            if current in excluded_roots:
                return True
            current = (node_by_id.get(current) or {}).get("parentRuntimeId")
        return False

    return [node for node in selected if not belongs_to_other_screen(node)]


def attach_interaction_prerequisites(interactions: list[dict], states: list[dict], selected: list[dict], id_map: dict[str, str]) -> None:
    node_by_runtime = {node.get("runtimeId"): node for node in selected}
    runtime_by_native = {native_id: runtime_id for runtime_id, native_id in id_map.items()}
    container_kinds = {"expansion", "sheet", "full-screen-overlay", "popover-overlay", "overlay"}
    container_states = {state.get("id"): state for state in states if state.get("kind") in container_kinds}
    opener_by_state: dict[str, str] = {}
    for interaction in interactions:
        if interaction.get("action") in {"dismiss", "update-value"}:
            continue
        for transition in (interaction.get("payload") or {}).get("transitions") or []:
            state_id = transition.get("targetStateId")
            if state_id in container_states:
                opener_by_state.setdefault(state_id, interaction.get("id"))

    def is_descendant(runtime_id: str, ancestor_runtime_ids: set[str]) -> bool:
        current = runtime_id
        while current:
            if current in ancestor_runtime_ids:
                return True
            current = (node_by_runtime.get(current) or {}).get("parentRuntimeId")
        return False

    for interaction in interactions:
        prerequisites = []
        source_runtime_ids = interaction.get("sourceRuntimeIds") or []
        for state_id, state in container_states.items():
            opener_id = opener_by_state.get(state_id)
            if not opener_id or opener_id == interaction.get("id"):
                continue
            target_runtime_ids = {runtime_by_native[node_id] for node_id in state.get("targetNodeIds") or [] if node_id in runtime_by_native}
            if target_runtime_ids and any(is_descendant(runtime_id, target_runtime_ids) for runtime_id in source_runtime_ids):
                prerequisites.append(opener_id)
        interaction["prerequisiteInteractionIds"] = list(dict.fromkeys(prerequisites))
        if prerequisites:
            interaction["sourceVisibleInitially"] = False


def infer_screen_regions(
    selected: list[dict],
    root: dict,
    id_map: dict[str, str],
    interactive_runtime_ids: set[str],
    excluded_node_ids: set[str],
) -> dict:
    """Infer persistent app chrome from geometry and behavior, not CSS names alone."""
    node_by_id = {str(node.get("runtimeId")): node for node in selected}
    children: dict[str, list[str]] = {}
    for node in selected:
        parent_id = str(node.get("parentRuntimeId") or "")
        children.setdefault(parent_id, []).append(str(node.get("runtimeId")))

    def descendant_count(runtime_id: str) -> int:
        pending = [runtime_id]
        seen = set()
        count = 0
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in interactive_runtime_ids:
                count += 1
            pending.extend(children.get(current, []))
        return count

    def eligible(runtime_id: str) -> bool:
        current = runtime_id
        is_candidate = True
        while current:
            node = node_by_id.get(current) or {}
            style = node.get("style") or {}
            if is_candidate and not node.get("visible"):
                return False
            if str(style.get("display") or "") == "none" or str(style.get("visibility") or "") == "hidden":
                return False
            if number := re.search(r"-?\d+(?:\.\d+)?", str(style.get("opacity") or "1")):
                if float(number.group(0)) <= 0:
                    return False
            if str(style.get("pointerEvents") or "") == "none":
                return False
            if id_map.get(current) in excluded_node_ids:
                return False
            current = str(node.get("parentRuntimeId") or "")
            is_candidate = False
        return True

    root_rect = root.get("rect") or {}
    root_x = float(root_rect.get("x") or 0)
    root_y = float(root_rect.get("y") or 0)
    root_width = max(float(root_rect.get("width") or 0), 1)
    root_height = max(float(root_rect.get("height") or 0), 1)
    top_candidates = []
    bottom_candidates = []
    floating_candidates = []

    for node in selected:
        runtime_id = str(node.get("runtimeId") or "")
        if not runtime_id or runtime_id == str(root.get("runtimeId")) or not eligible(runtime_id):
            continue
        rect = node.get("rect") or {}
        width = float(rect.get("width") or 0)
        height = float(rect.get("height") or 0)
        x = float(rect.get("x") or 0) - root_x
        y = float(rect.get("y") or 0) - root_y
        if width <= 0 or height <= 0:
            continue
        style = node.get("style") or {}
        if str(style.get("pointerEvents") or "") == "none":
            continue
        position = str(style.get("position") or "")
        tag = str(node.get("tag") or "").lower()
        hint = " ".join([str(node.get("domId") or ""), *node.get("classNames", [])]).lower()
        interactions = descendant_count(runtime_id)
        width_fraction = width / root_width
        horizontal = style.get("display") in {"flex", "inline-flex"} and style.get("flexDirection") in {"row", "row-reverse"}

        if width_fraction >= 0.72 and 32 <= height <= min(160, root_height * 0.22):
            if y <= max(96, root_height * 0.13):
                score = 0.0
                evidence = ["near-top", f"width-fraction:{width_fraction:.2f}"]
                if position in {"fixed", "sticky"}: score += 2.5; evidence.append(f"position:{position}")
                elif position == "absolute": score += 1.5; evidence.append("position:absolute-root-anchored")
                if tag in {"nav", "header"}: score += 3; evidence.append(f"tag:{tag}")
                if re.search(r"(?:^|[\s_-])(nav|navbar|topbar|top-bar|appbar|app-bar|toolbar|header)(?:$|[\s_-])", hint):
                    score += 2; evidence.append("name-pattern")
                if interactions: score += 1; evidence.append(f"interactive-descendants:{interactions}")
                if horizontal: score += 0.5; evidence.append("horizontal-layout")
                if score >= 3:
                    top_candidates.append((score, width * height, runtime_id, evidence))

            bottom_gap = root_height - (y + height)
            if y >= root_height * 0.62 and bottom_gap <= max(12, root_height * 0.035):
                score = 0.0
                evidence = ["touches-bottom", f"width-fraction:{width_fraction:.2f}"]
                if position in {"fixed", "sticky"}: score += 2.5; evidence.append(f"position:{position}")
                elif position == "absolute": score += 1.5; evidence.append("position:absolute-root-anchored")
                if tag in {"nav", "footer"}: score += 2; evidence.append(f"tag:{tag}")
                if re.search(r"(?:^|[\s_-])(bottom|footer|tabbar|tab-bar|actions?|toolbar|dock)(?:$|[\s_-])", hint):
                    score += 2; evidence.append("name-pattern")
                if interactions >= 2: score += 2; evidence.append(f"interactive-descendants:{interactions}")
                elif interactions == 1: score += 1; evidence.append("interactive-descendants:1")
                if horizontal: score += 1; evidence.append("horizontal-layout")
                if score >= 3:
                    bottom_candidates.append((score, width * height, runtime_id, evidence, interactions))

        if (
            position in {"fixed", "absolute"}
            and 36 <= width <= 96 and 36 <= height <= 96
            and x + width >= root_width * 0.78 and y + height >= root_height * 0.72
            and interactions >= 1
        ):
            floating_candidates.append((2.5, width * height, runtime_id, ["compact-bottom-trailing", f"position:{position}"]))

    def choose(candidates: list[tuple]) -> dict | None:
        if not candidates:
            return None
        score, _, runtime_id, evidence, *rest = sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)[0]
        return {
            "nodeId": id_map.get(runtime_id),
            "confidence": min(0.99, 0.55 + score * 0.07),
            "evidence": evidence,
        }

    top = choose(top_candidates)
    bottom = choose(bottom_candidates)
    floating = choose(floating_candidates)
    if bottom:
        bottom["kind"] = "bottom-action-bar"
    if top:
        top["kind"] = "custom-navigation-bar"
    if floating:
        floating["kind"] = "floating-action"
    return {"topBar": top, "bottomBar": bottom, "floatingAction": floating}


def build_bar_contracts(
    root: dict,
    nodes: list[dict],
    regions: dict,
    interactions: list[dict],
    screen_name: str,
) -> tuple[dict, dict | None]:
    node_by_id = {str(node.get("id")): node for node in nodes}
    children: dict[str, list[str]] = {}
    for node in nodes:
        parent_id = node.get("parentId")
        if parent_id:
            children.setdefault(str(parent_id), []).append(str(node.get("id")))

    def descendant_ids(root_id: str | None) -> set[str]:
        if not root_id:
            return set()
        result: set[str] = set()
        stack = [root_id]
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            stack.extend(children.get(current, []))
        return result

    root_attrs = root.get("attributes") or {}
    top_region = regions.get("topBar") or {}
    top_node = node_by_id.get(str(top_region.get("nodeId") or "")) or {}
    top_hints = top_node.get("iosHints") or {}
    declared_chrome = str(root_attrs.get("data-ios-system-chrome") or "").strip().lower()
    style = str(top_hints.get("navigation-style") or "").strip().lower()
    if not style:
        if declared_chrome == "native":
            style = "native"
        elif top_region:
            style = "custom"
        else:
            style = "hidden"
    navigation = {
        "style": style,
        "title": str(root_attrs.get("data-ios-screen-title") or screen_name),
        "titleMode": str(top_hints.get("title-mode") or root_attrs.get("data-ios-title-mode") or "inline"),
        "scrollEdgeAppearance": str(top_hints.get("scroll-edge") or root_attrs.get("data-ios-scroll-edge") or "automatic"),
        "backButton": str(top_hints.get("back-button") or root_attrs.get("data-ios-back-button") or "system"),
        "sourceNodeId": top_region.get("nodeId"),
        "toolbarItems": [],
    }
    top_descendants = descendant_ids(str(top_region.get("nodeId") or "") or None)
    for interaction in interactions:
        source_id = str(interaction.get("sourceNodeId") or "")
        if source_id not in top_descendants:
            continue
        source = node_by_id.get(source_id) or {}
        hints = source.get("iosHints") or {}
        content = source.get("content") or {}
        rect = (source.get("layout") or {}).get("rect") or {}
        placement = str(hints.get("toolbar-placement") or "")
        if not placement:
            placement = "leading" if interaction.get("action") in {"back", "pop", "dismiss"} or float(rect.get("x") or 0) < float((root.get("rect") or {}).get("width") or 393) / 2 else "trailing"
        navigation["toolbarItems"].append({
            "id": source_id,
            "title": str(content.get("accessibilityLabel") or content.get("text") or "").strip()[:80],
            "icon": hints.get("icon"),
            "placement": placement,
            "sourceNodeId": source_id,
        })

    bottom = regions.get("bottomBar") or {}
    bottom_id = str(bottom.get("nodeId") or "") or None
    bottom_descendants = descendant_ids(bottom_id)
    bottom_node = node_by_id.get(bottom_id or "") or {}
    bottom_hints = bottom_node.get("iosHints") or {}
    explicit_tab = bottom_node.get("semanticType") == "tab-bar" or bottom_hints.get("component") == "tab-bar" or bottom_hints.get("container") == "tab"
    tab_interactions = [
        interaction for interaction in interactions
        if interaction.get("sourceNodeId") in bottom_descendants
        and interaction.get("action") in {"switch-tab", "select-tab"}
        and interaction.get("target")
    ]
    if not bottom_id or (not explicit_tab and len(tab_interactions) < 2):
        if bottom:
            bottom["kind"] = "bottom-action-bar"
        return navigation, None

    items = []
    for index, interaction in enumerate(tab_interactions):
        source_id = str(interaction.get("sourceNodeId") or "")
        source = node_by_id.get(source_id) or {}
        hints = source.get("iosHints") or {}
        content = source.get("content") or {}
        target = str(interaction.get("target") or "")
        title = str(hints.get("tab-title") or content.get("accessibilityLabel") or content.get("text") or target)
        items.append({
            "id": str(hints.get("tab-id") or target or f"tab-{index + 1}"),
            "title": title.strip()[:80],
            "targetScreenId": target,
            "sourceNodeId": source_id,
            "icon": hints.get("icon"),
            "selectedIcon": hints.get("selected-icon"),
            "badge": hints.get("badge"),
            "role": str(hints.get("tab-role") or "normal"),
            "selected": bool((source.get("state") or {}).get("selected")),
        })
    if len(items) < 2:
        bottom["kind"] = "bottom-action-bar"
        return navigation, None
    bottom["kind"] = "tab-bar"
    return navigation, {
        "id": str(bottom_hints.get("tab-id") or f"{items[0]['targetScreenId']}.main-tabs"),
        "sourceNodeId": bottom_id,
        "items": items,
        "initialTabId": next((item["id"] for item in items if item["selected"]), items[0]["id"]),
        "reselectBehavior": str(bottom_hints.get("reselect") or "keep"),
        "visibility": str(bottom_hints.get("tab-visibility") or "automatic"),
    }


def matches_descendant_selector(node: dict, selector: str, node_by_runtime: dict[str, dict]) -> bool:
    if not selector or any(character in selector for character in "[],+~"):
        return False
    tokens = [token for token in re.split(r"\s+|\s*>\s*", selector.strip()) if token]
    if not tokens:
        return False

    def matches_token(candidate: dict, token: str) -> bool:
        token = re.sub(r":[A-Za-z-]+(?:\([^)]*\))?", "", token)
        tag_match = re.match(r"^[A-Za-z][A-Za-z0-9-]*", token)
        if tag_match and str(candidate.get("tag") or "").lower() != tag_match.group(0).lower():
            return False
        id_matches = re.findall(r"#([A-Za-z0-9_-]+)", token)
        if id_matches and candidate.get("domId") not in id_matches:
            return False
        classes = set(candidate.get("classNames") or [])
        if any(class_name not in classes for class_name in re.findall(r"\.([A-Za-z0-9_-]+)", token)):
            return False
        return bool(tag_match or id_matches or re.search(r"\.[A-Za-z0-9_-]+", token))

    if not matches_token(node, tokens[-1]):
        return False
    current_id = node.get("parentRuntimeId")
    for token in reversed(tokens[:-1]):
        matched = False
        while current_id:
            candidate = node_by_runtime.get(current_id) or {}
            if matches_token(candidate, token):
                matched = True
                current_id = candidate.get("parentRuntimeId")
                break
            current_id = candidate.get("parentRuntimeId")
        if not matched:
            return False
    return True


def build_ir(data: dict, args) -> dict:
    root = select_root(data, args.root_runtime_id, args.root_selector)
    all_nodes = data.get("nodes") or []
    selected = descendants(all_nodes, root["runtimeId"])
    selected = filter_other_route_screens(all_nodes, selected, args.route_graph_data, args.screen_id)
    selected = [
        node for node in selected
        if node.get("tag") not in SKIP_TAGS
        and not (node.get("tag") == "input" and node.get("attributes", {}).get("type", "text").lower() == "hidden")
    ]
    included_runtime_ids = {node.get("runtimeId") for node in selected}
    raw_interactions = [item for item in data.get("interactions", []) if item.get("sourceRuntimeId") in included_runtime_ids]
    raw_normalized_interactions = [normalize_interaction(item, args.screen_id, index) for index, item in enumerate(raw_interactions)]

    root_rect = root.get("rect") or {}
    source_width = float(root_rect.get("width") or data.get("document", {}).get("viewport", {}).get("width") or args.target_width)
    scale = args.target_width / source_width if source_width else 1.0
    root_x = float(root_rect.get("x") or 0)
    root_y = float(root_rect.get("y") or 0)
    id_map: dict[str, str] = {}
    used: set[str] = set()
    for node in selected:
        base = f"{args.screen_id}.{slug(str(node.get('runtimeId') or 'node'))}"
        native_id = base
        suffix = 2
        while native_id in used:
            native_id = f"{base}-{suffix}"
            suffix += 1
        used.add(native_id)
        id_map[node.get("runtimeId")] = native_id

    selector_to_runtime_ids: dict[str, list[str]] = {}
    selector_to_node_ids: dict[str, list[str]] = {}
    selector_visibility: dict[str, list[bool]] = {}
    for node in selected:
        aliases = set()
        if node.get("selector"):
            aliases.add(node["selector"])
        if node.get("domId"):
            aliases.add(f"#{node['domId']}")
        aliases.update(f".{class_name}" for class_name in node.get("classNames") or [] if class_name)
        for selector in aliases:
            selector_to_runtime_ids.setdefault(selector, []).append(node.get("runtimeId"))
            selector_to_node_ids.setdefault(selector, []).append(id_map[node.get("runtimeId")])
            selector_visibility.setdefault(selector, []).append(bool(node.get("visible")))
    selected_by_runtime = {node.get("runtimeId"): node for node in selected}
    contract_selectors = {
        selector
        for item in [*((args.interaction_graph_data or {}).get("interactions") or []), *((args.interaction_graph_data or {}).get("states") or [])]
        if isinstance(item, dict)
        for selector in [item.get("sourceSelector") or item.get("targetSelector")]
        if selector
    }
    for selector in contract_selectors:
        if selector in selector_to_runtime_ids:
            continue
        for node in selected:
            if not matches_descendant_selector(node, selector, selected_by_runtime):
                continue
            selector_to_runtime_ids.setdefault(selector, []).append(node.get("runtimeId"))
            selector_to_node_ids.setdefault(selector, []).append(id_map[node.get("runtimeId")])
            selector_visibility.setdefault(selector, []).append(bool(node.get("visible")))
    for selector, runtime_ids in selector_to_runtime_ids.items():
        ordered = sorted(
            zip(runtime_ids, selector_to_node_ids[selector], selector_visibility[selector]),
            key=lambda item: (
                "active" in (selected_by_runtime.get(item[0]) or {}).get("classNames", []),
                str(((selected_by_runtime.get(item[0]) or {}).get("attributes") or {}).get("aria-selected", "")).lower() == "true",
            ),
        )
        selector_to_runtime_ids[selector] = [item[0] for item in ordered]
        selector_to_node_ids[selector] = [item[1] for item in ordered]
        selector_visibility[selector] = [item[2] for item in ordered]
    dynamic_interactions, dynamic_states, dynamic_warnings = normalize_dynamic_contracts(
        args.interaction_graph_data,
        args.interaction_overrides_data,
        args.screen_id,
        selector_to_node_ids,
        selector_to_runtime_ids,
        selector_visibility,
    )
    attach_interaction_prerequisites(dynamic_interactions, dynamic_states, selected, id_map)
    dynamic_runtime_ids = {
        runtime_id
        for interaction in dynamic_interactions
        for runtime_id in interaction.get("sourceRuntimeIds") or []
    }
    raw_pairs = [
        (raw, normalized)
        for raw, normalized in zip(raw_interactions, raw_normalized_interactions)
        if raw.get("sourceRuntimeId") not in dynamic_runtime_ids
        and (not args.interaction_graph_data or normalized.get("action") != "unknown")
    ]
    interactions_by_runtime: dict[str, list[dict]] = {}
    for raw, normalized in raw_pairs:
        interactions_by_runtime.setdefault(raw.get("sourceRuntimeId"), []).append(normalized)
    for interaction in dynamic_interactions:
        for runtime_id in interaction.get("sourceRuntimeIds") or []:
            interactions_by_runtime.setdefault(runtime_id, []).append(interaction)

    assets = []
    asset_by_source: dict[str, str] = {}
    nodes_out = []
    warnings = []
    for warning in data.get("warnings") or []:
        if isinstance(warning, dict):
            warnings.append(warning)
        else:
            warnings.append({
                "code": "RENDER_TREE_WARNING",
                "severity": "warning",
                "nodeId": None,
                "message": str(warning),
                "fallback": "Review the source render and blocked resources.",
            })
    warnings.extend(dynamic_warnings)
    node_by_runtime = {node.get("runtimeId"): node for node in selected}
    for node in selected:
        runtime_id = node.get("runtimeId")
        parent_runtime = node.get("parentRuntimeId")
        while parent_runtime and parent_runtime not in id_map:
            parent_runtime = (node_by_runtime.get(parent_runtime) or {}).get("parentRuntimeId")
        node_interactions = interactions_by_runtime.get(runtime_id, [])
        mapping = semantic_mapping(node, bool(node_interactions))
        mapping["nativeMapping"]["availability"] = mapping_availability(mapping["nativeMapping"], args)
        rect = node.get("rect") or {}
        attrs = node.get("attributes") or {}
        properties = node.get("properties") or {}
        style = node.get("style") or {}
        asset_ref = None
        asset_source = node.get("asset")
        if asset_source:
            asset_details = node.get("assetDetails") or {}
            asset_kind = str(asset_details.get("kind") or "image")
            if asset_kind == "css-background":
                asset_source = css_background_url(asset_details.get("value"))
            markup = asset_details.get("markup") if asset_source == "inline-svg" else None
            if not asset_source:
                markup = None
            local_path = local_path_from_asset(str(asset_source)) if asset_source else None
            asset_key = (
                f"inline-svg:{hashlib.sha256(markup.encode('utf-8')).hexdigest()}"
                if markup
                else str(asset_source)
            )
            if asset_source and asset_key not in asset_by_source:
                asset_id = f"asset.{len(assets) + 1}"
                asset_by_source[asset_key] = asset_id
                ios_name = re.sub(r"[^A-Za-z0-9_]+", "_", f"html_{args.screen_id}_{runtime_id}").strip("_")
                assets.append({
                    "id": asset_id,
                    "kind": "inline-svg" if asset_source == "inline-svg" else asset_kind,
                    "source": asset_source,
                    "markup": markup,
                    "localPath": local_path,
                    "url": asset_source if not local_path else None,
                    "contentType": None,
                    "dimensions": {"width": rect.get("width", 0), "height": rect.get("height", 0)},
                    "renderMode": (asset_details.get("size") if asset_kind == "css-background" else style.get("objectFit")) or "original",
                    "position": (asset_details.get("position") if asset_kind == "css-background" else style.get("objectPosition")) or "50% 50%",
                    "repeat": asset_details.get("repeat") if asset_kind == "css-background" else "no-repeat",
                    "licenseStatus": "local-provided" if data.get("source", {}).get("kind") == "html-file" else "unknown",
                    "iosName": ios_name,
                    "integrationStatus": "pending",
                })
            asset_ref = asset_by_source.get(asset_key)
        keyboard_type, content_type = keyboard_metadata(str(attrs.get("type") or "text").lower())
        native_node_id = id_map[runtime_id]
        confidence = mapping["nativeMapping"]["confidence"]
        text_metrics = node.get("textMetrics") or {}
        scroll = scroll_contract(node)
        if node.get("rectEstimated"):
            warnings.append({
                "code": "SYNTHETIC_RECT_ESTIMATED",
                "severity": "info",
                "nodeId": native_node_id,
                "message": f"Pseudo-element bounds are estimated for {node.get('selector')}",
                "fallback": "Use multimodal visual review and adjust the native overlay against the baseline screenshot.",
            })
        if confidence < 0.7:
            warnings.append({
                "code": "LOW_MAPPING_CONFIDENCE",
                "severity": "warning",
                "nodeId": native_node_id,
                "message": f"Low-confidence native mapping for {node.get('selector')}",
                "fallback": "Review control-mapping-matrix.md and confirm the semantic type.",
            })
        nodes_out.append({
            "id": native_node_id,
            "parentId": id_map.get(parent_runtime),
            "source": {
                "selector": node.get("selector"),
                "tag": node.get("tag"),
                "domId": node.get("domId"),
                "runtimeId": runtime_id,
                "synthetic": node.get("synthetic"),
                "encapsulation": node.get("encapsulation"),
            },
            "semanticType": mapping["semanticType"],
            "layout": {
                "mode": layout_mode(style),
                "rect": {
                    "x": (float(rect.get("x") or 0) - root_x) * scale,
                    "y": (float(rect.get("y") or 0) - root_y) * scale,
                    "width": float(rect.get("width") or 0) * scale,
                    "height": float(rect.get("height") or 0) * scale,
                },
                "sourceRectCssPx": {key: rect.get(key, 0) for key in ("x", "y", "width", "height")},
                "estimated": bool(node.get("rectEstimated")),
                "display": style.get("display"),
                "position": style.get("position"),
                "overflowX": style.get("overflowX"),
                "overflowY": style.get("overflowY"),
                "scrollAxis": scroll["axis"],
                "scrollMetrics": {
                    key: scroll[key]
                    for key in (
                        "horizontalAllowed", "verticalAllowed",
                        "overflowsHorizontally", "overflowsVertically",
                        "scrollWidth", "scrollHeight", "clientWidth", "clientHeight",
                    )
                },
            },
            "style": style,
            "content": {
                "text": node.get("text") or None,
                "runs": [
                    {
                        "kind": item.get("kind"),
                        "text": item.get("text"),
                        "nodeId": id_map.get(item.get("runtimeId")) if item.get("runtimeId") else None,
                        "domIndex": item.get("domIndex"),
                        "rect": {
                            key: float((item.get("rect") or {}).get(key) or 0) * scale
                            for key in ("x", "y", "width", "height")
                        } if item.get("rect") else None,
                        "sourceRectCssPx": {
                            key: (item.get("rect") or {}).get(key, 0)
                            for key in ("x", "y", "width", "height")
                        } if item.get("rect") else None,
                    }
                    for item in (node.get("contentRuns") or [])
                    if item.get("kind") == "text" or id_map.get(item.get("runtimeId"))
                ],
                "placeholder": attrs.get("placeholder"),
                "value": first_known(properties.get("value"), attrs.get("value")),
                "accessibilityLabel": attrs.get("aria-label"),
                "lines": text_metrics.get("lineCount"),
                "lineRects": text_metrics.get("lineRects") or [],
                "clippedHorizontally": bool(text_metrics.get("clippedHorizontally")),
                "clippedVertically": bool(text_metrics.get("clippedVertically")),
                "isDecorative": attrs.get("aria-hidden") == "true",
            },
            "state": {
                "initiallyVisible": bool(node.get("visible")),
                "enabled": not first_known(properties.get("disabled"), attr_present(attrs, "disabled")),
                "selected": first_known(properties.get("selected"), attr_bool(attrs, "selected"), attr_bool(attrs, "aria-selected")),
                "checked": first_known(properties.get("checked"), attr_bool(attrs, "checked"), attr_bool(attrs, "aria-checked")),
                "expanded": first_known(properties.get("open"), attr_bool(attrs, "open"), attr_bool(attrs, "aria-expanded")),
                "readonly": first_known(properties.get("readOnly"), attr_present(attrs, "readonly")),
                "required": first_known(properties.get("required"), attr_present(attrs, "required")),
                "focused": properties.get("focused"),
                "min": attrs.get("min"),
                "max": attrs.get("max"),
                "step": attrs.get("step"),
                "value": first_known(properties.get("value"), attrs.get("value")),
                "groupName": attrs.get("name"),
                "keyboardType": keyboard_type,
                "contentType": content_type,
                "submitLabel": None,
            },
            "nativeMapping": mapping["nativeMapping"],
            "iosHints": {
                key.removeprefix("data-ios-"): value
                for key, value in attrs.items()
                if key.startswith("data-ios-")
            },
            "assetRef": asset_ref,
            "interactionRef": node_interactions[0]["id"] if node_interactions else None,
            "interactionRefs": [item["id"] for item in node_interactions],
            "support": mapping["support"],
        })

    normalized_interactions = []
    for raw, interaction in raw_pairs:
        interaction["sourceNodeId"] = id_map.get(interaction.pop("sourceRuntimeId"))
        normalized_interactions.append(interaction)
    for interaction in dynamic_interactions:
        interaction.pop("sourceRuntimeIds", None)
        normalized_interactions.append(interaction)

    selector_to_node_id = {selector: node_ids[0] for selector, node_ids in selector_to_node_ids.items() if node_ids}
    normalized_motions = [
        normalize_motion(item, args.screen_id, index, selector_to_node_id)
        for index, item in enumerate(data.get("motions") or [])
        if item.get("sourceSelector") in selector_to_node_id
    ]

    root_native_id = id_map[root["runtimeId"]]
    region_interactive_ids = {str(runtime_id) for runtime_id in interactions_by_runtime if runtime_id}
    presentation_node_ids = {
        str(node_id)
        for state in dynamic_states
        if str(state.get("kind") or "") in {"sheet", "full-screen", "fullscreen", "full-screen-overlay", "popover", "popover-overlay", "overlay", "dialog"}
        for node_id in state.get("targetNodeIds") or []
    }
    regions = infer_screen_regions(selected, root, id_map, region_interactive_ids, presentation_node_ids)
    route_screen = next((
        screen for screen in (args.route_graph_data or {}).get("screens") or []
        if isinstance(screen, dict) and screen.get("id") == args.screen_id
    ), None)
    screen_activation = dict((route_screen or {}).get("activation") or {})
    if screen_activation:
        capture_configuration = data.get("captureConfiguration") or {}
        screen_activation["settleDelayMs"] = int(capture_configuration.get("totalPostActivationWaitMs") or 0)
    screen_name = args.screen_name or str((route_screen or {}).get("title") or args.screen_id).title()
    navigation, tab_container = build_bar_contracts(root, nodes_out, regions, normalized_interactions, screen_name)
    return {
        "schemaVersion": "1.2",
        "source": {
            "kind": data.get("source", {}).get("kind"),
            "entry": data.get("source", {}).get("entry"),
            "language": data.get("document", {}).get("language"),
            "direction": data.get("document", {}).get("direction"),
            "viewport": data.get("document", {}).get("viewport"),
            "capturedAt": data.get("capturedAt"),
            "baselineScreenshot": data.get("screenshot"),
            "renderTree": str(args.render_tree.resolve()),
            "routeGraph": str(args.route_graph.resolve()) if args.route_graph else None,
            "interactionGraph": str(args.interaction_graph.resolve()) if args.interaction_graph else None,
            "interactionOverrides": str(args.interaction_overrides.resolve()) if args.interaction_overrides else None,
            "interactionFingerprint": ((args.interaction_graph_data or {}).get("source") or {}).get("fingerprint"),
            "screenActivation": screen_activation or None,
        },
        "target": {
            "platform": "ios",
            "uiStack": args.ui_stack,
            "device": args.device,
            "viewportPt": {"width": args.target_width, "height": args.target_height},
            "orientation": args.orientation,
            "appearance": args.appearance,
            "minimumIOS": args.minimum_ios,
            "scale": scale,
        },
        "screens": [{
            "id": args.screen_id,
            "name": screen_name,
            "moduleId": str((root.get("attributes") or {}).get("data-ios-module") or "").strip() or None,
            "rootNodeId": root_native_id,
            "sourceSelector": root.get("selector"),
            "sourceRuntimeId": root.get("runtimeId"),
            "systemChrome": {
                "statusBar": "native",
                "navigationBar": "none" if navigation["style"] == "hidden" else navigation["style"],
                "homeIndicator": "native",
            },
            "navigation": navigation,
            "tabContainer": tab_container,
            "regions": regions,
            "nodes": nodes_out,
        }],
        "interactions": normalized_interactions,
        "states": dynamic_states,
        "motions": normalized_motions,
        "visualStates": visual_states(root, normalized_interactions, normalized_motions),
        "assets": assets,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("render_tree", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--screen-id", default="screen")
    parser.add_argument("--screen-name")
    parser.add_argument("--ui-stack", choices=("swiftui", "uikit"), default="swiftui")
    parser.add_argument("--root-runtime-id")
    parser.add_argument("--root-selector")
    parser.add_argument("--target-width", type=float, default=393)
    parser.add_argument("--target-height", type=float, default=852)
    parser.add_argument("--device", default="iPhone 15 Pro")
    parser.add_argument("--minimum-ios")
    parser.add_argument("--verified-sdk", help="SDK identifier from inspect_ios_sdk.py, for example iphoneos-26.2")
    parser.add_argument("--sdk-report", type=Path, help="JSON report generated by inspect_ios_sdk.py")
    parser.add_argument("--route-graph", type=Path, help="JSON graph generated by discover_html_routes.cjs")
    parser.add_argument("--interaction-graph", type=Path, help="JSON graph generated by discover_html_interactions.cjs")
    parser.add_argument("--interaction-overrides", type=Path, help="Optional ambiguity resolutions for --interaction-graph")
    parser.add_argument("--orientation", choices=("portrait", "landscape"), default="portrait")
    parser.add_argument("--appearance", choices=("light", "dark"), default="light")
    args = parser.parse_args()
    try:
        args.sdk_report_data = json.loads(args.sdk_report.read_text(encoding="utf-8")) if args.sdk_report else None
        if args.sdk_report_data and args.sdk_report_data.get("schemaVersion") != "ios-sdk-report-1.0":
            raise ValueError("--sdk-report must use schemaVersion ios-sdk-report-1.0")
        args.route_graph_data = json.loads(args.route_graph.read_text(encoding="utf-8")) if args.route_graph else None
        if args.route_graph_data and args.route_graph_data.get("schemaVersion") != "html-route-graph-1.0":
            raise ValueError("--route-graph must use schemaVersion html-route-graph-1.0")
        args.interaction_graph_data = json.loads(args.interaction_graph.read_text(encoding="utf-8")) if args.interaction_graph else None
        if args.interaction_graph_data and args.interaction_graph_data.get("schemaVersion") != "interaction-state-graph-1.0":
            raise ValueError("--interaction-graph must use schemaVersion interaction-state-graph-1.0")
        args.interaction_overrides_data = json.loads(args.interaction_overrides.read_text(encoding="utf-8")) if args.interaction_overrides else None
        if args.interaction_overrides_data and args.interaction_overrides_data.get("schemaVersion") != "html-to-ios-overrides-1.0":
            raise ValueError("--interaction-overrides must use schemaVersion html-to-ios-overrides-1.0")
        if args.interaction_overrides_data and not args.interaction_graph_data:
            raise ValueError("--interaction-overrides requires --interaction-graph")
        data = json.loads(args.render_tree.read_text(encoding="utf-8"))
        if data.get("schemaVersion") not in {"render-tree-1.0", "render-tree-1.1", "render-tree-1.2"}:
            raise ValueError("Input schemaVersion must be render-tree-1.0, render-tree-1.1, or render-tree-1.2")
        result = build_ir(data, args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "out": str(args.out.resolve()),
        "screen": result["screens"][0]["id"],
        "sourceRoot": result["screens"][0]["sourceRuntimeId"],
        "nodes": len(result["screens"][0]["nodes"]),
        "interactions": len(result["interactions"]),
        "states": len(result.get("states") or []),
        "motions": len(result["motions"]),
        "visualStates": len(result["visualStates"]),
        "assets": len(result["assets"]),
        "warnings": len(result["warnings"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
