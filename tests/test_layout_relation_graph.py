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
FIDELITY_SCRIPT = ROOT / "scripts" / "validate_structural_fidelity.py"


def control_decision() -> dict:
    return {
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
    }


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
        },
        "nativeMapping": {
            "confidence": 0.98,
            "styleStrategy": "native-default",
            "nativeControlDecision": control_decision(),
        },
    }


def make_ir() -> dict:
    nodes = [
        node("home.root", None, "scroll", 0, 0, 393, 852, axis="vertical"),
        node("home.toolbar", "home.root", "container", 16, 20, 361, 48, axis="horizontal"),
        # DOM order differs from the rendered order. The graph must preserve rendered x order.
        node("home.title", "home.toolbar", "heading", 52, 30, 150, 28),
        node("home.icon", "home.toolbar", "icon", 16, 30, 24, 24),
        node("home.count", "home.toolbar", "label", 214, 30, 28, 28),
        node("home.filters", "home.root", "carousel", 16, 90, 361, 44, axis="horizontal"),
        node("home.filter.1", "home.filters", "button", 16, 90, 88, 36),
        node("home.filter.2", "home.filters", "button", 112, 90, 88, 36),
        node("home.filter.3", "home.filters", "button", 208, 90, 88, 36),
    ]
    return {
        "schemaVersion": "1.2",
        "target": {"uiStack": "uikit"},
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


class LayoutRelationGraphTests(unittest.TestCase):
    def build_outputs(self, root: Path, payload: dict | None = None) -> tuple[Path, Path, Path]:
        ir_path = root / "ui-ir.json"
        graph_path = root / "layout-relation-graph.json"
        architecture_path = root / "native-architecture-plan.json"
        ir_path.write_text(json.dumps(payload or make_ir()), encoding="utf-8")
        graph = subprocess.run([
            "python3", str(GRAPH_SCRIPT), "--ir", str(ir_path), "--out", str(graph_path),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(graph.returncode, 0, graph.stderr)
        architecture = subprocess.run([
            "python3", str(ARCHITECTURE_SCRIPT), "--ir", str(ir_path),
            "--out", str(architecture_path), "--ui-stack", "uikit",
        ], text=True, capture_output=True, check=False)
        self.assertEqual(architecture.returncode, 0, architecture.stderr)
        return ir_path, graph_path, architecture_path

    def test_graph_preserves_visual_order_square_geometry_and_axis_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, graph_path, _ = self.build_outputs(root)
            screen = json.loads(graph_path.read_text(encoding="utf-8"))["screens"][0]
            toolbar = next(item for item in screen["containers"] if item["containerNodeId"] == "home.toolbar")
            self.assertEqual(
                toolbar["orderedChildNodeIds"],
                ["home.icon", "home.title", "home.count"],
            )
            relation_kinds = [(item["kind"], item["nodeIds"]) for item in screen["relations"]]
            self.assertIn(("square-aspect", ["home.icon"]), relation_kinds)
            self.assertIn(("scroll-axis-ownership", ["home.root"]), relation_kinds)
            self.assertIn(("scroll-axis-ownership", ["home.filters"]), relation_kinds)
            first_sequence = next(
                item for item in screen["relations"]
                if item["kind"] == "visual-sequence" and item.get("containerNodeId") == "home.toolbar"
            )
            self.assertEqual(first_sequence["beforeNodeId"], "home.icon")
            self.assertEqual(first_sequence["afterNodeId"], "home.title")

    def test_structural_fidelity_passes_without_screenshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path, graph_path, architecture_path = self.build_outputs(root)
            report_path = root / "structural-fidelity-report.json"
            result = subprocess.run([
                "python3", str(FIDELITY_SCRIPT), "--ir", str(ir_path),
                "--layout-graph", str(graph_path), "--architecture-plan", str(architecture_path),
                "--out", str(report_path),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["qualityGate"]["passed"])
            self.assertFalse(report["qualityGate"]["requiresScreenshots"])
            self.assertFalse(report["qualityGate"]["requiresMultimodalModel"])

    def test_synthetic_state_container_does_not_require_browser_runtime_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = make_ir()
            payload["screens"][0]["nodes"].append({
                **node("state.menu.container", "home.root", "container", 0, 0, 393, 852),
                "source": {"runtimeId": None, "synthetic": "state-delta-container"},
            })
            ir_path, graph_path, architecture_path = self.build_outputs(root, payload)
            report_path = root / "structural-fidelity-report.json"
            result = subprocess.run([
                "python3", str(FIDELITY_SCRIPT), "--ir", str(ir_path),
                "--layout-graph", str(graph_path), "--architecture-plan", str(architecture_path),
                "--out", str(report_path),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertNotIn("SOURCE_RUNTIME_ID_MISSING", {item["code"] for item in report["issues"]})

    def test_structural_fidelity_blocks_native_order_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path, graph_path, architecture_path = self.build_outputs(root)
            architecture = json.loads(architecture_path.read_text(encoding="utf-8"))
            relations = architecture["screens"][0]["layers"]["contentContainer"]["layoutRelations"]
            toolbar = next(item for item in relations if item["containerNodeId"] == "home.toolbar")
            toolbar["orderedChildNodeIds"] = list(reversed(toolbar["orderedChildNodeIds"]))
            architecture_path.write_text(json.dumps(architecture), encoding="utf-8")
            report_path = root / "structural-fidelity-report.json"
            result = subprocess.run([
                "python3", str(FIDELITY_SCRIPT), "--ir", str(ir_path),
                "--layout-graph", str(graph_path), "--architecture-plan", str(architecture_path),
                "--out", str(report_path),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn("NATIVE_VISUAL_ORDER_MISMATCH", {item["code"] for item in report["issues"]})


if __name__ == "__main__":
    unittest.main()
