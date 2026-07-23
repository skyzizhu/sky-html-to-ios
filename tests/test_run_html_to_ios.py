#!/usr/bin/env python3

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_html_to_ios.py"
SPEC = importlib.util.spec_from_file_location("run_html_to_ios", SCRIPT)
assert SPEC and SPEC.loader
RUN_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUN_MODULE
SPEC.loader.exec_module(RUN_MODULE)


class RunHTMLToIOSTests(unittest.TestCase):
    def invoke(self, workspace: Path, ir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), "--workspace", str(workspace), "--ir", str(ir), *extra],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_empty_workspace_dry_run_plans_project_creation_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            ir = workspace / "screen.json"
            ir.write_text("{}\n", encoding="utf-8")
            result = self.invoke(workspace, ir, "--dry-run", "--app-name", "Sample App")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "planned")
            self.assertTrue(report["createdProject"])
            self.assertEqual(Path(report["project"]).name, "SampleApp.xcodeproj")
            self.assertEqual(report["qualityGates"]["uiIRValidation"], "pending")
            self.assertEqual(report["qualityGates"]["htmlVisualBaselines"], "skipped")
            self.assertFalse(any(workspace.glob("*.xcodeproj")))
            self.assertFalse((workspace / ".html-to-ios").exists())

    def test_missing_workspace_is_created_by_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "new-workspace"
            ir = root / "screen.json"
            ir.write_text("{}\n", encoding="utf-8")
            result = self.invoke(workspace, ir, "--dry-run", "--app-name", "Sample App")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(workspace.is_dir())
            self.assertEqual(json.loads(result.stdout)["status"], "planned")

    def test_multiple_projects_require_explicit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "One.xcodeproj").mkdir()
            (workspace / "Two.xcodeproj").mkdir()
            ir = workspace / "screen.json"
            ir.write_text("{}\n", encoding="utf-8")
            result = self.invoke(workspace, ir, "--dry-run")
            self.assertEqual(result.returncode, 2)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "needs-input")
            self.assertEqual(report["failedStage"], "select-project")

    def test_swift_package_does_not_get_an_implicit_host_app(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "Package.swift").write_text("// swift-tools-version: 5.9\n", encoding="utf-8")
            ir = workspace / "screen.json"
            ir.write_text("{}\n", encoding="utf-8")
            result = self.invoke(workspace, ir, "--dry-run")
            self.assertEqual(result.returncode, 2)
            report = json.loads(result.stdout)
            self.assertEqual(report["failedStage"], "create-project")
            self.assertIn("--create-package-host-app", report["message"])

    def test_invalid_ir_is_rejected_before_project_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            ir = workspace / "invalid.json"
            ir.write_text("{}\n", encoding="utf-8")
            result = self.invoke(workspace, ir)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report["failedStage"], "validate-ui-ir-1")
            self.assertFalse(any(workspace.glob("*.xcodeproj")))

    def test_explicit_interaction_overrides_are_not_replaced_by_generated_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            html = workspace / "prototype.html"
            html.write_text("<html></html>\n", encoding="utf-8")
            overrides = workspace / "confirmed-overrides.json"
            confirmed = {
                "schemaVersion": "html-to-ios-overrides-1.0",
                "resolutions": [{"id": "ambiguity-1", "resolution": "push"}],
            }
            overrides.write_text(json.dumps(confirmed), encoding="utf-8")
            args = SimpleNamespace(
                workspace=workspace,
                report_dir=workspace / ".html-to-ios",
                node=Path("/bin/echo"),
                dry_run=False,
                html=html,
                ir=None,
                skip_visual_baselines=False,
                interaction_overrides=overrides,
            )
            orchestrator = RUN_MODULE.Orchestrator(args)

            def fake_run_command(self, stage, command, **_kwargs):
                command = [Path(item) if isinstance(item, Path) else item for item in command]
                if stage == "validate-html-authoring-contract":
                    out = Path(command[command.index("--out") + 1])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps({"status": "passed-with-warnings", "level": "L0-inferred"}), encoding="utf-8")
                elif stage == "discover-html-routes":
                    out = Path(command[command.index("--out") + 1])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps({"screens": [{"id": "home"}]}), encoding="utf-8")
                elif stage == "discover-html-interactions":
                    out = Path(command[command.index("--out") + 1])
                    draft = Path(command[command.index("--overrides-out") + 1])
                    out.write_text(json.dumps({"unresolved": [{"id": "ambiguity-1"}]}), encoding="utf-8")
                    draft.write_text(json.dumps({"resolutions": []}), encoding="utf-8")
                return {}

            orchestrator.run_command = MethodType(fake_run_command, orchestrator)
            result = orchestrator.discover_html_contracts()
            self.assertEqual(json.loads(overrides.read_text(encoding="utf-8")), confirmed)
            self.assertNotEqual(result[3], Path(orchestrator.artifacts["generatedInteractionOverridesDraft"]))
            self.assertTrue(Path(orchestrator.artifacts["generatedInteractionOverridesDraft"]).is_file())


if __name__ == "__main__":
    unittest.main()
