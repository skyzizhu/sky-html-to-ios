#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH_SCRIPT = ROOT / "scripts" / "build_layout_relation_graph.py"
ARCHITECTURE_SCRIPT = ROOT / "scripts" / "build_native_architecture_plan.py"
GENERATOR_SCRIPT = ROOT / "scripts" / "generate_ios_from_ir.py"
VALIDATOR_SCRIPT = ROOT / "scripts" / "validate_native_structure_manifest.py"


def node(
    node_id: str,
    parent_id: str | None,
    semantic: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    axis: str = "none",
) -> dict:
    return {
        "id": node_id,
        "parentId": parent_id,
        "source": {"runtimeId": node_id, "selector": f"#{node_id}"},
        "semanticType": semantic,
        "layout": {
            "mode": "row" if axis == "horizontal" else "flow",
            "rect": {"x": x, "y": y, "width": width, "height": height},
            "scrollAxis": axis,
        },
        "style": {
            "display": "flex",
            "flexDirection": "row" if axis == "horizontal" else "column",
            "flexWrap": "nowrap",
            "flexGrow": "0",
            "flexShrink": "0",
            "fontSize": "16px",
            "fontWeight": "400",
            "color": "rgb(20, 20, 20)",
            "backgroundColor": "transparent",
            "padding": ["0px", "0px", "0px", "0px"],
            "cornerRadii": ["0px", "0px", "0px", "0px"],
            "gap": "8px",
            "textAlign": "start",
        },
        "content": {
            "text": "star.fill" if semantic == "icon" else "Item" if semantic in {"label", "button"} else None,
            "placeholder": None,
            "accessibilityLabel": None,
            "isDecorative": False,
        },
        "nativeMapping": {
            "confidence": 0.98,
            "styleStrategy": "native-default",
            "nativeControlDecision": {
                "policy": "system-first-visual-fit-gated",
                "decision": "system-control",
                "systemCandidate": True,
                "requiresCustomControl": False,
                "preserveSystemSemantics": True,
                "blockers": [],
                "fallbackChain": [],
                "evidence": [],
                "interactionActions": [],
                "interactionTriggers": [],
            },
        },
    }


def make_ir(ui_stack: str) -> dict:
    nodes = [
        node("home.root", None, "scroll", 0, 0, 393, 852, axis="vertical"),
        node("home.toolbar", "home.root", "container", 16, 20, 361, 48, axis="horizontal"),
        node("home.title", "home.toolbar", "label", 52, 30, 150, 24),
        node("home.icon", "home.toolbar", "icon", 16, 30, 24, 24),
        node("home.count", "home.toolbar", "label", 214, 30, 24, 24),
        node("home.filters", "home.root", "carousel", 16, 90, 361, 44, axis="horizontal"),
        node("home.filter.1", "home.filters", "button", 16, 90, 88, 36),
        node("home.filter.2", "home.filters", "button", 112, 90, 88, 36),
        node("home.filter.3", "home.filters", "button", 208, 90, 88, 36),
    ]
    return {
        "schemaVersion": "1.2",
        "target": {"uiStack": ui_stack},
        "screens": [{
            "id": "home",
            "rootNodeId": "home.root",
            "navigation": {"style": "hidden"},
            "regions": {"topBar": None, "bottomBar": None, "floatingAction": None},
            "sourceCoverage": {
                "rootSubtreeNodeCount": len(nodes),
                "routeScopedNodeCount": len(nodes),
                "mappedNodeCount": len(nodes),
                "excludedByRouteCount": 0,
                "excludedNonVisualOrUnsupportedTagCount": 0,
                "mappedRatio": 1.0,
            },
            "nodes": nodes,
        }],
        "interactions": [],
    }


class NativeStructureManifestTests(unittest.TestCase):
    def run_command(self, command: list[str], expect_success: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if expect_success and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def build_chain(self, root: Path, ui_stack: str) -> tuple[Path, Path, Path, Path]:
        ir_path = root / "ui-ir.json"
        graph_path = root / "layout-relation-graph.json"
        architecture_path = root / "native-architecture-plan.json"
        native_manifest_path = root / "native-structure-manifest.json"
        out_dir = root / "Generated" / "HTMLToIOS"
        ir_path.write_text(json.dumps(make_ir(ui_stack)), encoding="utf-8")
        self.run_command([
            "python3", str(GRAPH_SCRIPT), "--ir", str(ir_path), "--out", str(graph_path),
        ])
        self.run_command([
            "python3", str(ARCHITECTURE_SCRIPT), "--ir", str(ir_path),
            "--out", str(architecture_path), "--ui-stack", ui_stack,
        ])
        self.run_command([
            "python3", str(GENERATOR_SCRIPT), "--ir", str(ir_path),
            "--out-dir", str(out_dir), "--ui-stack", ui_stack,
            "--architecture-plan", str(architecture_path),
            "--layout-relation-graph", str(graph_path),
            "--native-structure-manifest", str(native_manifest_path),
        ])
        return graph_path, architecture_path, native_manifest_path, out_dir

    def validate_chain(
        self,
        root: Path,
        graph_path: Path,
        architecture_path: Path,
        native_manifest_path: Path,
        out_dir: Path,
        *,
        expect_success: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        report_path = root / "native-structure-validation.json"
        result = self.run_command([
            "python3", str(VALIDATOR_SCRIPT),
            "--manifest", str(native_manifest_path),
            "--layout-graph", str(graph_path),
            "--architecture-plan", str(architecture_path),
            "--generated-dir", str(out_dir),
            "--generation-manifest", str(out_dir / ".html-to-ios-generation.json"),
            "--out", str(report_path),
        ], expect_success=expect_success)
        return result, json.loads(report_path.read_text(encoding="utf-8"))

    def test_generated_native_structure_passes_for_swiftui_and_uikit_without_images(self) -> None:
        for ui_stack in ("swiftui", "uikit"):
            with self.subTest(ui_stack=ui_stack), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                outputs = self.build_chain(root, ui_stack)
                _, report = self.validate_chain(root, *outputs)
                manifest = json.loads(outputs[2].read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "passed")
                self.assertFalse(report["qualityGate"]["requiresScreenshots"])
                self.assertFalse(report["qualityGate"]["requiresMultimodalModel"])
                self.assertEqual(manifest["summary"]["missingNodeCount"], 0)
                self.assertEqual(manifest["summary"]["unconsumedRelationCount"], 0)

    def test_validator_rejects_consumer_changed_after_manifest_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs = self.build_chain(root, "swiftui")
            runtime = outputs[3] / "Core/Runtime/HTMLToIOSGeneratedRuntime.swift"
            runtime.write_text(runtime.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
            result, report = self.validate_chain(root, *outputs, expect_success=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn(
                "NATIVE_CONSUMER_HASH_MISMATCH",
                {item["code"] for item in report["issues"]},
            )

    def test_validator_rejects_architecture_plan_changed_after_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs = self.build_chain(root, "swiftui")
            architecture = json.loads(outputs[1].read_text(encoding="utf-8"))
            architecture["minimumIOS"] = "99.0"
            outputs[1].write_text(json.dumps(architecture), encoding="utf-8")
            result, report = self.validate_chain(root, *outputs, expect_success=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn(
                "STALE_ARCHITECTURE_PLAN_PROVENANCE",
                {item["code"] for item in report["issues"]},
            )


if __name__ == "__main__":
    unittest.main()
