#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IOSRuntimeCompatibilityReportTests(unittest.TestCase):
    def test_merges_runtime_evidence_and_preserves_pending_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compatibility = root / "compatibility.json"
            compatibility.write_text(json.dumps({
                "schemaVersion": "ios-compatibility-matrix-1.0",
                "profiles": [
                    {"id": "baseline-phone", "viewport": {"width": 393, "height": 852}, "orientation": "portrait", "horizontalSizeClass": "compact", "verticalSizeClass": "regular", "validation": "source-analyzed"},
                    {"id": "ipad-regular", "viewport": {"width": 834, "height": 1210}, "orientation": "portrait", "horizontalSizeClass": "regular", "verticalSizeClass": "regular", "validation": "pending-runtime-validation"},
                ],
            }), encoding="utf-8")
            runtime = root / "runtime.json"
            runtime.write_text(json.dumps({
                "schemaVersion": "responsive-ios-matrix-1.0",
                "cases": [{"id": "baseline-phone", "status": "passed", "device": "iPhone 16"}],
            }), encoding="utf-8")
            output = root / "report.json"
            result = subprocess.run([
                "python3", str(ROOT / "scripts/build_ios_runtime_compatibility_report.py"),
                "--compatibility-matrix", str(compatibility),
                "--runtime-matrix", str(runtime),
                "--require-profile", "baseline-phone",
                "--out", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            profiles = {item["id"]: item for item in report["profiles"]}
            self.assertEqual(profiles["baseline-phone"]["runtimeValidation"], "runtime-validated")
            self.assertEqual(profiles["ipad-regular"]["runtimeValidation"], "pending-runtime-validation")

    def test_required_pending_profile_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compatibility = root / "compatibility.json"
            compatibility.write_text(json.dumps({
                "schemaVersion": "ios-compatibility-matrix-1.0",
                "profiles": [{"id": "ipad-split-compact", "viewport": {"width": 507, "height": 1024}}],
            }), encoding="utf-8")
            output = root / "report.json"
            result = subprocess.run([
                "python3", str(ROOT / "scripts/build_ios_runtime_compatibility_report.py"),
                "--compatibility-matrix", str(compatibility),
                "--require-profile", "ipad-split-compact",
                "--out", str(output),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            summary = json.loads(output.read_text(encoding="utf-8"))["summary"]
            self.assertEqual(summary["requiredProfileFailures"], ["ipad-split-compact"])


if __name__ == "__main__":
    unittest.main()
