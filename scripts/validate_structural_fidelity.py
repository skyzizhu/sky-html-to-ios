#!/usr/bin/env python3
"""Validate source-to-native structural fidelity without screenshots or multimodal review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "structural-fidelity-report-1.0"
CONTROL_SEMANTICS = {
    "button", "icon-button", "link", "text-input", "secure-input", "search-input",
    "number-input", "date-input", "text-area", "file-input", "checkbox", "switch",
    "radio", "segmented-control", "select", "multi-select", "slider", "stepper",
    "color-picker", "disclosure-trigger", "tab-item", "menu-item",
}
NON_LAYOUT_CONTAINER_SEMANTICS = {"icon", "image", "decoration", "canvas-artwork"}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def issue(code: str, severity: str, screen_id: str, message: str, node_id: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "screenId": screen_id,
        "nodeId": node_id,
        "message": message,
    }


def screen_index(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for document in documents:
        for screen in document.get("screens") or []:
            screen_id = str(screen.get("id") or "")
            if not screen_id or screen_id in result:
                raise ValueError("UI IR inputs must contain unique non-empty screen IDs")
            result[screen_id] = screen
    return result


def validate_screen(
    screen_id: str,
    screen: dict[str, Any],
    graph: dict[str, Any] | None,
    architecture: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    nodes = {
        str(node.get("id") or ""): node
        for node in screen.get("nodes") or []
        if node.get("id")
    }
    children: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        children.setdefault(str(node.get("parentId") or ""), []).append(node_id)

    if not graph:
        issues.append(issue("MISSING_LAYOUT_GRAPH_SCREEN", "error", screen_id, "Layout relation graph has no matching screen."))
        return issues, {"nodeCount": len(nodes), "checks": 1, "passedChecks": 0}

    graph_nodes = {
        str(node.get("nodeId") or ""): node
        for node in graph.get("nodes") or []
        if node.get("nodeId")
    }
    missing_graph_nodes = sorted(set(nodes) - set(graph_nodes))
    extra_graph_nodes = sorted(set(graph_nodes) - set(nodes))
    for node_id in missing_graph_nodes:
        issues.append(issue("NODE_NOT_IN_LAYOUT_GRAPH", "error", screen_id, "UI IR node is missing from the layout graph.", node_id))
    for node_id in extra_graph_nodes:
        issues.append(issue("LAYOUT_GRAPH_NODE_NOT_IN_IR", "error", screen_id, "Layout graph contains a node absent from UI IR.", node_id))

    relation_items = graph.get("relations") or []
    containment = {
        (str(item.get("parentNodeId") or ""), str(item.get("childNodeId") or ""))
        for item in relation_items
        if item.get("kind") == "containment"
    }
    expected_containment = {
        (str(node.get("parentId") or ""), node_id)
        for node_id, node in nodes.items()
        if node.get("parentId")
    }
    for parent_id, child_id in sorted(expected_containment - containment):
        issues.append(issue(
            "MISSING_CONTAINMENT_RELATION", "error", screen_id,
            f"Missing parent-child ownership relation from {parent_id}.", child_id,
        ))
    for parent_id, child_id in sorted(containment - expected_containment):
        issues.append(issue(
            "INVALID_CONTAINMENT_RELATION", "error", screen_id,
            f"Unexpected parent-child ownership relation from {parent_id}.", child_id,
        ))

    containers = {
        str(item.get("containerNodeId") or ""): item
        for item in graph.get("containers") or []
        if item.get("containerNodeId")
    }
    for parent_id, child_ids in children.items():
        if not parent_id or not child_ids:
            continue
        if str((nodes.get(parent_id) or {}).get("semanticType") or "") in NON_LAYOUT_CONTAINER_SEMANTICS:
            # Vector/image internals remain traceable through containment but lower as one
            # native asset/layer, so their path children do not form a native layout container.
            continue
        relation = containers.get(parent_id)
        if not relation:
            issues.append(issue(
                "MISSING_CONTAINER_RELATION", "error", screen_id,
                "Container with children has no ordered layout contract.", parent_id,
            ))
            continue
        ordered = [str(item) for item in relation.get("orderedChildNodeIds") or []]
        if len(ordered) != len(set(ordered)):
            issues.append(issue("DUPLICATE_VISUAL_ORDER_NODE", "error", screen_id, "Visual order contains duplicate children.", parent_id))
        if set(ordered) != set(child_ids):
            issues.append(issue(
                "VISUAL_ORDER_CHILD_SET_MISMATCH", "error", screen_id,
                "Visual order does not contain exactly the container's direct children.", parent_id,
            ))

    scroll_relations = {
        str(item.get("ownerNodeId") or ""): str(item.get("axis") or "none")
        for item in relation_items
        if item.get("kind") == "scroll-axis-ownership"
    }
    for node_id, node in nodes.items():
        axis = str((node.get("layout") or {}).get("scrollAxis") or "none")
        graph_axis = scroll_relations.get(node_id, "none")
        if graph_axis != axis:
            issues.append(issue(
                "SCROLL_AXIS_OWNERSHIP_MISMATCH", "error", screen_id,
                f"UI IR scroll axis {axis!r} does not match graph ownership {graph_axis!r}.", node_id,
            ))

    strict_source = isinstance(screen.get("sourceCoverage"), dict)
    coverage = screen.get("sourceCoverage") or {}
    if strict_source:
        mapped = int(coverage.get("mappedNodeCount") or 0)
        scoped = int(coverage.get("routeScopedNodeCount") or 0)
        excluded = int(coverage.get("excludedNonVisualOrUnsupportedTagCount") or 0)
        if mapped + excluded != scoped or mapped <= 0:
            issues.append(issue(
                "SOURCE_COVERAGE_ACCOUNTING_MISMATCH", "error", screen_id,
                "Mapped and explicitly excluded source nodes do not account for the selected route subtree.",
            ))
        source_runtime_ids = [
            str((node.get("source") or {}).get("runtimeId") or "")
            for node in nodes.values()
            if not (node.get("source") or {}).get("synthetic")
            and not (node.get("source") or {}).get("generatedByStateId")
        ]
        missing_runtime = sum(not runtime_id for runtime_id in source_runtime_ids)
        if missing_runtime:
            issues.append(issue(
                "SOURCE_RUNTIME_ID_MISSING", "error", screen_id,
                f"{missing_runtime} base nodes cannot be traced to the browser render tree.",
            ))
    else:
        issues.append(issue(
            "SOURCE_COVERAGE_UNAVAILABLE", "info", screen_id,
            "Supplied UI IR has no browser source-coverage contract; source accounting is not applicable.",
        ))

    if not architecture:
        issues.append(issue("MISSING_ARCHITECTURE_SCREEN", "error", screen_id, "Native architecture plan has no matching screen."))
    else:
        layers = architecture.get("layers") or {}
        leaf_ids = {
            str(item.get("nodeId") or "")
            for item in (layers.get("leafComponents") or [])
            if item.get("nodeId")
        }
        expected_leaf_ids = {node_id for node_id in nodes if not children.get(node_id)}
        for node_id in sorted(expected_leaf_ids - leaf_ids):
            issues.append(issue(
                "NATIVE_LEAF_NOT_PLANNED", "error", screen_id,
                "Leaf node has no native component in the six-layer architecture.", node_id,
            ))
        architecture_relations = {
            str(item.get("containerNodeId") or ""): item
            for item in ((layers.get("contentContainer") or {}).get("layoutRelations") or [])
            if item.get("containerNodeId")
        }
        for container_id, relation in containers.items():
            planned = architecture_relations.get(container_id)
            if not planned:
                issues.append(issue(
                    "LAYOUT_RELATION_NOT_CONSUMED", "error", screen_id,
                    "Layout relation is absent from the native architecture plan.", container_id,
                ))
            elif planned.get("orderedChildNodeIds") != relation.get("orderedChildNodeIds"):
                issues.append(issue(
                    "NATIVE_VISUAL_ORDER_MISMATCH", "error", screen_id,
                    "Native architecture changes the browser-derived child order.", container_id,
                ))

        region_layers = layers.get("screenRegions") or {}
        regions = screen.get("regions") or {}
        top_node = (regions.get("topBar") or {}).get("nodeId")
        bottom_node = (regions.get("bottomBar") or {}).get("nodeId")
        planned_top = (region_layers.get("top") or {}).get("nodeId")
        planned_bottom = (region_layers.get("bottom") or {}).get("nodeId")
        navigation_style = str((screen.get("navigation") or {}).get("style") or "")
        tab_container = screen.get("tabContainer")
        if top_node and navigation_style == "custom" and planned_top != top_node:
            issues.append(issue("TOP_REGION_OWNERSHIP_MISMATCH", "error", screen_id, "Custom top region is not owned by the native screen region layer.", str(top_node)))
        if bottom_node and not tab_container and planned_bottom != bottom_node:
            issues.append(issue("BOTTOM_REGION_OWNERSHIP_MISMATCH", "error", screen_id, "Bottom region is not owned by the native screen region layer.", str(bottom_node)))

    if strict_source:
        for node_id, node in nodes.items():
            if str(node.get("semanticType") or "") not in CONTROL_SEMANTICS:
                continue
            decision = ((node.get("nativeMapping") or {}).get("nativeControlDecision") or {})
            if not decision:
                issues.append(issue(
                    "CONTROL_DECISION_MISSING", "error", screen_id,
                    "Interactive source node has no system-first native control decision.", node_id,
                ))
            elif decision.get("preserveSystemSemantics") is not True:
                issues.append(issue(
                    "CONTROL_SEMANTICS_NOT_PRESERVED", "error", screen_id,
                    "Interactive node loses native control semantics.", node_id,
                ))

    error_count = sum(item["severity"] == "error" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    checks = max(
        1,
        len(nodes)
        + len(expected_containment)
        + len(containers)
        + len(scroll_relations)
        + (len(nodes) if architecture else 0),
    )
    score = max(0.0, 1.0 - (error_count * 0.08 + warning_count * 0.02))
    return issues, {
        "nodeCount": len(nodes),
        "containerCount": len(containers),
        "relationCount": len(relation_items),
        "checks": checks,
        "passedChecks": max(checks - error_count - warning_count, 0),
        "score": round(score, 4),
        "status": "passed" if error_count == 0 else "failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--layout-graph", required=True, type=Path)
    parser.add_argument("--architecture-plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    ir_documents = [load_json(path) for path in args.ir]
    graph = load_json(args.layout_graph)
    architecture = load_json(args.architecture_plan)
    if graph.get("schemaVersion") != "layout-relation-graph-1.0":
        raise ValueError("--layout-graph must use layout-relation-graph-1.0")
    if architecture.get("schemaVersion") != "native-architecture-plan-1.1":
        raise ValueError("--architecture-plan must use native-architecture-plan-1.1")

    screens = screen_index(ir_documents)
    graph_screens = {str(item.get("screenId") or ""): item for item in graph.get("screens") or []}
    architecture_screens = {str(item.get("screenId") or ""): item for item in architecture.get("screens") or []}
    all_issues: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for screen_id, screen in screens.items():
        issues, summary = validate_screen(
            screen_id,
            screen,
            graph_screens.get(screen_id),
            architecture_screens.get(screen_id),
        )
        all_issues.extend(issues)
        summaries.append({"screenId": screen_id, **summary})

    errors = [item for item in all_issues if item["severity"] == "error"]
    warnings = [item for item in all_issues if item["severity"] == "warning"]
    score = min((item["score"] for item in summaries), default=0.0)
    gate_passed = not errors and score >= 0.92
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "passed" if gate_passed else "failed",
        "qualityGate": {
            "passed": gate_passed,
            "minimumScore": 0.92,
            "score": round(score, 4),
            "requiresScreenshots": False,
            "requiresMultimodalModel": False,
        },
        "screens": summaries,
        "issues": all_issues,
        "summary": {
            "screenCount": len(summaries),
            "errorCount": len(errors),
            "warningCount": len(warnings),
            "infoCount": sum(item["severity"] == "info" for item in all_issues),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **report["summary"], "status": report["status"]}, ensure_ascii=False, indent=2))
    if not gate_passed:
        codes = ", ".join(sorted({str(item["code"]) for item in errors})) or "score-below-threshold"
        print(
            f"Structural fidelity gate failed ({len(errors)} errors, score {score:.4f}): {codes}. "
            f"See {args.out.resolve()}",
            file=sys.stderr,
        )
    return 0 if gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
