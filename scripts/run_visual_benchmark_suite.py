#!/usr/bin/env python3
"""Run diverse HTML fixtures through the full converter and aggregate visual fidelity."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "html-to-ios-visual-benchmark-report-1.0"


def load_suite(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != "html-to-ios-visual-benchmark-suite-1.0":
        raise ValueError("Unsupported visual benchmark suite schemaVersion")
    cases = payload.get("cases") or []
    if not cases:
        raise ValueError("Visual benchmark suite must contain at least one case")
    seen: set[str] = set()
    for item in cases:
        case_id = str(item.get("id") or "").strip()
        html = path.parent / str(item.get("html") or "")
        if not case_id or case_id in seen:
            raise ValueError("Visual benchmark case IDs must be present and unique")
        if not html.is_file():
            raise ValueError(f"Missing visual benchmark HTML: {html}")
        if item.get("level") not in {1, 2, 3}:
            raise ValueError(f"Visual benchmark case {case_id!r} must declare level 1, 2, or 3")
        if not item.get("coverage"):
            raise ValueError(f"Visual benchmark case {case_id!r} must declare coverage")
        expected = item.get("expectedNative") or {}
        if expected and not isinstance(expected, dict):
            raise ValueError(f"Visual benchmark case {case_id!r} expectedNative must be an object")
        seen.add(case_id)
    return payload


def newest_review_bundles(case_dir: Path) -> list[Path]:
    by_screen: dict[Path, Path] = {}
    for candidate in case_dir.glob(".html-to-ios/screens/*/visual-review/iteration-*/review-bundle.json"):
        screen_dir = candidate.parents[2]
        current = by_screen.get(screen_dir)
        if current is None or candidate.stat().st_mtime > current.stat().st_mtime:
            by_screen[screen_dir] = candidate
    return sorted(by_screen.values())


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def native_quality_summary(case: dict[str, Any], case_dir: Path, ui_stack: str) -> dict[str, Any]:
    report_dir = case_dir / ".html-to-ios"
    architecture = load_optional_json(report_dir / "native-architecture-plan.json")
    controls = load_optional_json(report_dir / "native-control-configuration-plan.json")
    structure = load_optional_json(report_dir / "native-structure-validation.json")
    expectations = case.get("expectedNative") or {}
    architecture_screens = architecture.get("screens") or []
    content_kinds = sorted({
        str((((screen.get("layers") or {}).get("contentContainer") or {}).get("kind") or ""))
        for screen in architecture_screens
        if isinstance(screen, dict)
    } - {""})
    navigation_styles = sorted({
        str(((screen.get("navigation") or {}).get("barRendering") or ""))
        for screen in architecture_screens
        if isinstance(screen, dict)
    } - {""})
    control_records = [
        control
        for screen in controls.get("screens") or []
        for control in screen.get("controls") or []
        if isinstance(control, dict)
    ]
    primitive_key = "uiKit" if ui_stack == "uikit" else "swiftUI"
    primitives = sorted({
        str((control.get("nativePrimitive") or {}).get(primitive_key) or "")
        for control in control_records
    } - {""})
    system_count = sum(str(control.get("strategy") or "").startswith("system-control") for control in control_records)
    custom_count = len(control_records) - system_count
    system_ratio = system_count / len(control_records) if control_records else 1.0
    checks: list[dict[str, Any]] = []

    def add_check(name: str, expected: Any, actual: Any, passed: bool) -> None:
        checks.append({"name": name, "expected": expected, "actual": actual, "passed": passed})

    allowed_content = [str(item) for item in expectations.get("contentContainerKinds") or []]
    if allowed_content:
        add_check(
            "content-container-kind", allowed_content, content_kinds,
            bool(content_kinds) and all(item in allowed_content for item in content_kinds),
        )
    expected_navigation = expectations.get("navigationBarRendering")
    if expected_navigation:
        add_check(
            "navigation-bar-rendering", expected_navigation, navigation_styles,
            bool(navigation_styles) and all(item == expected_navigation for item in navigation_styles),
        )
    required_primitives = [
        str(item) for item in (
            expectations.get("requiredUIKitControls")
            if ui_stack == "uikit" else expectations.get("requiredSwiftUIControls")
        ) or []
    ]
    for primitive in required_primitives:
        add_check("required-native-control", primitive, primitives, primitive in primitives)
    minimum_ratio = float(expectations.get("minimumSystemControlRatio", 0))
    add_check("minimum-system-control-ratio", minimum_ratio, round(system_ratio, 4), system_ratio >= minimum_ratio)
    maximum_custom = int(expectations.get("maximumCustomControlCount", len(control_records)))
    add_check("maximum-custom-control-count", maximum_custom, custom_count, custom_count <= maximum_custom)
    structure_status = str(structure.get("status") or "missing")
    add_check("native-structure-validation", "passed", structure_status, structure_status == "passed")
    return {
        "status": "passed" if checks and all(item["passed"] for item in checks) else "failed",
        "contentContainerKinds": content_kinds,
        "navigationBarRendering": navigation_styles,
        "nativeControlPrimitives": primitives,
        "controlCount": len(control_records),
        "systemControlCount": system_count,
        "customControlCount": custom_count,
        "systemControlRatio": round(system_ratio, 4),
        "checks": checks,
    }


def summarize_case(
    case_id: str,
    case_dir: Path,
    command_result: subprocess.CompletedProcess[str] | None = None,
    *,
    case: dict[str, Any] | None = None,
    ui_stack: str = "uikit",
) -> dict[str, Any]:
    case = case or {"id": case_id}
    report_path = case_dir / ".html-to-ios" / "orchestration-report.json"
    orchestration = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    bundles = []
    states = []
    for path in newest_review_bundles(case_dir):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        bundles.append(str(path.resolve()))
        states.extend({"screenId": path.parents[2].name, **item} for item in bundle.get("states") or [])
    required = [item for item in states if item.get("required", True)]
    fidelity = (
        sum(float(item.get("fidelityPercent") or 0) for item in required) / len(required)
        if required else None
    )
    return {
        "id": case_id,
        "level": case.get("level"),
        "workspace": str(case_dir.resolve()),
        "commandReturnCode": command_result.returncode if command_result is not None else None,
        "orchestrationStatus": orchestration.get("status") or "missing-report",
        "failedStage": orchestration.get("failedStage"),
        "buildGate": (orchestration.get("qualityGates") or {}).get("build"),
        "visualDiffGate": (orchestration.get("qualityGates") or {}).get("visualDiff"),
        "requiredStateCount": len(required),
        "missingRequiredStateCount": sum(item.get("status") == "missing" for item in required),
        "fidelityPercent": round(fidelity, 4) if fidelity is not None else None,
        "exactFidelityAchieved": bool(required) and all(item.get("exactPixelMatch") is True for item in required),
        "reviewBundles": bundles,
        "states": [
            {
                "screenId": item.get("screenId"),
                "id": item.get("id"),
                "status": item.get("status"),
                "fidelityPercent": item.get("fidelityPercent"),
                "mismatchRatio": (item.get("report") or {}).get("mismatchRatio"),
                "meanAbsoluteDifference": (item.get("report") or {}).get("meanAbsoluteDifference"),
                "simplePixelSimilarity": (item.get("report") or {}).get("simplePixelSimilarity"),
            }
            for item in required
        ],
        "nativeQuality": native_quality_summary(case, case_dir, ui_stack),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# HTML to iOS Visual Benchmark",
        "",
        f"- UI stack: `{report['uiStack']}`",
        f"- Cases: {report['summary']['caseCount']}",
        f"- Built: {report['summary']['builtCaseCount']}",
        f"- Average fidelity: {report['summary']['averageFidelityPercent']}",
        f"- Exact 100% cases: {report['summary']['exactCaseCount']}",
        "",
        "| Case | Level | Build | Native quality | Visual gate | Fidelity | Required states |",
        "| --- | ---: | --- | --- | --- | ---: | ---: |",
    ]
    for item in report["cases"]:
        fidelity = "n/a" if item["fidelityPercent"] is None else f"{item['fidelityPercent']:.4f}%"
        lines.append(
            f"| {item['id']} | {item['level']} | {item['buildGate']} | {item['nativeQuality']['status']} | {item['visualDiffGate']} | {fidelity} | {item['requiredStateCount']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--ui-stack", choices=("uikit", "swiftui"), default="uikit")
    parser.add_argument("--case", action="append", dest="case_ids", default=[])
    parser.add_argument("--summarize-existing", action="store_true")
    args = parser.parse_args()

    suite_path = args.suite.expanduser().resolve()
    suite = load_suite(suite_path)
    target = suite.get("target") or {}
    selected = [
        item for item in suite["cases"]
        if not args.case_ids or str(item["id"]) in set(args.case_ids)
    ]
    if not selected:
        parser.error("No visual benchmark cases matched --case")
    output = args.out_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    orchestrator = Path(__file__).with_name("run_html_to_ios.py")
    cases = []
    for item in selected:
        case_id = str(item["id"])
        case_dir = output / case_id / args.ui_stack
        result = None
        if not args.summarize_existing:
            case_dir.mkdir(parents=True, exist_ok=True)
            command = [
                sys.executable, str(orchestrator),
                "--workspace", str(case_dir),
                "--html", str((suite_path.parent / item["html"]).resolve()),
                "--ui-stack", args.ui_stack,
                "--app-name", "Benchmark" + "".join(part.title() for part in case_id.split("-")),
                "--bundle-id", f"com.skyzizhu.htmltoios.benchmark.{case_id.replace('-', '')}",
                "--report-dir", str(case_dir / ".html-to-ios"),
                "--width", str(int(target.get("width") or 393)),
                "--height", str(int(target.get("height") or 852)),
                "--device", str(target.get("device") or "iPhone 16"),
                "--minimum-ios", str(target.get("minimumIOS") or "16.0"),
                "--verification-mode", "visual",
            ]
            result = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                check=False,
            )
            (case_dir / "benchmark-run.log").write_text(result.stdout, encoding="utf-8")
        summary = summarize_case(case_id, case_dir, result, case=item, ui_stack=args.ui_stack)
        summary["coverage"] = item.get("coverage") or []
        cases.append(summary)

    measured = [item["fidelityPercent"] for item in cases if item["fidelityPercent"] is not None]
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "suite": str(suite_path),
        "uiStack": args.ui_stack,
        "target": target,
        "summary": {
            "caseCount": len(cases),
            "builtCaseCount": sum(item["buildGate"] == "passed" for item in cases),
            "nativeQualityPassedCaseCount": sum(item["nativeQuality"]["status"] == "passed" for item in cases),
            "measuredCaseCount": len(measured),
            "averageFidelityPercent": round(sum(measured) / len(measured), 4) if measured else None,
            "minimumFidelityPercent": round(min(measured), 4) if measured else None,
            "exactCaseCount": sum(item["exactFidelityAchieved"] for item in cases),
            "targetFidelityPercent": 100.0,
        },
        "cases": cases,
    }
    report_path = output / f"benchmark-report-{args.ui_stack}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / f"benchmark-report-{args.ui_stack}.md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"out": str(report_path), **report["summary"]}, ensure_ascii=False, indent=2))
    return 0 if len(measured) == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
