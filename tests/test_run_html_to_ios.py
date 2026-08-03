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
            result = self.invoke(workspace, ir, "--dry-run", "--app-name", "Sample App", "--ui-stack", "swiftui")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "planned")
            self.assertTrue(report["createdProject"])
            self.assertEqual(Path(report["project"]).name, "SampleApp.xcodeproj")
            self.assertEqual(report["projectGenerationDecision"]["verification"]["resolved"], "build")
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
            result = self.invoke(workspace, ir, "--dry-run", "--app-name", "Sample App", "--ui-stack", "swiftui")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(workspace.is_dir())
            self.assertEqual(json.loads(result.stdout)["status"], "planned")

    def test_new_project_requires_explicit_ui_stack_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            ir = workspace / "screen.json"
            ir.write_text("{}\n", encoding="utf-8")
            result = self.invoke(workspace, ir, "--dry-run", "--app-name", "Sample App")
            self.assertEqual(result.returncode, 2)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "needs-input")
            self.assertEqual(report["failedStage"], "select-ui-stack")
            self.assertIn("--ui-stack swiftui", report["message"])

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

    def test_safe_visual_correction_updates_plan_ir_and_requires_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source_ir = workspace / "screen" / "ui-ir.json"
            source_ir.parent.mkdir(parents=True)
            source_ir.write_text("{}\n", encoding="utf-8")
            correction_plan = workspace / "plan.json"
            correction_plan.write_text("{}\n", encoding="utf-8")
            args = SimpleNamespace(
                workspace=workspace,
                report_dir=workspace / ".html-to-ios",
                node=None,
                dry_run=False,
                html=workspace / "prototype.html",
                ir=None,
                skip_visual_baselines=False,
            )
            orchestrator = RUN_MODULE.Orchestrator(args)
            visual_plan = {
                "screenId": "home",
                "uiIR": str(source_ir),
                "reviewDirectory": str(workspace / "screen" / "visual-review"),
            }
            orchestrator.artifacts["visualReviewPlans"] = [visual_plan]

            def fake_run_command(self, stage, command, **_kwargs):
                command = [str(item) for item in command]
                if stage.startswith("apply-visual-correction-plan"):
                    out = Path(command[command.index("--out") + 1])
                    report = Path(command[command.index("--report") + 1])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text("{}\n", encoding="utf-8")
                    report.write_text(json.dumps({"summary": {"appliedCount": 1}}), encoding="utf-8")
                    return {"appliedCount": 1, "requiresRegeneration": True}
                return ""

            orchestrator.run_command = MethodType(fake_run_command, orchestrator)
            corrected = orchestrator.apply_visual_corrections({
                "screens": [{
                    "screenId": "home",
                    "uiIR": str(source_ir),
                    "correctionPlan": str(correction_plan),
                    "correctionSummary": {"nextAction": "apply-plan-and-regenerate"},
                }],
            }, 1)
            self.assertIsNotNone(corrected)
            assert corrected is not None
            self.assertEqual(corrected[0], Path(visual_plan["uiIR"]))
            self.assertIn("visual-corrections/iteration-1/ui-ir.json", str(corrected[0]))
            self.assertEqual(orchestrator.report["visualCorrectionIterations"][0]["appliedCount"], 1)
            self.assertEqual(
                orchestrator.report["qualityGates"]["visualCorrectionApplication"],
                "applied-regeneration-required",
            )

    def test_visual_review_iterations_are_isolated_and_chain_previous_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            screen = workspace / "screen"
            screen.mkdir()
            source_ir = screen / "ui-ir.json"
            source_ir.write_text("{}\n", encoding="utf-8")
            manifest = screen / "manifest.json"
            manifest.write_text("{}\n", encoding="utf-8")
            previous = screen / "previous-plan.json"
            previous.write_text("{}\n", encoding="utf-8")
            args = SimpleNamespace(
                workspace=workspace,
                report_dir=workspace / ".html-to-ios",
                node=None,
                dry_run=False,
                html=workspace / "prototype.html",
                ir=None,
                skip_visual_baselines=False,
                device="iPhone 15 Pro",
            )
            orchestrator = RUN_MODULE.Orchestrator(args)
            orchestrator.artifacts["visualReviewPlans"] = [{
                "screenId": "home",
                "uiIR": str(source_ir),
                "manifest": str(manifest),
                "htmlDirectory": str(screen / "visual-states" / "html"),
                "iosDirectory": str(screen / "visual-states" / "ios"),
                "reviewDirectory": str(screen / "visual-review"),
            }]
            observed_correction_command = []

            def fake_container(self, _project):
                return "project", workspace / "App.xcodeproj"

            def fake_run_command(self, stage, command, **_kwargs):
                normalized = [str(item) for item in command]
                if stage.startswith("capture-ios-states"):
                    out_dir = Path(normalized[normalized.index("--out-dir") + 1])
                    return {"out": str(out_dir / "captures.json")}
                if stage.startswith("review-visual-states"):
                    out_dir = Path(normalized[normalized.index("--out-dir") + 1])
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / "review-bundle.json").write_text(json.dumps({
                        "schemaVersion": "visual-review-bundle-2.0",
                        "summary": {"requiredFailures": ["initial"]},
                    }), encoding="utf-8")
                    return {}
                if stage.startswith("build-visual-correction-plan"):
                    observed_correction_command.extend(normalized)
                    out = Path(normalized[normalized.index("--out") + 1])
                    out.write_text(json.dumps({
                        "schemaVersion": "visual-correction-plan-1.0",
                        "summary": {"nextAction": "apply-plan-and-regenerate"},
                    }), encoding="utf-8")
                    return {}
                return {}

            orchestrator.choose_build_container = MethodType(fake_container, orchestrator)
            orchestrator.run_command = MethodType(fake_run_command, orchestrator)
            result = orchestrator.capture_and_review_visual_states(
                workspace / "App.xcodeproj",
                "App",
                "16.0",
                2,
                {"home": previous},
            )
            self.assertFalse(result["passed"])
            self.assertIn("iteration-2", result["screens"][0]["correctionPlan"])
            self.assertIn("--previous-plan", observed_correction_command)
            self.assertIn(str(previous), observed_correction_command)
            self.assertEqual(orchestrator.report["qualityGates"]["visualCorrectionPlan"], "automatic-correction-ready")


if __name__ == "__main__":
    unittest.main()
