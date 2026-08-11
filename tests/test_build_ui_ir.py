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
    def test_semantic_root_selector_resolves_extracted_context_runtime_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852})
            app["selector"] = "body > main.phone"
            app["attributes"].update({"data-ios-app-root": "true", "data-ios-screen": "dashboard"})
            source = root / "render-tree.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps({
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "screenContext": {
                    "visualRootSelector": 'main[data-ios-screen="dashboard"]',
                    "contentRootSelector": 'main[data-ios-screen="dashboard"]',
                    "visualRootRuntimeId": "app",
                    "contentRootRuntimeId": "app",
                    "visualRootRect": app["rect"],
                },
                "nodes": [app],
                "interactions": [],
                "phoneCandidates": [],
            }), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-selector", 'main[data-ios-screen="dashboard"]', "--screen-id", "dashboard",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(generated["screens"][0]["nodes"][0]["source"]["runtimeId"], "app")

    def test_content_root_inherits_visual_envelope_and_shared_bottom_region(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visual = render_node("visual", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}, dom_id="phone")
            visual["style"]["backgroundColor"] = "rgb(13, 15, 28)"
            content = render_node("content", "visual", "section", {"x": 0, "y": 44, "width": 393, "height": 1000}, dom_id="page")
            positioned = render_node("floating", "content", "div", {"x": 20, "y": 120, "width": 80, "height": 40}, position="absolute")
            positioned["positioning"] = {
                "offsetParentRuntimeId": "content",
                "offsetParentSelector": "#page",
                "offsetParentRect": {"x": 0, "y": 44, "width": 393, "height": 1000},
            }
            bottom = render_node("bottom", "visual", "footer", {"x": 0, "y": 778, "width": 393, "height": 74}, position="fixed", dom_id="bottom")
            bottom["attributes"]["data-ios-component"] = "bottom-action-bar"
            source = root / "render-tree.json"
            output = root / "ui-ir.json"
            layout_report = root / "source-layout.json"
            layout_report.write_text(json.dumps({
                "schemaVersion": "responsive-layout-analysis-1.0",
                "sourceClassification": {
                    "kind": "fixed-mobile-artboard",
                    "conversionStatus": "automatic",
                    "reasons": ["fixed fixture"],
                },
            }), encoding="utf-8")
            source.write_text(json.dumps({
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "screenContext": {
                    "visualRootSelector": "#phone",
                    "contentRootSelector": "#page",
                    "visualRootRuntimeId": "visual",
                    "contentRootRuntimeId": "content",
                    "visualRootRect": visual["rect"],
                    "viewportBackground": {"backgroundColor": "rgb(13, 15, 28)", "backgroundImage": "none"},
                    "sharedRegions": [{"runtimeId": "bottom", "selector": "#bottom", "edge": "bottom"}],
                    "ancestorChain": [{
                        "runtimeId": "scroll-owner",
                        "selector": ".content-scroll",
                        "style": {"overflowX": "hidden", "overflowY": "auto"},
                    }],
                },
                "nodes": [visual, content, positioned, bottom],
                "interactions": [],
                "phoneCandidates": [],
            }), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "content", "--screen-id", "home",
                "--source-layout-report", str(layout_report),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            screen = generated["screens"][0]
            self.assertEqual(generated["source"]["layoutClassification"]["kind"], "fixed-mobile-artboard")
            by_runtime = {item["source"]["runtimeId"]: item for item in screen["nodes"]}
            self.assertEqual(by_runtime["content"]["style"]["backgroundColor"], "rgb(13, 15, 28)")
            self.assertEqual(by_runtime["content"]["layout"]["scrollAxis"], "vertical")
            self.assertEqual(
                by_runtime["content"]["layout"]["scrollOwnershipSource"]["runtimeId"],
                "scroll-owner",
            )
            self.assertEqual(by_runtime["bottom"]["parentId"], screen["rootNodeId"])
            self.assertEqual(screen["regions"]["bottomBar"]["nodeId"], by_runtime["bottom"]["id"])
            self.assertEqual(by_runtime["floating"]["source"]["positioning"]["offsetParentNodeId"], by_runtime["content"]["id"])

    def test_explicit_apple_system_controls_map_to_native_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = {
                "button": ("button", "UIButton"),
                "color-picker": ("color-picker", "UIColorWell"),
                "date-picker": ("date-input", "UIDatePicker"),
                "page-control": ("page-control", "UIPageControl"),
                "paste-control": ("paste-control", "UIPasteControl"),
                "refresh-control": ("refresh-control", "UIRefreshControl"),
                "segmented-control": ("segmented-control", "UISegmentedControl"),
                "slider": ("slider", "UISlider"),
                "stepper": ("stepper", "UIStepper"),
                "switch": ("switch", "UISwitch"),
                "text-field": ("text-input", "UITextField"),
                "search-field": ("search-input", "UISearchTextField"),
                "search-bar": ("search-bar", "UISearchBar"),
                "activity-indicator": ("activity-indicator", "UIActivityIndicatorView"),
                "progress": ("progress", "UIProgressView"),
                "wheel-picker": ("wheel-picker", "UIPickerView"),
                "calendar-view": ("calendar-view", "UICalendarView"),
            }
            nodes = [render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852})]
            for index, component in enumerate(expected):
                item = render_node(component, "app", "div", {"x": 20, "y": 20 + index * 32, "width": 200, "height": 28})
                item["attributes"]["data-ios-component"] = component
                nodes.append(item)
            source = root / "render-tree.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps({
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": nodes,
                "interactions": [],
                "phoneCandidates": [],
            }), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "app", "--screen-id", "home",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            by_runtime_id = {
                item["source"]["runtimeId"]: item
                for item in generated["screens"][0]["nodes"]
            }
            for component, (semantic, uikit) in expected.items():
                self.assertEqual(by_runtime_id[component]["semanticType"], semantic)
                self.assertIn(uikit, by_runtime_id[component]["nativeMapping"]["uiKit"])
                self.assertTrue(by_runtime_id[component]["nativeMapping"]["nativeControlDecision"]["systemCandidate"])

    def test_visual_artboard_state_contract_survives_into_ui_ir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
                render_node("open", "app", "button", {"x": 320, "y": 20, "width": 44, "height": 44}, dom_id="open"),
            ]
            nodes[1]["attributes"]["data-ios-target"] = "home-menu"
            render_tree = {
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": nodes,
                "interactions": [],
                "phoneCandidates": [],
            }
            interaction_graph = {
                "schemaVersion": "interaction-state-graph-1.0",
                "source": {"fingerprint": "fixture"},
                "screens": [{"id": "home"}],
                "states": [{
                    "id": "home.menu.1",
                    "ownerScreenId": "home",
                    "kind": "overlay",
                    "targetSelector": "#home-menu",
                    "classes": [],
                    "confidence": 0.91,
                    "visualRepresentation": {
                        "screenId": "home-menu",
                        "sourceSelector": "#home-menu",
                        "presentationStyle": "menu",
                    },
                }],
                "interactions": [{
                    "id": "interaction-1",
                    "sourceSelector": 'button[data-ios-target="home-menu"]',
                    "sourceScreenId": "home",
                    "trigger": "tap",
                    "confidence": 0.91,
                }],
                "transitions": [{
                    "id": "transition-1",
                    "interactionId": "interaction-1",
                    "sourceScreenId": "home",
                    "targetStateId": "home.menu.1",
                    "trigger": "tap",
                    "kind": "presentation",
                    "recommendedNativeAction": "present-overlay",
                    "confidence": 0.91,
                    "requiresOverride": False,
                }],
                "unresolved": [],
                "warnings": [],
                "summary": {
                    "screens": 1, "states": 1, "interactions": 1, "transitions": 1,
                    "automaticTransitions": 0, "runtimeVerified": 0, "unresolved": 0,
                },
            }
            source = root / "render-tree.json"
            graph = root / "interaction-state-graph.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps(render_tree), encoding="utf-8")
            graph.write_text(json.dumps(interaction_graph), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "app", "--screen-id", "home",
                "--interaction-graph", str(graph),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(generated["states"][0]["id"], "home.menu.1")
            self.assertEqual(
                generated["states"][0]["visualRepresentation"]["screenId"],
                "home-menu",
            )
            self.assertEqual(generated["interactions"][0]["action"], "overlay")
            self.assertEqual(
                generated["interactions"][0]["payload"]["transitions"][0]["targetStateId"],
                "home.menu.1",
            )

    def test_runtime_state_geometry_becomes_layout_only_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852})
            toggle = render_node("toggle", "app", "button", {"x": 20, "y": 20, "width": 120, "height": 44})
            panel = render_node("panel", "app", "section", {"x": 20, "y": 72, "width": 353, "height": 54})
            render_tree = {
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": [app, toggle, panel],
                "interactions": [],
                "phoneCandidates": [],
            }
            interaction_graph = {
                "schemaVersion": "interaction-state-graph-1.0",
                "source": {"fingerprint": "fixture"},
                "screens": [{"id": "home"}],
                "states": [{
                    "id": "home.panel.expanded",
                    "ownerScreenId": "home",
                    "kind": "expansion",
                    "targetSelector": "#panel",
                    "confidence": 0.94,
                }],
                "interactions": [{
                    "id": "interaction-1",
                    "sourceSelector": "#toggle",
                    "sourceScreenId": "home",
                    "trigger": "tap",
                    "confidence": 0.94,
                    "runtimeEvidence": {
                        "sourceIndex": 0,
                        "before": {"targets": {"#panel": {"rect": {"x": 20, "y": 72, "width": 353, "height": 54}}}},
                        "after": {"targets": {"#panel": {"rect": {"x": 20, "y": 72, "width": 353, "height": 307}}}},
                    },
                }],
                "transitions": [{
                    "id": "transition-1",
                    "interactionId": "interaction-1",
                    "sourceScreenId": "home",
                    "targetStateId": "home.panel.expanded",
                    "trigger": "tap",
                    "kind": "local-state",
                    "recommendedNativeAction": "toggle-expanded",
                    "confidence": 0.94,
                    "requiresOverride": False,
                }],
                "unresolved": [],
                "warnings": [],
            }
            source = root / "render-tree.json"
            graph = root / "interaction-state-graph.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps(render_tree), encoding="utf-8")
            graph.write_text(json.dumps(interaction_graph), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "app", "--screen-id", "home",
                "--interaction-graph", str(graph),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            variant = generated["interactions"][0]["payload"]["contentVariants"][0]
            self.assertEqual(variant["mode"], "layout-only")
            self.assertEqual(variant["targetNodeId"], "home.panel")
            self.assertEqual(variant["items"], [])
            self.assertEqual(variant["targetRectAfterCssPx"]["height"], 307)

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
                        "lineTexts": ["Single line"],
                        "firstBaselineY": 113,
                        "lastBaselineY": 113,
                        "fontMetrics": {"ascent": 15, "descent": 5},
                        "fontResolution": {
                            "requestedFamilies": ["missing web font", "monospace"],
                            "resolvedFamily": "monospace",
                            "status": "generic-fallback",
                            "failedFamilies": ["missing web font"],
                            "confidence": 0.9,
                        },
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
            nodes[3]["contentRuns"] = [{
                "kind": "text",
                "text": "Single line",
                "domIndex": 0,
                "rect": {"x": 36, "y": 98, "width": 64, "height": 20},
            }]
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
            self.assertEqual(by_runtime_id["label"]["content"]["lineTexts"], ["Single line"])
            self.assertEqual(by_runtime_id["label"]["content"]["firstBaselineY"], 113)
            self.assertEqual(by_runtime_id["label"]["content"]["lastBaselineY"], 113)
            self.assertEqual(by_runtime_id["label"]["content"]["fontMetrics"], {"ascent": 15, "descent": 5})
            self.assertEqual(by_runtime_id["label"]["content"]["fontResolution"]["resolvedFamily"], "monospace")
            self.assertEqual(by_runtime_id["label"]["content"]["fontResolution"]["status"], "generic-fallback")
            self.assertEqual(by_runtime_id["label"]["content"]["runs"][0]["domIndex"], 0)
            self.assertEqual(
                by_runtime_id["label"]["content"]["runs"][0]["sourceRectCssPx"],
                {"x": 36, "y": 98, "width": 64, "height": 20},
            )

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
            self.assertEqual(screen["tabContainer"]["rendering"], "system")
            self.assertIn("appearance", screen["tabContainer"])

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
            decision = toggle["nativeMapping"]["nativeControlDecision"]
            self.assertEqual(decision["policy"], "system-first-visual-fit-gated")
            self.assertEqual(decision["decision"], "system-control")
            self.assertTrue(decision["systemCandidate"])
            self.assertFalse(decision["requiresCustomControl"])
            self.assertEqual(toggle["iosHints"]["state"], "notifications-enabled")

    def test_complex_css_keeps_system_control_and_adds_native_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
                render_node("action", "app", "button", {"x": 20, "y": 40, "width": 160, "height": 48}),
            ]
            nodes[1]["style"].update({
                "clipPath": "polygon(0 0, 100% 0, 90% 100%, 0 100%)",
                "boxShadow": "0px 8px 20px rgba(0, 0, 0, 0.2)",
                "borderWidths": ["1px", "2px", "1px", "2px"],
                "borderColors": ["red", "red", "red", "red"],
                "borderStyles": ["solid"] * 4,
            })
            data = {
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": nodes,
                "interactions": [],
                "phoneCandidates": [],
            }
            source, output = root / "render-tree.json", root / "ui-ir.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "app", "--screen-id", "home",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            action = next(node for node in generated["screens"][0]["nodes"] if node["source"]["runtimeId"] == "action")
            decision = action["nativeMapping"]["nativeControlDecision"]
            self.assertEqual(decision["decision"], "system-control-with-native-wrapper")
            self.assertIn("non-rectangular-clip-path", decision["blockers"])
            self.assertIn("asymmetric-border-widths", decision["customization"])
            self.assertTrue(decision["preserveSystemSemantics"])

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
            self.assertEqual(screen["regions"]["bottomBar"]["placement"], "viewport-overlay")
            self.assertEqual(screen["systemChrome"]["navigationBar"], "native")
            self.assertEqual(
                screen["navigation"]["renderingDecision"]["policy"],
                "system-first-visual-fit-gated",
            )
            self.assertTrue(screen["navigation"]["renderingDecision"]["compatible"])
            self.assertEqual(screen["navigation"]["toolbarItems"][0]["id"], "home.back")
            self.assertTrue(screen["navigation"]["toolbarItems"][0]["hasAction"])
            self.assertTrue(screen["navigation"]["renderingDecision"]["hasTopActions"])
            self.assertEqual(screen["navigation"]["renderingDecision"]["topActionCount"], 1)

    def test_navigation_title_mode_uses_leading_large_and_centered_compact_geometry(self) -> None:
        def convert(nodes: list[dict], interactions: list[dict]) -> dict:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "render-tree.json"
                output = root / "ui-ir.json"
                source.write_text(json.dumps({
                    "schemaVersion": "render-tree-1.2",
                    "source": {"kind": "html-file", "entry": "/tmp/example.html"},
                    "document": {"viewport": {"width": 393, "height": 852}},
                    "nodes": nodes,
                    "interactions": interactions,
                    "phoneCandidates": [],
                }), encoding="utf-8")
                result = subprocess.run([
                    "python3", str(SCRIPT), str(source), "--out", str(output),
                    "--root-runtime-id", "app", "--screen-id", "home",
                ], text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                return json.loads(output.read_text(encoding="utf-8"))["screens"][0]

        large_nodes = [
            render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
            render_node("header", "app", "header", {"x": 0, "y": 47, "width": 393, "height": 58}, direction="row"),
            render_node("title", "header", "h1", {"x": 20, "y": 58, "width": 92, "height": 36}),
            render_node("action", "header", "button", {"x": 337, "y": 56, "width": 36, "height": 36}),
            render_node("content", "app", "section", {"x": 0, "y": 105, "width": 393, "height": 747}),
        ]
        large_nodes[2]["text"] = "概览"
        large_nodes[2]["style"].update({"fontSize": "28px", "textAlign": "start"})
        large = convert(large_nodes, [{"sourceRuntimeId": "action", "sourceTag": "button", "trigger": "tap"}])
        self.assertEqual(large["navigation"]["titleMode"], "large")
        self.assertIn("leading-title-geometry", large["navigation"]["renderingDecision"]["titleModeEvidence"])

        inline_nodes = [
            render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
            render_node("header", "app", "header", {"x": 0, "y": 47, "width": 393, "height": 52}, direction="row"),
            render_node("back", "header", "button", {"x": 14, "y": 54, "width": 38, "height": 38}),
            render_node("title", "header", "h1", {"x": 154, "y": 63, "width": 85, "height": 22}),
            render_node("content", "app", "section", {"x": 0, "y": 99, "width": 393, "height": 753}),
        ]
        inline_nodes[3]["text"] = "设计方法"
        inline_nodes[3]["style"].update({"fontSize": "17px", "textAlign": "center"})
        inline = convert(inline_nodes, [{"sourceRuntimeId": "back", "sourceTag": "button", "trigger": "tap"}])
        self.assertEqual(inline["navigation"]["titleMode"], "inline")
        self.assertIn("centered-title", inline["navigation"]["renderingDecision"]["titleModeEvidence"])

    def test_complex_search_header_remains_custom_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
                render_node("header", "app", "header", {"x": 0, "y": 0, "width": 393, "height": 112}, direction="row"),
                render_node("search", "header", "input", {"x": 20, "y": 58, "width": 353, "height": 42}),
            ]
            nodes[1]["attributes"]["data-ios-component"] = "navigation-bar"
            nodes[2]["attributes"].update({"type": "search", "placeholder": "Search"})
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
                "--root-runtime-id", "app", "--screen-id", "search",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            screen = json.loads(output.read_text(encoding="utf-8"))["screens"][0]
            self.assertEqual(screen["navigation"]["style"], "custom")
            self.assertIn(
                "embedded-complex-content:search-input",
                screen["navigation"]["renderingDecision"]["divergences"],
            )

    def test_text_controls_preserve_single_line_multiline_and_readonly_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
                render_node("name", "app", "input", {"x": 20, "y": 40, "width": 353, "height": 44}),
                render_node("notes", "app", "textarea", {"x": 20, "y": 100, "width": 353, "height": 120}),
                render_node("article", "app", "p", {"x": 20, "y": 240, "width": 353, "height": 96}),
            ]
            nodes[1]["attributes"] = {
                "type": "text", "readonly": "", "value": "Sky", "name": "display-name",
                "autofocus": "", "enterkeyhint": "next", "autocapitalize": "words",
                "spellcheck": "false", "data-ios-validation": "required",
            }
            nodes[1]["properties"] = {"value": "Sky", "readOnly": True, "disabled": False}
            nodes[1]["placeholderStyle"] = {
                "fontSize": "14px", "fontWeight": "500", "color": "rgba(80, 90, 110, 0.65)",
                "lineHeight": "20px", "letterSpacing": "0.2px", "opacity": "0.8",
            }
            nodes[1]["controlStateStyles"] = {
                "focused": {
                    "color": "rgb(20, 30, 40)", "backgroundColor": "rgb(255, 255, 255)",
                    "borderTopColor": "rgb(80, 90, 240)", "borderRightColor": "rgb(80, 90, 240)",
                    "borderBottomColor": "rgb(80, 90, 240)", "borderLeftColor": "rgb(80, 90, 240)",
                    "borderTopWidth": "2px", "borderRightWidth": "2px",
                    "borderBottomWidth": "2px", "borderLeftWidth": "2px",
                    "borderRadius": "10px", "boxShadow": "none", "opacity": "1", "transform": "none",
                    "backgroundImage": "none",
                },
            }
            nodes[2]["attributes"] = {
                "maxlength": "200", "placeholder": "Notes",
                "data-ios-data-source": "profile-notes", "data-ios-item-id": "id",
                "data-ios-state-role": "content", "data-ios-pagination": "cursor",
            }
            nodes[2]["properties"] = {"value": "Line one\nLine two", "readOnly": False, "disabled": False}
            nodes[2]["style"].update({"overflowY": "auto", "overflowX": "hidden"})
            nodes[2]["scroll"] = {"scrollWidth": 353, "scrollHeight": 180, "clientWidth": 353, "clientHeight": 120}
            nodes[3]["text"] = "Selectable result"
            nodes[3]["attributes"] = {"data-ios-text-control": "text-view", "data-ios-selectable": "true"}
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
            by_runtime_id = {item["source"]["runtimeId"]: item for item in generated["screens"][0]["nodes"]}
            self.assertEqual(by_runtime_id["name"]["semanticType"], "text-input")
            self.assertEqual(by_runtime_id["name"]["textBehavior"]["nativeControl"], "text-field")
            self.assertFalse(by_runtime_id["name"]["textBehavior"]["editable"])
            self.assertEqual(by_runtime_id["name"]["textBehavior"]["fieldID"], "display-name")
            self.assertTrue(by_runtime_id["name"]["textBehavior"]["autofocus"])
            self.assertEqual(by_runtime_id["name"]["textBehavior"]["returnKey"], "next")
            self.assertEqual(by_runtime_id["name"]["textBehavior"]["autocapitalization"], "words")
            self.assertFalse(by_runtime_id["name"]["textBehavior"]["autocorrection"])
            self.assertEqual(by_runtime_id["name"]["textBehavior"]["validation"], "required")
            self.assertEqual(by_runtime_id["name"]["textBehavior"]["placeholderStyle"]["fontSize"], "14px")
            self.assertEqual(
                by_runtime_id["name"]["textBehavior"]["placeholderStyle"]["foreground"],
                "rgba(80, 90, 110, 0.65)",
            )
            self.assertEqual(
                by_runtime_id["name"]["controlVisualStates"]["focused"]["borderTopWidth"],
                "2px",
            )
            self.assertEqual(by_runtime_id["notes"]["semanticType"], "text-area")
            self.assertTrue(by_runtime_id["notes"]["textBehavior"]["multiline"])
            self.assertTrue(by_runtime_id["notes"]["textBehavior"]["scrollable"])
            self.assertEqual(by_runtime_id["notes"]["dataBinding"]["sourceID"], "profile-notes")
            self.assertEqual(by_runtime_id["notes"]["dataBinding"]["stateRole"], "content")
            self.assertEqual(by_runtime_id["notes"]["dataBinding"]["pagination"], "cursor")
            self.assertTrue(by_runtime_id["notes"]["dataBinding"]["requiresViewModel"])
            self.assertTrue(by_runtime_id["notes"]["dataBinding"]["snapshotIsSampleData"])
            self.assertEqual(by_runtime_id["article"]["textBehavior"]["nativeControl"], "text-view")
            self.assertFalse(by_runtime_id["article"]["textBehavior"]["editable"])
            self.assertTrue(by_runtime_id["article"]["textBehavior"]["selectable"])

    def test_form_semantics_override_input_like_visual_appearance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                render_node("app", None, "main", {"x": 0, "y": 0, "width": 393, "height": 852}),
                render_node("editable", "app", "input", {"x": 20, "y": 40, "width": 353, "height": 44}),
                render_node("bordered-label", "app", "div", {"x": 20, "y": 100, "width": 353, "height": 44}),
                render_node("rich-input", "app", "div", {"x": 20, "y": 160, "width": 353, "height": 90}),
                render_node("readonly-widget", "app", "div", {"x": 20, "y": 270, "width": 353, "height": 44}),
                render_node("checkbox", "app", "input", {"x": 20, "y": 330, "width": 22, "height": 22}),
            ]
            nodes[1]["attributes"] = {"type": "text", "placeholder": "Type here"}
            nodes[1]["properties"] = {"value": "", "readOnly": False, "disabled": False}
            nodes[2]["text"] = "Display only"
            nodes[2]["style"].update({"borderTopWidth": "1px", "borderRadius": "8px"})
            nodes[3]["attributes"] = {"contenteditable": ""}
            nodes[3]["properties"] = {"isContentEditable": True, "disabled": False}
            nodes[3]["text"] = "Editable content"
            nodes[4]["attributes"] = {"role": "textbox", "aria-readonly": "true"}
            nodes[4]["text"] = "Read only widget"
            nodes[5]["attributes"] = {"type": "checkbox"}
            nodes[5]["properties"] = {"checked": True, "disabled": False}
            source = root / "render-tree.json"
            output = root / "ui-ir.json"
            source.write_text(json.dumps({
                "schemaVersion": "render-tree-1.2",
                "source": {"kind": "html-file", "entry": "/tmp/form.html"},
                "document": {"viewport": {"width": 393, "height": 852}},
                "nodes": nodes,
                "interactions": [],
                "phoneCandidates": [],
            }), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), str(source), "--out", str(output),
                "--root-runtime-id", "app", "--screen-id", "form",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = json.loads(output.read_text(encoding="utf-8"))
            by_id = {item["source"]["runtimeId"]: item for item in generated["screens"][0]["nodes"]}
            self.assertTrue(by_id["editable"]["textBehavior"]["editable"])
            self.assertEqual(by_id["editable"]["textBehavior"]["sourceKind"], "html-control")
            self.assertEqual(by_id["bordered-label"]["semanticType"], "text")
            self.assertEqual(by_id["bordered-label"]["textBehavior"]["nativeControl"], "label")
            self.assertFalse(by_id["bordered-label"]["textBehavior"]["editable"])
            self.assertEqual(by_id["rich-input"]["semanticType"], "text-area")
            self.assertTrue(by_id["rich-input"]["textBehavior"]["editable"])
            self.assertEqual(by_id["rich-input"]["textBehavior"]["sourceKind"], "contenteditable")
            self.assertFalse(by_id["readonly-widget"]["textBehavior"]["editable"])
            self.assertTrue(by_id["readonly-widget"]["textBehavior"]["readOnly"])
            self.assertEqual(by_id["checkbox"]["semanticType"], "checkbox")
            self.assertIsNone(by_id["checkbox"]["textBehavior"])


if __name__ == "__main__":
    unittest.main()
