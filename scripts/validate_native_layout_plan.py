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
    parser.add_argument("--responsive-analysis", action="append", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = load_json(args.plan)
    architecture = load_json(args.architecture_plan)
    graph = load_json(args.layout_graph)
    layout_schema = str(plan.get("schemaVersion") or "")
    if layout_schema not in {"native-layout-plan-1.1", "native-layout-plan-1.2"}:
        raise ValueError("--plan must use native-layout-plan-1.1 or native-layout-plan-1.2")
    requires_geometry_system = layout_schema == "native-layout-plan-1.2"
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
    expected_responsive = {str(path.resolve()): sha256(path) for path in (args.responsive_analysis or [])}
    actual_responsive = {str(item.get("path") or ""): str(item.get("sha256") or "") for item in plan.get("responsiveInputs") or []}
    if expected_responsive != actual_responsive:
        add("STALE_RESPONSIVE_LAYOUT_ANALYSIS", None, "Native layout plan does not match the supplied responsive analyses.")

    ir_screens: dict[str, dict[str, Any]] = {}
    ir_context_by_screen: dict[str, dict[str, Any]] = {}
    target_height_by_screen: dict[str, float] = {}
    ir_states_by_screen: dict[str, dict[str, dict[str, Any]]] = {}
    for path in args.ir:
        payload = load_json(path)
        payload_screens = payload.get("screens") or []
        payload_screen_ids = [str(item.get("id") or "") for item in payload_screens]
        for screen in payload_screens:
            payload_screen_id = str(screen.get("id") or "")
            ir_screens[payload_screen_id] = screen
            ir_context_by_screen[payload_screen_id] = (payload.get("source") or {}).get("screenContext") or {}
            target_height_by_screen[payload_screen_id] = float(
                ((payload.get("target") or {}).get("viewportPt") or {}).get("height") or 0
            )
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
        overlap_relations_by_container: dict[str, list[dict[str, Any]]] = {}
        for relation in (graph_screens.get(screen_id) or {}).get("relations") or []:
            if relation.get("kind") == "overlap-order":
                overlap_relations_by_container.setdefault(
                    str(relation.get("containerNodeId") or ""), []
                ).append(relation)
        for container_id, container in plan_containers.items():
            expected_order = [str(item) for item in (architecture_relations.get(container_id) or {}).get("orderedChildNodeIds") or []]
            if container.get("orderedChildNodeIds") != expected_order:
                add("LAYOUT_VISUAL_ORDER_MISMATCH", screen_id, "Container visual order changed during lowering.", container_id)
            paint_order = [str(item) for item in container.get("paintOrderNodeIds") or []]
            expected_paint_order = [
                str(item)
                for item in (architecture_relations.get(container_id) or {}).get("paintChildNodeIds")
                or expected_order
            ]
            if len(paint_order) != len(set(paint_order)) or set(paint_order) != set(expected_paint_order):
                add("LAYOUT_PAINT_ORDER_SET_MISMATCH", screen_id, "Container paint order must contain every visual child exactly once.", container_id)
            paint_index = {node_id: index for index, node_id in enumerate(paint_order)}
            for relation in overlap_relations_by_container.get(container_id) or []:
                back_id = str(relation.get("backNodeId") or "")
                front_id = str(relation.get("frontNodeId") or "")
                if back_id not in paint_index or front_id not in paint_index or paint_index[back_id] >= paint_index[front_id]:
                    add("LAYOUT_PAINT_ORDER_MISMATCH", screen_id, "Overlapping children do not preserve the browser stacking order.", str(relation.get("id") or container_id))
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
            if requires_geometry_system:
                geometry = container.get("geometrySystem") or {}
                if geometry.get("schemaVersion") != "container-geometry-system-1.0":
                    add("CONTAINER_GEOMETRY_SYSTEM_MISSING", screen_id, "Container has no executable geometry system.", container_id)
                if geometry.get("containerNodeId") != container_id or geometry.get("axis") != container.get("axis"):
                    add("CONTAINER_GEOMETRY_OWNER_MISMATCH", screen_id, "Geometry system owner or axis differs from its container.", container_id)
                geometry_children = [
                    str(item.get("nodeId") or "")
                    for item in geometry.get("childContracts") or []
                    if isinstance(item, dict)
                ]
                if geometry_children != expected_order:
                    add("CONTAINER_GEOMETRY_CHILD_ORDER_MISMATCH", screen_id, "Geometry system must preserve every child in visual order.", container_id)
                if geometry.get("mainAxisDistribution") not in {"equal-share", "source-sized"}:
                    add("INVALID_MAIN_AXIS_DISTRIBUTION", screen_id, "Container main-axis distribution is not executable.", container_id)
                valid_sizing_modes = {"equal-share", "fixed", "intrinsic", "flexible", "parent-relative"}
                if any(
                    str(item.get("mainAxisSizingMode") or "") not in valid_sizing_modes
                    for item in geometry.get("childContracts") or []
                    if isinstance(item, dict)
                ):
                    add("INVALID_CHILD_GEOMETRY_SIZING", screen_id, "Geometry child has an unsupported main-axis sizing mode.", container_id)
                if geometry.get("mainAxisDistribution") == "equal-share" and any(
                    item.get("mainAxisSizingMode") != "equal-share" or float(item.get("weight") or 0) <= 0
                    for item in geometry.get("childContracts") or []
                    if isinstance(item, dict)
                ):
                    add("INCOMPLETE_EQUAL_SHARE_GEOMETRY", screen_id, "Every child in an equal-share container must consume one positive share.", container_id)
                required_solve_steps = {
                    "resolve-container-content-box", "measure-intrinsic-children",
                    "resolve-parent-relative-children", "distribute-residual-main-axis-space",
                    "resolve-cross-axis-alignment",
                }
                if not required_solve_steps.issubset(set(geometry.get("solveOrder") or [])):
                    add("INCOMPLETE_GEOMETRY_SOLVE_ORDER", screen_id, "Geometry system is missing a required layout pass.", container_id)
            architecture_sizing = {
                str(item.get("nodeId") or ""): item
                for item in (architecture_relations.get(container_id) or {}).get("childSizing") or []
                if isinstance(item, dict) and item.get("nodeId")
            }
            planned_sizing = {
                str(item.get("nodeId") or ""): item
                for item in container.get("childSizing") or []
                if isinstance(item, dict) and item.get("nodeId")
            }
            for child_id in expected_order[1:]:
                expected_child = architecture_sizing.get(child_id) or {}
                planned_child = planned_sizing.get(child_id) or {}
                if expected_child.get("gapBeforePt") is not None and planned_child.get("gapBeforePt") is None:
                    add(
                        "PER_CHILD_GAP_MISSING", screen_id,
                        "Linear containers must preserve each child's measured leading gap.", child_id,
                    )
        for node_id, node_plan in plan_nodes.items():
            box = node_plan.get("boxModel") or {}
            content_geometry = node_plan.get("contentGeometry") or {}
            appearance = node_plan.get("appearance") or {}
            for key in ("cornerRadiiXPt", "cornerRadiiYPt", "borderWidthsPt", "borderColors", "borderStyles"):
                values = appearance.get(key)
                if not isinstance(values, list) or len(values) != 4:
                    add("INVALID_APPEARANCE_EDGE_CONTRACT", screen_id, f"{key} must preserve four CSS corners or edges.", node_id)
            for key in ("cornerRadiiXPt", "cornerRadiiYPt", "borderWidthsPt"):
                if any(float(value or 0) < 0 for value in appearance.get(key) or []):
                    add("NEGATIVE_APPEARANCE_GEOMETRY", screen_id, f"{key} cannot contain negative geometry.", node_id)
            if appearance.get("preservesPerCornerGeometry") is not True:
                add("CORNER_GEOMETRY_WAS_FLATTENED", screen_id, "Per-corner CSS geometry must remain executable.", node_id)
            if appearance.get("preservesPerEdgeBorders") is not True:
                add("BORDER_GEOMETRY_WAS_FLATTENED", screen_id, "Per-edge CSS borders must remain executable.", node_id)
            if not 0 <= float(appearance.get("opacity", 1)) <= 1:
                add("INVALID_APPEARANCE_OPACITY", screen_id, "Appearance opacity must be in the closed 0...1 interval.", node_id)
            if content_geometry.get("widthMode") not in {"fixed", "intrinsic", "flexible", "parent-relative"}:
                add("INVALID_CONTENT_WIDTH_MODE", screen_id, "Node content width mode is not executable.", node_id)
            if content_geometry.get("heightMode") not in {"fixed", "intrinsic", "flexible", "parent-relative"}:
                add("INVALID_CONTENT_HEIGHT_MODE", screen_id, "Node content height mode is not executable.", node_id)
            if float(content_geometry.get("sourceWidthPt") or 0) < 0 or float(content_geometry.get("sourceHeightPt") or 0) < 0:
                add("NEGATIVE_CONTENT_GEOMETRY", screen_id, "Node content geometry cannot be negative.", node_id)
            aspect_ratio = content_geometry.get("aspectRatio")
            if aspect_ratio is not None and float(aspect_ratio) <= 0:
                add("INVALID_CONTENT_ASPECT_RATIO", screen_id, "Content aspect ratio must be positive.", node_id)
            if content_geometry.get("singleLine") is True and int(content_geometry.get("lineCount") or 1) > 1:
                add("SINGLE_LINE_CONTENT_CONFLICT", screen_id, "Single-line content cannot declare multiple measured lines.", node_id)
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
            compositing = node_plan.get("compositing") or {}
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
            extracted_owner = str((((ir_nodes.get(node_id) or {}).get("source") or {}).get("positioning") or {}).get("offsetParentNodeId") or "")
            if scheme == "absolute" and extracted_owner in ir_nodes and positioning.get("containingBlockNodeId") != extracted_owner:
                add(
                    "EXTRACTED_POSITIONING_OWNER_CHANGED", screen_id,
                    "Absolute positioning owner differs from the browser offsetParent.", node_id,
                )
            stacking_owner = compositing.get("stackingContextOwnerNodeId")
            if compositing.get("createsStackingContext") is True and stacking_owner not in {None, node_id} and stacking_owner not in ir_nodes:
                add("STACKING_CONTEXT_OWNER_MISSING", screen_id, "Stacking context owner is absent from the screen node set.", node_id)
            if compositing.get("clipOwnerNodeId") not in {None, node_id}:
                add("CLIP_OWNER_MISMATCH", screen_id, "Overflow clipping must be owned by the source border box.", node_id)
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
            item_sizing = collection.get("itemSizingByNodeId") or {}
            if set(item_sizing) != set(collection.get("itemNodeIds") or []):
                add("COLLECTION_ITEM_SIZING_SET_MISMATCH", screen_id, "Every reusable item requires an item-level sizing contract.", container_id)
            for item_id, item_contract in item_sizing.items():
                if item_contract.get("widthMode") not in {"full-width", "fixed", "fractional", "estimated"}:
                    add("INVALID_ITEM_WIDTH_MODE", screen_id, "Item-level width mode is not executable.", item_id)
                if item_contract.get("heightMode") not in {"fixed", "estimated", "aspect-ratio"}:
                    add("INVALID_ITEM_HEIGHT_MODE", screen_id, "Item-level height mode is not executable.", item_id)
                if int(item_contract.get("columnSpan") or 1) < 1 or int(item_contract.get("rowSpan") or 1) < 1:
                    add("INVALID_ITEM_GRID_SPAN", screen_id, "Item-level Grid spans must be positive.", item_id)
            breakpoints = collection.get("responsiveBreakpoints") or []
            target_widths = [float(item.get("targetWidthPt") or 0) for item in breakpoints]
            container_widths = [float(item.get("containerWidthPt") or 0) for item in breakpoints]
            layouts_by_container_width: dict[float, set[tuple[int, float, float]]] = {}
            for item in breakpoints:
                container_width = round(float(item.get("containerWidthPt") or 0), 3)
                layouts_by_container_width.setdefault(container_width, set()).add((
                    int(item.get("columnCount") or 0),
                    round(float(item.get("itemWidthPt") or 0), 3),
                    round(float(item.get("itemHeightPt") or 0), 3),
                ))
            if (
                any(width <= 0 for width in target_widths)
                or len(target_widths) != len(set(target_widths))
                or any(width <= 0 for width in container_widths)
                or any(len(layouts) > 1 for layouts in layouts_by_container_width.values())
            ):
                add(
                    "INVALID_COLLECTION_BREAKPOINTS",
                    screen_id,
                    "Responsive collection breakpoints require unique positive target widths, positive container widths, and one executable layout per container width.",
                    container_id,
                )
            if any(int(item.get("columnCount") or 0) < 1 for item in breakpoints):
                add("INVALID_BREAKPOINT_COLUMN_COUNT", screen_id, "Every responsive breakpoint requires at least one column.", container_id)
            adaptive = collection.get("adaptiveColumns") or {}
            if adaptive and adaptive.get("minimumItemWidthPt") is not None and float(adaptive["minimumItemWidthPt"]) <= 0:
                add("INVALID_ADAPTIVE_ITEM_WIDTH", screen_id, "Adaptive Grid minimum item width must be positive.", container_id)
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
        root_id = str(ir_screen.get("rootNodeId") or "")
        root_style = (ir_nodes.get(root_id) or {}).get("style") or {}
        viewport_background = (ir_context_by_screen.get(screen_id) or {}).get("viewportBackground") or {}
        expected_background = str(viewport_background.get("backgroundColor") or "").lower()
        actual_background = str(root_style.get("backgroundColor") or "").lower()
        transparent_values = {"", "transparent", "rgba(0, 0, 0, 0)"}
        if expected_background not in transparent_values and actual_background in transparent_values:
            add(
                "VIEWPORT_BACKGROUND_NOT_INHERITED", screen_id,
                "The content root must inherit the visible viewport background before native generation.", root_id,
            )
        target_height = target_height_by_screen.get(screen_id, 0)
        for region_name in ("topBar", "bottomBar"):
            region = (ir_screen.get("regions") or {}).get(region_name) or {}
            region_id = str(region.get("nodeId") or "")
            if region_id and region_id not in ir_nodes:
                add("CRITICAL_REGION_NODE_MISSING", screen_id, f"{region_name} references a missing node.", region_id)
            if region_name == "bottomBar" and region_id in ir_nodes and target_height > 0:
                region_rect = (ir_nodes[region_id].get("layout") or {}).get("rect") or {}
                region_bottom = float(region_rect.get("y") or 0) + float(region_rect.get("height") or 0)
                if abs(region_bottom - target_height) > max(target_height * 0.18, 80):
                    add(
                        "BOTTOM_REGION_GEOMETRY_IMPLAUSIBLE", screen_id,
                        "Bottom region is not geometrically attached to the target viewport.", region_id,
                    )
        for compound in screen_plan.get("compoundControls") or []:
            slot_ids = [str(item) for item in compound.get("orderedSlotIds") or []]
            slots = [str(item.get("slotId") or "") for item in compound.get("orderedSlots") or []]
            if len(slot_ids) < 2 or slot_ids != slots or len(slot_ids) != len(set(slot_ids)):
                add("INVALID_COMPOUND_SLOT_ORDER", screen_id, "Compound-control slots must be unique and preserve visual order.", str(compound.get("nodeId") or ""))
            for index, slot in enumerate(compound.get("orderedSlots") or []):
                geometry = slot.get("contentGeometry") or {}
                if geometry.get("widthMode") not in {"fixed", "intrinsic", "flexible", "parent-relative"}:
                    add("INVALID_COMPOUND_SLOT_WIDTH_MODE", screen_id, "Compound slot has no executable width mode.", str(slot.get("slotId") or ""))
                if geometry.get("heightMode") not in {"fixed", "intrinsic", "flexible", "parent-relative"}:
                    add("INVALID_COMPOUND_SLOT_HEIGHT_MODE", screen_id, "Compound slot has no executable height mode.", str(slot.get("slotId") or ""))
                if index == 0 and slot.get("gapBeforePt") is not None:
                    add("INVALID_COMPOUND_INITIAL_GAP", screen_id, "The first compound slot cannot have a leading gap.", str(slot.get("slotId") or ""))
                if float(slot.get("gapBeforePt") or 0) < 0:
                    add("NEGATIVE_COMPOUND_SLOT_GAP", screen_id, "Compound slot gaps cannot be negative.", str(slot.get("slotId") or ""))
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
