#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply_visual_correction_plan.py"


class ApplyVisualCorrectionPlanTests(unittest.TestCase):
    def fixture(self, root: Path, *, amount: float = -3, expected_before: float = 20) -> tuple[Path, Path]:
        ir = root / "source-ir.json"
        ir.write_text(json.dumps({
            "schemaVersion": "1.2",
            "screens": [{"id": "home", "nodes": [{
                "id": "home.title",
                "semanticType": "text",
                "layout": {"rect": {"x": 20, "y": 80, "width": 120, "height": 24}},
            }]}],
        }), encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(json.dumps({
            "schemaVersion": "visual-correction-plan-1.0",
            "policy": {"mutationOwnership": "ui-ir-and-derived-contracts-only"},
            "iteration": 1,
            "corrections": [{
                "id": "correction.1",
                "nodeId": "home.title",
                "automaticEligible": True,
                "prohibitedMutation": "generated-swift-source",
                "proposedMutation": {
                    "schemaVersion": "ui-ir-bounded-mutation-1.0",
                    "owner": "ui-ir",
                    "nodeId": "home.title",
                    "operations": [{
                        "path": "layout.rect.x", "operation": "add", "amount": amount,
                        "expectedBefore": expected_before, "expectedAfter": expected_before + amount,
                        "sourceDelta": -amount, "limitPoints": 12,
                    }],
                },
            }],
        }), encoding="utf-8")
        return ir, plan

    def invoke(self, root: Path, ir: Path, plan: Path) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        output = root / "corrected-ir.json"
        report = root / "application-report.json"
        result = subprocess.run([
            sys.executable, str(SCRIPT), str(plan), "--ir", str(ir),
            "--out", str(output), "--report", str(report),
        ], text=True, capture_output=True, check=False)
        return result, output, report

    def test_applies_bounded_operation_to_copied_ir_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir, plan = self.fixture(root)
            source_before = ir.read_text(encoding="utf-8")
            result, output, report_path = self.invoke(root, ir, plan)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(ir.read_text(encoding="utf-8"), source_before)
            corrected = json.loads(output.read_text(encoding="utf-8"))
            node = corrected["screens"][0]["nodes"][0]
            self.assertEqual(node["layout"]["rect"]["x"], 17)
            self.assertEqual(node["calibration"]["visualCorrections"][0]["correctionId"], "correction.1")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["appliedCount"], 1)
            self.assertTrue(report["summary"]["requiresRegeneration"])

    def test_rejects_stale_or_out_of_bounds_mutation_without_partial_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir, plan = self.fixture(root, amount=-13, expected_before=19)
            result, output, report_path = self.invoke(root, ir, plan)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            corrected = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(corrected["screens"][0]["nodes"][0]["layout"]["rect"]["x"], 20)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["appliedCount"], 0)
            self.assertIn(report["rejected"][0]["reason"], {"stale-expected-before-x", "amount-exceeds-plan-limit-x"})


if __name__ == "__main__":
    unittest.main()
