#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCRIPT = ROOT / "scripts" / "build_visual_state_manifest.py"
DIFF_SCRIPT = ROOT / "scripts" / "visual_diff.py"
BUNDLE_SCRIPT = ROOT / "scripts" / "build_visual_review_bundle.py"
PREPARE_IOS_TESTS_SCRIPT = ROOT / "scripts" / "prepare_visual_ui_tests.rb"
GEOMETRY_SCRIPT = ROOT / "scripts" / "compare_node_geometry.py"


def node(node_id: str, parent_id: str | None, semantic: str, rect: list[int], text: str = "") -> dict:
    return {
        "id": node_id,
        "parentId": parent_id,
        "semanticType": semantic,
        "layout": {"rect": {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]}},
        "content": {"text": text or None, "placeholder": None},
        "state": {"initiallyVisible": True},
        "assetRef": "asset.logo" if semantic == "image" else None,
        "source": {"selector": f"#{node_id}"},
    }


class VisualValidationTests(unittest.TestCase):
    def test_advisory_motion_checkpoints_are_not_scheduled_for_ui_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "motion", "rootNodeId": "motion.root", "sourceSelector": "#motion",
                    "systemChrome": {}, "regions": {},
                    "nodes": [node("motion.root", None, "container", [0, 0, 393, 852])],
                }],
                "interactions": [],
                "visualStates": [
                    {"id": "initial", "required": True},
                    {
                        "id": "motion-50", "required": False, "animationProgress": 0.5,
                        "captureSupport": "advisory-until-native-motion-hook-exists",
                    },
                ],
            }
            source, output = root / "ui-ir.json", root / "manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                check=True, text=True, capture_output=True,
            )
            self.assertEqual(
                [item["id"] for item in json.loads(output.read_text(encoding="utf-8"))["states"]],
                ["initial"],
            )

    def test_ios_visual_taps_prefer_hittable_matches_after_state_changes(self) -> None:
        source = PREPARE_IOS_TESTS_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("requireHittable: Bool = false", source)
        self.assertIn("matches.allElementsBoundByIndex.first(where: { $0.exists && $0.isHittable })", source)
        self.assertIn("requireHittable: true", source)
        self.assertIn("app.swipeUp(velocity: .slow)", source)
        self.assertIn("app.swipeDown(velocity: .slow)", source)
        self.assertIn("Missing accessibility identifier after native scroll reveal", source)
        self.assertIn('domain: "HTMLToIOSVisualValidation"', source)
        self.assertIn("captureGeometry(name:", source)
        self.assertIn("candidate.elementType == .secureTextField", source)
        self.assertIn('label == %@ OR identifier == %@', source)
        self.assertIn('actual=\\\\(renderedValue)', source)
        self.assertIn('.allElementsBoundByIndex', source)
        self.assertIn('.filter(\\\\.exists)', source)
        self.assertIn('beforeSelectionImage', source)
        self.assertIn('screenshot().pngRepresentation != beforeSelectionImage', source)
        self.assertIn('"-HTMLToIOSGeometryCapture", "1"', source)
        self.assertIn('manifest["geometryNodes"]', source)
        self.assertIn('uniformTypeIdentifier: "public.json"', source)
        self.assertNotIn('XCTFail("Element exists but is not hittable', source)

    def test_form_checks_cover_editable_readonly_disabled_and_selection_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            editable = node("form.name", "form.root", "text-input", [20, 80, 353, 44])
            editable["textBehavior"] = {"role": "input", "editable": True}
            readonly = node("form.code", "form.root", "text-input", [20, 140, 353, 44])
            readonly["textBehavior"] = {"role": "input", "editable": False, "readOnly": True}
            disabled = node("form.locked", "form.root", "text-input", [20, 200, 353, 44])
            disabled["textBehavior"] = {"role": "input", "editable": False}
            disabled["state"]["enabled"] = False
            select = node("form.language", "form.root", "select", [20, 260, 353, 44])
            first = node("form.language.en", "form.language", "option", [0, 0, 0, 0], "English")
            first["state"].update({"enabled": True, "selected": True, "value": "en"})
            first["content"]["value"] = "en"
            second = node("form.language.zh", "form.language", "option", [0, 0, 0, 0], "中文")
            second["state"].update({"enabled": True, "selected": False, "value": "zh"})
            second["content"]["value"] = "zh"
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "form", "rootNodeId": "form.root", "sourceSelector": "#form",
                    "systemChrome": {}, "regions": {},
                    "nodes": [node("form.root", None, "container", [0, 0, 393, 852]), editable, readonly, disabled, select, first, second],
                }],
                "interactions": [],
                "visualStates": [{"id": "initial", "required": True}],
            }
            source, output = root / "ui-ir.json", root / "manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                check=True, text=True, capture_output=True,
            )
            checks = json.loads(output.read_text(encoding="utf-8"))["formChecks"]
            self.assertEqual(
                [(item["type"], item["accessibilityIdentifier"]) for item in checks],
                [
                    ("input", "form.name"),
                    ("readonly", "form.code"),
                    ("disabled", "form.locked"),
                    ("select", "form.language"),
                ],
            )
            self.assertEqual(checks[-1]["value"], "中文")
            self.assertEqual(checks[-1]["expectedValue"], "zh")
            self.assertEqual(checks[-1]["resultAccessibilityIdentifier"], "form.language")

    def test_multi_select_does_not_create_unstable_xcui_value_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            multi = node("form.teams", "form.root", "multi-select", [20, 80, 353, 80])
            option = node("form.teams.design", "form.teams", "option", [0, 0, 0, 0], "Design")
            option["state"].update({"enabled": True, "selected": False, "value": "design"})
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "form", "rootNodeId": "form.root", "sourceSelector": "#form",
                    "systemChrome": {}, "regions": {},
                    "nodes": [node("form.root", None, "container", [0, 0, 393, 852]), multi, option],
                }],
                "interactions": [],
                "visualStates": [{"id": "initial", "required": True}],
            }
            source, output = root / "ui-ir.json", root / "manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                check=True, text=True, capture_output=True,
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["formChecks"], [])
            prepare_source = PREPARE_IOS_TESTS_SCRIPT.read_text(encoding="utf-8")
            self.assertIn('when "select"', prepare_source)
            self.assertIn("assertReadOnly(identifier:", prepare_source)
            self.assertIn("assertDisabled(identifier:", prepare_source)
            self.assertIn("escaped = value.to_s", prepare_source)
            self.assertNotIn("value.to_s.dump", prepare_source)

    def test_fixed_artboard_validation_geometry_uses_cover_center_crop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {
                    "viewportPt": {"width": 393, "height": 852},
                    "appearance": "light",
                    "scale": 393 / 318,
                },
                "screens": [{
                    "id": "home",
                    "rootNodeId": "home.root",
                    "sourceSelector": "#home",
                    "systemChrome": {},
                    "regions": {},
                    "nodes": [
                        node("home.root", None, "container", [0, 0, 393, 862.6226]),
                        node("home.title", "home.root", "heading", [20, 52, 120, 30], "Title"),
                    ],
                }],
                "interactions": [],
                "visualStates": [{"id": "initial", "required": True}],
            }
            source, output = root / "ui-ir.json", root / "manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                check=True,
                text=True,
                capture_output=True,
            )
            manifest = json.loads(output.read_text(encoding="utf-8"))
            title = next(
                item for item in manifest["validationRegions"] if item["nodeId"] == "home.title"
            )
            self.assertEqual(title["geometryRect"], [20, 47, 120, 30])
            self.assertEqual(
                [item["nodeId"] for item in manifest["geometryNodes"]],
                ["home.root", "home.title"],
            )

    def test_fixed_artboard_regions_use_visual_root_browser_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            screen_root = node("home.root", None, "container", [0, 0, 393, 640])
            screen_root["layout"]["sourceRectCssPx"] = {
                "x": 40, "y": 242, "width": 318, "height": 520,
            }
            bottom = node("home.bottom", "home.root", "button", [0, 712, 393, 99], "Continue")
            bottom["layout"]["sourceRectCssPx"] = {
                "x": 40, "y": 818, "width": 318, "height": 80,
            }
            payload = {
                "schemaVersion": "1.2",
                "source": {
                    "entry": str(root / "prototype.html"),
                    "viewport": {"width": 393, "height": 852},
                    "screenContext": {
                        "visualRootRect": {"x": 40, "y": 200, "width": 318, "height": 698},
                        "contentRootRect": {"x": 40, "y": 242, "width": 318, "height": 520},
                    },
                },
                "target": {
                    "viewportPt": {"width": 393, "height": 852},
                    "scale": 393 / 318,
                },
                "screens": [{
                    "id": "home",
                    "rootNodeId": "home.root",
                    "sourceSelector": "#home",
                    "systemChrome": {},
                    "regions": {"bottomBar": {"nodeId": "home.bottom"}},
                    "nodes": [screen_root, bottom],
                }],
                "interactions": [],
                "visualStates": [{"id": "initial", "required": True}],
            }
            source, output = root / "ui-ir.json", root / "manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                check=True,
                text=True,
                capture_output=True,
            )
            manifest = json.loads(output.read_text(encoding="utf-8"))
            regions = {item["id"]: item for item in manifest["validationRegions"]}
            # The bottom bar is 618 CSS px below the Visual Root, not 576 px
            # below the Content Root. Cover normalization then crops 5 pt.
            self.assertEqual(regions["screen.bottom-bar"]["rect"], [0, 758, 393, 94])
            self.assertEqual(regions["node.home.bottom"]["geometryRect"], [0, 758, 393, 94])

    def test_node_geometry_report_exposes_vertical_accumulation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "geometryNodes": [
                    {"nodeId": "top", "hasChildren": False},
                    {"nodeId": "middle"},
                    {"nodeId": "bottom"},
                    {"nodeId": "missing-container"},
                ],
                "validationRegions": [
                    {"nodeId": "top", "category": "typography", "geometryRect": [10, 100, 80, 20]},
                    {"nodeId": "middle", "category": "control", "geometryRect": [10, 300, 100, 40]},
                    {"nodeId": "bottom", "category": "typography", "geometryRect": [10, 600, 120, 20]},
                    {"nodeId": "misidentified", "category": "typography", "geometryRect": [200, 700, 120, 20]},
                ]
            }
            actual = {
                "stateId": "initial",
                "nodes": [
                    {"nodeId": "top", "elementType": 1, "frame": {"x": 10, "y": 101, "width": 80, "height": 20}},
                    {"nodeId": "middle", "elementType": 9, "frame": {"x": 10, "y": 306, "width": 100, "height": 38}},
                    {"nodeId": "bottom", "elementType": 48, "frame": {"x": 10, "y": 615, "width": 120, "height": 20}},
                    {"nodeId": "misidentified", "elementType": 48, "frame": {"x": 5, "y": 200, "width": 40, "height": 20}},
                ],
            }
            manifest_path, actual_path, output = root / "manifest.json", root / "actual.json", root / "report.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            actual_path.write_text(json.dumps(actual), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(GEOMETRY_SCRIPT),
                    str(manifest_path),
                    str(actual_path),
                    "--out",
                    str(output),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["matchedNodeCount"], 4)
            self.assertEqual(report["summary"]["reliableMatchedNodeCount"], 3)
            self.assertEqual(report["summary"]["verticalDriftSpanPt"], 14)
            self.assertEqual(report["summary"]["bands"]["top"]["medianYDeltaPt"], 1)
            self.assertEqual(report["summary"]["bands"]["bottom"]["medianYDeltaPt"], 15)
            self.assertEqual(report["summary"]["geometryCaptureCoverage"]["requestedNodeCount"], 4)
            self.assertEqual(report["summary"]["geometryCaptureCoverage"]["capturedNodeCount"], 3)
            self.assertEqual(report["summary"]["geometryCaptureCoverage"]["captureRate"], 0.75)
            top = next(item for item in report["nodes"] if item["nodeId"] == "top")
            self.assertEqual(top["geometryConfidence"], "medium")
            self.assertTrue(top["verticalAggregationEligible"])
            misidentified = next(item for item in report["nodes"] if item["nodeId"] == "misidentified")
            self.assertFalse(misidentified["verticalAggregationEligible"])

    def test_manifest_asserts_local_state_ancestor_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "home", "rootNodeId": "home.root", "sourceSelector": "#home",
                    "systemChrome": {}, "regions": {},
                    "nodes": [
                        node("home.root", None, "container", [0, 0, 393, 852]),
                        node("home.card", "home.root", "container", [20, 100, 353, 200]),
                        node("home.accept", "home.card", "button", [200, 240, 140, 44], "Accept"),
                    ],
                }],
                "states": [{"id": "remove-card", "kind": "local-state", "targetNodeIds": ["home.card"]}],
                "interactions": [{
                    "id": "accept", "sourceNodeId": "home.accept", "target": "remove-card",
                }],
                "visualStates": [{"id": "after-accept", "required": True, "interactionSequence": ["accept"]}],
            }
            source, output = root / "ui-ir.json", root / "visual-manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            action = json.loads(output.read_text(encoding="utf-8"))["states"][0]["iosActions"][0]
            self.assertEqual(action["assertion"], {
                "type": "not-exists", "accessibilityIdentifier": "home.card",
            })

    def test_manifest_does_not_assert_removal_when_runtime_target_remains_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "home", "rootNodeId": "home.root", "sourceSelector": "#home",
                    "systemChrome": {}, "regions": {},
                    "nodes": [
                        node("home.root", None, "container", [0, 0, 393, 852]),
                        node("home.card", "home.root", "container", [20, 100, 353, 200]),
                        node("home.accept", "home.card", "button", [200, 240, 140, 44], "Accept"),
                    ],
                }],
                "states": [{
                    "id": "remove-card",
                    "kind": "local-state",
                    "targetSelector": ".card",
                    "targetNodeIds": ["home.card"],
                }],
                "interactions": [{
                    "id": "accept",
                    "sourceNodeId": "home.accept",
                    "target": "remove-card",
                    "evidence": {
                        "runtime": {
                            "after": {"targets": {".card": {"visible": True}}},
                        }
                    },
                }],
                "visualStates": [{"id": "after-accept", "required": True, "interactionSequence": ["accept"]}],
            }
            source, output = root / "ui-ir.json", root / "visual-manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            action = json.loads(output.read_text(encoding="utf-8"))["states"][0]["iosActions"][0]
            self.assertNotIn("assertion", action)

    def test_manifest_asserts_presentation_target_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "home", "rootNodeId": "home.root", "sourceSelector": "#home",
                    "systemChrome": {}, "regions": {},
                    "nodes": [
                        node("home.root", None, "container", [0, 0, 393, 852]),
                        node("home.open", "home.root", "button", [20, 100, 100, 44], "Open"),
                        node("home.sheet", "home.root", "container", [0, 400, 393, 452]),
                    ],
                }],
                "states": [{"id": "sheet-open", "kind": "sheet-overlay", "targetNodeIds": ["home.sheet"]}],
                "interactions": [{
                    "id": "open-sheet", "sourceNodeId": "home.open", "target": "sheet-open",
                }],
                "visualStates": [{"id": "after-sheet", "required": True, "interactionSequence": ["open-sheet"]}],
            }
            source, output = root / "ui-ir.json", root / "visual-manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                text=True, capture_output=True, check=True,
            )
            action = json.loads(output.read_text(encoding="utf-8"))["states"][0]["iosActions"][0]
            self.assertEqual(action["assertion"], {
                "type": "exists", "accessibilityIdentifier": "home.sheet",
            })

    def test_manifest_uses_swipe_for_contextual_state_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "home", "rootNodeId": "home.root", "sourceSelector": "#home",
                    "systemChrome": {}, "regions": {},
                    "nodes": [
                        node("home.root", None, "container", [0, 0, 393, 852]),
                        node("home.row", "home.root", "list-item", [20, 100, 353, 56], "Document"),
                        {
                            **node("state.home.actions.delete", "home.root", "button", [285, 100, 88, 56], "Delete"),
                            "state": {"initiallyVisible": False},
                            "iosHints": {"state-owner": "home.actions"},
                        },
                    ],
                }],
                "states": [{
                    "id": "home.actions",
                    "kind": "expansion",
                    "targetNodeIds": ["home.root"],
                    "visualRepresentation": {"sourceSelector": "[data-ios-screen='home-actions']"},
                    "stateDelta": {
                        "nativeStrategy": "contextual-item-actions",
                        "contextualTargetNodeId": "home.row",
                        "contextualActionRootNodeIds": ["state.home.actions.delete"],
                    },
                }],
                "interactions": [{
                    "id": "reveal-actions",
                    "sourceNodeId": "home.row",
                    "target": "home.actions",
                }],
                "visualStates": [{
                    "id": "after-swipe",
                    "required": True,
                    "interactionSequence": ["reveal-actions"],
                }],
            }
            source, output = root / "ui-ir.json", root / "visual-manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                text=True, capture_output=True, check=True,
            )
            action = json.loads(output.read_text(encoding="utf-8"))["states"][0]["iosActions"][0]
            self.assertEqual(action["type"], "swipe-left")
            self.assertEqual(action["accessibilityIdentifier"], "home.row")
            self.assertEqual(action["assertion"], {
                "type": "exists",
                "accessibilityIdentifier": "home.actions.contextual.1",
            })
            state_manifest = json.loads(output.read_text(encoding="utf-8"))["states"][0]
            self.assertEqual(state_manifest["htmlRootSelector"], "[data-ios-screen='home-actions']")
            self.assertFalse(any(item.get("interactionId") for item in state_manifest["htmlActions"]))
            self.assertIn(
                "state.home.actions.delete",
                {item["nodeId"] for item in state_manifest["validationRegions"] if item.get("nodeId")},
            )
            prepare_source = PREPARE_IOS_TESTS_SCRIPT.read_text(encoding="utf-8")
            self.assertIn('when "swipe-left", "swipe-right"', prepare_source)
            self.assertIn("candidate.swipeLeft(velocity: .slow)", prepare_source)

    def test_manifest_replays_render_tree_activation_settle_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {
                    "entry": str(root / "prototype.html"),
                    "viewport": {"width": 393, "height": 852},
                    "screenActivation": {
                        "type": "click",
                        "selectors": ["[data-page='results']"],
                        "settleDelayMs": 1600,
                    },
                },
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "results", "rootNodeId": "results.root", "sourceSelector": "#phone",
                    "systemChrome": {}, "regions": {},
                    "nodes": [node("results.root", None, "container", [0, 0, 393, 852])],
                }],
                "interactions": [],
                "visualStates": [{"id": "initial", "required": True}],
            }
            source, output = root / "ui-ir.json", root / "visual-manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            actions = json.loads(output.read_text(encoding="utf-8"))["states"][0]["htmlActions"]
            self.assertEqual(actions[:2], [
                {"type": "click", "selector": "[data-page='results']", "purpose": "activate-screen"},
                {"type": "wait", "ms": 1600, "purpose": "match-render-tree-capture-checkpoint"},
            ])

    def test_manifest_contains_node_aligned_validation_regions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}, "appearance": "light"},
                "screens": [{
                    "id": "home",
                    "rootNodeId": "home.root",
                    "sourceSelector": "#home",
                    "systemChrome": {"statusBar": "native", "navigationBar": "custom", "homeIndicator": "native"},
                    "regions": {
                        "topBar": {"nodeId": "home.top"},
                        "bottomBar": {"nodeId": "home.tabs"},
                    },
                    "nodes": [
                        node("home.root", None, "container", [0, 0, 393, 852]),
                        node("home.top", "home.root", "navigation-bar", [0, 0, 393, 88]),
                        node("home.title", "home.top", "heading", [20, 44, 120, 32], "Home"),
                        node("home.cta", "home.root", "button", [20, 680, 353, 48], "Continue"),
                        node("home.logo", "home.root", "image", [20, 120, 80, 80]),
                        node("home.tabs", "home.root", "tab-bar", [0, 768, 393, 84]),
                    ],
                }],
                "interactions": [],
                "visualStates": [{"id": "initial", "required": True, "scroll": "top"}],
            }
            source = root / "ui-ir.json"
            output = root / "visual-manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(manifest["screenId"], "home")
            self.assertEqual(manifest["states"][0]["iosActions"], [])
            regions = {item["id"]: item for item in manifest["validationRegions"]}
            self.assertEqual(regions["screen.navigation"]["criticality"], "critical")
            self.assertEqual(regions["node.home.title"]["toleranceProfile"], "text")
            self.assertEqual(regions["node.home.cta"]["category"], "control")
            self.assertEqual(regions["node.home.logo"]["category"], "asset")
            self.assertEqual(
                {item["reason"] for item in manifest["comparisonMasks"]},
                {
                    "native-status-bar-is-system-owned-and-time-dependent",
                    "native-home-indicator-is-system-owned",
                },
            )

    def test_manifest_uses_visual_root_for_capture_and_scroll_owner_for_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "source": {
                    "entry": str(root / "prototype.html"),
                    "viewport": {"width": 393, "height": 852},
                    "screenContext": {
                        "visualRootSelector": "#phone-screen",
                        "contentRootSelector": "#page-results",
                        "ancestorChain": [
                            {
                                "selector": "#phone-screen",
                                "style": {"overflowY": "hidden"},
                            },
                            {
                                "selector": "#content-scroll",
                                "style": {"overflowY": "auto"},
                            },
                        ],
                    },
                },
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "results",
                    "rootNodeId": "results.root",
                    "sourceSelector": "#page-results",
                    "systemChrome": {},
                    "regions": {},
                    "nodes": [node("results.root", None, "container", [0, 0, 393, 852])],
                }],
                "interactions": [],
                "visualStates": [{"id": "initial", "required": True, "scroll": "top"}],
            }
            source, output = root / "ui-ir.json", root / "visual-manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                text=True, capture_output=True, check=True,
            )
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(manifest["rootSelector"], "#phone-screen")
            self.assertEqual(manifest["states"][0]["htmlActions"][0], {
                "type": "scroll",
                "selector": "#content-scroll",
                "position": "top",
            })

    def test_manifest_excludes_descendants_of_initially_hidden_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hidden_parent = node("home.panel", "home.root", "container", [0, 100, 393, 200])
            hidden_parent["state"]["initiallyVisible"] = False
            payload = {
                "schemaVersion": "1.2",
                "source": {"entry": str(root / "prototype.html"), "viewport": {"width": 393, "height": 852}},
                "target": {"viewportPt": {"width": 393, "height": 852}},
                "screens": [{
                    "id": "home", "rootNodeId": "home.root", "sourceSelector": "#home",
                    "systemChrome": {}, "regions": {},
                    "nodes": [
                        node("home.root", None, "container", [0, 0, 393, 852]),
                        hidden_parent,
                        node("home.hidden-title", "home.panel", "heading", [20, 120, 200, 30], "Hidden"),
                    ],
                }],
                "interactions": [], "visualStates": [{"id": "initial", "required": True}],
            }
            source, output = root / "ui-ir.json", root / "visual-manifest.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MANIFEST_SCRIPT), str(source), "--out", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            region_ids = {item["id"] for item in json.loads(output.read_text(encoding="utf-8"))["validationRegions"]}
            self.assertNotIn("node.home.hidden-title", region_ids)

    def test_visual_diff_reports_semantic_and_text_edge_regions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = Image.new("RGB", (100, 100), "white")
            current = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(reference).rectangle((10, 10, 60, 28), fill="black")
            ImageDraw.Draw(current).rectangle((16, 10, 66, 28), fill="black")
            reference_path, current_path = root / "reference.png", root / "current.png"
            reference.save(reference_path)
            current.save(current_path)
            regions_path = root / "regions.json"
            regions_path.write_text(json.dumps({"validationRegions": [{
                "id": "node.home.title",
                "nodeId": "home.title",
                "category": "typography",
                "criticality": "high",
                "toleranceProfile": "text",
                "rect": [8, 8, 62, 24],
            }]}), encoding="utf-8")
            out_dir = root / "diff"
            result = subprocess.run(
                [sys.executable, str(DIFF_SCRIPT), str(reference_path), str(current_path), "--out-dir", str(out_dir), "--regions-json", str(regions_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["schemaVersion"], "visual-diff-report-2.0")
            self.assertGreater(report["diagnostics"]["maxTextEdgeMismatchRatio"], 0)
            self.assertEqual(report["diagnostics"]["dominantCategory"], "typography")
            self.assertTrue((out_dir / "regions.png").is_file())

    def test_required_threshold_failure_returns_quality_gate_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            html_dir, ios_dir, out_dir = root / "html", root / "ios", root / "review"
            html_dir.mkdir()
            ios_dir.mkdir()
            reference = Image.new("RGB", (80, 80), "white")
            current = Image.new("RGB", (80, 80), "black")
            reference.save(html_dir / "initial.png")
            current.save(ios_dir / "initial.png")
            manifest = {
                "schemaVersion": "visual-state-manifest-1.0",
                "validationRegions": [{
                    "id": "screen.navigation",
                    "category": "system-chrome",
                    "criticality": "critical",
                    "toleranceProfile": "structure",
                    "rect": [0, 0, 80, 20],
                }],
                "states": [{
                    "id": "initial",
                    "required": True,
                    "validationRegions": [{
                        "id": "node.state-specific",
                        "nodeId": "state-specific",
                        "category": "control",
                        "criticality": "high",
                        "toleranceProfile": "control",
                        "rect": [10, 20, 40, 30],
                    }],
                    "geometryNodes": [{"nodeId": "state-specific"}],
                }],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(BUNDLE_SCRIPT), str(manifest_path), "--html-dir", str(html_dir), "--ios-dir", str(ios_dir), "--out-dir", str(out_dir), "--multimodal-capability", "unavailable"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
            bundle = json.loads((out_dir / "review-bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["summary"]["qualityGate"], "failed")
            self.assertEqual(bundle["summary"]["requiredFailures"], ["initial"])
            self.assertLess(bundle["summary"]["fidelityPercent"], 100)
            self.assertFalse(bundle["summary"]["exactFidelityAchieved"])
            self.assertIn("critical-region-mismatch", {item["gate"] for item in bundle["states"][0]["gateFailures"]})
            state_validation = json.loads(
                (out_dir / "initial" / "state-validation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                state_validation["validationRegions"][0]["id"],
                "node.state-specific",
            )

    def test_review_bundle_applies_manifest_comparison_masks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            html_dir, ios_dir, out_dir = root / "html", root / "ios", root / "review"
            html_dir.mkdir()
            ios_dir.mkdir()
            reference = Image.new("RGB", (80, 80), "white")
            current = Image.new("RGB", (80, 80), "white")
            ImageDraw.Draw(current).rectangle((0, 0, 79, 19), fill="black")
            reference.save(html_dir / "initial.png")
            current.save(ios_dir / "initial.png")
            manifest = {
                "schemaVersion": "visual-state-manifest-1.0",
                "comparisonMasks": [{"reason": "system-chrome", "rect": [0, 0, 80, 20]}],
                "validationRegions": [],
                "states": [{"id": "initial", "required": True}],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(BUNDLE_SCRIPT), str(manifest_path), "--html-dir", str(html_dir), "--ios-dir", str(ios_dir), "--out-dir", str(out_dir)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads((out_dir / "initial" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["mismatchRatio"], 0)
            self.assertEqual(report["masks"], [[0, 0, 80, 20]])
            bundle = json.loads((out_dir / "review-bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["summary"]["fidelityPercent"], 100)
            self.assertTrue(bundle["summary"]["exactFidelityAchieved"])


if __name__ == "__main__":
    unittest.main()
