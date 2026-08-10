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
    parser.add_argument("--application-plan", type=Path)
    parser.add_argument("--native-layout-plan", required=True, type=Path)
    parser.add_argument("--scroll-attachment-plan", type=Path)
    parser.add_argument("--control-configuration-plan", type=Path)
    parser.add_argument("--presentation-plan", type=Path)
    parser.add_argument("--appearance-plan", type=Path)
    parser.add_argument("--interaction-motion-plan", type=Path)
    parser.add_argument("--compatibility-matrix", type=Path)
    parser.add_argument("--api-fallback-plan", type=Path)
    parser.add_argument("--generated-dir", required=True, type=Path)
    parser.add_argument("--generation-manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    graph = load_json(args.layout_graph)
    architecture = load_json(args.architecture_plan)
    application_plan = load_json(args.application_plan) if args.application_plan else {}
    native_layout = load_json(args.native_layout_plan)
    scroll_attachment = load_json(args.scroll_attachment_plan) if args.scroll_attachment_plan else {}
    control_configuration = load_json(args.control_configuration_plan) if args.control_configuration_plan else {}
    presentation_plan = load_json(args.presentation_plan) if args.presentation_plan else {}
    appearance_plan = load_json(args.appearance_plan) if args.appearance_plan else {}
    interaction_motion_plan = load_json(args.interaction_motion_plan) if args.interaction_motion_plan else {}
    compatibility_matrix = load_json(args.compatibility_matrix) if args.compatibility_matrix else {}
    api_fallback_plan = load_json(args.api_fallback_plan) if args.api_fallback_plan else {}
    generation = load_json(args.generation_manifest)
    if manifest.get("schemaVersion") != "native-structure-manifest-1.0":
        raise ValueError("--manifest must use native-structure-manifest-1.0")
    if graph.get("schemaVersion") != "layout-relation-graph-1.0":
        raise ValueError("--layout-graph must use layout-relation-graph-1.0")
    if architecture.get("schemaVersion") != "native-architecture-plan-1.1":
        raise ValueError("--architecture-plan must use native-architecture-plan-1.1")
    if args.application_plan and application_plan.get("schemaVersion") != "native-application-plan-1.0":
        raise ValueError("--application-plan must use native-application-plan-1.0")
    if native_layout.get("schemaVersion") != "native-layout-plan-1.1":
        raise ValueError("--native-layout-plan must use native-layout-plan-1.1")
    if args.scroll_attachment_plan and scroll_attachment.get("schemaVersion") != "scroll-and-attachment-plan-1.0":
        raise ValueError("--scroll-attachment-plan must use scroll-and-attachment-plan-1.0")
    if args.control_configuration_plan and control_configuration.get("schemaVersion") not in {"native-control-configuration-plan-1.0", "native-control-configuration-plan-1.1"}:
        raise ValueError("--control-configuration-plan must use native-control-configuration-plan-1.0 or 1.1")
    if args.presentation_plan and presentation_plan.get("schemaVersion") != "native-presentation-plan-1.0":
        raise ValueError("--presentation-plan must use native-presentation-plan-1.0")
    if args.appearance_plan and appearance_plan.get("schemaVersion") != "native-appearance-plan-1.0":
        raise ValueError("--appearance-plan must use native-appearance-plan-1.0")
    if args.interaction_motion_plan and interaction_motion_plan.get("schemaVersion") != "native-interaction-motion-plan-1.0":
        raise ValueError("--interaction-motion-plan must use native-interaction-motion-plan-1.0")
    if args.compatibility_matrix and compatibility_matrix.get("schemaVersion") != "ios-compatibility-matrix-1.0":
        raise ValueError("--compatibility-matrix must use ios-compatibility-matrix-1.0")
    if args.api_fallback_plan and api_fallback_plan.get("schemaVersion") != "native-api-fallback-plan-1.0":
        raise ValueError("--api-fallback-plan must use native-api-fallback-plan-1.0")
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
    cross_cutting = manifest.get("crossCuttingContractConsumption") or {}
    for argument, manifest_key, consumption_key, label in (
        (args.application_plan, "applicationPlanSha256", "application", "application"),
        (args.appearance_plan, "appearancePlanSha256", "appearance", "appearance"),
        (args.interaction_motion_plan, "interactionMotionPlanSha256", "interactionMotion", "interaction and motion"),
    ):
        if not argument:
            continue
        if manifest.get(manifest_key) != sha256_file(argument):
            issues.append(issue(
                f"STALE_{consumption_key.upper()}_PLAN_PROVENANCE", None,
                f"Native structure manifest was not generated from the current {label} plan.",
            ))
        if cross_cutting.get(consumption_key) != "consumed":
            issues.append(issue(
                f"{consumption_key.upper()}_PLAN_NOT_CONSUMED", None,
                f"Generated native structure does not report the {label} contract as consumed.",
            ))
    if manifest.get("nativeLayoutPlanSha256") != sha256_file(args.native_layout_plan):
        issues.append(issue(
            "STALE_NATIVE_LAYOUT_PLAN_PROVENANCE", None,
            "Native structure manifest was not generated from the current executable layout plan.",
        ))
    if args.scroll_attachment_plan and manifest.get("scrollAttachmentPlanSha256") != sha256_file(args.scroll_attachment_plan):
        issues.append(issue(
            "STALE_SCROLL_ATTACHMENT_PLAN_PROVENANCE", None,
            "Native structure manifest was not generated from the current scroll and attachment plan.",
        ))
    if args.control_configuration_plan and manifest.get("controlConfigurationPlanSha256") != sha256_file(args.control_configuration_plan):
        issues.append(issue(
            "STALE_CONTROL_CONFIGURATION_PLAN_PROVENANCE", None,
            "Native structure manifest was not generated from the current control configuration plan.",
        ))
    if args.presentation_plan and manifest.get("presentationPlanSha256") != sha256_file(args.presentation_plan):
        issues.append(issue("STALE_PRESENTATION_PLAN_PROVENANCE", None, "Native structure manifest was not generated from the current presentation plan."))
    if args.compatibility_matrix and manifest.get("compatibilityMatrixSha256") != sha256_file(args.compatibility_matrix):
        issues.append(issue("STALE_COMPATIBILITY_MATRIX_PROVENANCE", None, "Native structure manifest was not generated from the current compatibility matrix."))
    if args.api_fallback_plan and manifest.get("apiFallbackPlanSha256") != sha256_file(args.api_fallback_plan):
        issues.append(issue("STALE_API_FALLBACK_PLAN_PROVENANCE", None, "Native structure manifest was not generated from the current API fallback plan."))
    for record in manifest.get("apiFallbackConsumption") or []:
        if record.get("required") is True and record.get("consumed") is not True:
            issues.append(issue(
                "NATIVE_API_FALLBACK_NOT_CONSUMED", None,
                f"Generated runtime does not consume compatibility decision {record.get('capabilityId')!r}.",
                str(record.get("capabilityId") or "") or None,
            ))
    if manifest.get("generationManifestSha256") != sha256_file(args.generation_manifest):
        issues.append(issue(
            "STALE_GENERATION_MANIFEST_PROVENANCE", None,
            "Native structure manifest does not reference the current generation manifest.",
        ))

    graph_screens = {str(item.get("screenId") or ""): item for item in graph.get("screens") or []}
    manifest_screens = {str(item.get("screenId") or ""): item for item in manifest.get("screens") or []}
    architecture_screens = {str(item.get("screenId") or ""): item for item in architecture.get("screens") or []}
    scroll_screens = {str(item.get("screenId") or ""): item for item in scroll_attachment.get("screens") or []}
    control_screens = {str(item.get("screenId") or ""): item for item in control_configuration.get("screens") or []}
    presentation_screens = {str(item.get("screenId") or ""): item for item in presentation_plan.get("screens") or []}
    if set(graph_screens) != set(manifest_screens):
        issues.append(issue(
            "NATIVE_SCREEN_SET_MISMATCH", None,
            f"Graph screens {sorted(graph_screens)} do not match native manifest screens {sorted(manifest_screens)}.",
        ))
    if args.scroll_attachment_plan and set(scroll_screens) != set(manifest_screens):
        issues.append(issue(
            "SCROLL_ATTACHMENT_SCREEN_SET_MISMATCH", None,
            f"Scroll-plan screens {sorted(scroll_screens)} do not match native manifest screens {sorted(manifest_screens)}.",
        ))
    if args.control_configuration_plan and set(control_screens) != set(manifest_screens):
        issues.append(issue(
            "CONTROL_CONFIGURATION_SCREEN_SET_MISMATCH", None,
            f"Control-plan screens {sorted(control_screens)} do not match native manifest screens {sorted(manifest_screens)}.",
        ))
    if args.presentation_plan and set(presentation_screens) != set(manifest_screens):
        issues.append(issue("PRESENTATION_SCREEN_SET_MISMATCH", None, "Presentation-plan screens do not match native manifest screens."))

    generation_files = generation.get("files") or {}
    if generation.get("conflicts"):
        issues.append(issue(
            "GENERATION_CONFLICTS_PRESENT", None,
            "Generated structural consumers have unresolved file conflicts.",
        ))

    screen_summaries = []
    merge_strategies = {
        "svg-resource-merged",
        "svg-computed-state-merged",
        "attributed-text-merged",
        "selection-indicator-merged",
        "native-decoration-merged",
        "native-animation-merged",
        "compound-control-merged",
        "presentation-backdrop-merged",
        "native-control-option-model-merged",
        "system-chrome-merged",
    }

    def valid_optimized_record(record: dict[str, Any]) -> bool:
        if record.get("status") != "optimized-equivalent":
            return False
        strategy = str(record.get("strategy") or "")
        if strategy == "empty-structural-wrapper-elided":
            return True
        if strategy == "detached-native-owner":
            checks = record.get("checks") or {}
            return all(
                checks.get(key) is True
                for key in ("node", "widthKind", "heightKind", "positioning", "owner", "nativeOwner")
            ) and checks.get("positionedUnderOwner") is False
        evidence = record.get("mergeEvidence") or {}
        return bool(
            strategy in merge_strategies
            and evidence.get("ownerNodeId")
            and evidence.get("sourceNodeIds")
            and evidence.get("nativePrimitive")
        )

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
            elif record.get("status") == "optimized-equivalent" and not valid_optimized_record(record):
                issues.append(issue(
                    "NATIVE_NODE_OPTIMIZATION_EVIDENCE_INVALID", screen_id,
                    "Optimized native node is missing supported, auditable merge evidence.", node_id,
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
            if record.get("status") != "consumed" and not valid_optimized_record(record):
                issues.append(issue(
                    "NATIVE_LAYOUT_CONTAINER_NOT_CONSUMED", screen_id,
                    "Generated native container did not consume its executable layout plan.",
                    str(record.get("containerNodeId") or "") or None,
                ))
        for record in layout_consumption.get("collections") or []:
            if record.get("status") != "consumed":
                issues.append(issue(
                    "NATIVE_COLLECTION_LAYOUT_NOT_CONSUMED", screen_id,
                    "Generated native collection did not consume item sizing, supplementary, or scroll-isolation contracts.",
                    str(record.get("containerNodeId") or "") or None,
                ))
        for record in layout_consumption.get("compoundControls") or []:
            if record.get("status") != "consumed" and not valid_optimized_record(record):
                issues.append(issue(
                    "NATIVE_COMPOUND_LAYOUT_NOT_CONSUMED", screen_id,
                    "Generated compound control did not preserve planned slot order.",
                    str(record.get("nodeId") or "") or None,
                ))
        for record in layout_consumption.get("nodes") or []:
            if record.get("status") != "consumed" and not valid_optimized_record(record):
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
            scroll_region = (((native_screen.get("scrollAttachmentConsumption") or {}).get("regions") or {}).get(edge) or {})
            if planned_node and str(planned_node) != str(generated_node) and scroll_region.get("status") != "consumed":
                issues.append(issue(
                    "SCREEN_REGION_NOT_CONSUMED", screen_id,
                    f"Generated {edge} region does not preserve planned native ownership.", str(planned_node),
                ))

        if args.scroll_attachment_plan:
            planned_scroll = scroll_screens.get(screen_id) or {}
            consumed_scroll = native_screen.get("scrollAttachmentConsumption") or {}
            if str(consumed_scroll.get("rootScrollOwnerNodeId") or "") != str(planned_scroll.get("rootScrollOwnerNodeId") or ""):
                issues.append(issue(
                    "SCROLL_OWNER_NOT_CONSUMED", screen_id,
                    "Generated native structure does not preserve the planned root scroll owner.",
                ))
            if str(consumed_scroll.get("generatedScrollAxis") or "none") != str(planned_scroll.get("rootScrollAxis") or "none"):
                issues.append(issue(
                    "SCROLL_AXIS_NOT_CONSUMED", screen_id,
                    "Generated native structure does not preserve the planned root scroll axis.",
                ))
            planned_safe = planned_scroll.get("safeArea") or {}
            generated_safe = (consumed_scroll.get("safeArea") or {}).get("generated") or {}
            if str(generated_safe.get("owner") or "") != str(planned_safe.get("owner") or ""):
                issues.append(issue(
                    "SAFE_AREA_OWNER_NOT_CONSUMED", screen_id,
                    "Generated native structure does not preserve the planned Safe Area owner.",
                ))
            for edge, record in ((consumed_scroll.get("regions") or {}).items()):
                if record.get("status") != "consumed":
                    issues.append(issue(
                        "SCROLL_REGION_ATTACHMENT_NOT_CONSUMED", screen_id,
                        f"Generated {edge} region does not preserve attachment, behavior, and ownership.",
                        str(record.get("nodeId") or "") or None,
                    ))
        if args.control_configuration_plan:
            planned_ids = {
                str(item.get("nodeId") or "")
                for item in (control_screens.get(screen_id) or {}).get("controls") or []
            }
            records = {
                str(item.get("nodeId") or ""): item
                for item in native_screen.get("controlConfigurationConsumption") or []
            }
            if planned_ids != set(records):
                issues.append(issue(
                    "CONTROL_CONFIGURATION_SET_MISMATCH", screen_id,
                    "Generated native structure does not report every planned system control.",
                ))
            for node_id, record in records.items():
                if record.get("status") != "consumed":
                    issues.append(issue(
                        "CONTROL_CONFIGURATION_NOT_CONSUMED", screen_id,
                        "Generated payload does not consume the planned internal control configuration.", node_id,
                    ))
        if args.presentation_plan:
            planned_ids = {
                str(item.get("stateId") or "")
                for item in (presentation_screens.get(screen_id) or {}).get("presentations") or []
            }
            records = {
                str(item.get("stateId") or ""): item
                for item in native_screen.get("presentationConsumption") or []
            }
            if planned_ids != set(records):
                issues.append(issue(
                    "PRESENTATION_CONSUMPTION_SET_MISMATCH", screen_id,
                    "Generated native structure does not report every planned presentation state.",
                ))
            for state_id, record in records.items():
                if record.get("status") != "consumed":
                    issues.append(issue(
                        "PRESENTATION_PLAN_NOT_CONSUMED", screen_id,
                        "Generated payload does not consume the planned presentation contract.", state_id,
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
