#!/usr/bin/env python3
"""Merge simulator matrix evidence without mutating the generation-time compatibility contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ios-runtime-compatibility-report-1.0"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_status(case: dict[str, Any]) -> str:
    status = str(case.get("status") or "pending")
    if status == "passed":
        return "runtime-validated"
    if status == "review-required":
        return "runtime-review-required"
    if status == "missing-device":
        return "pending-runtime-validation"
    return "runtime-failed" if status == "failed" else "pending-runtime-validation"


def combined_runtime_status(evidence: list[dict[str, Any]]) -> str:
    statuses = [runtime_status(item.get("case") or {}) for item in evidence]
    for candidate in (
        "runtime-failed",
        "runtime-review-required",
        "pending-runtime-validation",
        "runtime-validated",
    ):
        if candidate in statuses:
            return candidate
    return "pending-runtime-validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compatibility-matrix", type=Path, required=True)
    parser.add_argument("--runtime-matrix", type=Path, action="append", default=[])
    parser.add_argument("--require-profile", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    compatibility = load(args.compatibility_matrix)
    if compatibility.get("schemaVersion") != "ios-compatibility-matrix-1.0":
        parser.error("Unsupported compatibility matrix schema")
    reports = [load(path) for path in args.runtime_matrix]
    for report in reports:
        if report.get("schemaVersion") != "responsive-ios-matrix-1.0":
            parser.error("Unsupported responsive runtime matrix schema")

    evidence_by_profile: dict[str, list[dict[str, Any]]] = {}
    for report_path, report in zip(args.runtime_matrix, reports):
        for case in report.get("cases") or []:
            profile_id = str(case.get("id") or "")
            if not profile_id:
                continue
            evidence_by_profile.setdefault(profile_id, []).append({
                "runtimeMatrix": str(report_path.resolve()),
                "runtimeMatrixSha256": digest(report_path),
                "case": case,
            })

    required = set(args.require_profile)
    profiles = []
    known_ids = set()
    for planned in compatibility.get("profiles") or []:
        profile_id = str(planned.get("id") or "")
        known_ids.add(profile_id)
        evidence = evidence_by_profile.get(profile_id) or []
        status = combined_runtime_status(evidence)
        profiles.append({
            "id": profile_id,
            "plannedViewport": planned.get("viewport"),
            "plannedOrientation": planned.get("orientation") or (
                "landscape" if float((planned.get("viewport") or {}).get("width") or 0) > float((planned.get("viewport") or {}).get("height") or 0) else "portrait"
            ),
            "horizontalSizeClass": planned.get("horizontalSizeClass"),
            "verticalSizeClass": planned.get("verticalSizeClass"),
            "sourceValidation": planned.get("validation"),
            "runtimeValidation": status,
            "required": profile_id in required,
            "evidence": evidence,
        })

    unknown_required = sorted(required - known_ids)
    required_failures = [
        item["id"] for item in profiles
        if item["required"] and item["runtimeValidation"] != "runtime-validated"
    ]
    failed = [item["id"] for item in profiles if item["runtimeValidation"] == "runtime-failed"]
    review = [item["id"] for item in profiles if item["runtimeValidation"] == "runtime-review-required"]
    pending = [item["id"] for item in profiles if item["runtimeValidation"] == "pending-runtime-validation"]
    status = "failed" if unknown_required or required_failures or failed else "review-required" if review else "passed"
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "compatibilityMatrix": {
            "path": str(args.compatibility_matrix.resolve()),
            "sha256": digest(args.compatibility_matrix),
        },
        "profiles": profiles,
        "summary": {
            "status": status,
            "runtimeValidatedProfileIDs": [item["id"] for item in profiles if item["runtimeValidation"] == "runtime-validated"],
            "pendingRuntimeProfileIDs": pending,
            "reviewRequiredProfileIDs": review,
            "failedProfileIDs": failed,
            "requiredProfileIDs": sorted(required),
            "requiredProfileFailures": required_failures,
            "unknownRequiredProfileIDs": unknown_required,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **result["summary"]}, ensure_ascii=False, indent=2))
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
