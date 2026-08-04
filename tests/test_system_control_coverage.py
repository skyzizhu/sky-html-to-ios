#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_system_control_coverage.py"
sys.path.insert(0, str(ROOT / "scripts"))
from system_control_catalog import SYSTEM_CONTROLS  # noqa: E402


class SystemControlCoverageTests(unittest.TestCase):
    def run_audit(self, root: Path, symbols: dict) -> tuple[subprocess.CompletedProcess[str], dict]:
        sdk = root / "sdk.json"
        report = root / "coverage.json"
        sdk.write_text(json.dumps({
            "sdk": {"version": "26.2"},
            "minimumIOS": "16.0",
            "symbols": {"uikit": symbols},
        }), encoding="utf-8")
        result = subprocess.run([
            "python3", str(SCRIPT), "--sdk-report", str(sdk),
            "--skill-root", str(ROOT), "--out", str(report),
        ], capture_output=True, text=True)
        return result, json.loads(report.read_text(encoding="utf-8"))

    def test_catalog_covers_every_public_uicontrol_subclass_in_installed_sdk_set(self) -> None:
        expected = {
            "UIButton", "UIColorWell", "UIDatePicker", "UIPageControl", "UIPasteControl",
            "UIRefreshControl", "UISegmentedControl", "UISlider", "UIStepper", "UISwitch", "UITextField",
        }
        catalog = {item["uikit"] for item in SYSTEM_CONTROLS}
        self.assertTrue(expected.issubset(catalog))

    def test_complete_catalog_passes_and_missing_sdk_symbol_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            symbols = {item["uikit"]: {"status": "available"} for item in SYSTEM_CONTROLS}
            result, report = self.run_audit(root, symbols)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(report["summary"]["supportedCount"], len(SYSTEM_CONTROLS))

            symbols["UISwitch"] = {"status": "unavailable"}
            result, report = self.run_audit(root, symbols)
            self.assertNotEqual(result.returncode, 0)
            switch = next(item for item in report["controls"] if item["uikit"] == "UISwitch")
            self.assertEqual(switch["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
