#!/usr/bin/env python3
"""Validate compatibility matrix and native API fallback plan consistency."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--api-fallback-plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    matrix = load(args.matrix)
    fallback = load(args.api_fallback_plan)
    issues = []
    if matrix.get("schemaVersion") != "ios-compatibility-matrix-1.0":
        issues.append("unsupported-compatibility-matrix-schema")
    if fallback.get("schemaVersion") != "native-api-fallback-plan-1.0":
        issues.append("unsupported-api-fallback-plan-schema")
    for key in ("uiStack", "minimumIOS", "runtimeBaseline"):
        left = matrix.get(key)
        right = fallback.get(key) if key != "runtimeBaseline" else (fallback.get(key) or {}).get("minimumIOS")
        if str(left) != str(right):
            issues.append(f"contract-mismatch:{key}")
    if not (matrix.get("layoutPolicy") or {}).get("usesAutoLayout"):
        issues.append("auto-layout-policy-missing")
    if (matrix.get("layoutPolicy") or {}).get("wholePageScalingAllowed") is not False:
        issues.append("whole-page-scaling-not-forbidden")
    profiles = {str(item.get("id")): item for item in matrix.get("profiles") or [] if item.get("id")}
    for profile_id in ("compact-phone", "phone-375", "baseline-phone", "large-phone", "landscape-phone", "ipad-split-compact", "ipad-regular"):
        if profile_id not in profiles:
            issues.append(f"required-profile-missing:{profile_id}")
    if (profiles.get("landscape-phone") or {}).get("orientation") != "landscape":
        issues.append("landscape-profile-orientation-invalid")
    blocked = (fallback.get("summary") or {}).get("blockedCapabilityIDs") or []
    if blocked:
        issues.extend(f"blocked-required-capability:{item}" for item in blocked)
    status = "passed" if not issues else "failed"
    report = {
        "schemaVersion": "ios-compatibility-validation-1.0",
        "status": status,
        "issues": issues,
        "summary": {
            "profileCount": len(matrix.get("profiles") or []),
            "requiredCapabilityCount": (fallback.get("summary") or {}).get("requiredCapabilityCount", 0),
            "fallbackCapabilityCount": len((fallback.get("summary") or {}).get("fallbackCapabilityIDs") or []),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **report}, indent=2))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
