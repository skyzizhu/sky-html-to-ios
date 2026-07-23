#!/usr/bin/env python3
"""Orchestrate project decisions, HTML/UI IR conversion, Xcode integration, and optional verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "html-to-ios-orchestration-1.4"
PROJECT_MARKER_NAME = ".html-to-ios-created-project.json"
SKIP_PARTS = {".git", ".build", "build", "DerivedData", "Pods", "Carthage", "node_modules", "xcuserdata"}


class OrchestrationError(RuntimeError):
    def __init__(self, stage: str, message: str, status: str = "failed") -> None:
        super().__init__(message)
        self.stage = stage
        self.status = status


@dataclass
class CommandResult:
    stage: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    log: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Agent workspace root; defaults to cwd")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--html", type=Path, help="Local HTML entry; runs discovery and UI IR generation")
    source.add_argument("--ir", action="append", type=Path, help="Resolved UI IR; repeat for multiple screens")
    parser.add_argument("--interaction-overrides", type=Path)
    parser.add_argument("--project", type=Path, help="Explicit .xcodeproj when multiple projects exist")
    parser.add_argument("--xcode-workspace", type=Path, help="Optional .xcworkspace used for xcodebuild")
    parser.add_argument("--target")
    parser.add_argument("--scheme")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--ui-stack", choices=("swiftui", "uikit"))
    parser.add_argument("--name-prefix", help="Generated page/type prefix; inferred for existing targets and defaults to Sky for new projects")
    parser.add_argument("--minimum-ios", default=None)
    parser.add_argument("--app-name")
    parser.add_argument("--bundle-id")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--width", type=int, default=393)
    parser.add_argument("--height", type=int, default=852)
    parser.add_argument("--device", default="iPhone 15 Pro")
    parser.add_argument("--appearance", choices=("light", "dark"), default="light")
    parser.add_argument(
        "--responsive-widths",
        default="320,375,393,430",
        help="Comma-separated logical widths used to infer native layout constraints",
    )
    parser.add_argument(
        "--skip-visual-baselines",
        action="store_true",
        help="Diagnostic escape hatch: skip visual-state manifests and HTML captures",
    )
    parser.add_argument("--no-create", action="store_true", help="Do not create an App when no Xcode project exists")
    parser.add_argument("--create-package-host-app", action="store_true", help="Allow creating an App beside a Swift Package")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--verification-mode",
        choices=("auto", "ask", "build", "visual", "none"),
        default="auto",
        help="auto uses visual verification for a newly created project and asks before building an existing project",
    )
    parser.add_argument("--dry-run", action="store_true", help="Inspect and print decisions without modifying files")
    return parser.parse_args()


def json_from_output(value: str) -> Any:
    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError("Command did not return a JSON object")


def is_skipped(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def discover(workspace: Path, pattern: str) -> list[Path]:
    return sorted(path.resolve() for path in workspace.rglob(pattern) if not is_skipped(path))


def resolve_input(path: Path | None, workspace: Path) -> Path | None:
    if path is None:
        return None
    candidate = path.expanduser()
    return (workspace / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()


def safe_app_name(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    result = "".join(word[:1].upper() + word[1:] for word in words)
    if not result:
        result = "HTMLToIOSApp"
    if result[0].isdigit():
        result = "App" + result
    return result[:48]


def version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value)) or (0,)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_node(explicit: Path | None) -> tuple[Path | None, dict[str, str]]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit.expanduser())
    env_node = os.environ.get("CODEX_NODE")
    if env_node:
        candidates.append(Path(env_node).expanduser())
    found = shutil.which("node")
    if found:
        candidates.append(Path(found))
    candidates.extend(Path.home().glob(".cache/codex-runtimes/*/dependencies/node/bin/node"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            environment = os.environ.copy()
            node_modules = candidate.parent.parent / "node_modules"
            if node_modules.is_dir() and not environment.get("NODE_PATH"):
                environment["NODE_PATH"] = str(node_modules)
            return candidate.resolve(), environment
    return None, os.environ.copy()


class Orchestrator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scripts = Path(__file__).resolve().parent
        self.workspace = args.workspace.expanduser().resolve()
        self.report_dir = resolve_input(args.report_dir, self.workspace) or self.workspace / ".html-to-ios"
        self.report_path = self.report_dir / "orchestration-report.json"
        self.commands: list[dict[str, Any]] = []
        self.artifacts: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.created_project = False
        self.managed_project = False
        self.managed_project_info: dict[str, Any] = {}
        self.entry_wired = False
        self.node, self.node_environment = find_node(args.node)
        self.report: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "running",
            "workspace": str(self.workspace),
            "inputMode": "html" if args.html else "ui-ir",
            "createdProject": False,
            "managedProject": False,
            "entryWired": False,
            "commands": self.commands,
            "artifacts": self.artifacts,
            "warnings": self.warnings,
            "qualityGates": {
                "htmlAuthoringContract": "pending" if args.html else "not-applicable-with-supplied-ir",
                "uiIRValidation": "pending",
                "textCalibration": "pending" if args.html else "not-applicable-with-supplied-ir",
                "responsiveAnalysis": "pending" if args.html else "not-applicable-with-supplied-ir",
                "scrollBehaviorAnalysis": "pending" if args.html else "not-applicable-with-supplied-ir",
                "nativeArchitecturePlan": "pending",
                "projectGenerationDecision": "pending",
                "nativeNamingPlan": "pending",
                "htmlVisualBaselines": "pending" if args.html and not args.skip_visual_baselines else "skipped",
                "build": "pending",
                "iosStateCapture": "pending-agent-runtime",
                "visualDiff": "pending-agent-runtime",
            },
        }

    def write_report(self) -> None:
        if self.args.dry_run:
            return
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run_command(
        self,
        stage: str,
        command: list[str | Path],
        *,
        environment: dict[str, str] | None = None,
        parse_json: bool = True,
    ) -> Any:
        normalized = [str(item) for item in command]
        if self.args.dry_run:
            self.commands.append({"stage": stage, "command": normalized, "status": "planned"})
            return {}
        self.report_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            normalized,
            cwd=self.workspace,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        log_path = self.report_dir / f"{len(self.commands) + 1:02d}-{safe_app_name(stage).lower()}.log"
        log_path.write_text(
            "$ " + " ".join(shlex.quote(part) for part in normalized) + "\n\n" + result.stdout + result.stderr,
            encoding="utf-8",
        )
        record = {
            "stage": stage,
            "command": normalized,
            "returnCode": result.returncode,
            "status": "completed" if result.returncode == 0 else "failed",
            "log": str(log_path),
        }
        self.commands.append(record)
        self.write_report()
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise OrchestrationError(stage, detail[-1] if detail else f"Command failed with {result.returncode}")
        if not parse_json:
            return result.stdout
        try:
            return json_from_output(result.stdout)
        except ValueError as error:
            raise OrchestrationError(stage, str(error)) from error

    def inspect_workspace(self) -> dict[str, Any]:
        report_path = self.report_dir / "ios-project-report.json"
        self.run_command(
            "inspect-ios-project",
            [sys.executable, self.scripts / "inspect_ios_project.py", self.workspace, "--out", report_path],
            parse_json=False,
        )
        if not self.args.dry_run:
            self.artifacts["iosProjectReport"] = str(report_path)
            return json.loads(report_path.read_text(encoding="utf-8"))
        projects = discover(self.workspace, "*.xcodeproj")
        packages = discover(self.workspace, "Package.swift")
        return {
            "projectState": "existing-xcode" if projects else "swift-package-only" if packages else "empty-no-ios-project",
            "projects": [{"path": str(path.relative_to(self.workspace))} for path in projects],
            "swiftPackages": [str(path.relative_to(self.workspace)) for path in packages],
            "sourceSignals": {"recommendedUIStack": "unknown-new-project-default-swiftui"},
            "deploymentTargets": [],
        }

    def choose_minimum_ios(self, inspection: dict[str, Any]) -> str:
        if self.args.minimum_ios:
            return self.args.minimum_ios
        targets = [str(item).strip('"') for item in inspection.get("deploymentTargets") or []]
        return sorted(targets, key=version_key)[-1] if targets else "16.0"

    def choose_project(self, inspection: dict[str, Any], ui_stack: str, minimum_ios: str) -> tuple[Path, Path]:
        explicit = resolve_input(self.args.project, self.workspace)
        project_candidates = discover(self.workspace, "*.xcodeproj")
        if explicit:
            if explicit.suffix != ".xcodeproj" or not explicit.is_dir():
                raise OrchestrationError("select-project", f"Invalid --project: {explicit}")
            if explicit not in project_candidates:
                project_candidates.append(explicit)
            project = explicit
        elif len(project_candidates) == 1:
            project = project_candidates[0]
        elif len(project_candidates) > 1:
            relative = [str(path.relative_to(self.workspace)) for path in project_candidates]
            raise OrchestrationError(
                "select-project",
                "Multiple Xcode projects found; pass --project. Candidates: " + ", ".join(relative),
                "needs-input",
            )
        else:
            state = inspection.get("projectState")
            if self.args.no_create:
                raise OrchestrationError("create-project", "No Xcode project found and --no-create was supplied.", "needs-input")
            if state == "swift-package-only" and not self.args.create_package_host_app:
                raise OrchestrationError(
                    "create-project",
                    "A Swift Package exists without an App project. Pass --create-package-host-app after confirming an App host is required.",
                    "needs-input",
                )
            app_name = safe_app_name(self.args.app_name or self.workspace.name)
            command: list[str | Path] = [
                "ruby", self.scripts / "create_ios_project.rb",
                "--root", self.workspace,
                "--name", app_name,
                "--ui-stack", ui_stack,
                "--minimum-ios", minimum_ios,
            ]
            if self.args.bundle_id:
                command.extend(["--bundle-id", self.args.bundle_id])
            created = self.run_command("create-ios-project", command)
            if self.args.dry_run:
                project = self.workspace / f"{app_name}.xcodeproj"
                source_root = self.workspace / app_name
            else:
                project = Path(str(created["project"])).resolve()
                source_root = Path(str(created["sourceDirectory"])).resolve()
            self.created_project = True
            self.managed_project = True
            self.report["createdProject"] = True
            self.report["managedProject"] = True
            if not self.args.dry_run:
                entry_name = "ContentView.swift" if ui_stack == "swiftui" else "AppDelegate.swift"
                entry_file = source_root / entry_name
                marker = {
                    "schemaVersion": "html-to-ios-created-project-1.0",
                    "project": str(project),
                    "sourceRoot": str(source_root),
                    "uiStack": ui_stack,
                    "minimumIOS": minimum_ios,
                    "entryFile": str(entry_file),
                    "entryTemplateSha256": file_sha256(entry_file),
                }
                marker_path = self.workspace / PROJECT_MARKER_NAME
                marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self.managed_project_info = marker
                self.artifacts["createdProjectMarker"] = str(marker_path)
            return project, source_root

        source_root = resolve_input(self.args.source_root, self.workspace)
        if source_root is None:
            target_hint = self.args.target or project.stem
            conventional = project.parent / target_hint
            source_root = conventional if conventional.is_dir() else project.parent
        marker_path = self.workspace / PROJECT_MARKER_NAME
        if marker_path.is_file():
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                if Path(str(marker.get("project"))).resolve() == project.resolve():
                    self.managed_project = True
                    self.report["managedProject"] = True
                    self.managed_project_info = marker
                    self.artifacts["createdProjectMarker"] = str(marker_path)
            except (OSError, ValueError, json.JSONDecodeError):
                self.warnings.append(f"Ignoring invalid project ownership marker: {marker_path}")
        return project, source_root

    def xcode_listing(self, project: Path) -> dict[str, Any]:
        if self.args.dry_run and not project.exists():
            name = safe_app_name(self.args.app_name or self.workspace.name)
            return {"project": {"targets": [name], "schemes": [name]}}
        return self.run_command("list-xcode-project", ["xcodebuild", "-list", "-json", "-project", project])

    def choose_target_and_scheme(self, project: Path) -> tuple[str, str]:
        listing = self.xcode_listing(project)
        if not isinstance(listing, dict):
            raise OrchestrationError("list-xcode-project", "xcodebuild returned an unexpected project listing.")
        container = listing.get("project") or {}
        targets = [str(item) for item in container.get("targets") or []]
        schemes = [str(item) for item in container.get("schemes") or []]
        target = self.args.target
        if target and targets and target not in targets:
            raise OrchestrationError("select-target", f"Target {target!r} not found. Candidates: {', '.join(targets)}", "needs-input")
        if not target:
            app_like = [item for item in targets if not re.search(r"(?:Tests|UITests)$", item, re.IGNORECASE)]
            matching = [item for item in app_like if item.lower() == project.stem.lower()]
            if len(matching) == 1:
                target = matching[0]
            elif len(app_like) == 1:
                target = app_like[0]
            else:
                raise OrchestrationError(
                    "select-target",
                    "Cannot choose a unique App target; pass --target. Candidates: " + ", ".join(targets),
                    "needs-input",
                )
        scheme = self.args.scheme
        if scheme and schemes and scheme not in schemes:
            raise OrchestrationError("select-scheme", f"Scheme {scheme!r} not found. Candidates: {', '.join(schemes)}", "needs-input")
        if not scheme:
            if target in schemes:
                scheme = target
            elif len(schemes) == 1:
                scheme = schemes[0]
            else:
                raise OrchestrationError(
                    "select-scheme",
                    "Cannot choose a unique shared scheme; pass --scheme. Candidates: " + ", ".join(schemes),
                    "needs-input",
                )
        return target, scheme

    def target_minimum_ios(self, project: Path, target: str, fallback: str) -> str:
        if self.args.minimum_ios:
            return self.args.minimum_ios
        managed_minimum = self.managed_project_info.get("minimumIOS")
        if managed_minimum:
            return str(managed_minimum)
        if self.args.dry_run and not project.exists():
            return fallback
        settings = self.run_command(
            "read-target-build-settings",
            ["xcodebuild", "-showBuildSettings", "-json", "-project", project, "-target", target],
        )
        if isinstance(settings, list) and settings:
            build_settings = settings[0].get("buildSettings") or {}
            value = build_settings.get("IPHONEOS_DEPLOYMENT_TARGET")
            if value:
                return str(value)
        self.warnings.append(f"Could not read IPHONEOS_DEPLOYMENT_TARGET for {target}; using {fallback}.")
        return fallback

    def choose_build_container(self, project: Path) -> tuple[str, Path]:
        explicit = resolve_input(self.args.xcode_workspace, self.workspace)
        workspaces = discover(self.workspace, "*.xcworkspace")
        if explicit:
            if not explicit.is_dir():
                raise OrchestrationError("select-workspace", f"Invalid --xcode-workspace: {explicit}")
            return "workspace", explicit
        local = [path for path in workspaces if path.parent == project.parent]
        if len(local) == 1:
            return "workspace", local[0]
        if len(local) > 1:
            self.warnings.append("Multiple Xcode workspaces found beside the project; building the project directly. Pass --xcode-workspace if required.")
        return "project", project

    def build_project_generation_decision(
        self,
        project_state: str,
        source_root: Path | None,
        target: str | None,
    ) -> dict[str, Any]:
        verification_mode = "none" if self.args.skip_build else self.args.verification_mode
        if self.args.dry_run:
            creating = project_state in {"empty-no-ios-project", "swift-package-only"}
            selected = self.args.ui_stack
            recommendation = "unknown"
            if source_root:
                swiftui = 0
                uikit = 0
                for path in source_root.rglob("*.swift"):
                    if is_skipped(path):
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")[:300_000]
                    swiftui += len(re.findall(r"(?m)^\s*import\s+SwiftUI\b", text))
                    uikit += len(re.findall(r"(?m)^\s*import\s+UIKit\b", text))
                if swiftui > uikit * 1.35:
                    recommendation = "swiftui"
                elif uikit > swiftui * 1.35:
                    recommendation = "uikit"
                if selected is None and recommendation in {"swiftui", "uikit"}:
                    selected = recommendation
            resolved_verification = ("visual" if creating else "ask") if verification_mode == "auto" else verification_mode
            decision = {
                "schemaVersion": "project-generation-decision-1.0",
                "projectState": project_state,
                "target": target,
                "sourceRoot": str(source_root) if source_root else None,
                "uiStack": {
                    "selected": selected,
                    "requiresUserSelection": selected is None,
                    "source": "explicit-request" if self.args.ui_stack else "dry-run-module-detection" if selected else "user-selection-required",
                },
                "verification": {"requested": verification_mode, "resolved": resolved_verification},
            }
            self.report["projectGenerationDecision"] = decision
            self.report["qualityGates"]["projectGenerationDecision"] = (
                "needs-user-selection" if decision["uiStack"]["requiresUserSelection"] else "planned"
            )
            return decision

        decision_path = self.report_dir / "project-generation-decision.json"
        command: list[str | Path] = [
            sys.executable, self.scripts / "build_project_generation_decision.py",
            "--project-state", project_state,
            "--verification-mode", verification_mode,
            "--out", decision_path,
        ]
        if source_root:
            command.extend(["--source-root", source_root])
        if target:
            command.extend(["--target", target])
        if self.args.ui_stack:
            command.extend(["--requested-ui-stack", self.args.ui_stack])
        self.run_command("build-project-generation-decision", command)
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        self.artifacts["projectGenerationDecision"] = str(decision_path)
        self.report["projectGenerationDecision"] = decision
        self.report["qualityGates"]["projectGenerationDecision"] = (
            "needs-user-selection" if (decision.get("uiStack") or {}).get("requiresUserSelection") else "passed"
        )
        return decision

    def inspect_sdk_and_components(self, source_root: Path, minimum_ios: str) -> tuple[Path, Path]:
        component_report = self.report_dir / "ios-component-index.json"
        self.run_command(
            "discover-ios-components",
            [sys.executable, self.scripts / "discover_ios_components.py", source_root, "--out", component_report],
            parse_json=False,
        )
        sdk_report = self.report_dir / "ios-sdk-report.json"
        self.run_command(
            "inspect-ios-sdk",
            [sys.executable, self.scripts / "inspect_ios_sdk.py", "--minimum-ios", minimum_ios, "--out", sdk_report],
            parse_json=False,
        )
        self.artifacts["componentIndex"] = str(component_report)
        self.artifacts["sdkReport"] = str(sdk_report)
        return sdk_report, component_report

    def build_native_naming_plan(
        self,
        project_state: str,
        target: str,
        component_report: Path,
    ) -> Path:
        plan = self.report_dir / "native-naming-plan.json"
        previous_plan_exists = plan.is_file()
        command: list[str | Path] = [
            sys.executable, self.scripts / "build_native_naming_plan.py",
            "--project-state", project_state,
            "--target", target,
            "--component-index", component_report,
            "--out", plan,
        ]
        if self.args.name_prefix:
            command.extend(["--name-prefix", self.args.name_prefix])
        elif previous_plan_exists:
            command.extend(["--previous-plan", plan])
        self.run_command("build-native-naming-plan", command)
        self.artifacts["nativeNamingPlan"] = str(plan)
        self.report["nativeNamingPlan"] = json.loads(plan.read_text(encoding="utf-8"))
        self.report["qualityGates"]["nativeNamingPlan"] = "passed"
        return plan

    def unresolved_interactions(self, graph: dict[str, Any], overrides_path: Path | None) -> list[str]:
        resolved_ids: set[str] = set()
        if overrides_path and overrides_path.is_file():
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
            for item in overrides.get("resolutions") or []:
                item_id = item.get("id") or item.get("ambiguityId")
                resolution = item.get("resolution") or item.get("nativeAction")
                if item_id and resolution:
                    resolved_ids.add(str(item_id))
        unresolved = []
        for item in graph.get("unresolved") or []:
            item_id = str(item.get("id") or item.get("ambiguityId") or "unknown")
            if item_id not in resolved_ids:
                unresolved.append(item_id)
        return unresolved

    def discover_html_contracts(self) -> tuple[Path, Path, Path, Path, list[dict[str, Any]]]:
        html = resolve_input(self.args.html, self.workspace)
        if not html or not html.is_file():
            raise OrchestrationError("validate-html", f"HTML file does not exist: {html}")
        contract_report = self.report_dir / "html-authoring-contract-report.json"
        self.run_command(
            "validate-html-authoring-contract",
            [
                sys.executable,
                self.scripts / "validate_html_authoring_contract.py",
                html,
                "--out",
                contract_report,
            ],
            parse_json=False,
        )
        self.artifacts["htmlAuthoringContractReport"] = str(contract_report)
        if not self.args.dry_run:
            contract_data = json.loads(contract_report.read_text(encoding="utf-8"))
            self.report["htmlAuthoringContractLevel"] = contract_data.get("level")
            self.report["qualityGates"]["htmlAuthoringContract"] = contract_data.get("status", "passed")
        if not self.node:
            raise OrchestrationError("locate-node", "Node.js with Playwright is required for HTML extraction.", "needs-input")
        route_graph = self.report_dir / "html-route-graph.json"
        self.run_command(
            "discover-html-routes",
            [self.node, self.scripts / "discover_html_routes.cjs", "--html", html, "--out", route_graph],
            environment=self.node_environment,
        )
        interaction_graph = self.report_dir / "interaction-state-graph.json"
        explicit_overrides = resolve_input(self.args.interaction_overrides, self.workspace)
        generated_overrides = self.report_dir / (
            "html-to-ios.overrides.generated.json" if explicit_overrides else "html-to-ios.overrides.json"
        )
        self.run_command(
            "discover-html-interactions",
            [
                self.node, self.scripts / "discover_html_interactions.cjs",
                "--html", html,
                "--route-graph", route_graph,
                "--out", interaction_graph,
                "--overrides-out", generated_overrides,
            ],
            environment=self.node_environment,
        )
        overrides = explicit_overrides or generated_overrides
        if explicit_overrides and not explicit_overrides.is_file():
            raise OrchestrationError("validate-interaction-overrides", f"Interaction overrides do not exist: {explicit_overrides}")
        self.run_command(
            "validate-interaction-graph",
            [sys.executable, self.scripts / "validate_interaction_graph.py", interaction_graph, "--overrides", overrides],
            parse_json=False,
        )
        graph = json.loads(interaction_graph.read_text(encoding="utf-8")) if not self.args.dry_run else {}
        unresolved = self.unresolved_interactions(graph, overrides)
        self.artifacts.update({
            "routeGraph": str(route_graph),
            "interactionGraph": str(interaction_graph),
            "interactionOverrides": str(overrides),
        })
        if explicit_overrides:
            self.artifacts["generatedInteractionOverridesDraft"] = str(generated_overrides)
        if unresolved:
            self.report["unresolvedInteractions"] = unresolved
            raise OrchestrationError(
                "resolve-interactions",
                "Interaction ownership requires resolution: " + ", ".join(unresolved),
                "needs-input",
            )
        route_data = json.loads(route_graph.read_text(encoding="utf-8"))
        screens = [screen for screen in route_data.get("screens") or [] if screen.get("includeInNativeConversion", True)]
        if not screens:
            raise OrchestrationError("discover-html-routes", "No native-conversion screens were discovered.")
        return html, route_graph, interaction_graph, overrides, screens

    def build_irs_from_html(
        self,
        ui_stack: str,
        minimum_ios: str,
        sdk_report: Path,
        contracts: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    ) -> list[Path]:
        html, route_graph, interaction_graph, overrides, screens = contracts
        ir_paths: list[Path] = []
        text_calibrations: list[str] = []
        responsive_analyses: list[str] = []
        scroll_behavior_analyses: list[str] = []
        visual_manifests: list[str] = []
        html_state_captures: list[str] = []
        visual_review_plans: list[dict[str, Any]] = []
        self.artifacts["uiIRs"] = []
        self.artifacts["textCalibrations"] = text_calibrations
        self.artifacts["responsiveAnalyses"] = responsive_analyses
        self.artifacts["scrollBehaviorAnalyses"] = scroll_behavior_analyses
        if not self.args.skip_visual_baselines:
            self.artifacts["visualStateManifests"] = visual_manifests
            self.artifacts["htmlStateCaptures"] = html_state_captures
            self.artifacts["visualReviewPlans"] = visual_review_plans
        screen_root = self.report_dir / "screens"
        for screen in screens:
            screen_id = str(screen.get("id") or f"screen-{len(ir_paths) + 1}")
            screen_dir = screen_root / safe_app_name(screen_id).lower()
            render_tree = screen_dir / "render-tree.json"
            screenshot = screen_dir / "html-baseline.png"
            extract: list[str | Path] = [
                self.node, self.scripts / "extract_render_tree.cjs",
                "--html", html,
                "--out", render_tree,
                "--screenshot", screenshot,
                "--width", str(self.args.width),
                "--height", str(self.args.height),
            ]
            container_selector = screen.get("containerSelector")
            if container_selector:
                extract.extend(["--selector", str(container_selector)])
            activation = screen.get("activation") or {}
            selectors = activation.get("selectors") or []
            if activation.get("type") == "click" and selectors:
                extract.extend(["--activate-selector", str(selectors[0])])
            self.run_command(f"extract-{screen_id}", extract, environment=self.node_environment)
            ir_path = screen_dir / "ui-ir.json"
            build_command: list[str | Path] = [
                sys.executable, self.scripts / "build_ui_ir.py", render_tree,
                "--out", ir_path,
                "--screen-id", screen_id,
                "--screen-name", str(screen.get("title") or screen_id),
                "--ui-stack", ui_stack,
                "--route-graph", route_graph,
                "--interaction-graph", interaction_graph,
                "--interaction-overrides", overrides,
                "--sdk-report", sdk_report,
                "--minimum-ios", minimum_ios,
                "--target-width", str(self.args.width),
                "--target-height", str(self.args.height),
                "--device", self.args.device,
                "--appearance", self.args.appearance,
            ]
            self.run_command(f"build-ui-ir-{screen_id}", build_command)
            self.run_command(
                f"validate-ui-ir-{screen_id}",
                [sys.executable, self.scripts / "validate_ui_ir.py", ir_path],
                parse_json=False,
            )
            ir_paths.append(ir_path)
            self.artifacts["uiIRs"].append(str(ir_path))
            self.report["qualityGates"]["uiIRValidation"] = "passed-for-processed-screens"

            text_calibration = screen_dir / "text-calibration.json"
            self.run_command(
                f"calibrate-text-{screen_id}",
                [
                    sys.executable,
                    self.scripts / "build_text_calibration.py",
                    render_tree,
                    "--out",
                    text_calibration,
                    "--target-width",
                    str(self.args.width),
                ],
                parse_json=False,
            )
            text_calibrations.append(str(text_calibration))
            self.report["qualityGates"]["textCalibration"] = "generated-for-processed-screens"

            responsive_analysis = screen_dir / "responsive-layout.json"
            responsive_command: list[str | Path] = [
                self.node,
                self.scripts / "analyze_responsive_layout.cjs",
                "--html",
                html,
                "--out",
                responsive_analysis,
                "--widths",
                self.args.responsive_widths,
                "--height",
                str(self.args.height),
                "--baseline-width",
                str(self.args.width),
                "--mode",
                "auto",
            ]
            if container_selector:
                responsive_command.extend(["--selector", str(container_selector)])
            if activation.get("type") == "click" and selectors:
                responsive_command.extend(["--activate-selector", str(selectors[0])])
            self.run_command(
                f"analyze-responsive-layout-{screen_id}",
                responsive_command,
                environment=self.node_environment,
                parse_json=False,
            )
            responsive_analyses.append(str(responsive_analysis))
            self.report["qualityGates"]["responsiveAnalysis"] = "generated-for-processed-screens"

            scroll_behavior = screen_dir / "scroll-region-behavior.json"
            scroll_command: list[str | Path] = [
                self.node,
                self.scripts / "probe_scroll_region_behaviors.cjs",
                "--html",
                html,
                "--out",
                scroll_behavior,
                "--screen-id",
                screen_id,
                "--width",
                str(self.args.width),
                "--height",
                str(self.args.height),
            ]
            if container_selector:
                scroll_command.extend(["--selector", str(container_selector)])
            if activation.get("type") == "click" and selectors:
                scroll_command.extend(["--activate-selector", str(selectors[0])])
            self.run_command(
                f"probe-scroll-behavior-{screen_id}",
                scroll_command,
                environment=self.node_environment,
                parse_json=False,
            )
            scroll_behavior_analyses.append(str(scroll_behavior))
            self.report["qualityGates"]["scrollBehaviorAnalysis"] = "generated-for-processed-screens"

            if not self.args.skip_visual_baselines:
                visual_manifest = screen_dir / "visual-state-manifest.json"
                self.run_command(
                    f"build-visual-manifest-{screen_id}",
                    [
                        sys.executable,
                        self.scripts / "build_visual_state_manifest.py",
                        ir_path,
                        "--out",
                        visual_manifest,
                        "--html",
                        html,
                    ],
                    parse_json=False,
                )
                visual_manifests.append(str(visual_manifest))
                self.report["qualityGates"]["htmlVisualBaselines"] = "capturing"
                html_capture_dir = screen_dir / "visual-states" / "html"
                self.run_command(
                    f"capture-html-states-{screen_id}",
                    [
                        self.node,
                        self.scripts / "capture_html_states.cjs",
                        "--manifest",
                        visual_manifest,
                        "--out-dir",
                        html_capture_dir,
                    ],
                    environment=self.node_environment,
                    parse_json=False,
                )
                html_state_captures.append(str(html_capture_dir / "captures.json"))
                ios_capture_dir = screen_dir / "visual-states" / "ios"
                review_dir = screen_dir / "visual-review"
                visual_review_plans.append({
                    "screenId": screen_id,
                    "manifest": str(visual_manifest),
                    "htmlDirectory": str(html_capture_dir),
                    "iosDirectory": str(ios_capture_dir),
                    "reviewDirectory": str(review_dir),
                    "captureCommand": [
                        sys.executable,
                        str(self.scripts / "capture_ios_states.py"),
                        str(visual_manifest),
                        "--project", "<resolved-project>",
                        "--target", "<resolved-target>",
                        "--out-dir", str(ios_capture_dir),
                        "--device", self.args.device,
                    ],
                    "reviewCommand": [
                        sys.executable,
                        str(self.scripts / "build_visual_review_bundle.py"),
                        str(visual_manifest),
                        "--html-dir", str(html_capture_dir),
                        "--ios-dir", str(ios_capture_dir),
                        "--out-dir", str(review_dir),
                        "--multimodal-capability", "auto",
                    ],
                })
                self.report["qualityGates"]["htmlVisualBaselines"] = "captured-for-processed-screens"
        self.report["qualityGates"].update({
            "uiIRValidation": "passed",
            "textCalibration": "generated",
            "responsiveAnalysis": "generated",
            "scrollBehaviorAnalysis": "generated",
            "htmlVisualBaselines": "captured" if visual_manifests else "skipped",
        })
        return ir_paths

    def build_native_architecture_plan(
        self,
        ir_paths: list[Path],
        ui_stack: str,
        minimum_ios: str,
    ) -> Path:
        plan = self.report_dir / "native-architecture-plan.json"
        command: list[str | Path] = [sys.executable, self.scripts / "build_native_architecture_plan.py"]
        for path in ir_paths:
            command.extend(["--ir", path])
        for path in self.artifacts.get("scrollBehaviorAnalyses") or []:
            command.extend(["--scroll-behavior", Path(path)])
        command.extend([
            "--out", plan,
            "--ui-stack", ui_stack,
            "--minimum-ios", minimum_ios,
        ])
        self.run_command("build-native-architecture-plan", command)
        self.artifacts["nativeArchitecturePlan"] = str(plan)
        self.report["qualityGates"]["nativeArchitecturePlan"] = "passed"
        return plan

    def validate_supplied_irs(self) -> list[Path]:
        paths = [resolve_input(path, self.workspace) for path in self.args.ir or []]
        resolved = [path for path in paths if path is not None]
        if not resolved or any(not path.is_file() for path in resolved):
            missing = [str(path) for path in resolved if not path.is_file()]
            raise OrchestrationError("validate-ui-ir", "Missing UI IR files: " + ", ".join(missing))
        for index, path in enumerate(resolved, start=1):
            self.run_command(
                f"validate-ui-ir-{index}",
                [sys.executable, self.scripts / "validate_ui_ir.py", path],
                parse_json=False,
            )
        self.artifacts["uiIRs"] = [str(path) for path in resolved]
        self.report["qualityGates"]["uiIRValidation"] = "passed"
        return resolved

    def generate_and_integrate(
        self,
        ir_paths: list[Path],
        project: Path,
        target: str,
        source_root: Path,
        ui_stack: str,
        minimum_ios: str,
        architecture_plan: Path,
        naming_plan: Path,
    ) -> Path:
        generated_dir = source_root / "Generated" / "HTMLToIOS"
        command: list[str | Path] = [sys.executable, self.scripts / "generate_ios_from_ir.py"]
        for path in ir_paths:
            command.extend(["--ir", path])
        command.extend([
            "--out-dir", generated_dir,
            "--ui-stack", ui_stack,
            "--module-name", target,
            "--architecture-plan", architecture_plan,
            "--naming-plan", naming_plan,
        ])
        generation = self.run_command("generate-ios-code", command)
        self.run_command(
            "integrate-generated-sources",
            [
                "ruby", self.scripts / "integrate_generated_sources.rb",
                "--project", project,
                "--target", target,
                "--generated-dir", generated_dir,
                "--minimum-ios", minimum_ios,
            ],
        )
        self.artifacts["generatedDirectory"] = str(generated_dir)
        if isinstance(generation, dict):
            self.artifacts["generationManifest"] = generation.get("manifest")
            self.report["entrySymbol"] = generation.get("entrySymbol")
        return generated_dir

    def wire_managed_entry(self, source_root: Path, ui_stack: str) -> None:
        if not self.managed_project or self.args.dry_run:
            return
        if ui_stack == "swiftui":
            entry = source_root / "ContentView.swift"
            expected = entry.read_text(encoding="utf-8")
            if "HTMLToIOSGeneratedRootView()" not in expected:
                expected_hash = self.managed_project_info.get("entryTemplateSha256")
                if not expected_hash or file_sha256(entry) != expected_hash:
                    self.warnings.append(f"Managed project entry was modified; preserving it without automatic wiring: {entry}")
                    return
                body = '''import SwiftUI

struct ContentView: View {
    var body: some View {
        HTMLToIOSGeneratedRootView()
    }
}

#Preview {
    ContentView()
}
'''
                entry.write_text(body, encoding="utf-8")
        else:
            entry = source_root / "AppDelegate.swift"
            text = entry.read_text(encoding="utf-8")
            marker = "window.rootViewController = ViewController()"
            if "HTMLToIOSGeneratedRootViewController()" not in text:
                expected_hash = self.managed_project_info.get("entryTemplateSha256")
                if not expected_hash or file_sha256(entry) != expected_hash:
                    self.warnings.append(f"Managed project entry was modified; preserving it without automatic wiring: {entry}")
                    return
                if marker not in text:
                    raise OrchestrationError("wire-entry", f"Created UIKit entry template changed unexpectedly: {entry}")
                entry.write_text(text.replace(marker, "window.rootViewController = HTMLToIOSGeneratedRootViewController()"), encoding="utf-8")
        self.entry_wired = True
        self.report["entryWired"] = True
        self.artifacts["entryFile"] = str(entry)

    def detect_existing_entry(self, source_root: Path, symbol: str) -> bool:
        for path in source_root.rglob("*.swift"):
            if is_skipped(path):
                continue
            try:
                if symbol in path.read_text(encoding="utf-8", errors="ignore") and "HTMLToIOSGeneratedRoot.swift" not in path.name:
                    return True
            except OSError:
                continue
        return False

    def build(self, project: Path, scheme: str) -> None:
        if self.args.skip_build:
            self.warnings.append("xcodebuild was skipped by --skip-build.")
            self.report["qualityGates"]["build"] = "skipped"
            return
        kind, container = self.choose_build_container(project)
        derived_data = self.report_dir / "DerivedData"
        command: list[str | Path] = [
            "xcodebuild", "-quiet",
            f"-{kind}", container,
            "-scheme", scheme,
            "-destination", "generic/platform=iOS Simulator",
            "-configuration", "Debug",
            "-derivedDataPath", derived_data,
            "CODE_SIGNING_ALLOWED=NO",
            "build",
        ]
        self.run_command("xcodebuild", command, parse_json=False)
        self.artifacts["derivedData"] = str(derived_data)
        self.report["qualityGates"]["build"] = "passed"

    def capture_and_review_visual_states(self, project: Path, target: str, minimum_ios: str) -> None:
        plans = self.artifacts.get("visualReviewPlans") or []
        if not plans:
            self.report["qualityGates"]["iosStateCapture"] = "skipped"
            self.report["qualityGates"]["visualDiff"] = "skipped"
            return
        capture_reports: list[str] = []
        review_bundles: list[str] = []
        failed_states: list[str] = []
        kind, container = self.choose_build_container(project)
        for plan in plans:
            screen_id = str(plan["screenId"])
            capture_command: list[str | Path] = [
                sys.executable,
                self.scripts / "capture_ios_states.py",
                Path(plan["manifest"]),
                "--project", project,
                "--target", target,
                "--out-dir", Path(plan["iosDirectory"]),
                "--device", self.args.device,
                "--minimum-ios", minimum_ios,
            ]
            if kind == "workspace":
                capture_command.extend(["--workspace", container])
            try:
                capture = self.run_command(f"capture-ios-states-{screen_id}", capture_command)
            except OrchestrationError:
                self.report["qualityGates"]["iosStateCapture"] = "failed"
                self.report["qualityGates"]["visualDiff"] = "blocked-by-ios-state-capture"
                raise
            capture_reports.append(str(capture.get("out") or Path(plan["iosDirectory"]) / "captures.json"))
            try:
                self.run_command(
                    f"review-visual-states-{screen_id}",
                    [
                        sys.executable,
                        self.scripts / "build_visual_review_bundle.py",
                        Path(plan["manifest"]),
                        "--html-dir", Path(plan["htmlDirectory"]),
                        "--ios-dir", Path(plan["iosDirectory"]),
                        "--out-dir", Path(plan["reviewDirectory"]),
                        "--multimodal-capability", "auto",
                        "--advisory",
                    ],
                )
            except OrchestrationError:
                self.report["qualityGates"]["visualDiff"] = "failed"
                raise
            bundle_path = Path(plan["reviewDirectory"]) / "review-bundle.json"
            review_bundles.append(str(bundle_path))
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            summary = bundle.get("summary") or {}
            failed_states.extend(f"{screen_id}:{state_id}" for state_id in summary.get("missingRequired") or [])
            failed_states.extend(f"{screen_id}:{state_id}" for state_id in summary.get("requiredFailures") or [])
        self.artifacts["iosStateCaptures"] = capture_reports
        self.artifacts["visualReviewBundles"] = review_bundles
        self.report["qualityGates"]["iosStateCapture"] = "passed"
        self.report["qualityGates"]["visualDiff"] = "failed" if failed_states else "passed"
        if failed_states:
            self.report["visualQualityGateFailures"] = failed_states
            raise OrchestrationError(
                "visual-quality-gate",
                "Required visual states failed deterministic acceptance: " + ", ".join(failed_states),
            )

    def execute(self) -> dict[str, Any]:
        if not self.workspace.exists():
            self.workspace.mkdir(parents=True, exist_ok=True)
        elif not self.workspace.is_dir():
            raise OrchestrationError("validate-workspace", f"Workspace is not a directory: {self.workspace}")
        inspection = self.inspect_workspace()
        state = str(inspection.get("projectState") or "unknown")
        creating = state in {"empty-no-ios-project", "swift-package-only"} and not discover(self.workspace, "*.xcodeproj")
        preliminary_minimum_ios = self.choose_minimum_ios(inspection)
        html_contracts = None
        ir_paths = None
        if self.args.dry_run:
            if self.args.html:
                html = resolve_input(self.args.html, self.workspace)
                if not html or not html.is_file():
                    raise OrchestrationError("validate-html", f"HTML file does not exist: {html}")
            else:
                supplied = [resolve_input(path, self.workspace) for path in self.args.ir or []]
                if not supplied or any(path is None or not path.is_file() for path in supplied):
                    raise OrchestrationError("validate-ui-ir", "One or more supplied UI IR files do not exist.")
        elif self.args.html:
            html_contracts = self.discover_html_contracts()
        else:
            ir_paths = self.validate_supplied_irs()

        if state == "swift-package-only" and creating and not self.args.create_package_host_app:
            raise OrchestrationError(
                "create-project",
                "A Swift Package exists without an App project. Pass --create-package-host-app after confirming an App host is required.",
                "needs-input",
            )

        if creating:
            provisional_target = safe_app_name(self.args.app_name or self.workspace.name)
            decision = self.build_project_generation_decision(state, None, provisional_target)
            selected_stack = (decision.get("uiStack") or {}).get("selected")
            if not selected_stack:
                raise OrchestrationError(
                    "select-ui-stack",
                    "A new App requires an explicit UI stack choice: pass --ui-stack swiftui or --ui-stack uikit.",
                    "needs-input",
                )
            ui_stack = str(selected_stack)
            project, source_root = self.choose_project(inspection, ui_stack, preliminary_minimum_ios)
            target, scheme = self.choose_target_and_scheme(project)
        else:
            project, source_root = self.choose_project(inspection, self.args.ui_stack or "swiftui", preliminary_minimum_ios)
            target, scheme = self.choose_target_and_scheme(project)
            decision = self.build_project_generation_decision(state, source_root, target)
            selected_stack = (decision.get("uiStack") or {}).get("selected")
            if not selected_stack:
                module = decision.get("moduleInspection") or {}
                raise OrchestrationError(
                    "select-ui-stack",
                    "The target module is mixed or ambiguous; pass --ui-stack swiftui or --ui-stack uikit. "
                    f"Detected SwiftUI score {module.get('swiftUIScore', 0)} and UIKit score {module.get('uiKitScore', 0)}.",
                    "needs-input",
                )
            ui_stack = str(selected_stack)

        verification_mode = str((decision.get("verification") or {}).get("resolved") or "ask")
        minimum_ios = self.target_minimum_ios(project, target, preliminary_minimum_ios)
        if ui_stack == "swiftui" and version_key(minimum_ios) < (16, 0):
            raise OrchestrationError(
                "check-deployment-target",
                f"The generated SwiftUI navigation runtime requires iOS 16.0; target {target} uses {minimum_ios}.",
                "needs-input",
            )
        self.report.update({
            "projectState": state,
            "project": str(project),
            "sourceRoot": str(source_root),
            "target": target,
            "scheme": scheme,
            "uiStack": ui_stack,
            "minimumIOS": minimum_ios,
            "verificationMode": verification_mode,
        })
        if self.args.dry_run:
            prefix = self.args.name_prefix or ("Sky" if creating else safe_app_name(target))
            self.report["nativeNamingPlan"] = {
                "schemaVersion": "native-naming-plan-1.0",
                "prefix": prefix,
                "source": "explicit-request" if self.args.name_prefix else "new-project-default" if creating else "target-name-fallback",
            }
            self.report["status"] = "planned"
            return self.report
        sdk_report, component_report = self.inspect_sdk_and_components(source_root, minimum_ios)
        naming_plan = self.build_native_naming_plan(state, target, component_report)
        if html_contracts is not None:
            ir_paths = self.build_irs_from_html(ui_stack, minimum_ios, sdk_report, html_contracts)
        if ir_paths is None:
            raise OrchestrationError("prepare-ui-ir", "No UI IR inputs are available.")
        architecture_plan = self.build_native_architecture_plan(ir_paths, ui_stack, minimum_ios)
        self.generate_and_integrate(ir_paths, project, target, source_root, ui_stack, minimum_ios, architecture_plan, naming_plan)
        self.wire_managed_entry(source_root, ui_stack)
        symbol = "HTMLToIOSGeneratedRootView" if ui_stack == "swiftui" else "HTMLToIOSGeneratedRootViewController"
        self.entry_wired = self.entry_wired or self.detect_existing_entry(source_root, symbol)
        self.report["entryWired"] = self.entry_wired
        if not self.entry_wired:
            self.warnings.append(f"Generated code is integrated into the target, but {symbol} is not connected to the existing App flow.")

        if verification_mode in {"build", "visual"}:
            self.build(project, scheme)
        elif verification_mode == "ask":
            self.report["qualityGates"]["build"] = "pending-user-confirmation"
            self.report["qualityGates"]["iosStateCapture"] = "pending-user-confirmation"
            self.report["qualityGates"]["visualDiff"] = "pending-user-confirmation"
            self.report["nextActions"] = ["Confirm build-only or full visual verification."]
        else:
            self.report["qualityGates"]["build"] = "skipped"
            self.report["qualityGates"]["iosStateCapture"] = "skipped"
            self.report["qualityGates"]["visualDiff"] = "skipped"

        if self.args.html and not self.args.skip_visual_baselines:
            if verification_mode == "visual" and self.entry_wired:
                self.capture_and_review_visual_states(project, target, minimum_ios)
            elif verification_mode in {"build", "visual"}:
                self.report["qualityGates"]["iosStateCapture"] = "required-pending"
                self.report["qualityGates"]["visualDiff"] = "blocked-pending-ios-captures"
                self.warnings.append(
                    "HTML visual baselines and node-aligned regions are ready; required simulator states must pass the visual quality gate before claiming completion."
                )
        if not self.entry_wired:
            self.report["status"] = "generated-needs-entry-integration"
        elif verification_mode == "ask":
            self.report["status"] = "generated-awaiting-verification"
        elif verification_mode == "none":
            self.report["status"] = "generated-without-verification"
        elif verification_mode == "build" and self.args.html and not self.args.skip_visual_baselines:
            self.report["status"] = "built-awaiting-visual-verification"
        elif self.args.html and not self.args.skip_visual_baselines and self.report["qualityGates"]["visualDiff"] != "passed":
            self.report["status"] = "built-pending-visual-acceptance"
        else:
            self.report["status"] = "completed"
        self.write_report()
        return self.report


def main() -> int:
    args = parse_args()
    orchestrator = Orchestrator(args)
    try:
        report = orchestrator.execute()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except OrchestrationError as error:
        orchestrator.report.update({
            "status": error.status,
            "failedStage": error.stage,
            "message": str(error),
            "createdProject": orchestrator.created_project,
            "entryWired": orchestrator.entry_wired,
        })
        orchestrator.write_report()
        print(json.dumps(orchestrator.report, ensure_ascii=False, indent=2))
        return 2 if error.status == "needs-input" else 1
    except Exception as error:  # pragma: no cover - last-resort structured failure report
        orchestrator.report.update({
            "status": "failed",
            "failedStage": "unexpected",
            "message": str(error),
            "traceback": traceback.format_exc(),
        })
        orchestrator.write_report()
        print(json.dumps(orchestrator.report, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
