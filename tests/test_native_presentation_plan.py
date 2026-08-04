import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NativePresentationPlanTests(unittest.TestCase):
    def test_builds_and_validates_shared_native_presentation_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir = {
                "schemaVersion": "1.2",
                "target": {"scale": 1},
                "screens": [{
                    "id": "home", "rootNodeId": "home.root",
                    "nodes": [
                        {"id": "home.root", "parentId": None, "layout": {"rect": {"x": 0, "y": 0, "width": 393, "height": 852}}, "style": {}},
                        {"id": "home.open", "parentId": "home.root", "layout": {"rect": {"x": 300, "y": 40, "width": 44, "height": 44}}, "style": {}},
                        {"id": "home.sheet", "parentId": "home.root", "layout": {"rect": {"x": 0, "y": 430, "width": 393, "height": 422}, "scrollAxis": "vertical"}, "style": {"borderRadius": "24px"}, "source": {"ios": {"backdropDismiss": "false"}}},
                    ],
                }],
                "states": [{"id": "filters", "kind": "sheet", "targetNodeIds": ["home.sheet"]}],
                "interactions": [{
                    "id": "open-filters", "sourceNodeId": "home.open", "sourceNodeIds": ["home.open"],
                    "presentation": {"style": "page-sheet", "detents": ["height:422", "large"], "grabberVisible": True},
                    "payload": {"transitions": [{"action": "present-sheet", "targetStateId": "filters"}]},
                }],
            }
            ir_path = root / "ir.json"
            plan_path = root / "plan.json"
            report_path = root / "report.json"
            ir_path.write_text(json.dumps(ir), encoding="utf-8")
            subprocess.run([sys.executable, ROOT / "scripts/build_native_presentation_plan.py", "--ir", ir_path, "--out", plan_path], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, ROOT / "scripts/validate_native_presentation_plan.py", "--plan", plan_path, "--out", report_path], check=True, capture_output=True, text=True)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            item = plan["screens"][0]["presentations"][0]
            self.assertEqual(item["strategy"], "system-sheet")
            self.assertEqual(item["detents"], ["height:422", "large"])
            self.assertFalse(item["backdrop"]["dismisses"])
            self.assertEqual(item["panel"]["cornerRadiusPt"], 24)
            self.assertEqual(item["scrollOwnership"], "presentation-content")
            self.assertEqual(item["anchor"]["sourceRect"], [300, 40, 44, 44])
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["status"], "passed")

    def test_ignores_transition_only_states_without_visual_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schemaVersion": "1.2",
                "screens": [{"id": "home", "rootNodeId": "root", "nodes": [{"id": "root", "parentId": None, "layout": {"rect": {"x": 0, "y": 0, "width": 393, "height": 852}}, "style": {}}]}],
                "states": [{"id": "unresolved-cover", "kind": "full-screen-overlay", "targetNodeIds": []}],
                "interactions": [{"id": "open", "payload": {"transitions": [{"action": "present-fullscreen", "targetStateId": "unresolved-cover"}]}}],
            }
            ir_path = root / "ir.json"
            out = root / "plan.json"
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run([sys.executable, ROOT / "scripts/build_native_presentation_plan.py", "--ir", ir_path, "--out", out], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["screens"][0]["presentations"], [])


if __name__ == "__main__":
    unittest.main()
