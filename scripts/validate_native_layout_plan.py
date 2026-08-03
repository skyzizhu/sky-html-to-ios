#!/usr/bin/env python3
"""Validate the executable native layout plan against UI IR, architecture, and relation graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--architecture-plan", required=True, type=Path)
    parser.add_argument("--layout-graph", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = load_json(args.plan)
    architecture = load_json(args.architecture_plan)
    graph = load_json(args.layout_graph)
    if plan.get("schemaVersion") != "native-layout-plan-1.0":
        raise ValueError("--plan must use native-layout-plan-1.0")
    issues: list[dict[str, Any]] = []

    def add(code: str, screen_id: str | None, message: str, reference_id: str | None = None) -> None:
        issues.append({"code": code, "severity": "error", "screenId": screen_id, "referenceId": reference_id, "message": message})

    if plan.get("architecturePlanSha256") != sha256(args.architecture_plan):
        add("STALE_LAYOUT_ARCHITECTURE", None, "Native layout plan does not match the architecture plan.")
    if plan.get("layoutRelationGraphSha256") != sha256(args.layout_graph):
        add("STALE_LAYOUT_RELATION_GRAPH", None, "Native layout plan does not match the relation graph.")
    expected_inputs = {str(path.resolve()): sha256(path) for path in args.ir}
    actual_inputs = {str(item.get("path") or ""): str(item.get("sha256") or "") for item in plan.get("inputs") or []}
    if expected_inputs != actual_inputs:
        add("STALE_LAYOUT_UI_IR", None, "Native layout plan does not match the supplied UI IR files.")

    ir_screens: dict[str, dict[str, Any]] = {}
    for path in args.ir:
        for screen in load_json(path).get("screens") or []:
            ir_screens[str(screen.get("id") or "")] = screen
    plan_screens = {str(item.get("screenId") or ""): item for item in plan.get("screens") or []}
    graph_screens = {str(item.get("screenId") or ""): item for item in graph.get("screens") or []}
    architecture_screens = {str(item.get("screenId") or ""): item for item in architecture.get("screens") or []}
    if set(plan_screens) != set(ir_screens):
        add("LAYOUT_SCREEN_SET_MISMATCH", None, "Native layout plan screen set differs from UI IR.")

    summaries = []
    for screen_id, ir_screen in ir_screens.items():
        screen_plan = plan_screens.get(screen_id) or {}
        ir_nodes = {str(item.get("id") or ""): item for item in ir_screen.get("nodes") or []}
        plan_nodes = {str(item.get("nodeId") or ""): item for item in screen_plan.get("nodes") or []}
        if set(ir_nodes) != set(plan_nodes):
            add("LAYOUT_NODE_SET_MISMATCH", screen_id, "Every UI IR node must have one box-model plan.")
        architecture_relations = {
            str(item.get("containerNodeId") or ""): item
            for item in ((((architecture_screens.get(screen_id) or {}).get("layers") or {}).get("contentContainer") or {}).get("layoutRelations") or [])
        }
        plan_containers = {str(item.get("containerNodeId") or ""): item for item in screen_plan.get("containers") or []}
        if set(architecture_relations) != set(plan_containers):
            add("LAYOUT_CONTAINER_SET_MISMATCH", screen_id, "Executable containers differ from architecture layout relations.")
        graph_container_ids = {
            str(item.get("containerNodeId") or "")
            for item in (graph_screens.get(screen_id) or {}).get("containers") or []
        }
        for container_id, container in plan_containers.items():
            expected_order = [str(item) for item in (architecture_relations.get(container_id) or {}).get("orderedChildNodeIds") or []]
            if container.get("orderedChildNodeIds") != expected_order:
                add("LAYOUT_VISUAL_ORDER_MISMATCH", screen_id, "Container visual order changed during lowering.", container_id)
            if container_id in graph_container_ids and not container.get("relationIds"):
                add("LAYOUT_RELATION_EVIDENCE_MISSING", screen_id, "Container has no relation-graph evidence.", container_id)
        for node_id, node_plan in plan_nodes.items():
            box = node_plan.get("boxModel") or {}
            if box.get("boxSizing") not in {"border-box", "content-box"}:
                add("UNSUPPORTED_BOX_SIZING", screen_id, "Box sizing must be border-box or content-box.", node_id)
            for key in ("borderBoxWidthPt", "borderBoxHeightPt", "contentWidthPt", "contentHeightPt"):
                if float(box.get(key) or 0) < 0:
                    add("NEGATIVE_BOX_DIMENSION", screen_id, f"{key} cannot be negative.", node_id)
        for compound in screen_plan.get("compoundControls") or []:
            slot_ids = [str(item) for item in compound.get("orderedSlotIds") or []]
            slots = [str(item.get("slotId") or "") for item in compound.get("orderedSlots") or []]
            if len(slot_ids) < 2 or slot_ids != slots or len(slot_ids) != len(set(slot_ids)):
                add("INVALID_COMPOUND_SLOT_ORDER", screen_id, "Compound-control slots must be unique and preserve visual order.", str(compound.get("nodeId") or ""))
        screen_errors = sum(item.get("screenId") == screen_id for item in issues)
        summaries.append({"screenId": screen_id, "status": "passed" if screen_errors == 0 else "failed", "errorCount": screen_errors})

    report = {
        "schemaVersion": "native-layout-plan-validation-1.0",
        "status": "passed" if not issues else "failed",
        "qualityGate": {"passed": not issues, "requiresScreenshots": False, "requiresMultimodalModel": False},
        "screens": summaries,
        "issues": issues,
        "summary": {"screenCount": len(summaries), "errorCount": len(issues)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "status": report["status"], **report["summary"]}, ensure_ascii=False, indent=2))
    if issues:
        print("Native layout plan gate failed: " + ", ".join(sorted({item["code"] for item in issues})), file=sys.stderr)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
