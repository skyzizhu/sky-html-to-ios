#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_ui_ir.py"


def style(position: str = "static", direction: str = "column") -> dict:
    return {
        "display": "flex",
        "position": position,
        "flexDirection": direction,
        "overflowX": "visible",
        "overflowY": "visible",
        "padding": ["0px"] * 4,
        "margin": ["0px"] * 4,
        "cornerRadii": ["0px"] * 4,
        "backgroundColor": "transparent",
        "backgroundImage": "none",
        "color": "rgb(0, 0, 0)",
        "fontSize": "16px",
        "fontWeight": "400",
        "gap": "0px",
    }


def render_node(
    runtime_id: str,
    parent: str | None,
    tag: str,
    rect: dict,
    *,
    position: str = "static",
    direction: str = "column",
    dom_id: str | None = None,
    scroll: dict | None = None,
    text_metrics: dict | None = None,
) -> dict:
    return {
        "runtimeId": runtime_id,
        "parentRuntimeId": parent,
        "selector": f"#{dom_id or runtime_id}",
        "tag": tag,
        "domId": dom_id,
        "classNames": [],
        "attributes": {},
        "properties": {},
        "text": None,
        "contentRuns": [],
        "rect": rect,
        "visible": True,
        "style": style(position, direction),
        "asset": None,
        "assetDetails": None,
        "scroll": scroll or {
            "scrollWidth": rect["width"],
            "scrollHeight": rect["height"],
            "clientWidth": rect["width"],
            "clientHeight": rect["height"],
        },
        "textMetrics": text_metrics,
    }


