#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "visual-benchmarks" / "suite.json"
SCRIPT = ROOT / "scripts" / "run_visual_benchmark_suite.py"
SPEC = importlib.util.spec_from_file_location("visual_benchmark", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class VisualBenchmarkSuiteTests(unittest.TestCase):
    def test_suite_has_distinct_mobile_ui_coverage(self) -> None:
        suite = MODULE.load_suite(SUITE)
        self.assertEqual(len(suite["cases"]), 3)
        coverage = {token for item in suite["cases"] for token in item["coverage"]}
        self.assertTrue({"table-view", "grid-collection", "text-field", "fixed-artboard", "responsive-root"}.issubset(coverage))

    def test_fixture_authoring_contracts_are_valid(self) -> None:
        suite = MODULE.load_suite(SUITE)
        validator = ROOT / "scripts" / "validate_html_authoring_contract.py"
        for item in suite["cases"]:
            fixture = SUITE.parent / item["html"]
            with self.subTest(case=item["id"]):
                result = subprocess.run(
                    [sys.executable, str(validator), str(fixture)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout)

    def test_case_summary_uses_latest_review_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case_dir = Path(temporary)
            report_dir = case_dir / ".html-to-ios"
            review = report_dir / "screens" / "home" / "visual-review" / "iteration-1"
            review.mkdir(parents=True)
            (report_dir / "orchestration-report.json").write_text(json.dumps({
                "status": "built-pending-visual-acceptance",
                "qualityGates": {"build": "passed", "visualDiff": "failed"},
            }), encoding="utf-8")
            (review / "review-bundle.json").write_text(json.dumps({
                "states": [{
                    "id": "initial", "required": True, "status": "failed-threshold",
                    "fidelityPercent": 91.25, "exactPixelMatch": False,
                    "report": {"mismatchRatio": 0.1, "meanAbsoluteDifference": 11, "simplePixelSimilarity": 0.95},
                }],
            }), encoding="utf-8")
            summary = MODULE.summarize_case("fixture", case_dir)
            self.assertEqual(summary["fidelityPercent"], 91.25)
            self.assertEqual(summary["buildGate"], "passed")
            self.assertEqual(summary["requiredStateCount"], 1)


if __name__ == "__main__":
    unittest.main()
