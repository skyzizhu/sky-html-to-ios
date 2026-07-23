#!/usr/bin/env python3
"""Capture manifest states through a generator-owned XCUITest target."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image


def run(
    command: list[str],
    *,
    capture: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=capture, check=True, timeout=timeout)


def choose_simulator(device_name: str) -> str:
    payload = json.loads(run(["xcrun", "simctl", "list", "devices", "available", "-j"], capture=True).stdout)
    candidates = []
    for runtime, devices in (payload.get("devices") or {}).items():
        for device in devices:
            if device.get("isAvailable") and device.get("name") == device_name:
                candidates.append((device.get("state") == "Booted", runtime, device["udid"]))
    if not candidates:
        raise RuntimeError(f"No available simulator named {device_name!r}")
    candidates.sort(reverse=True)
    return candidates[0][2]


def find_exported_attachment(export_dir: Path, export_manifest, state_id: str, state_index: int) -> Path | None:
    expected = f"{state_id}.png"
    for test_record in export_manifest if isinstance(export_manifest, list) else []:
        test_identifier = str(test_record.get("testIdentifier") or "")
        index_matches = f"/test_{state_index}_" in test_identifier or f"test_{state_index}_" in test_identifier
        for record in test_record.get("attachments") or []:
            suggested = str(record.get("suggestedHumanReadableName") or "")
            exported = record.get("exportedFileName")
            name_matches = suggested == expected or suggested.startswith(f"{state_id}_")
            if (name_matches or index_matches) and exported and (export_dir / exported).is_file():
                return export_dir / exported
    direct = export_dir / expected
    if direct.is_file():
        return direct
    for candidate in export_dir.glob("*.png"):
        if expected in candidate.name:
            return candidate
    return None


def normalize(source: Path, destination: Path, width: int, height: int) -> dict:
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
        original = image.size
        if original != (width, height):
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination)
    return {
        "originalSize": {"width": original[0], "height": original[1]},
        "outputSize": {"width": width, "height": height},
        "normalized": original != (width, height),
        "normalization": "logical-viewport-lanczos" if original != (width, height) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, help="Optional xcworkspace used to resolve app dependencies")
    parser.add_argument("--target", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--device", default="iPhone 15 Pro")
    parser.add_argument("--minimum-ios", default="16.0")
    parser.add_argument("--derived-data", type=Path)
    parser.add_argument("--test-timeout-seconds", type=int, default=240)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "visual-state-manifest-1.0":
        parser.error("Unsupported visual state manifest")
    viewport = manifest.get("targetViewport") or manifest.get("viewport") or {}
    width, height = round(float(viewport.get("width", 393))), round(float(viewport.get("height", 852)))
    work_dir = args.out_dir.parent / ".ios-visual-capture"
    source_dir = work_dir / "VisualTests"
    result_bundle = work_dir / "VisualCapture.xcresult"
    export_dir = work_dir / "Attachments"
    derived_data = args.derived_data or work_dir / "DerivedData"
    if result_bundle.exists():
        shutil.rmtree(result_bundle)
    if export_dir.exists():
        shutil.rmtree(export_dir)

    preparer = Path(__file__).with_name("prepare_visual_ui_tests.rb")
    prepared = run([
        "ruby", str(preparer), "--project", str(args.project), "--target", args.target,
        "--manifest", str(args.manifest), "--source-dir", str(source_dir), "--minimum-ios", args.minimum_ios,
    ], capture=True)
    prepared_report = json.loads(prepared.stdout)
    simulator_id = choose_simulator(args.device)
    container = ["-workspace", str(args.workspace)] if args.workspace else ["-project", str(args.project)]
    run([
        "xcodebuild", "-quiet", *container, "-scheme", prepared_report["scheme"],
        "-destination", f"platform=iOS Simulator,id={simulator_id}", "-derivedDataPath", str(derived_data),
        "-resultBundlePath", str(result_bundle), "CODE_SIGNING_ALLOWED=NO", "test",
    ], timeout=max(args.test_timeout_seconds, 30))
    run(["xcrun", "xcresulttool", "export", "attachments", "--path", str(result_bundle), "--output-path", str(export_dir)])
    export_manifest_path = export_dir / "manifest.json"
    export_manifest = json.loads(export_manifest_path.read_text(encoding="utf-8")) if export_manifest_path.is_file() else {}

    captures = []
    missing = []
    for state_index, state in enumerate(manifest.get("states") or []):
        state_id = state["id"]
        exported = find_exported_attachment(export_dir, export_manifest, state_id, state_index)
        if not exported:
            missing.append(state_id)
            continue
        destination = args.out_dir / f"{state_id}.png"
        captures.append({
            "id": state_id,
            "required": state.get("required", True),
            "screenshot": str(destination.resolve()),
            "actions": state.get("iosActions") or [],
            **normalize(exported, destination, width, height),
        })
    report = {
        "schemaVersion": "ios-state-captures-1.0",
        "manifest": str(args.manifest.resolve()),
        "project": str(args.project.resolve()),
        "target": args.target,
        "scheme": prepared_report["scheme"],
        "simulatorId": simulator_id,
        "captures": captures,
        "missing": missing,
        "resultBundle": str(result_bundle.resolve()),
        "attachmentManifest": str(export_manifest_path.resolve()),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.out_dir / "captures.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(report_path.resolve()), "captures": len(captures), "missing": missing}, ensure_ascii=False, indent=2))
    required_missing = {state["id"] for state in manifest.get("states") or [] if state.get("required", True)} & set(missing)
    return 1 if required_missing else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(error.stdout, file=sys.stderr)
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        raise
    except subprocess.TimeoutExpired as error:
        print(f"iOS visual capture timed out after {error.timeout} seconds", file=sys.stderr)
        raise SystemExit(124)
