#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISION_SCRIPT = ROOT / "scripts" / "build_project_generation_decision.py"
NAMING_SCRIPT = ROOT / "scripts" / "build_native_naming_plan.py"


class ProjectDecisionAndNamingTests(unittest.TestCase):
    def test_new_project_requires_stack_and_defaults_to_visual_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "decision.json"
            result = subprocess.run([
                "python3", str(DECISION_SCRIPT),
                "--project-state", "empty-no-ios-project",
                "--target", "SampleApp",
                "--verification-mode", "auto",
                "--out", str(out),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(decision["uiStack"]["requiresUserSelection"])
            self.assertIsNone(decision["uiStack"]["selected"])
            self.assertEqual(decision["verification"]["resolved"], "visual")

    def test_existing_swiftui_module_is_detected_and_waits_for_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "HomeView.swift").write_text(
                "import SwiftUI\nstruct HomeView: View { var body: some View { Text(\"Home\") } }\n",
                encoding="utf-8",
            )
            out = root / "decision.json"
            result = subprocess.run([
                "python3", str(DECISION_SCRIPT),
                "--project-state", "existing-xcode",
                "--source-root", str(root),
                "--target", "SampleApp",
                "--verification-mode", "auto",
                "--out", str(out),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(decision["language"], "swift")
            self.assertEqual(decision["uiStack"]["selected"], "swiftui")
            self.assertEqual(decision["uiStack"]["source"], "target-module-detection")
            self.assertEqual(decision["verification"]["resolved"], "ask")
            self.assertFalse(decision["verification"]["launchesApplication"])

    def test_mixed_module_requires_explicit_stack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SwiftScreen.swift").write_text("import SwiftUI\nstruct SwiftScreen: View { var body: some View { EmptyView() } }\n", encoding="utf-8")
            (root / "UIKitScreen.swift").write_text("import UIKit\nfinal class UIKitScreen: UIViewController {}\n", encoding="utf-8")
            out = root / "decision.json"
            subprocess.run([
                "python3", str(DECISION_SCRIPT), "--project-state", "existing-xcode",
                "--source-root", str(root), "--target", "Mixed", "--out", str(out),
            ], text=True, capture_output=True, check=True)
            decision = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(decision["uiStack"]["requiresUserSelection"])
            self.assertIsNone(decision["uiStack"]["selected"])

    def test_new_project_naming_defaults_to_sky(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "naming.json"
            subprocess.run([
                "python3", str(NAMING_SCRIPT), "--project-state", "empty-no-ios-project",
                "--target", "SampleApp", "--out", str(out),
            ], text=True, capture_output=True, check=True)
            naming = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(naming["prefix"], "Sky")
            self.assertEqual(naming["source"], "new-project-default")

    def test_existing_dominant_prefix_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            component_index = root / "components.json"
            component_index.write_text(json.dumps({
                "components": [
                    {"name": "ABCMainViewController"},
                    {"name": "ABCOrderView"},
                    {"name": "ABCCourseCell"},
                    {"name": "Utility"},
                ]
            }), encoding="utf-8")
            out = root / "naming.json"
            subprocess.run([
                "python3", str(NAMING_SCRIPT), "--project-state", "existing-xcode",
                "--target", "SampleApp", "--component-index", str(component_index), "--out", str(out),
            ], text=True, capture_output=True, check=True)
            naming = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(naming["prefix"], "ABC")
            self.assertEqual(naming["source"], "existing-module-dominant-prefix")

    def test_previous_generation_prefix_remains_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = root / "previous.json"
            previous.write_text(json.dumps({
                "schemaVersion": "native-naming-plan-1.0",
                "prefix": "Sky",
            }), encoding="utf-8")
            out = root / "naming.json"
            subprocess.run([
                "python3", str(NAMING_SCRIPT), "--project-state", "existing-xcode",
                "--target", "RenamedTarget", "--previous-plan", str(previous), "--out", str(out),
            ], text=True, capture_output=True, check=True)
            naming = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(naming["prefix"], "Sky")
            self.assertEqual(naming["source"], "previous-generation-plan")


if __name__ == "__main__":
    unittest.main()