class BuildUIIRTests(unittest.TestCase):
    def test_scroll_axis_text_lines_and_horizontal_carousel_survive_ir_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node(
                    "screen",
                    None,
                    "main",
                    {"x": 0, "y": 0, "width": 393, "height": 852},
                    scroll={
                        "scrollWidth": 393,
                        "scrollHeight": 1450,
                        "clientWidth": 393,
                        "clientHeight": 852,
                    },
                ),
                render_node(
                    "rail",
                    "screen",
                    "section",
                    {"x": 20, "y": 80, "width": 353, "height": 56},
                    direction="row",
                    scroll={
                        "scrollWidth": 620,
                        "scrollHeight": 56,
                        "clientWidth": 353,
                        "clientHeight": 56,
                    },
                ),
                render_node("item", "rail", "div", {"x": 20, "y": 88, "width": 88, "height": 40}, direction="row"),
                render_node(
                    "label",
                    "item",
                    "span",
                    {"x": 36, "y": 98, "width": 64, "height": 20},
                    text_metrics={
                        "lineCount": 1,
                        "lineRects": [{"x": 36, "y": 98, "width": 64, "height": 20}],
                        "clippedHorizontally": False,
                        "clippedVertically": False,
                    },
                ),
            ]
            nodes[0]["style"].update({"overflowX": "hidden", "overflowY": "auto"})
            nodes[1]["style"].update({
                "overflowX": "auto",
                "overflowY": "hidden",
                "flexWrap": "nowrap",
            })
            nodes[3]["text"] = "Single line"
            nodes[3]["style"]["whiteSpace"] = "nowrap"
            data = {
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": nodes,
                "interactions": [],
                "phoneCandidates": [],
            }
            source = root / "render-tree.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "screen", "--screen-id", "generic",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            generated = json.loads(output.read_text(encoding="utf-8"))
            by_runtime_id = {
                item["source"]["runtimeId"]: item
                for item in generated["screens"][0]["nodes"]
            }
            self.assertEqual(by_runtime_id["screen"]["layout"]["scrollAxis"], "vertical")
            self.assertFalse(by_runtime_id["screen"]["layout"]["scrollMetrics"]["overflowsHorizontally"])
            self.assertEqual(by_runtime_id["rail"]["semanticType"], "carousel")
            self.assertEqual(by_runtime_id["rail"]["layout"]["scrollAxis"], "horizontal")
            self.assertEqual(by_runtime_id["label"]["content"]["lines"], 1)
            self.assertEqual(len(by_runtime_id["label"]["content"]["lineRects"]), 1)

    def test_explicit_native_navigation_and_tab_contracts_enter_ir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
                render_node("tabs", "app", "nav", {"x": 0, "y": 772, "width": 393, "height": 80}, position="fixed", direction="row"),
                render_node("home-tab", "tabs", "button", {"x": 0, "y": 772, "width": 196, "height": 80}),
                render_node("profile-tab", "tabs", "button", {"x": 196, "y": 772, "width": 197, "height": 80}),
            ]
            nodes[0]["attributes"] = {
                "data-ios-module": "main-shell",
                "data-ios-screen-title": "Home",
                "data-ios-system-chrome": "native",
                "data-ios-title-mode": "large",
            }
            nodes[1]["attributes"] = {
                "data-ios-component": "tab-bar",
                "data-ios-container": "tab",
                "data-ios-reselect": "pop-to-root",
            }
            nodes[2]["attributes"] = {
                "data-ios-tab-id": "home-tab",
                "data-ios-tab-title": "Home",
                "data-ios-icon": "house",
                "aria-selected": "true",
            }
            nodes[3]["attributes"] = {
                "data-ios-tab-id": "profile-tab",
                "data-ios-tab-title": "Profile",
                "data-ios-icon": "person",
            }
            data = {
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": nodes,
                "interactions": [
                    {"sourceRuntimeId": "home-tab", "sourceTag": "button", "trigger": "tap", "iosAction": "select-tab", "iosTarget": "home"},
                    {"sourceRuntimeId": "profile-tab", "sourceTag": "button", "trigger": "tap", "iosAction": "select-tab", "iosTarget": "profile"},
                ],
                "phoneCandidates": [],
            }
            source = root / "render-tree.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "app", "--screen-id", "home", "--screen-name", "Home",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            screen = generated["screens"][0]
            self.assertEqual(screen["moduleId"], "main-shell")
            self.assertEqual(screen["navigation"]["style"], "native")
            self.assertEqual(screen["navigation"]["titleMode"], "large")
            self.assertEqual(screen["regions"]["bottomBar"]["kind"], "tab-bar")
            self.assertEqual(screen["tabContainer"]["initialTabId"], "home-tab")
            self.assertEqual([item["targetScreenId"] for item in screen["tabContainer"]["items"]], ["home", "profile"])

    def test_explicit_semantic_component_maps_to_native_control_and_preserves_hints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
                render_node("toggle", "app", "div", {"x": 20, "y": 40, "width": 51, "height": 31}),
            ]
            nodes[1]["attributes"] = {
                "data-ios-component": "switch",
                "data-ios-node-id": "home.notifications",
                "data-ios-state": "notifications-enabled",
            }
            data = {
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": nodes,
                "interactions": [],
                "phoneCandidates": [],
            }
            source = root / "render-tree.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "app", "--screen-id", "home",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            toggle = next(node for node in generated["screens"][0]["nodes"] if node["source"]["runtimeId"] == "toggle")
            self.assertEqual(toggle["semanticType"], "switch")
            self.assertEqual(toggle["nativeMapping"]["swiftUI"], "Toggle")
            self.assertEqual(toggle["nativeMapping"]["uiKit"], "UISwitch")
            self.assertEqual(toggle["iosHints"]["state"], "notifications-enabled")

    def test_geometry_and_interactions_infer_unnamed_top_and_bottom_bars(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
                render_node("top", "app", "header", {"x": 0, "y": 0, "width": 393, "height": 56}, direction="row"),
                render_node("back", "top", "button", {"x": 12, "y": 8, "width": 40, "height": 40}),
                render_node("content", "app", "section", {"x": 0, "y": 56, "width": 393, "height": 716}),
                render_node("bottom", "app", "div", {"x": 0, "y": 772, "width": 393, "height": 80}, position="absolute", direction="row"),
                render_node("primary", "bottom", "button", {"x": 20, "y": 786, "width": 168, "height": 48}),
                render_node("secondary", "bottom", "button", {"x": 205, "y": 786, "width": 168, "height": 48}),
                render_node("hidden-overlay", "app", "div", {"x": 0, "y": 0, "width": 393, "height": 0}),
                render_node("hidden-actions", "hidden-overlay", "div", {"x": 0, "y": 752, "width": 393, "height": 100}, position="fixed", direction="row"),
                render_node("hidden-edit", "hidden-actions", "button", {"x": 20, "y": 770, "width": 168, "height": 48}),
                render_node("hidden-done", "hidden-actions", "button", {"x": 205, "y": 770, "width": 168, "height": 48}),
            ]
            nodes[7]["visible"] = False
            nodes[7]["style"]["opacity"] = "0"
            nodes[7]["style"]["pointerEvents"] = "none"
            data = {
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": nodes,
                "interactions": [
                    {"sourceRuntimeId": "back", "sourceTag": "button", "trigger": "tap"},
                    {"sourceRuntimeId": "primary", "sourceTag": "button", "trigger": "tap"},
                    {"sourceRuntimeId": "secondary", "sourceTag": "button", "trigger": "tap"},
                    {"sourceRuntimeId": "hidden-edit", "sourceTag": "button", "trigger": "tap"},
                    {"sourceRuntimeId": "hidden-done", "sourceTag": "button", "trigger": "tap"},
                ],
                "phoneCandidates": [],
            }
            source = root / "render-tree.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "app", "--screen-id", "home",
                "--target-width", "393", "--target-height", "852",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            screen = generated["screens"][0]
            self.assertEqual(screen["regions"]["topBar"]["nodeId"], "home.top")
            self.assertEqual(screen["regions"]["bottomBar"]["nodeId"], "home.bottom")
            self.assertEqual(screen["regions"]["bottomBar"]["kind"], "bottom-action-bar")
            self.assertEqual(screen["systemChrome"]["navigationBar"], "custom")


if __name__ == "__main__":
    unittest.main()
