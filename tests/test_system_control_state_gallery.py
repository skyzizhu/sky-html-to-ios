#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = Path(os.environ.get(
    "HTML_TO_IOS_NODE",
    "/Users/skyzizhu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node",
))
NODE_MODULES = NODE.parent.parent / "node_modules"
FIXTURE = ROOT / "tests" / "fixtures" / "system-controls-state-gallery.html"


@unittest.skipUnless(NODE.is_file() and NODE_MODULES.is_dir(), "bundled Node/Playwright runtime unavailable")
class SystemControlStateGalleryTests(unittest.TestCase):
    def test_gallery_reaches_ui_ir_and_validated_native_state_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            render_tree = output / "render-tree.json"
            ui_ir = output / "ui-ir.json"
            plan = output / "native-control-configuration-plan.json"
            validation = output / "native-control-configuration-validation.json"
            environment = dict(os.environ)
            environment["NODE_PATH"] = str(NODE_MODULES)
            result = subprocess.run([
                str(NODE), str(ROOT / "scripts" / "extract_render_tree.cjs"),
                "--html", str(FIXTURE), "--out", str(render_tree),
                "--selector", "#app", "--width", "393", "--height", "852",
            ], text=True, capture_output=True, env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([
                "python3", str(ROOT / "scripts" / "build_ui_ir.py"), str(render_tree),
                "--out", str(ui_ir), "--screen-id", "controls", "--root-selector", "#app",
                "--ui-stack", "swiftui", "--target-width", "393", "--target-height", "852",
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([
                "python3", str(ROOT / "scripts" / "validate_ui_ir.py"), str(ui_ir),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([
                "python3", str(ROOT / "scripts" / "build_native_control_configuration_plan.py"),
                "--ir", str(ui_ir), "--out", str(plan),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            controls = {item["semantic"]: item for item in payload["screens"][0]["controls"]}
            expected = {
                "button", "text-input", "text-area", "search-input", "search-bar", "switch", "slider",
                "stepper", "segmented-control", "date-input", "wheel-picker", "color-picker", "page-control",
                "progress", "activity-indicator", "paste-control", "refresh-control", "calendar-view",
            }
            self.assertTrue(expected.issubset(controls), expected - set(controls))
            self.assertTrue(all("normal" in item["stateAppearances"] for item in controls.values()))
            self.assertIn("highlighted", controls["button"]["stateAppearances"])
            self.assertIn("editing", controls["text-input"]["stateAppearances"])
            self.assertIn("checked", controls["switch"]["stateAppearances"])
            result = subprocess.run([
                "python3", str(ROOT / "scripts" / "validate_native_control_configuration_plan.py"),
                "--plan", str(plan), "--out", str(validation),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
