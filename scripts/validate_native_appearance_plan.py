#!/usr/bin/env python3
"""Validate node appearance ownership and transitional layout mirror consistency."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--native-layout-plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    layout = json.loads(args.native_layout_plan.read_text(encoding="utf-8"))
    issues = []
    if plan.get("schemaVersion") != "native-appearance-plan-1.0":
        issues.append({"code": "SCHEMA_VERSION_INVALID"})
    if plan.get("layoutPlanSha256") != hashlib.sha256(args.native_layout_plan.read_bytes()).hexdigest():
        issues.append({"code": "STALE_LAYOUT_PLAN_PROVENANCE"})
    appearance_screens = {str(item.get("screenId") or ""): item for item in plan.get("screens") or []}
    for layout_screen in layout.get("screens") or []:
        screen_id = str(layout_screen.get("screenId") or "")
        actual = {str(item.get("nodeId") or ""): item for item in (appearance_screens.get(screen_id) or {}).get("nodes") or []}
        expected = {str(item.get("nodeId") or ""): item for item in layout_screen.get("nodes") or []}
        if set(actual) != set(expected):
            issues.append({"code": "APPEARANCE_NODE_SET_MISMATCH", "screenId": screen_id})
        for node_id, node in actual.items():
            mirror = (expected.get(node_id) or {}).get("appearance") or {}
            for key in ("cornerRadiiXPt", "cornerRadiiYPt", "borderWidthsPt", "borderColors", "borderStyles"):
                if node.get(key) != mirror.get(key) or not isinstance(node.get(key), list) or len(node[key]) != 4:
                    issues.append({"code": "APPEARANCE_MIRROR_MISMATCH", "screenId": screen_id, "nodeId": node_id, "field": key})
            if (node.get("typography") or {}).get("metricOwner") != "native-layout-plan":
                issues.append({"code": "TYPOGRAPHY_METRIC_OWNER_MISSING", "screenId": screen_id, "nodeId": node_id})
    report = {
        "schemaVersion": "native-appearance-plan-validation-1.0",
        "status": "passed" if not issues else "failed",
        "qualityGate": {"passed": not issues, "requiresScreenshots": False, "requiresMultimodalModel": False},
        "issues": issues,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "out": str(args.out.resolve()),
        "status": report["status"],
        "screenCount": len(appearance_screens),
        "nodeCount": sum(len((item or {}).get("nodes") or []) for item in appearance_screens.values()),
    }, ensure_ascii=False, indent=2))
    if issues:
        print("Native appearance plan gate failed.", file=sys.stderr)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
