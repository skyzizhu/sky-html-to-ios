#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_html_authoring_contract.py"


class HTMLAuthoringContractTests(unittest.TestCase):
    def run_validator(self, html: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "prototype.html"
            source.write_text(html, encoding="utf-8")
            result = subprocess.run(["python3", str(SCRIPT), str(source)], text=True, capture_output=True, check=False)
            return result, json.loads(result.stdout)

    def test_structured_contract_passes_and_resolves_screen_target(self) -> None:
        result, report = self.run_validator("""
        <main data-ios-app-root>
          <section data-ios-screen="home" data-ios-module="main-shell" data-ios-screen-initial>
            <button data-ios-node-id="home.open" data-ios-component="button" data-ios-action="push" data-ios-target="detail">Open</button>
          </section>
          <section data-ios-screen="detail"><button data-ios-action="pop">Back</button></section>
        </main>
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["level"], "L1-structured")
        self.assertEqual(report["summary"]["errors"], 0)
        self.assertEqual(report["screens"][0]["moduleId"], "main-shell")

    def test_invalid_module_id_fails(self) -> None:
        result, report = self.run_validator('''
        <main data-ios-app-root>
          <section data-ios-screen="home" data-ios-module="Home Pages"></section>
        </main>
        ''')
        self.assertEqual(result.returncode, 1)
        self.assertIn("INVALID_MODULE_ID", {item["code"] for item in report["issues"]})

    def test_duplicate_screen_and_unknown_target_fail(self) -> None:
        result, report = self.run_validator("""
        <main data-ios-app-root>
          <section data-ios-screen="home"><button data-ios-action="push" data-ios-target="missing">Open</button></section>
          <section data-ios-screen="home"></section>
        </main>
        """)
        self.assertEqual(result.returncode, 1)
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("DUPLICATE_SCREEN_ID", codes)
        self.assertIn("UNKNOWN_ACTION_TARGET", codes)

    def test_plain_html_remains_valid_at_inference_level(self) -> None:
        result, report = self.run_validator("<html><body><button>Continue</button></body></html>")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report["level"], "L0-inferred")
        self.assertEqual(report["summary"]["warnings"], 2)


if __name__ == "__main__":
    unittest.main()
