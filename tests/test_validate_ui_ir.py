#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_ui_ir.py"
SPEC = importlib.util.spec_from_file_location("validate_ui_ir", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def node(semantic: str, axis: str, *, parent_id: str | None = None, horizontal_overflow: bool = False) -> dict:
    return {
        "id": f"node.{semantic}",
        "parentId": parent_id,
        "semanticType": semantic,
        "layout": {
            "rect": {"x": 0, "y": 0, "width": 393, "height": 100},
            "scrollAxis": axis,
            "scrollMetrics": {
                "horizontalAllowed": axis in {"horizontal", "both"},
                "verticalAllowed": axis in {"vertical", "both"},
                "overflowsHorizontally": horizontal_overflow,
                "overflowsVertically": axis in {"vertical", "both"},
                "scrollWidth": 420 if horizontal_overflow else 393,
                "scrollHeight": 200 if axis in {"vertical", "both"} else 100,
                "clientWidth": 393,
                "clientHeight": 100,
            },
        },
        "style": {},
        "content": {},
        "state": {},
        "nativeMapping": {
            "swiftUI": "ScrollView",
            "uiKit": "UIScrollView",
            "styleStrategy": "custom-native-view",
            "confidence": 0.9,
            "rationale": ["test-contract"],
            "availability": {
                "swiftUI": {"status": "available"},
                "uiKit": {"status": "available"},
            },
        },
        "support": "native",
    }


def ir(payload_node: dict) -> dict:
    return {
        "schemaVersion": "1.2",
        "source": {},
        "target": {"uiStack": "swiftui"},
        "screens": [{
            "id": "screen",
            "rootNodeId": payload_node["id"],
            "systemChrome": {
                "statusBar": "native",
                "navigationBar": "none",
                "homeIndicator": "native",
            },
            "nodes": [payload_node],
        }],
        "states": [],
        "interactions": [],
        "assets": [],
        "motions": [],
        "visualStates": [{"id": "screen.initial"}],
        "warnings": [],
    }


class ValidateUIIRTests(unittest.TestCase):
    def test_carousel_requires_horizontal_axis(self) -> None:
        errors, _ = MODULE.validate(ir(node("carousel", "vertical")))
        self.assertTrue(any("carousel must declare" in error for error in errors))

    def test_vertical_root_reports_unexpected_horizontal_overflow(self) -> None:
        errors, warnings = MODULE.validate(ir(node("scroll", "vertical", horizontal_overflow=True)))
        self.assertEqual(errors, [])
        self.assertTrue(any("measured horizontal overflow" in warning for warning in warnings))

    def test_external_data_binding_requires_source_and_valid_state_contract(self) -> None:
        payload = node("scroll", "vertical")
        payload["dataBinding"] = {
            "sourceID": None,
            "itemIDKey": "id",
            "stateRole": "busy",
            "pagination": "offset",
            "ownership": "external",
            "requiresViewModel": True,
            "snapshotIsSampleData": True,
        }
        errors, _ = MODULE.validate(ir(payload))
        self.assertTrue(any("stateRole has invalid value" in error for error in errors))
        self.assertTrue(any("pagination has invalid value" in error for error in errors))
        self.assertTrue(any("sourceID is required" in error for error in errors))

    def test_control_visual_states_use_supported_state_names(self) -> None:
        payload = node("button", "none")
        payload["controlVisualStates"] = {"active": {"opacity": "0.8"}}
        errors, _ = MODULE.validate(ir(payload))
        self.assertTrue(any("invalid state: active" in error for error in errors))

    def test_native_control_decision_requires_system_first_contract(self) -> None:
        payload = node("button", "none")
        payload["nativeMapping"]["nativeControlDecision"] = {
            "policy": "custom-first",
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
        errors, _ = MODULE.validate(ir(payload))
        self.assertTrue(any("nativeControlDecision.policy is invalid" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
