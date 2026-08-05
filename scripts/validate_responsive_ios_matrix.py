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


CASE_PATTERN = re.compile(
    r"^(?:(?P<profile>[A-Za-z0-9._-]+)=)?"
    r"(?P<width>\d+)x(?P<height>\d+)"
    r"(?:@(?P<orientation>portrait|landscape))?:(?P<device>.+)$"
)


def parse_case(value: str) -> dict:
    match = CASE_PATTERN.fullmatch(value.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            "case must use [PROFILE=]WIDTHxHEIGHT[@portrait|landscape]:SIMULATOR_NAME"
        )
    width = int(match.group("width"))
    height = int(match.group("height"))
    if min(width, height) < 240 or max(width, height) < 400:
        raise argparse.ArgumentTypeError("case viewport is implausibly small")
    orientation = match.group("orientation") or ("landscape" if width > height else "portrait")
    return {
        "id": match.group("profile") or f"{width}x{height}-{orientation}",
        "width": width,
        "height": height,
        "orientation": orientation,
        "device": match.group("device").strip(),
    }


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


def validate_device_viewport(capture: dict | None, width: int, height: int, orientation: str = "portrait") -> dict:
    original = (capture or {}).get("originalSize") or {}
    original_width = float(original.get("width") or 0)
    original_height = float(original.get("height") or 0)
    if original_width <= 0 or original_height <= 0:
        return {
            "status": "failed",
            "reason": "missing-original-screenshot-size",
            "declaredViewport": {"width": width, "height": height},
        }
    rotated_native_capture = orientation == "landscape" and original_height > original_width
    source_width = original_height if rotated_native_capture else original_width
    source_height = original_width if rotated_native_capture else original_height
    scale_x = source_width / width
    scale_y = source_height / height
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
        "rotatedNativeCapture": rotated_native_capture,
    }


def validate_app_viewport(geometry: dict | None, width: int, height: int, orientation: str) -> dict:
    frame = (geometry or {}).get("appFrame") or {}
    actual_width = float(frame.get("width") or 0)
    actual_height = float(frame.get("height") or 0)
    if actual_width <= 0 or actual_height <= 0:
        return {
            "status": "unavailable",
            "reason": "missing-app-window-frame",
            "declaredViewport": {"width": width, "height": height},
            "orientation": orientation,
        }
    matches = abs(actual_width - width) <= 1 and abs(actual_height - height) <= 1
    orientation_matches = (actual_width > actual_height) == (orientation == "landscape")
    return {
        "status": "passed" if matches and orientation_matches else "failed",
        "reason": None if matches and orientation_matches else "app-window-does-not-match-declared-viewport",
        "declaredViewport": {"width": width, "height": height},
        "actualAppFrame": frame,
        "orientation": orientation,
        "orientationMatches": orientation_matches,
    }


def inferred_size_classes(width: int, height: int, device: str = "") -> dict:
    is_phone = device.lower().startswith("iphone")
    return {
        "horizontal": "compact" if is_phone else "regular" if width >= 600 else "compact",
        "vertical": "compact" if is_phone and width > height else "regular" if height >= 600 else "compact",
        "evidence": "device-family-and-app-window-inference",
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
    validation_ids = {
        str(item.get("nodeId"))
        for item in manifest.get("validationRegions") or []
        if item.get("nodeId")
    }
    root_ids = {
        node_id
        for node_id, item in definitions.items()
        if not item.get("parentNodeId")
    }
    overflow = []
    for item in geometry.get("nodes") or []:
        node_id = str(item.get("nodeId") or "")
        definition = definitions.get(node_id) or {}
        checks_structural_container = (
            node_id in validation_ids
            or str(definition.get("parentNodeId") or "") in root_ids
        )
        if (
            definition.get("isDecorative")
            or (definition.get("hasChildren") and not checks_structural_container)
        ):
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
    parser.add_argument("--compatibility-matrix", type=Path)
    parser.add_argument("--minimum-ios", default="16.0")
    parser.add_argument("--allow-missing-devices", action="store_true")
    parser.add_argument("--reuse-captures", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    case_ids = [case["id"] for case in args.case]
    if len(case_ids) != len(set(case_ids)):
        parser.error("each runtime profile id may only appear once")
    compatibility = (
        json.loads(args.compatibility_matrix.read_text(encoding="utf-8"))
        if args.compatibility_matrix else None
    )
    planned_profiles = {
        str(item.get("id")): item
        for item in (compatibility or {}).get("profiles") or []
        if item.get("id")
    }
    devices = available_device_names()
    capture_script = Path(__file__).with_name("capture_ios_states.py")
    results = []
    missing_devices = []
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for case in args.case:
        case_id = case["id"]
        case_dir = args.out_dir / case_id
        planned = planned_profiles.get(case_id)
        if compatibility and not planned:
            results.append({**case, "status": "failed", "reason": "profile-not-declared-in-compatibility-matrix"})
            continue
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
                    "--orientation",
                    case["orientation"],
                ]
                if args.workspace:
                    command.extend(["--workspace", str(args.workspace)])
                subprocess.run(command, text=True, check=True)
        captures = json.loads((case_dir / "captures.json").read_text(encoding="utf-8"))
        initial = next((item for item in captures.get("captures") or [] if item.get("id") == "initial"), None)
        viewport_validation = validate_device_viewport(initial, case["width"], case["height"], case["orientation"])
        geometry_path = Path(initial["geometry"]) if initial and initial.get("geometry") else None
        geometry = json.loads(geometry_path.read_text(encoding="utf-8")) if geometry_path and geometry_path.is_file() else None
        diagnostics = (
            analyze_geometry(manifest, geometry, case["width"])
            if geometry
            else {"status": "review-required", "reason": "missing-geometry"}
        )
        diagnostics["deviceViewportValidation"] = viewport_validation
        diagnostics["appViewportValidation"] = validate_app_viewport(
            geometry, case["width"], case["height"], case["orientation"]
        )
        diagnostics["sizeClasses"] = inferred_size_classes(case["width"], case["height"], case["device"])
        if planned:
            diagnostics["plannedSizeClasses"] = {
                "horizontal": planned.get("horizontalSizeClass"),
                "vertical": planned.get("verticalSizeClass"),
            }
            if (
                planned.get("horizontalSizeClass") != diagnostics["sizeClasses"]["horizontal"]
                or planned.get("verticalSizeClass") != diagnostics["sizeClasses"]["vertical"]
            ):
                diagnostics["status"] = "failed"
                diagnostics["reason"] = "runtime-size-class-does-not-match-plan"
        if diagnostics["appViewportValidation"]["status"] == "failed" or viewport_validation["status"] == "failed":
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
        "compatibilityMatrix": str(args.compatibility_matrix.resolve()) if args.compatibility_matrix else None,
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
