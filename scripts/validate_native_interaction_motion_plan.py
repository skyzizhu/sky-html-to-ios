#!/usr/bin/env python3
"""Validate native behavior ownership without visual inference."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


VALID_OWNERS = {"application", "navigation-stack", "screen-host", "reusable-content", "source-component"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    issues = []
    if plan.get("schemaVersion") != "native-interaction-motion-plan-1.0":
        issues.append({"code": "SCHEMA_VERSION_INVALID"})
    for screen in plan.get("screens") or []:
        screen_id = screen.get("screenId")
        ids = []
        for item in (screen.get("actions") or []) + (screen.get("motions") or []):
            ids.append(item.get("id"))
            if item.get("owner") not in VALID_OWNERS or not item.get("ownerId") or not item.get("executor"):
                issues.append({"code": "BEHAVIOR_OWNER_MISSING", "screenId": screen_id, "itemId": item.get("id")})
        if len(ids) != len(set(ids)):
            issues.append({"code": "DUPLICATE_BEHAVIOR_ID", "screenId": screen_id})
    report = {
        "schemaVersion": "native-interaction-motion-plan-validation-1.0",
        "status": "passed" if not issues else "failed",
        "qualityGate": {"passed": not issues, "requiresScreenshots": False, "requiresMultimodalModel": False},
        "issues": issues,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if issues:
        print("Native interaction and motion plan gate failed.", file=sys.stderr)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
