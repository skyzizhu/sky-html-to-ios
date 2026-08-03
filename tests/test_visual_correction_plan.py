#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_visual_correction_plan.py"


class VisualCorrectionPlanTests(unittest.TestCase):
    def test_failed_system_control_region_generates_ir_owned_correction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schemaVersion": "visual-state-manifest-1.0",
                "states": [{"id": "initial", "activeStateId": None}],
            }), encoding="utf-8")
            ir_path = root / "ui-ir.json"
            ir_path.write_text(json.dumps({
                "schemaVersion": "1.2",
                "screens": [{
                    "id": "home",
                    "nodes": [{
                        "id": "home.submit",
                        "semanticType": "button",
                        "nativeMapping": {
                            "nativeControlDecision": {
                                "policy": "system-first-visual-fit-gated",
                                "decision": "system-control",
                                "systemCandidate": True,
                            },
                        },
                    }],
                }],
                "states": [],
            }), encoding="utf-8")
            bundle_path = root / "review-bundle.json"
            bundle_path.write_text(json.dumps({
                "schemaVersion": "visual-review-bundle-2.0",
                "manifest": str(manifest),
                "states": [{
                    "id": "initial",
                    "status": "failed-threshold",
                    "report": {"diagnostics": {"worstSemanticRegions": [{
                        "nodeId": "home.submit",
                        "category": "control",
                        "criticality": "high",
                        "mismatchRatio": 0.48,
                        "edgeMismatchRatio": 0.31,
                    }]}},
                    "geometryReport": {"nodes": []},
                }],
                "summary": {"fidelityPercent": 88.5},
            }), encoding="utf-8")
            output = root / "correction-plan.json"
            result = subprocess.run([
                sys.executable, str(SCRIPT), str(bundle_path),
                "--ir", str(ir_path), "--out", str(output),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            plan = json.loads(output.read_text(encoding="utf-8"))
            correction = plan["corrections"][0]
            self.assertEqual(correction["mutationTarget"], "native-control-configuration")
            self.assertEqual(correction["mutationScope"], "ui-ir-or-derived-contract")
            self.assertEqual(correction["prohibitedMutation"], "generated-swift-source")
            self.assertIn("Keep the system control", correction["recommendedCorrection"])
            self.assertEqual(plan["summary"]["nextAction"], "apply-plan-and-regenerate")

    def test_iteration_guard_stops_when_fidelity_does_not_improve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"states": [{"id": "initial"}]}), encoding="utf-8")
            ir_path = root / "ui-ir.json"
            ir_path.write_text(json.dumps({
                "screens": [{"nodes": [{"id": "home.title", "semanticType": "text", "nativeMapping": {}}]}],
                "states": [],
            }), encoding="utf-8")
            bundle = root / "bundle.json"
            bundle.write_text(json.dumps({
                "schemaVersion": "visual-review-bundle-2.0",
                "manifest": str(manifest),
                "states": [{
                    "id": "initial", "status": "failed-threshold",
                    "report": {"diagnostics": {"worstSemanticRegions": [{
                        "nodeId": "home.title", "category": "typography",
                        "mismatchRatio": 0.2, "edgeMismatchRatio": 0.3,
                    }]}},
                }],
                "summary": {"fidelityPercent": 90.1},
            }), encoding="utf-8")
            previous = root / "previous.json"
            previous.write_text(json.dumps({"summary": {"sourceFidelityPercent": 90.0}}), encoding="utf-8")
            output = root / "plan.json"
            result = subprocess.run([
                sys.executable, str(SCRIPT), str(bundle), "--ir", str(ir_path),
                "--out", str(output), "--iteration", "2", "--previous-plan", str(previous),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads(output.read_text(encoding="utf-8"))["summary"]
            self.assertIn("fidelity-improvement-below-threshold", summary["stopReasons"])
            self.assertEqual(summary["nextAction"], "human-review")


if __name__ == "__main__":
    unittest.main()
