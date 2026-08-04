#!/usr/bin/env python3
"""Validate scroll ownership, coordinate spaces, Safe Area, and region attachment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = load(args.plan)
    issues: list[dict[str, Any]] = []

    def add(code: str, screen_id: str, message: str, node_id: str | None = None) -> None:
        issues.append({"code": code, "screenId": screen_id, "nodeId": node_id, "message": message})

    if plan.get("schemaVersion") != "scroll-and-attachment-plan-1.0":
        add("SCHEMA_VERSION_INVALID", "", "Expected scroll-and-attachment-plan-1.0.")
    for screen in plan.get("screens") or []:
        screen_id = str(screen.get("screenId") or "")
        nodes = {str(item.get("nodeId") or ""): item for item in screen.get("nodes") or []}
        root_owner = str(screen.get("rootScrollOwnerNodeId") or "")
        root_axis = str(screen.get("rootScrollAxis") or "none")
        if root_owner and root_owner not in nodes:
            add("ROOT_SCROLL_OWNER_MISSING", screen_id, "Root scroll owner is not present in node contracts.", root_owner)
        if root_axis == "both" and screen.get("rootBidirectionalScrollExplicit") is not True:
            add("ROOT_BIDIRECTIONAL_SCROLL_REQUIRES_EXPLICIT_REVIEW", screen_id, "Page roots may not scroll on both axes without an explicit specialized canvas or data-table contract.", root_owner)
        if (screen.get("safeArea") or {}).get("subtractFromContainerDimensions") is not False:
            add("SAFE_AREA_DOUBLE_SUBTRACTION", screen_id, "Safe Area must not be subtracted from scroll container dimensions.")
        for edge, region in (screen.get("regions") or {}).items():
            node_id = region.get("nodeId")
            if not node_id:
                continue
            lifted = bool(region.get("liftedFromContent"))
            if lifted and region.get("scrollOwnerNodeId") is not None:
                add("LIFTED_REGION_HAS_SCROLL_OWNER", screen_id, f"Lifted {edge} region must be owned by the viewport.", str(node_id))
            if region.get("behavior") == "scroll-away" and lifted:
                add("SCROLL_AWAY_REGION_LIFTED", screen_id, f"Scroll-away {edge} region must remain in content.", str(node_id))
            if region.get("subtractSafeAreaFromDimensions") is not False:
                add("REGION_SAFE_AREA_DOUBLE_SUBTRACTION", screen_id, f"{edge} region subtracts Safe Area twice.", str(node_id))
        for node_id, node in nodes.items():
            owner_id = str(node.get("scrollOwnerNodeId") or "")
            node_axis = str(node.get("scrollAxis") or "none")
            if owner_id and owner_id not in nodes:
                add("SCROLL_OWNER_MISSING", screen_id, "Scroll owner is not present in this screen.", node_id)
            if node.get("attachment") == "viewport-fixed" and owner_id:
                add("FIXED_NODE_HAS_SCROLL_OWNER", screen_id, "Viewport-fixed node cannot move with a scroll owner.", node_id)
            if node.get("attachment") == "scroll-sticky" and not owner_id:
                add("STICKY_NODE_OWNER_MISSING", screen_id, "Sticky node requires a scroll owner.", node_id)
            if owner_id and owner_id in nodes:
                owner_axis = str(nodes[owner_id].get("scrollAxis") or "none")
                if node_axis != "none" and node_axis == owner_axis and not node.get("allowsSameAxisNestedScroll"):
                    add("SAME_AXIS_NESTED_SCROLL", screen_id, "Nested scrolling on the same axis is forbidden by default.", node_id)

    report = {
        "schemaVersion": "scroll-and-attachment-validation-1.0",
        "status": "passed" if not issues else "failed",
        "qualityGate": {"passed": not issues, "requiresScreenshots": False, "requiresMultimodalModel": False},
        "issues": issues,
        "summary": {"screenCount": len(plan.get("screens") or []), "errorCount": len(issues)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
