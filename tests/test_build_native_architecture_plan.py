#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_native_architecture_plan.py"


def make_ir(screen_id: str = "home") -> dict:
    root_id = f"{screen_id}.root"
    return {
        "schemaVersion": "1.2",
        "target": {"uiStack": "uikit"},
        "screens": [{
            "id": screen_id,
            "rootNodeId": root_id,
            "navigation": {"style": "custom", "scrollEdgeAppearance": "automatic"},
            "regions": {
                "topBar": {"nodeId": f"{screen_id}.top", "kind": "custom-navigation-bar"},
                "bottomBar": {"nodeId": f"{screen_id}.bottom", "kind": "bottom-action-bar"},
            },
            "nodes": [
                {"id": root_id, "semanticType": "scroll"},
                {"id": f"{screen_id}.top", "semanticType": "navigation"},
                {"id": f"{screen_id}.bottom", "semanticType": "footer"},
            ],
        }],
        "interactions": [{"action": "push"}, {"action": "present-sheet"}],
    }


class BuildNativeArchitecturePlanTests(unittest.TestCase):
    def test_scroll_frame_never_subtracts_safe_area(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path = root / "ui-ir.json"
            behavior_path = root / "scroll.json"
            output = root / "native-architecture-plan.json"
            ir_path.write_text(json.dumps(make_ir()), encoding="utf-8")
            behavior_path.write_text(json.dumps({
                "schemaVersion": "scroll-region-behavior-1.0",
                "screenId": "home",
                "regions": [
                    {"nodeId": "home.top", "edge": "top", "behavior": "scroll-away", "confidence": 0.94, "evidence": ["moved with content"]},
                    {"nodeId": "home.bottom", "edge": "bottom", "behavior": "fixed", "confidence": 0.92, "evidence": ["stable"]},
                ],
            }), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--scroll-behavior", str(behavior_path),
                "--out", str(output), "--ui-stack", "uikit",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(output.read_text(encoding="utf-8"))
            screen = plan["screens"][0]
            self.assertEqual(screen["navigation"]["barBehavior"], "scroll-away")
            self.assertEqual(screen["bottomRegion"]["behavior"], "fixed")
            self.assertEqual(screen["safeArea"]["owner"], "system")
            self.assertFalse(screen["safeArea"]["subtractFromContainerDimensions"])
            self.assertFalse(screen["scroll"]["subtractSafeAreaFromFrame"])
            self.assertEqual(screen["scroll"]["containerWidthPolicy"], "full-parent-bounds")
            self.assertEqual(screen["scroll"]["containerHeightPolicy"], "full-parent-bounds")
            self.assertEqual(screen["scroll"]["contentInsetAdjustment"], "automatic")
            self.assertEqual(screen["controller"]["navigationContainer"], "UINavigationController")
            self.assertEqual(screen["presentations"], ["present-sheet"])

    def test_immersive_page_owns_insets_without_dimension_subtraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = make_ir("detail")
            payload["screens"][0]["navigation"]["style"] = "immersive"
            ir_path = root / "ui-ir.json"
            output = root / "plan.json"
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--out", str(output), "--ui-stack", "swiftui",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            screen = json.loads(output.read_text(encoding="utf-8"))["screens"][0]
            self.assertEqual(screen["safeArea"]["owner"], "immersive-content")
            self.assertEqual(screen["scroll"]["contentInsetAdjustment"], "never")
            self.assertFalse(screen["scroll"]["subtractSafeAreaFromFrame"])


if __name__ == "__main__":
    unittest.main()
