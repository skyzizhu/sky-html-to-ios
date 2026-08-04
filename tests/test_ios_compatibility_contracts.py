#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IOSCompatibilityContractTests(unittest.TestCase):
    def write_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        sdk = root / "sdk.json"
        sdk.write_text(json.dumps({
            "schemaVersion": "ios-sdk-report-1.0",
            "sdk": {"version": "26.2"},
            "minimumIOS": "16.0",
            "symbols": {
                "swiftui": {
                    "NavigationStack": {"status": "available", "introduced": "16.0"},
                    "scrollDismissesKeyboard": {"status": "available", "introduced": "16.0"},
                    "presentationDetents": {"status": "available", "introduced": "16.0"},
                    "keyframeAnimator": {"status": "requires-fallback", "introduced": "17.0"},
                },
                "uikit": {
                    "UINavigationController": {"status": "available", "introduced": "2.0"},
                    "UISheetPresentationController": {"status": "available", "introduced": "15.0"},
                    "UIView": {"status": "available", "introduced": "2.0"},
                },
            },
        }), encoding="utf-8")
        ir = root / "ir.json"
        ir.write_text(json.dumps({
            "schemaVersion": "1.2",
            "target": {"uiStack": "swiftui", "minimumIOS": "16.0", "viewportPt": {"width": 393, "height": 852}},
            "screens": [{"id": "home", "rootNodeId": "root", "nodes": [
                {"id": "root", "parentId": None, "semanticType": "container"},
                {"id": "sheet", "parentId": "root", "semanticType": "container"},
            ]}],
            "states": [{"id": "filters", "kind": "sheet", "targetNodeIds": ["sheet"]}],
            "motions": [{"id": "pulse", "sourceNodeId": "root", "keyframes": []}],
        }), encoding="utf-8")
        responsive = root / "responsive.json"
        responsive.write_text(json.dumps({
            "schemaVersion": "responsive-layout-analysis-1.0",
            "sampleWidthsPt": [320, 375, 393, 430],
            "sourceClassification": {"kind": "responsive-mobile-root"},
            "summary": {"ambiguousNodes": 2},
        }), encoding="utf-8")
        return sdk, ir, responsive

    def test_builds_version_device_and_fallback_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk, ir, responsive = self.write_fixture(root)
            fallback = root / "fallback.json"
            matrix = root / "matrix.json"
            validation = root / "validation.json"
            subprocess.run([
                "python3", str(ROOT / "scripts/build_native_api_fallback_plan.py"),
                "--sdk-report", str(sdk), "--ir", str(ir), "--ui-stack", "swiftui",
                "--minimum-ios", "16.0", "--out", str(fallback),
            ], check=True, capture_output=True, text=True)
            subprocess.run([
                "python3", str(ROOT / "scripts/build_ios_compatibility_matrix.py"),
                "--sdk-report", str(sdk), "--api-fallback-plan", str(fallback),
                "--ir", str(ir), "--responsive-analysis", str(responsive),
                "--ui-stack", "swiftui", "--minimum-ios", "16.0", "--out", str(matrix),
            ], check=True, capture_output=True, text=True)
            subprocess.run([
                "python3", str(ROOT / "scripts/validate_ios_compatibility_contracts.py"),
                "--matrix", str(matrix), "--api-fallback-plan", str(fallback), "--out", str(validation),
            ], check=True, capture_output=True, text=True)

            fallback_payload = json.loads(fallback.read_text(encoding="utf-8"))
            decisions = {item["id"]: item for item in fallback_payload["capabilities"]}
            self.assertEqual(decisions["sheet-presentation"]["activeResolution"], "system-native")
            self.assertEqual(decisions["keyframe-animation"]["activeResolution"], "fallback")
            self.assertEqual(
                decisions["keyframe-animation"]["stacks"]["swiftui"]["fallback"],
                "timeline-sampled-animation",
            )
            matrix_payload = json.loads(matrix.read_text(encoding="utf-8"))
            profiles = {item["id"]: item for item in matrix_payload["profiles"]}
            self.assertEqual(profiles["baseline-phone"]["validation"], "source-analyzed")
            self.assertEqual(profiles["phone-375"]["validation"], "source-analyzed")
            self.assertEqual(profiles["landscape-phone"]["orientation"], "landscape")
            self.assertEqual(profiles["ipad-regular"]["validation"], "pending-runtime-validation")
            self.assertFalse(matrix_payload["layoutPolicy"]["wholePageScalingAllowed"])
            self.assertEqual(json.loads(validation.read_text(encoding="utf-8"))["status"], "passed")

    def test_api_plan_blocks_a_target_below_generated_runtime_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk, ir, _ = self.write_fixture(root)
            sdk_payload = json.loads(sdk.read_text(encoding="utf-8"))
            sdk_payload["minimumIOS"] = "15.0"
            sdk.write_text(json.dumps(sdk_payload), encoding="utf-8")
            fallback = root / "fallback.json"
            result = subprocess.run([
                "python3", str(ROOT / "scripts/build_native_api_fallback_plan.py"),
                "--sdk-report", str(sdk), "--ir", str(ir), "--ui-stack", "swiftui",
                "--minimum-ios", "15.0", "--out", str(fallback),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            summary = json.loads(fallback.read_text(encoding="utf-8"))["summary"]
            self.assertIn("navigation-container", summary["blockedCapabilityIDs"])


if __name__ == "__main__":
    unittest.main()
