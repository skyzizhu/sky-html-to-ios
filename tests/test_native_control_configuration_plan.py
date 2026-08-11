#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_native_control_configuration_plan.py"
VALIDATE = ROOT / "scripts" / "validate_native_control_configuration_plan.py"


class NativeControlConfigurationPlanTests(unittest.TestCase):
    def test_computed_geometry_colors_and_states_form_valid_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir = root / "ui-ir.json"
            plan = root / "plan.json"
            validation = root / "validation.json"
            ir.write_text(json.dumps({
                "target": {"scale": 1},
                "screens": [{
                    "id": "home",
                    "nodes": [{
                        "id": "home.switch",
                        "semanticType": "switch",
                        "layout": {"rect": {"width": 51, "height": 31}},
                        "style": {
                            "color": "rgb(20, 20, 20)",
                            "backgroundColor": "rgb(220, 220, 225)",
                            "borderColors": ["rgb(0, 122, 255)"] * 4,
                            "padding": ["2px", "4px", "2px", "4px"],
                            "gap": "6px",
                        },
                        "controlVisualStates": {
                            "selected": {"backgroundColor": "rgb(0, 122, 255)", "color": "rgb(255, 255, 255)"},
                            "disabled": {"opacity": "0.4"},
                        },
                        "nativeMapping": {"nativeControlDecision": {"decision": "system-control"}},
                    }, {
                        "id": "home.switch.thumb",
                        "parentId": "home.switch",
                        "semanticType": "decoration",
                        "style": {"backgroundColor": "rgb(250, 250, 250)"},
                    }, {
                        "id": "home.pages",
                        "semanticType": "page-control",
                        "layout": {"rect": {"width": 48, "height": 8}},
                        "style": {"backgroundColor": "transparent", "gap": "5px"},
                        "nativeMapping": {"nativeControlDecision": {"decision": "system-control"}},
                    }, {
                        "id": "home.page.0", "parentId": "home.pages", "semanticType": "container",
                        "source": {"selector": ".pages i"},
                        "style": {"backgroundColor": "rgb(190, 195, 205)"},
                    }, {
                        "id": "home.page.1", "parentId": "home.pages", "semanticType": "container",
                        "source": {"selector": ".pages i.active"},
                        "style": {"backgroundColor": "rgb(0, 122, 255)"},
                    }],
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                ["python3", str(BUILD), "--ir", str(ir), "--out", str(plan)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(payload["schemaVersion"], "native-control-configuration-plan-1.1")
            control = payload["screens"][0]["controls"][0]
            self.assertEqual(control["selection"]["semanticCandidate"], "switch")
            self.assertEqual(control["selection"]["finalDecision"], "system-control")
            self.assertEqual(control["selection"]["geometryFit"]["boundedResolutionPasses"], 2)
            self.assertEqual(control["geometry"]["contentInsetsPt"], [2, 4, 2, 4])
            self.assertEqual(control["geometry"]["itemSpacingPt"], 6)
            self.assertEqual(control["appearance"]["fillTint"], "rgb(0, 122, 255)")
            self.assertEqual(control["appearance"]["thumbTint"], "rgb(250, 250, 250)")
            self.assertEqual(control["stateAppearances"]["selected"]["thumbTint"], "rgb(250, 250, 250)")
            self.assertEqual(control["appearance"]["trackTint"], "rgb(220, 220, 225)")
            self.assertEqual(control["behavior"]["stateNames"], ["checked", "disabled", "normal", "selected"])
            self.assertEqual(control["stateAppearances"]["selected"]["fillTint"], "rgb(0, 122, 255)")
            self.assertEqual(control["stateAppearances"]["checked"], control["stateAppearances"]["selected"])
            page_control = payload["screens"][0]["controls"][1]
            self.assertEqual(page_control["derivedConfiguration"], {"pageCount": 2, "currentPage": 1})
            self.assertEqual(page_control["appearance"]["fillTint"], "rgb(0, 122, 255)")
            self.assertEqual(page_control["appearance"]["trackTint"], "rgb(190, 195, 205)")
            self.assertEqual(page_control["stateAppearances"]["normal"]["fillTint"], "rgb(0, 122, 255)")
            result = subprocess.run(
                ["python3", str(VALIDATE), "--plan", str(plan), "--out", str(validation)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(json.loads(validation.read_text(encoding="utf-8"))["status"], "passed")

    def test_validator_rejects_invalid_insets_and_disabled_native_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / "plan.json"
            report = root / "report.json"
            plan.write_text(json.dumps({
                "schemaVersion": "native-control-configuration-plan-1.0",
                "screens": [{"screenId": "home", "controls": [{
                    "nodeId": "home.slider", "strategy": "system-control",
                    "geometry": {"contentInsetsPt": [0, -1]},
                    "behavior": {"usesNativeStateMachine": False},
                }]}],
            }), encoding="utf-8")
            result = subprocess.run(
                ["python3", str(VALIDATE), "--plan", str(plan), "--out", str(report)],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            codes = {item["code"] for item in json.loads(report.read_text(encoding="utf-8"))["issues"]}
            self.assertEqual(codes, {
                "CONTROL_INSETS_INVALID", "NATIVE_STATE_MACHINE_DISABLED",
                "CONTROL_NORMAL_STATE_MISSING",
            })


if __name__ == "__main__":
    unittest.main()
