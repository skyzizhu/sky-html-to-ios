#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_scroll_attachment_plan.py"
VALIDATE = ROOT / "scripts" / "validate_scroll_attachment_plan.py"


def node(node_id: str, parent_id: str | None, semantic: str, axis: str = "none", position: str = "static") -> dict:
    return {
        "id": node_id,
        "parentId": parent_id,
        "semanticType": semantic,
        "source": {"runtimeId": node_id, "selector": f"#{node_id}"},
        "layout": {"scrollAxis": axis, "position": position},
        "style": {"position": position},
    }


class ScrollAttachmentPlanTests(unittest.TestCase):
    def write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def build(self, root: Path, nodes: list[dict], *, root_axis: str = "vertical", top_behavior: str = "fixed") -> dict:
        ir = root / "ir.json"
        architecture = root / "architecture.json"
        probe = root / "probe.json"
        plan = root / "plan.json"
        self.write_json(ir, {"screens": [{"id": "home", "nodes": nodes}]})
        self.write_json(architecture, {
            "schemaVersion": "native-architecture-plan-1.1",
            "screens": [{
                "screenId": "home",
                "safeArea": {"owner": "system"},
                "scroll": {"contentInsetAdjustment": "automatic"},
                "layers": {
                    "contentContainer": {"nodeId": "home.root", "scrollAxis": root_axis},
                    "screenRegions": {
                        "top": {"nodeId": "home.top", "behavior": top_behavior},
                        "bottom": {"nodeId": None, "behavior": "none"},
                    },
                },
            }],
        })
        self.write_json(probe, {
            "screenId": "home",
            "regions": [{
                "edge": "top",
                "nodeId": "home.top",
                "selector": "#home.top",
                "behavior": top_behavior,
                "confidence": 1,
                "evidence": ["exact-node-match"],
            }],
        })
        subprocess.run([
            "python3", str(BUILD), "--ir", str(ir), "--architecture-plan", str(architecture),
            "--scroll-behavior", str(probe), "--out", str(plan),
        ], check=True, capture_output=True, text=True)
        return json.loads(plan.read_text(encoding="utf-8"))

    def validate(self, root: Path, payload: dict) -> subprocess.CompletedProcess[str]:
        plan = root / "validation-input.json"
        report = root / "validation.json"
        self.write_json(plan, payload)
        return subprocess.run(
            ["python3", str(VALIDATE), "--plan", str(plan), "--out", str(report)],
            capture_output=True, text=True,
        )

    def test_fixed_region_is_lifted_and_safe_area_is_not_subtracted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.build(root, [
                node("home.root", None, "scroll", "vertical"),
                node("home.top", "home.root", "navigation", position="fixed"),
                node("home.body", "home.root", "container"),
            ])
            screen = payload["screens"][0]
            self.assertEqual(screen["regions"]["top"]["attachment"], "viewport-overlay")
            self.assertTrue(screen["regions"]["top"]["liftedFromContent"])
            self.assertIsNone(screen["regions"]["top"]["scrollOwnerNodeId"])
            self.assertFalse(screen["safeArea"]["subtractFromContainerDimensions"])
            self.assertEqual(self.validate(root, payload).returncode, 0)

    def test_unrelated_probe_candidate_does_not_override_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.build(root, [
                node("home.root", None, "scroll", "vertical"),
                node("home.top", "home.root", "navigation"),
            ], top_behavior="scroll-away")
            screen = payload["screens"][0]
            self.assertEqual(screen["regions"]["top"]["behavior"], "scroll-away")
            self.assertFalse(screen["regions"]["top"]["liftedFromContent"])

    def test_same_axis_nested_scroll_fails_but_explicit_data_table_both_axis_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.build(root, [
                node("home.root", None, "scroll", "vertical"),
                node("home.nested", "home.root", "list", "vertical"),
                node("home.top", "home.root", "navigation"),
            ])
            result = self.validate(root, payload)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SAME_AXIS_NESTED_SCROLL", result.stdout)

            table_nodes = [
                node("home.root", None, "data-table", "both"),
                node("home.top", "home.root", "navigation"),
            ]
            table_nodes[0]["iosHints"] = {"scroll-root": "both"}
            table_payload = self.build(root, table_nodes, root_axis="both")
            self.assertTrue(table_payload["screens"][0]["rootBidirectionalScrollExplicit"])
            self.assertEqual(self.validate(root, table_payload).returncode, 0)

    def test_native_control_internal_scroll_does_not_claim_page_axis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.build(root, [
                node("home.root", None, "scroll", "vertical"),
                node("home.multi", "home.root", "multi-select", "vertical"),
                node("home.editor", "home.root", "text-area", "vertical"),
                node("home.top", "home.root", "navigation"),
            ])
            contracts = {
                item["nodeId"]: item
                for item in payload["screens"][0]["nodes"]
            }
            self.assertEqual(contracts["home.multi"]["scrollAxis"], "none")
            self.assertEqual(contracts["home.editor"]["scrollAxis"], "none")
            self.assertEqual(self.validate(root, payload).returncode, 0)


if __name__ == "__main__":
    unittest.main()
