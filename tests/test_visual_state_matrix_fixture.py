#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "visual-state-matrix.html"
DISCOVERY = ROOT / "scripts" / "discover_html_routes.cjs"
VALIDATOR = ROOT / "scripts" / "validate_html_authoring_contract.py"
NODE = next(
    (
        path
        for path in Path.home().glob(".cache/codex-runtimes/*/dependencies/node/bin/node")
        if path.is_file()
    ),
    None,
)


class VisualStateMatrixFixtureTests(unittest.TestCase):
    def test_authoring_contract_is_valid(self) -> None:
        completed = subprocess.run(
            ["python3", str(VALIDATOR), str(FIXTURE)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["summary"]["errors"], 0)

    def test_route_discovery_keeps_business_page_and_deduplicates_states(self) -> None:
        if NODE is None:
            self.skipTest("Bundled Node.js is unavailable")
        node_modules = NODE.parents[1] / "node_modules"
        environment = {**os.environ, "NODE_PATH": str(node_modules)}
        probe = subprocess.run(
            [str(NODE), "-e", "require('playwright')"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            self.skipTest("Playwright is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "routes.json"
            completed = subprocess.run(
                [str(NODE), str(DISCOVERY), "--html", str(FIXTURE), "--out", str(output)],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            graph = json.loads(output.read_text(encoding="utf-8"))
            native_ids = {
                item["id"]
                for item in graph["screens"]
                if item.get("includeInNativeConversion")
            }
            self.assertEqual(native_ids, {"home", "detail"})
            by_representation = {
                item["representationScreenId"]: item
                for item in graph["visualStates"]
            }
            self.assertEqual(len(by_representation), 5)
            self.assertEqual(by_representation["home-actions"]["localEffect"], "swipe-actions")
            self.assertEqual(by_representation["home-sheet"]["presentationStyle"], "sheet")
            self.assertEqual(by_representation["home-status"]["localEffect"], "revealed-content")


if __name__ == "__main__":
    unittest.main()
