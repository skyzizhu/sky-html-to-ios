#!/usr/bin/env python3
"""Capture initial iOS state across an explicit simulator viewport matrix."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


CASE_PATTERN = re.compile(r"^(?P<width>\d+)x(?P<height>\d+):(?P<device>.+)$")


def parse_case(value: str) -> dict:
    match = CASE_PATTERN.fullmatch(value.strip())
    if not match:
        raise argparse.ArgumentTypeError("case must use WIDTHxHEIGHT:SIMULATOR_NAME")
    width = int(match.group("width"))
    height = int(match.group("height"))
    if width < 240 or height < 400:
        raise argparse.ArgumentTypeError("case viewport is implausibly small")
    return {"width": width, "height": height, "device": match.group("device").strip()}


def available_device_names() -> set[str]:
    result = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "available", "-j"],
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    return {
        str(device.get("name"))
        for devices in (payload.get("devices") or {}).values()
        for device in devices
        if device.get("isAvailable") and device.get("name")
    }


def initial_manifest(manifest: dict, width: int, height: int) -> dict:
    result = json.loads(json.dumps(manifest))
    states = result.get("states") or []
    initial = next((item for item in states if item.get("id") == "initial"), states[0] if states else None)
    result["states"] = [initial] if initial else []
    result["targetViewport"] = {"width": width, "height": height}
    return result


def validate_device_viewport(capture: dict | None, width: int, height: int) -> dict:
    original = (capture or {}).get("originalSize") or {}
    original_width = float(original.get("width") or 0)
    original_height = float(original.get("height") or 0)
    if original_width <= 0 or original_height <= 0:
        return {
            "status": "failed",
            "reason": "missing-original-screenshot-size",
            "declaredViewport": {"width": width, "height": height},
        }
    scale_x = original_width / width
    scale_y = original_height / height
    native_scale = min((1, 2, 3), key=lambda candidate: abs(scale_x - candidate) + abs(scale_y - candidate))
    matches = abs(scale_x - native_scale) <= 0.03 and abs(scale_y - native_scale) <= 0.03
    return {
        "status": "passed" if matches else "failed",
        "reason": None if matches else "simulator-screenshot-does-not-match-declared-viewport",
        "declaredViewport": {"width": width, "height": height},
        "originalScreenshotSize": {"width": original_width, "height": original_height},
        "inferredScaleX": round(scale_x, 4),
        "inferredScaleY": round(scale_y, 4),
        "expectedNativeScale": native_scale,
    }


def has_horizontal_scroll_owner(node_id: str, nodes: dict[str, dict]) -> bool:
    visited: set[str] = set()
    current = nodes.get(node_id)
    while current:
        current_id = str(current.get("nodeId") or "")
        if current_id in visited:
            break
        visited.add(current_id)
        if current.get("scrollAxis") in {"horizontal", "both"}:
            return True
        current = nodes.get(str(current.get("parentNodeId") or ""))
    return False


def analyze_geometry(manifest: dict, geometry: dict, width: int) -> dict:
    definitions = {
        str(item.get("nodeId")): item
        for item in manifest.get("geometryNodes") or []
        if item.get("nodeId")
    }
    overflow = []
    for item in geometry.get("nodes") or []:
        node_id = str(item.get("nodeId") or "")
        definition = definitions.get(node_id) or {}
        if definition.get("hasChildren") or definition.get("isDecorative"):
            continue
        frame = item.get("frame") or {}
        left = float(frame.get("x") or 0)
        item_width = float(frame.get("width") or 0)
        if left < -1 or left + item_width > width + 1:
            overflow.append({
                "nodeId": node_id,
                "frame": frame,
                "ownedByHorizontalScroller": has_horizontal_scroll_owner(node_id, definitions),
            })
    unowned = [item for item in overflow if not item["ownedByHorizontalScroller"]]
    requested = len(definitions)
    captured = len({str(item.get("nodeId")) for item in geometry.get("nodes") or []} & set(definitions))
    validation_ids = {
        str(item.get("nodeId"))
        for item in manifest.get("validationRegions") or []
        if item.get("nodeId")
    }
    captured_ids = {str(item.get("nodeId")) for item in geometry.get("nodes") or []}
    return {
        "requestedGeometryNodes": requested,
        "capturedGeometryNodes": captured,
        "geometryCaptureRate": round(captured / requested, 4) if requested else None,
        "validationGeometryNodes": len(validation_ids),
        "capturedValidationGeometryNodes": len(validation_ids & captured_ids),
        "validationGeometryCaptureRate": (
            round(len(validation_ids & captured_ids) / len(validation_ids), 4)
            if validation_ids else None
        ),
        "horizontalOverflowNodes": overflow,
        "unownedHorizontalOverflowNodes": unowned,
        "status": "passed" if not unowned else "review-required",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", type=parse_case, required=True)
    parser.add_argument("--minimum-ios", default="16.0")
    parser.add_argument("--allow-missing-devices", action="store_true")
    parser.add_argument("--reuse-captures", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    case_ids = [f"{case['width']}x{case['height']}" for case in args.case]
    if len(case_ids) != len(set(case_ids)):
        parser.error("each WIDTHxHEIGHT viewport may only appear once")
    devices = available_device_names()
    capture_script = Path(__file__).with_name("capture_ios_states.py")
    results = []
    missing_devices = []
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for case in args.case:
        case_id = f"{case['width']}x{case['height']}"
        case_dir = args.out_dir / case_id
        if case["device"] not in devices:
            missing_devices.append(case["device"])
            results.append({**case, "id": case_id, "status": "missing-device"})
            continue
        if not args.reuse_captures or not (case_dir / "captures.json").is_file():
            with tempfile.TemporaryDirectory(prefix="html-to-ios-matrix-") as temporary:
                case_manifest = initial_manifest(manifest, case["width"], case["height"])
                manifest_path = Path(temporary) / "manifest.json"
                manifest_path.write_text(json.dumps(case_manifest, ensure_ascii=False), encoding="utf-8")
                command = [
                    sys.executable,
                    str(capture_script),
                    str(manifest_path),
                    "--project",
                    str(args.project),
                    "--target",
                    args.target,
                    "--out-dir",
                    str(case_dir),
                    "--device",
                    case["device"],
                    "--minimum-ios",
                    args.minimum_ios,
                    "--viewport-width",
                    str(case["width"]),
                    "--viewport-height",
                    str(case["height"]),
                ]
                if args.workspace:
                    command.extend(["--workspace", str(args.workspace)])
                subprocess.run(command, text=True, check=True)
        captures = json.loads((case_dir / "captures.json").read_text(encoding="utf-8"))
        initial = next((item for item in captures.get("captures") or [] if item.get("id") == "initial"), None)
        viewport_validation = validate_device_viewport(initial, case["width"], case["height"])
        geometry_path = Path(initial["geometry"]) if initial and initial.get("geometry") else None
        diagnostics = (
            analyze_geometry(manifest, json.loads(geometry_path.read_text(encoding="utf-8")), case["width"])
            if geometry_path and geometry_path.is_file()
            else {"status": "review-required", "reason": "missing-geometry"}
        )
        diagnostics["deviceViewportValidation"] = viewport_validation
        if viewport_validation["status"] == "failed":
            diagnostics["status"] = "failed"
        results.append({**case, "id": case_id, "capture": initial, "diagnostics": diagnostics, "status": diagnostics["status"]})

    summary = {
        "passed": sum(item["status"] == "passed" for item in results),
        "reviewRequired": [item["id"] for item in results if item["status"] == "review-required"],
        "missingDevices": missing_devices,
        "qualityGate": (
            "failed"
            if missing_devices and not args.allow_missing_devices
            else "failed" if any(item["status"] == "failed" for item in results)
            else "review-required" if any(item["status"] == "review-required" for item in results)
            else "passed"
        ),
    }
    report = {
        "schemaVersion": "responsive-ios-matrix-1.0",
        "manifest": str(args.manifest.resolve()),
        "project": str(args.project.resolve()),
        "target": args.target,
        "cases": results,
        "summary": summary,
    }
    report_path = args.out_dir / "responsive-matrix.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(report_path.resolve()), **summary}, ensure_ascii=False, indent=2))
    return 1 if summary["qualityGate"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
