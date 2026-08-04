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
    if plan.get("schemaVersion") != "native-layout-plan-1.1":
        raise ValueError("--plan must use native-layout-plan-1.1")
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
    ir_states_by_screen: dict[str, dict[str, dict[str, Any]]] = {}
    for path in args.ir:
        payload = load_json(path)
        payload_screens = payload.get("screens") or []
        payload_screen_ids = [str(item.get("id") or "") for item in payload_screens]
        for screen in payload_screens:
            ir_screens[str(screen.get("id") or "")] = screen
        default_owner = payload_screen_ids[0] if len(payload_screen_ids) == 1 else ""
        for state in payload.get("states") or []:
            owner = str(state.get("ownerScreenId") or default_owner)
            if owner:
                ir_states_by_screen.setdefault(owner, {})[str(state.get("id") or "")] = state
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
            if container.get("layoutAlgorithm") not in {"stack", "wrapping-stack", "grid", "positioned-overlay"}:
                add("INVALID_LAYOUT_ALGORITHM", screen_id, "Container has no executable layout algorithm.", container_id)
            if container.get("wraps") is True and container.get("layoutAlgorithm") != "wrapping-stack":
                add("WRAP_ALGORITHM_MISMATCH", screen_id, "A wrapping container must lower to wrapping-stack.", container_id)
            if container.get("axis") == "grid":
                grid = container.get("grid") or {}
                if not grid.get("columnTracks"):
                    add("GRID_TRACKS_MISSING", screen_id, "Grid containers require explicit column tracks.", container_id)
            if float(container.get("rowGapPt") or 0) < 0 or float(container.get("columnGapPt") or 0) < 0:
                add("NEGATIVE_CONTAINER_GAP", screen_id, "Container row/column gaps cannot be negative.", container_id)
        for node_id, node_plan in plan_nodes.items():
            box = node_plan.get("boxModel") or {}
            if box.get("boxSizing") not in {"border-box", "content-box"}:
                add("UNSUPPORTED_BOX_SIZING", screen_id, "Box sizing must be border-box or content-box.", node_id)
            for key in ("borderBoxWidthPt", "borderBoxHeightPt", "contentWidthPt", "contentHeightPt"):
                if float(box.get(key) or 0) < 0:
                    add("NEGATIVE_BOX_DIMENSION", screen_id, f"{key} cannot be negative.", node_id)
            for key in (
                "widthContract", "heightContract", "minWidthContract", "maxWidthContract",
                "minHeightContract", "maxHeightContract",
            ):
                contract = box.get(key) or {}
                if contract.get("referenceAxis") not in {"horizontal", "vertical"}:
                    add("INVALID_LENGTH_REFERENCE", screen_id, f"{key} requires an explicit reference axis.", node_id)
                if contract.get("kind") == "calculation" and not contract.get("terms"):
                    add("UNRESOLVED_CALC_CONTRACT", screen_id, f"{key} calc() expression has no executable terms.", node_id)
            positioning = node_plan.get("positioning") or {}
            grid_item = node_plan.get("gridItem") or {}
            for key in ("columnSpan", "rowSpan"):
                if grid_item.get(key) is not None and int(grid_item[key]) < 1:
                    add("INVALID_GRID_SPAN", screen_id, f"{key} must be at least one.", node_id)
            scheme = positioning.get("scheme")
            if scheme not in {"static", "relative", "absolute", "fixed", "sticky"}:
                add("INVALID_POSITIONING_SCHEME", screen_id, "Node positioning scheme is unsupported.", node_id)
            if scheme in {"absolute", "sticky"} and not positioning.get("containingBlockNodeId"):
                add("POSITIONING_OWNER_MISSING", screen_id, "Positioned node has no containing-block owner.", node_id)
            if scheme == "fixed" and positioning.get("coordinateSpace") != "viewport":
                add("FIXED_COORDINATE_SPACE_INVALID", screen_id, "Fixed nodes must use the viewport coordinate space.", node_id)
            if scheme in {"absolute", "fixed"} and not positioning.get("nativeOwnerNodeId"):
                add("NATIVE_POSITIONING_OWNER_MISSING", screen_id, "Positioned node has no executable native owner.", node_id)
        architecture_layers = (architecture_screens.get(screen_id) or {}).get("layers") or {}
        architecture_sections = {
            str(item.get("sourceNodeId") or ""): item
            for item in ((architecture_layers.get("reusableContent") or {}).get("sections") or [])
            if isinstance(item, dict) and item.get("sourceNodeId")
        }
        strategies = {
            str(item.get("nodeId") or ""): str(item.get("kind") or "")
            for item in ((architecture_layers.get("contentContainer") or {}).get("nodeStrategies") or [])
            if isinstance(item, dict)
        }
        expected_collection_ids = {
            node_id for node_id, strategy in strategies.items()
            if strategy in {"table-view", "collection-view", "compositional-collection"}
            and node_id in architecture_sections
        }
        collection_layouts = {
            str(item.get("containerNodeId") or ""): item
            for item in screen_plan.get("collectionLayouts") or []
            if isinstance(item, dict)
        }
        if set(collection_layouts) != expected_collection_ids:
            add("COLLECTION_LAYOUT_SET_MISMATCH", screen_id, "Reusable native containers must have exactly one collection layout contract.")
        for container_id, collection in collection_layouts.items():
            section = architecture_sections.get(container_id) or {}
            if collection.get("nativeContainerKind") != strategies.get(container_id):
                add("COLLECTION_KIND_MISMATCH", screen_id, "Collection layout kind differs from architecture selection.", container_id)
            if [str(item) for item in collection.get("itemNodeIds") or []] != [str(item) for item in section.get("itemNodeIds") or []]:
                add("COLLECTION_ITEM_ORDER_MISMATCH", screen_id, "Collection item order differs from reusable section order.", container_id)
            if collection.get("scrollAxis") not in {"horizontal", "vertical"}:
                add("INVALID_COLLECTION_AXIS", screen_id, "Native collection must own exactly one scroll axis.", container_id)
            if collection.get("allowsSameAxisNestedScroll") is not False:
                add("SAME_AXIS_SCROLL_NOT_REJECTED", screen_id, "Native collection must reject same-axis nested scrolling.", container_id)
            sizing = collection.get("itemSizing") or {}
            if sizing.get("widthMode") not in {"full-width", "fixed", "fractional", "estimated"}:
                add("INVALID_COLLECTION_WIDTH_MODE", screen_id, "Collection item width mode is not executable.", container_id)
            if sizing.get("heightMode") not in {"fixed", "estimated", "aspect-ratio"}:
                add("INVALID_COLLECTION_HEIGHT_MODE", screen_id, "Collection item height mode is not executable.", container_id)
            header_id = str(collection.get("headerNodeId") or "")
            if collection.get("pinsHeader") is True:
                header_plan = plan_nodes.get(header_id) or {}
                if not header_id or (header_plan.get("positioning") or {}).get("scheme") != "sticky":
                    add("PINNED_HEADER_NOT_STICKY", screen_id, "Pinned collection headers require sticky source evidence.", container_id)
        pinned_node_ids = {
            str(collection.get("headerNodeId") or "")
            for collection in collection_layouts.values()
            if collection.get("pinsHeader") is True
        } | {
            str(collection.get("footerNodeId") or "")
            for collection in collection_layouts.values()
            if collection.get("pinsFooter") is True
        }
        region_node_ids = {
            str((region or {}).get("nodeId") or "")
            for region in (architecture_layers.get("screenRegions") or {}).values()
            if isinstance(region, dict)
        }
        for node_id, node_plan in plan_nodes.items():
            if (node_plan.get("positioning") or {}).get("scheme") != "sticky":
                continue
            if node_id not in pinned_node_ids and node_id not in region_node_ids:
                add(
                    "UNRESOLVED_STICKY_MAPPING", screen_id,
                    "Sticky nodes must map to a native screen region or pinned collection supplementary view.", node_id,
                )
        for compound in screen_plan.get("compoundControls") or []:
            slot_ids = [str(item) for item in compound.get("orderedSlotIds") or []]
            slots = [str(item.get("slotId") or "") for item in compound.get("orderedSlots") or []]
            if len(slot_ids) < 2 or slot_ids != slots or len(slot_ids) != len(set(slot_ids)):
                add("INVALID_COMPOUND_SLOT_ORDER", screen_id, "Compound-control slots must be unique and preserve visual order.", str(compound.get("nodeId") or ""))
        ir_states = ir_states_by_screen.get(screen_id) or {}
        planned_states = {str(item.get("stateId") or ""): item for item in screen_plan.get("stateLayouts") or []}
        expected_state_ids = {
            state_id for state_id, state in ir_states.items()
            if (state.get("stateDelta") or {}).get("operations")
        }
        if set(planned_states) != expected_state_ids:
            add("STATE_LAYOUT_SET_MISMATCH", screen_id, "State layout contracts differ from UI IR state deltas.")
        for state_id, state_plan in planned_states.items():
            for operation in state_plan.get("operations") or []:
                generated_id = str(operation.get("generatedRootNodeId") or "")
                if generated_id and generated_id not in plan_nodes:
                    add("STATE_LAYOUT_NODE_MISSING", screen_id, "State operation references an unplanned generated node.", state_id)
        screen_errors = sum(item.get("screenId") == screen_id for item in issues)
        summaries.append({"screenId": screen_id, "status": "passed" if screen_errors == 0 else "failed", "errorCount": screen_errors})

    report = {
        "schemaVersion": "native-layout-plan-validation-1.1",
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
