#!/usr/bin/env python3
"""Validate that generated Swift and payload files consume the structural contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-structure-validation-1.0"


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def issue(code: str, screen_id: str | None, message: str, reference_id: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "error",
        "screenId": screen_id,
        "referenceId": reference_id,
        "message": message,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--layout-graph", required=True, type=Path)
    parser.add_argument("--architecture-plan", required=True, type=Path)
    parser.add_argument("--native-layout-plan", required=True, type=Path)
    parser.add_argument("--generated-dir", required=True, type=Path)
    parser.add_argument("--generation-manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    graph = load_json(args.layout_graph)
    architecture = load_json(args.architecture_plan)
    native_layout = load_json(args.native_layout_plan)
    generation = load_json(args.generation_manifest)
    if manifest.get("schemaVersion") != "native-structure-manifest-1.0":
        raise ValueError("--manifest must use native-structure-manifest-1.0")
    if graph.get("schemaVersion") != "layout-relation-graph-1.0":
        raise ValueError("--layout-graph must use layout-relation-graph-1.0")
    if architecture.get("schemaVersion") != "native-architecture-plan-1.1":
        raise ValueError("--architecture-plan must use native-architecture-plan-1.1")
    if native_layout.get("schemaVersion") != "native-layout-plan-1.1":
        raise ValueError("--native-layout-plan must use native-layout-plan-1.1")
    if generation.get("schemaVersion") != "html-to-ios-generation-1.0":
        raise ValueError("--generation-manifest must use html-to-ios-generation-1.0")

    issues: list[dict[str, Any]] = []
    for capability, record in (manifest.get("runtimeCapabilities") or {}).items():
        if record.get("required") is True and record.get("consumed") is not True:
            issues.append(issue(
                "NATIVE_LAYOUT_RUNTIME_CAPABILITY_MISSING", None,
                f"Generated runtime does not execute required layout capability {capability!r}.", capability,
            ))
    actual_graph_hash = sha256_file(args.layout_graph)
    if manifest.get("layoutRelationGraphSha256") != actual_graph_hash:
        issues.append(issue(
            "STALE_LAYOUT_GRAPH_PROVENANCE", None,
            "Native structure manifest was not generated from the current layout relation graph.",
        ))
    if manifest.get("architecturePlanSha256") != sha256_file(args.architecture_plan):
        issues.append(issue(
            "STALE_ARCHITECTURE_PLAN_PROVENANCE", None,
            "Native structure manifest was not generated from the current native architecture plan.",
        ))
    if manifest.get("nativeLayoutPlanSha256") != sha256_file(args.native_layout_plan):
        issues.append(issue(
            "STALE_NATIVE_LAYOUT_PLAN_PROVENANCE", None,
            "Native structure manifest was not generated from the current executable layout plan.",
        ))
    if manifest.get("generationManifestSha256") != sha256_file(args.generation_manifest):
        issues.append(issue(
            "STALE_GENERATION_MANIFEST_PROVENANCE", None,
            "Native structure manifest does not reference the current generation manifest.",
        ))

    graph_screens = {str(item.get("screenId") or ""): item for item in graph.get("screens") or []}
    manifest_screens = {str(item.get("screenId") or ""): item for item in manifest.get("screens") or []}
    architecture_screens = {str(item.get("screenId") or ""): item for item in architecture.get("screens") or []}
    if set(graph_screens) != set(manifest_screens):
        issues.append(issue(
            "NATIVE_SCREEN_SET_MISMATCH", None,
            f"Graph screens {sorted(graph_screens)} do not match native manifest screens {sorted(manifest_screens)}.",
        ))

    generation_files = generation.get("files") or {}
    if generation.get("conflicts"):
        issues.append(issue(
            "GENERATION_CONFLICTS_PRESENT", None,
            "Generated structural consumers have unresolved file conflicts.",
        ))

    screen_summaries = []
    for screen_id, graph_screen in graph_screens.items():
        native_screen = manifest_screens.get(screen_id) or {}
        graph_node_ids = {str(item.get("nodeId") or "") for item in graph_screen.get("nodes") or []}
        native_nodes = {str(item.get("nodeId") or ""): item for item in native_screen.get("nodes") or []}
        if graph_node_ids != set(native_nodes):
            issues.append(issue(
                "NATIVE_NODE_SET_MISMATCH", screen_id,
                "Generated node-consumption records do not exactly match graph nodes.",
            ))
        for node_id, record in native_nodes.items():
            if record.get("status") == "missing":
                issues.append(issue(
                    "NATIVE_NODE_NOT_CONSUMED", screen_id,
                    "Layout graph node is absent from generated native output.", node_id,
                ))

        graph_relation_ids = {str(item.get("id") or "") for item in graph_screen.get("relations") or []}
        native_relations = {str(item.get("relationId") or ""): item for item in native_screen.get("relations") or []}
        if graph_relation_ids != set(native_relations):
            issues.append(issue(
                "NATIVE_RELATION_SET_MISMATCH", screen_id,
                "Generated relation-consumption records do not exactly match graph relations.",
            ))
        for relation_id, record in native_relations.items():
            if record.get("status") not in {"consumed", "optimized-equivalent"}:
                issues.append(issue(
                    "LAYOUT_RELATION_NOT_CONSUMED", screen_id,
                    f"Generated native output did not consume {record.get('kind')!r}.", relation_id,
                ))
            failed_checks = [item for item in record.get("checks") or [] if item.get("passed") is not True]
            if failed_checks:
                issues.append(issue(
                    "NATIVE_RELATION_PROOF_FAILED", screen_id,
                    "One or more native relation evidence checks failed.", relation_id,
                ))

        layout_consumption = native_screen.get("layoutPlanConsumption") or {}
        for record in layout_consumption.get("containers") or []:
            if record.get("status") != "consumed":
                issues.append(issue(
                    "NATIVE_LAYOUT_CONTAINER_NOT_CONSUMED", screen_id,
                    "Generated native container did not consume its executable layout plan.",
                    str(record.get("containerNodeId") or "") or None,
                ))
        for record in layout_consumption.get("compoundControls") or []:
            if record.get("status") != "consumed":
                issues.append(issue(
                    "NATIVE_COMPOUND_LAYOUT_NOT_CONSUMED", screen_id,
                    "Generated compound control did not preserve planned slot order.",
                    str(record.get("nodeId") or "") or None,
                ))
        for record in layout_consumption.get("nodes") or []:
            if record.get("status") not in {"consumed", "optimized-equivalent"}:
                issues.append(issue(
                    "NATIVE_NODE_LAYOUT_NOT_CONSUMED", screen_id,
                    "Generated native node did not consume its sizing or positioning contract.",
                    str(record.get("nodeId") or "") or None,
                ))
        for record in layout_consumption.get("stateLayouts") or []:
            if record.get("status") != "consumed":
                issues.append(issue(
                    "NATIVE_STATE_LAYOUT_NOT_CONSUMED", screen_id,
                    "Generated native state did not consume its layout delta contract.",
                    str(record.get("stateId") or "") or None,
                ))

        architecture_screen = architecture_screens.get(screen_id) or {}
        layers = architecture_screen.get("layers") or {}
        planned_content = layers.get("contentContainer") or {}
        generated_content = native_screen.get("contentContainer") or {}
        for key in ("nodeId", "kind", "scrollAxis"):
            planned = planned_content.get(key)
            generated = generated_content.get(key)
            if planned is not None and str(planned) != str(generated):
                issues.append(issue(
                    "CONTENT_CONTAINER_NOT_CONSUMED", screen_id,
                    f"Generated content container {key}={generated!r} differs from plan {planned!r}.",
                ))

        region_plan = layers.get("screenRegions") or {}
        native_regions = native_screen.get("regions") or {}
        for edge in ("top", "bottom"):
            planned_node = (region_plan.get(edge) or {}).get("nodeId")
            generated_node = (native_regions.get(edge) or {}).get("generatedNodeId")
            if planned_node and str(planned_node) != str(generated_node):
                issues.append(issue(
                    "SCREEN_REGION_NOT_CONSUMED", screen_id,
                    f"Generated {edge} region does not preserve planned native ownership.", str(planned_node),
                ))

        for consumer in native_screen.get("consumerFiles") or []:
            relative = str(consumer.get("relativePath") or "")
            path = args.generated_dir / relative
            generation_entry = generation_files.get(relative) or {}
            if not relative or not path.is_file():
                issues.append(issue(
                    "NATIVE_CONSUMER_FILE_MISSING", screen_id,
                    f"Generated structural consumer file is missing: {relative!r}.", relative or None,
                ))
                continue
            actual_hash = sha256_file(path)
            if consumer.get("sha256") != actual_hash or generation_entry.get("sha256") != actual_hash:
                issues.append(issue(
                    "NATIVE_CONSUMER_HASH_MISMATCH", screen_id,
                    f"Consumer file changed after structural manifest generation: {relative}.", relative,
                ))
            if generation_entry.get("status") == "preserved-user-modified" or generation_entry.get("owned") is False:
                issues.append(issue(
                    "NATIVE_CONSUMER_CONFLICT", screen_id,
                    f"Structural consumer could not be updated because it contains user changes: {relative}.", relative,
                ))

        screen_issue_count = sum(item.get("screenId") == screen_id for item in issues)
        screen_summaries.append({
            "screenId": screen_id,
            "nodeCount": len(graph_node_ids),
            "relationCount": len(graph_relation_ids),
            "errorCount": screen_issue_count,
            "status": "passed" if screen_issue_count == 0 else "failed",
        })

    report = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "passed" if not issues else "failed",
        "qualityGate": {
            "passed": not issues,
            "requiresScreenshots": False,
            "requiresMultimodalModel": False,
        },
        "screens": screen_summaries,
        "issues": issues,
        "summary": {
            "screenCount": len(screen_summaries),
            "errorCount": len(issues),
            "nodeCount": sum(item["nodeCount"] for item in screen_summaries),
            "relationCount": sum(item["relationCount"] for item in screen_summaries),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "status": report["status"], **report["summary"]}, ensure_ascii=False, indent=2))
    if issues:
        codes = ", ".join(sorted({str(item["code"]) for item in issues}))
        print(f"Native structure gate failed: {codes}. See {args.out.resolve()}", file=sys.stderr)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
