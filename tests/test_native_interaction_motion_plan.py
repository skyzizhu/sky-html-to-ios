#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_native_interaction_motion_plan.py"
VALIDATE = ROOT / "scripts" / "validate_native_interaction_motion_plan.py"


class NativeInteractionMotionPlanTests(unittest.TestCase):
    def test_nested_transitions_receive_native_owners_and_motion_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir = root / "ui-ir.json"
            application = root / "application.json"
            presentation = root / "presentation.json"
            plan = root / "interaction.json"
            ir.write_text(json.dumps({
                "schemaVersion": "1.2", "screens": [{"id": "home"}],
                "interactions": [{
                    "id": "open", "sourceNodeId": "home.open", "trigger": "tap",
                    "payload": {"transitions": [
                        {"action": "push", "targetScreenId": "details"},
                        {"action": "present-sheet", "targetStateId": "filters", "schedule": {"delayMs": 120}},
                    ]},
                }],
                "motions": [{
                    "id": "pulse", "sourceNodeId": "home.open", "kind": "keyframe-animation",
                    "durationMs": 300, "delayMs": 20, "timingFunction": "ease-in-out", "keyframes": [],
                }],
            }), encoding="utf-8")
            application.write_text(json.dumps({
                "schemaVersion": "native-application-plan-1.0",
                "screenMemberships": [{
                    "screenId": "home", "applicationContainerId": "main-application",
                    "navigationStackId": "main-navigation",
                }],
            }), encoding="utf-8")
            presentation.write_text(json.dumps({
                "schemaVersion": "native-presentation-plan-1.0",
                "screens": [{"screenId": "home", "presentations": [{"stateId": "filters"}]}],
            }), encoding="utf-8")
            result = subprocess.run([
                "python3", str(BUILD), "--ir", str(ir), "--application-plan", str(application),
                "--presentation-plan", str(presentation), "--out", str(plan),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            payload = json.loads(plan.read_text(encoding="utf-8"))["screens"][0]
            self.assertEqual([item["owner"] for item in payload["actions"]], ["navigation-stack", "screen-host"])
            self.assertEqual(payload["actions"][1]["delayMilliseconds"], 120)
            self.assertEqual(payload["motions"][0]["durationMilliseconds"], 300)
            report = root / "validation.json"
            validated = subprocess.run([
                "python3", str(VALIDATE), "--plan", str(plan), "--out", str(report),
            ], text=True, capture_output=True)
            self.assertEqual(validated.returncode, 0, validated.stderr or validated.stdout)


if __name__ == "__main__":
    unittest.main()
