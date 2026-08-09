#!/usr/bin/env python3
"""Validate global application ownership without screenshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    issues = []
    if plan.get("schemaVersion") != "native-application-plan-1.0":
        issues.append({"code": "SCHEMA_VERSION_INVALID", "message": "Expected native-application-plan-1.0."})
    container = plan.get("applicationContainer") or {}
    memberships = plan.get("screenMemberships") or []
    stacks = {str(item.get("id") or ""): item for item in plan.get("navigationStacks") or []}
    ids = [str(item.get("screenId") or "") for item in memberships]
    if not container.get("id"):
        issues.append({"code": "APPLICATION_CONTAINER_MISSING", "message": "One global application container is required."})
    if not ids or len(ids) != len(set(ids)) or "" in ids:
        issues.append({"code": "SCREEN_MEMBERSHIP_INVALID", "message": "Every screen requires one unique membership."})
    if str(plan.get("initialScreenId") or "") not in set(ids):
        issues.append({"code": "INITIAL_SCREEN_INVALID", "message": "The initial screen must have an application membership."})
    for item in memberships:
        if item.get("applicationContainerId") != container.get("id"):
            issues.append({"code": "APPLICATION_OWNER_MISMATCH", "screenId": item.get("screenId")})
        if str(item.get("navigationStackId") or "") not in stacks:
            issues.append({"code": "NAVIGATION_STACK_MISSING", "screenId": item.get("screenId")})
    tabs = plan.get("tabContainer") or {}
    tab_ids = {str(item.get("id") or "") for item in tabs.get("items") or []}
    if tabs and (len(tab_ids) < 2 or tabs.get("initialTabId") not in tab_ids):
        issues.append({"code": "TAB_CONTAINER_INVALID", "message": "Tab containers require at least two items and a valid initial item."})
    screen_ids = set(ids)
    for stack_id, stack in stacks.items():
        if not stack_id or str(stack.get("rootScreenId") or "") not in screen_ids:
            issues.append({"code": "NAVIGATION_STACK_ROOT_INVALID", "navigationStackId": stack_id})
    if tabs:
        item_targets = {str(item.get("targetScreenId") or "") for item in tabs.get("items") or []}
        if not item_targets or not item_targets <= screen_ids:
            issues.append({"code": "TAB_TARGET_INVALID", "message": "Every tab target must reference a known screen."})
    report = {
        "schemaVersion": "native-application-plan-validation-1.0",
        "status": "passed" if not issues else "failed",
        "qualityGate": {"passed": not issues, "requiresScreenshots": False, "requiresMultimodalModel": False},
        "issues": issues,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if issues:
        print("Native application plan gate failed.", file=sys.stderr)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
