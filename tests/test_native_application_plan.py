#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_native_application_plan.py"
VALIDATE = ROOT / "scripts" / "validate_native_application_plan.py"


class NativeApplicationPlanTests(unittest.TestCase):
    def test_flow_state_screen_transition_is_an_application_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = []
            for screen_id, target in (("step1", "step2"), ("step2", None)):
                payload = {
                    "schemaVersion": "1.2", "screens": [{"id": screen_id}],
                    "interactions": ([{
                        "id": "advance", "sourceNodeId": "step1.button",
                        "payload": {"transitions": [{"action": "set-flow-state", "targetScreenId": target}]},
                    }] if target else []),
                }
                path = root / f"{screen_id}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                inputs.extend(["--ir", str(path)])
            plan = root / "plan.json"
            result = subprocess.run(
                ["python3", str(BUILD), *inputs, "--ui-stack", "uikit", "--out", str(plan)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            route = json.loads(plan.read_text(encoding="utf-8"))["routes"][0]
            self.assertEqual(route["action"], "set-flow-state")
            self.assertEqual(route["targetScreenId"], "step2")

    def test_global_tab_and_navigation_ownership_is_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tabs = {
                "id": "main-tabs", "initialTabId": "home-tab",
                "items": [
                    {"id": "home-tab", "title": "Home", "targetScreenId": "home", "icon": "house"},
                    {"id": "profile-tab", "title": "Profile", "targetScreenId": "profile", "icon": "person"},
                ],
            }
            home = {
                "schemaVersion": "1.2", "screens": [{"id": "home", "tabContainer": tabs}],
                "interactions": [{
                    "id": "open-profile", "sourceNodeId": "home.profile",
                    "payload": {"transitions": [{"action": "push", "targetScreenId": "profile"}]},
                }],
            }
            profile_tabs = {**tabs, "initialTabId": "profile-tab"}
            profile = {
                "schemaVersion": "1.2", "screens": [{"id": "profile", "tabContainer": profile_tabs}],
                "interactions": [],
            }
            inputs = []
            for name, payload in (("home", home), ("profile", profile)):
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                inputs.extend(["--ir", str(path)])
            plan = root / "plan.json"
            result = subprocess.run(
                ["python3", str(BUILD), *inputs, "--ui-stack", "uikit", "--out", str(plan)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(payload["applicationContainer"]["kind"], "tab-navigation")
            self.assertEqual(payload["initialScreenId"], "home")
            self.assertEqual(len(payload["navigationStacks"]), 2)
            self.assertEqual(payload["routes"][0]["targetScreenId"], "profile")
            self.assertEqual(len({item["screenId"] for item in payload["screenMemberships"]}), 2)
            report = root / "validation.json"
            validated = subprocess.run(
                ["python3", str(VALIDATE), "--plan", str(plan), "--out", str(report)],
                text=True, capture_output=True,
            )
            self.assertEqual(validated.returncode, 0, validated.stderr or validated.stdout)
            self.assertEqual(json.loads(validated.stdout)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
