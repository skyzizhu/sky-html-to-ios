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
                "states": [{"id": "initial", "required": True}],
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
