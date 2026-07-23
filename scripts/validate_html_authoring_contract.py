#!/usr/bin/env python3
"""Validate optional HTML annotations used for deterministic iOS conversion."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


SCHEMA_VERSION = "html-to-ios-authoring-contract-report-1.0"

COMPONENTS = {
    "activity-indicator", "alert", "button", "checkbox", "collection", "color-picker",
    "context-menu", "date-picker", "disclosure", "file-picker", "form", "gauge", "image",
    "label", "link", "list", "map", "menu", "navigation-bar", "page-control", "picker",
    "popover", "progress", "radio", "scroll-view", "search-field", "segmented-control",
    "sheet", "slider", "split-view", "stepper", "switch", "tab-bar", "table", "text",
    "text-editor", "text-field", "toolbar", "video", "web-content",
}
ACTIONS = {
    "add-child", "dismiss", "open-url", "pop", "pop-to-root", "present-fullscreen",
    "present-popover", "present-sheet", "push", "remove-child", "replace-root",
    "select-tab", "show-alert", "show-context-menu", "toggle-state", "update-state",
}
TARGET_ACTIONS = {
    "add-child", "present-fullscreen", "present-popover", "present-sheet", "push",
    "replace-root", "select-tab", "show-alert", "toggle-state", "update-state",
}
PRESENTATIONS = {"automatic", "form-sheet", "full-screen", "page-sheet", "popover"}
CONTAINERS = {"child-controller", "navigation", "page", "split", "tab"}
OWNERS = {"child-controller", "navigation", "overlay", "screen", "sheet"}
ANIMATIONS = {"custom", "fade", "matched", "none", "progress", "scale", "slide-leading", "slide-trailing", "slide-up", "spring"}
EASINGS = {"ease-in", "ease-in-out", "ease-out", "linear", "spring"}
SYSTEM_CHROME = {"custom", "native", "none"}
NAVIGATION_STYLES = {"custom", "hidden", "immersive", "native"}
TITLE_MODES = {"inline", "large"}
SCROLL_EDGES = {"automatic", "opaque", "transparent"}
TOOLBAR_PLACEMENTS = {"leading", "principal", "primary", "trailing"}
BACK_BUTTONS = {"custom", "hidden", "system"}
TAB_ROLES = {"normal", "search"}
TAB_RESELECT = {"keep", "pop-to-root", "scroll-to-top"}
TAB_VISIBILITY = {"always", "automatic", "hide-on-push"}
BOOLEAN_ATTRIBUTES = {
    "data-ios-app-root", "data-ios-backdrop-dismiss", "data-ios-ignore", "data-ios-interactive-dismiss",
    "data-ios-required-state", "data-ios-screen-initial", "data-ios-scroll-root", "data-ios-shell",
}
INTERACTIVE_TAGS = {"a", "button", "input", "select", "summary", "textarea"}


def present(attrs: dict[str, str | None], name: str) -> bool:
    if name not in attrs:
        return False
    return str(attrs.get(name) or "").lower() not in {"0", "false", "no", "none"}


def issue(code: str, severity: str, message: str, line: int, attribute: str | None = None) -> dict:
    return {"code": code, "severity": severity, "message": message, "line": line, "attribute": attribute}


class ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append({"tag": tag.lower(), "attrs": dict(attrs), "line": self.getpos()[0]})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def enum_check(issues: list[dict], attrs: dict, line: int, name: str, allowed: set[str]) -> None:
    value = str(attrs.get(name) or "").strip()
    if name in attrs and value not in allowed:
        issues.append(issue("INVALID_ENUM", "error", f"{name}={value!r} is not supported", line, name))


def validate(path: Path) -> dict:
    parser = ContractParser()
    parser.feed(path.read_text(encoding="utf-8"))
    issues: list[dict] = []
    screens: list[dict] = []
    node_ids: list[tuple[str, int]] = []
    html_ids: set[str] = set()
    app_roots = 0
    advanced_hints = 0
    interactive_count = 0
    annotated_interactive_count = 0

    for element in parser.elements:
        tag, attrs, line = element["tag"], element["attrs"], element["line"]
        if attrs.get("id"):
            html_ids.add(str(attrs["id"]))
        if present(attrs, "data-ios-app-root"):
            app_roots += 1
        if attrs.get("data-ios-screen"):
            screen_id = str(attrs["data-ios-screen"])
            screens.append({
                "id": screen_id,
                "moduleId": str(attrs.get("data-ios-module") or "").strip() or None,
                "line": line,
                "initial": present(attrs, "data-ios-screen-initial"),
            })
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", screen_id):
                issues.append(issue("INVALID_SCREEN_ID", "error", "data-ios-screen must use lower-kebab-case", line, "data-ios-screen"))
        if attrs.get("data-ios-module"):
            module_id = str(attrs["data-ios-module"])
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", module_id):
                issues.append(issue("INVALID_MODULE_ID", "error", "data-ios-module must use lower-kebab-case", line, "data-ios-module"))
        if attrs.get("data-ios-node-id"):
            node_ids.append((str(attrs["data-ios-node-id"]), line))
        if attrs.get("data-ios-tab-id"):
            tab_id = str(attrs["data-ios-tab-id"])
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", tab_id):
                issues.append(issue("INVALID_TAB_ID", "error", "data-ios-tab-id must use lower-kebab-case", line, "data-ios-tab-id"))

        is_interactive = tag in INTERACTIVE_TAGS or str(attrs.get("role") or "") in {"button", "checkbox", "link", "radio", "slider", "switch", "tab"}
        if is_interactive:
            interactive_count += 1
            if attrs.get("data-ios-component") or attrs.get("data-ios-action") or attrs.get("data-ios-node-id"):
                annotated_interactive_count += 1

        enum_check(issues, attrs, line, "data-ios-component", COMPONENTS)
        project_component = str(attrs.get("data-ios-project-component") or "").strip()
        if project_component and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", project_component):
            issues.append(issue("INVALID_PROJECT_COMPONENT", "error", "data-ios-project-component must be a Swift type or namespaced component identifier", line, "data-ios-project-component"))
        enum_check(issues, attrs, line, "data-ios-action", ACTIONS)
        enum_check(issues, attrs, line, "data-ios-presentation-style", PRESENTATIONS)
        enum_check(issues, attrs, line, "data-ios-container", CONTAINERS)
        enum_check(issues, attrs, line, "data-ios-owner", OWNERS)
        enum_check(issues, attrs, line, "data-ios-animation", ANIMATIONS)
        enum_check(issues, attrs, line, "data-ios-system-chrome", SYSTEM_CHROME)
        enum_check(issues, attrs, line, "data-ios-navigation-style", NAVIGATION_STYLES)
        enum_check(issues, attrs, line, "data-ios-title-mode", TITLE_MODES)
        enum_check(issues, attrs, line, "data-ios-scroll-edge", SCROLL_EDGES)
        enum_check(issues, attrs, line, "data-ios-toolbar-placement", TOOLBAR_PLACEMENTS)
        enum_check(issues, attrs, line, "data-ios-back-button", BACK_BUTTONS)
        enum_check(issues, attrs, line, "data-ios-tab-role", TAB_ROLES)
        enum_check(issues, attrs, line, "data-ios-reselect", TAB_RESELECT)
        enum_check(issues, attrs, line, "data-ios-tab-visibility", TAB_VISIBILITY)
        easing = str(attrs.get("data-ios-easing") or "").strip()
        if easing and easing not in EASINGS and not re.fullmatch(r"cubic-bezier\([^)]*\)", easing):
            issues.append(issue("INVALID_EASING", "error", f"Unsupported data-ios-easing={easing!r}", line, "data-ios-easing"))
        for name in ("data-ios-duration-ms", "data-ios-delay-ms"):
            value = str(attrs.get(name) or "").strip()
            if value and (not re.fullmatch(r"\d+(?:\.\d+)?", value) or float(value) < 0):
                issues.append(issue("INVALID_DURATION", "error", f"{name} must be a non-negative number", line, name))
        for name in BOOLEAN_ATTRIBUTES:
            value = str(attrs.get(name) or "").lower()
            if name in attrs and value not in {"", "0", "1", "false", "no", "true", "yes", "none"}:
                issues.append(issue("INVALID_BOOLEAN", "warning", f"Prefer true/false for {name}", line, name))
        if any(name in attrs for name in ("data-ios-state", "data-ios-animation", "data-ios-visual-state", "data-ios-owner")):
            advanced_hints += 1

    screen_counts = Counter(item["id"] for item in screens)
    for screen_id, count in screen_counts.items():
        if count > 1:
            lines = [str(item["line"]) for item in screens if item["id"] == screen_id]
            issues.append(issue("DUPLICATE_SCREEN_ID", "error", f"Screen {screen_id!r} appears {count} times at lines {', '.join(lines)}", int(lines[0]), "data-ios-screen"))
    node_counts = Counter(item[0] for item in node_ids)
    for node_id, count in node_counts.items():
        if count > 1:
            lines = [str(line) for value, line in node_ids if value == node_id]
            issues.append(issue("DUPLICATE_NODE_ID", "error", f"Node ID {node_id!r} appears {count} times at lines {', '.join(lines)}", int(lines[0]), "data-ios-node-id"))
    if sum(1 for item in screens if item["initial"]) > 1:
        issues.append(issue("MULTIPLE_INITIAL_SCREENS", "error", "Only one screen may use data-ios-screen-initial", 1, "data-ios-screen-initial"))
    if app_roots > 1:
        issues.append(issue("MULTIPLE_APP_ROOTS", "error", "Only one data-ios-app-root is allowed per HTML document", 1, "data-ios-app-root"))

    known_targets = set(screen_counts) | html_ids | set(node_counts)
    for element in parser.elements:
        attrs, line = element["attrs"], element["line"]
        action = str(attrs.get("data-ios-action") or "").strip()
        target = str(attrs.get("data-ios-target") or "").strip()
        if action in TARGET_ACTIONS and not target:
            issues.append(issue("MISSING_ACTION_TARGET", "error", f"{action} requires data-ios-target", line, "data-ios-target"))
        if target and action != "open-url":
            normalized = target.lstrip("#")
            parsed = urlparse(target)
            if not parsed.scheme and normalized not in known_targets:
                issues.append(issue("UNKNOWN_ACTION_TARGET", "error", f"Target {target!r} does not match a screen or element ID", line, "data-ios-target"))

    if not screens:
        issues.append(issue("NO_EXPLICIT_SCREENS", "warning", "No data-ios-screen annotations; screen discovery will use runtime inference", 1, "data-ios-screen"))
    if app_roots == 0:
        issues.append(issue("NO_EXPLICIT_APP_ROOT", "warning", "No data-ios-app-root; the mobile app root will be inferred", 1, "data-ios-app-root"))

    coverage = round(annotated_interactive_count / interactive_count, 3) if interactive_count else 1.0
    if screens and app_roots and advanced_hints and coverage >= 0.8:
        level = "L2-deterministic"
    elif screens and app_roots:
        level = "L1-structured"
    else:
        level = "L0-inferred"
    errors = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] == "warning"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "failed" if errors else "passed-with-warnings" if warnings else "passed",
        "source": str(path.resolve()),
        "level": level,
        "metrics": {
            "appRoots": app_roots,
            "screens": len(screens),
            "interactiveElements": interactive_count,
            "annotatedInteractiveElements": annotated_interactive_count,
            "interactiveAnnotationCoverage": coverage,
            "advancedHintElements": advanced_hints,
        },
        "screens": screens,
        "issues": issues,
        "summary": {"errors": len(errors), "warnings": len(warnings)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not args.html.is_file():
        parser.error(f"HTML file does not exist: {args.html}")
    report = validate(args.html)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 1 if report["summary"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
