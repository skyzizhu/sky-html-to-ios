#!/usr/bin/env python3
"""Attribute visual differences to UI IR nodes and propose bounded native corrections."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


MAX_AUTOMATIC_ITERATIONS = 3
MIN_IMPROVEMENT_PERCENT = 0.25
MAX_AUTOMATIC_TRANSLATION_POINTS = 12.0
MAX_AUTOMATIC_SIZE_POINTS = 12.0
MAX_AUTOMATIC_SIZE_RATIO = 0.15


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def node_index(irs: list[dict[str, Any]]) -> tuple[dict[str, dict], dict[str, dict]]:
    nodes: dict[str, dict] = {}
    states: dict[str, dict] = {}
    for ir in irs:
        for screen in ir.get("screens") or []:
            for node in screen.get("nodes") or []:
                if node.get("id"):
                    nodes[str(node["id"])] = node
        for state in ir.get("states") or []:
            if state.get("id"):
                states[str(state["id"])] = state
    return nodes, states


def geometry_by_node(state: dict) -> dict[str, dict]:
    report = state.get("geometryReport") or {}
    return {
        str(item.get("nodeId")): item
        for item in report.get("nodes") or []
        if item.get("nodeId")
    }


def changed_geometry_properties(item: dict | None) -> list[str]:
    delta = (item or {}).get("delta") or {}
    return [
        key
        for key in ("x", "y", "width", "height")
        if abs(finite(delta.get(key))) >= 1
    ]


def proposed_geometry_mutation(
    state_id: str,
    active_state_id: str,
    node: dict,
    geometry: dict | None,
    target: str,
) -> tuple[dict | None, list[str]]:
    if target != "layout-contract" or not geometry:
        return None, ["mutation-target-has-no-deterministic-geometry-operation"]
    if str(geometry.get("geometryConfidence") or "").lower() != "high":
        return None, ["geometry-confidence-is-not-high"]
    node_id = str(node.get("id") or "")
    if state_id != "initial" and not node_id.startswith("state."):
        return None, ["state-specific-delta-would-mutate-shared-node"]
    if active_state_id and state_id == "initial":
        return None, ["initial-state-correction-has-active-state-owner"]

    rect = (node.get("layout") or {}).get("rect") or {}
    delta = geometry.get("delta") or {}
    operations = []
    rejection_reasons = []
    for prop in ("x", "y", "width", "height"):
        amount = -finite(delta.get(prop))
        if abs(amount) < 0.5:
            continue
        before = finite(rect.get(prop), math.nan)
        if not math.isfinite(before):
            rejection_reasons.append(f"missing-layout-rect-{prop}")
            continue
        limit = MAX_AUTOMATIC_TRANSLATION_POINTS
        if prop in {"width", "height"}:
            limit = min(MAX_AUTOMATIC_SIZE_POINTS, max(2.0, abs(before) * MAX_AUTOMATIC_SIZE_RATIO))
        if abs(amount) > limit:
            rejection_reasons.append(f"{prop}-adjustment-exceeds-{round(limit, 3)}pt-limit")
            continue
        after = before + amount
        if prop in {"width", "height"} and after < 1:
            rejection_reasons.append(f"{prop}-adjustment-would-produce-invalid-size")
            continue
        operations.append({
            "path": f"layout.rect.{prop}",
            "operation": "add",
            "amount": round(amount, 4),
            "expectedBefore": round(before, 4),
            "expectedAfter": round(after, 4),
            "sourceDelta": round(-amount, 4),
            "limitPoints": round(limit, 4),
        })
    if rejection_reasons or not operations:
        if not operations and not rejection_reasons:
            rejection_reasons.append("geometry-delta-is-below-automatic-threshold")
        return None, rejection_reasons
    return {
        "schemaVersion": "ui-ir-bounded-mutation-1.0",
        "owner": "ui-ir",
        "nodeId": node_id,
        "operations": operations,
        "rollback": "discard-corrected-ir-and-restore-source-ir",
    }, []


def attribution(
    state_id: str,
    active_state: dict,
    region: dict,
    geometry: dict | None,
    node: dict,
) -> tuple[str, str, list[str]]:
    category = str(region.get("category") or "unknown")
    semantic = str(node.get("semanticType") or region.get("semanticType") or "")
    state_kind = str(active_state.get("kind") or "").lower()
    changed_geometry = changed_geometry_properties(geometry)
    if any(token in state_kind for token in ("sheet", "modal", "overlay", "popover", "alert")):
        return "presentation-geometry", "presentation-strategy", changed_geometry
    if category == "system-chrome":
        return "system-region-ownership", "native-architecture-plan", changed_geometry
    if changed_geometry:
        axis = "vertical" if set(changed_geometry) <= {"y", "height"} else "horizontal" if set(changed_geometry) <= {"x", "width"} else "two-axis"
        return f"{axis}-geometry", "layout-contract", changed_geometry
    if category == "typography" or region.get("toleranceProfile") == "text":
        return "text-metrics", "text-calibration", ["font", "lineHeight", "baseline", "wrapping"]
    if category == "asset":
        return "asset-rendering", "asset-contract", ["contentMode", "intrinsicSize", "clipping"]
    if category == "control" or semantic in {
        "button", "icon-button", "switch", "slider", "stepper", "text-input",
        "text-area", "select", "segmented-control", "checkbox", "radio",
    }:
        return "control-appearance", "native-control-configuration", ["insets", "background", "border", "cornerRadius", "stateAppearance"]
    if state_id != "initial":
        return "state-layout-or-appearance", "state-delta", []
    return "surface-appearance", "style-tokens", ["background", "border", "shadow", "opacity"]


def recommendation(target: str, control: dict, properties: list[str]) -> str:
    decision = str(control.get("decision") or "")
    if target == "native-control-configuration":
        if decision in {"system-control", "system-control-with-native-wrapper"}:
            return "Keep the system control and adjust official configuration, plain style, content insets, or its native wrapper."
        if decision == "native-composition":
            return "Retain system semantics in child controls and adjust the native composition; do not replace it with a gesture-only view."
        return "Re-evaluate the system control candidate before introducing a custom native control."
    if target == "presentation-strategy":
        return "Re-evaluate system sheet/popover/alert geometry first; use a custom native presentation container only when system configuration cannot match the observed bounds."
    if target == "layout-contract":
        return f"Adjust UI IR constraints for {', '.join(properties) or 'the affected geometry'} and regenerate both stacks."
    if target == "text-calibration":
        return "Adjust resolved font, line height, wrapping, and bounded baseline calibration in the text contract."
    if target == "asset-contract":
        return "Correct the asset intrinsic size, content mode, clipping, or source conversion without rasterizing the surrounding control."
    if target == "native-architecture-plan":
        return "Correct system/custom chrome ownership and apply Safe Area or bar insets exactly once."
    if target == "state-delta":
        return "Correct the owner state delta or conditional subtree instead of generating another business screen."
    return "Adjust the originating UI IR style tokens and regenerate; do not patch generated Swift source directly."


def build_plan(bundle: dict, irs: list[dict], iteration: int, previous: dict | None) -> dict:
    nodes, states = node_index(irs)
    manifest_path = Path(str(bundle.get("manifest") or ""))
    manifest = load_json(manifest_path) if manifest_path.is_file() else {}
    manifest_states = {str(item.get("id")): item for item in manifest.get("states") or []}
    corrections = []
    seen: set[tuple[str, str, str]] = set()

    for state in bundle.get("states") or []:
        if state.get("status") != "failed-threshold":
            continue
        state_id = str(state.get("id") or "")
        active_state_id = str((manifest_states.get(state_id) or {}).get("activeStateId") or "")
        active_state = states.get(active_state_id) or {}
        geometries = geometry_by_node(state)
        regions = ((state.get("report") or {}).get("diagnostics") or {}).get("worstSemanticRegions") or []
        for region in regions:
            if finite(region.get("mismatchRatio")) <= 0:
                continue
            node_id = str(region.get("nodeId") or "")
            if not node_id or node_id not in nodes:
                continue
            node = nodes[node_id]
            control = ((node.get("nativeMapping") or {}).get("nativeControlDecision") or {})
            issue, target, properties = attribution(
                state_id,
                active_state,
                region,
                geometries.get(node_id),
                node,
            )
            key = (state_id, node_id, issue)
            if key in seen:
                continue
            seen.add(key)
            mismatch = finite(region.get("mismatchRatio"))
            edge = finite(region.get("edgeMismatchRatio"))
            confidence = min(0.98, 0.62 + mismatch * 0.25 + (0.08 if geometries.get(node_id) else 0) + (0.06 if control else 0))
            proposed_mutation, automatic_rejection_reasons = proposed_geometry_mutation(
                state_id,
                active_state_id,
                node,
                geometries.get(node_id),
                target,
            )
            corrections.append({
                "id": f"correction.{len(corrections) + 1}",
                "stateId": state_id,
                "activeStateId": active_state_id or None,
                "nodeId": node_id,
                "semanticType": node.get("semanticType"),
                "category": region.get("category"),
                "attribution": issue,
                "mutationTarget": target,
                "mutationScope": "ui-ir-or-derived-contract",
                "properties": properties,
                "metrics": {
                    "mismatchRatio": round(mismatch, 6),
                    "edgeMismatchRatio": round(edge, 6),
                    "geometryDelta": (geometries.get(node_id) or {}).get("delta"),
                    "geometryConfidence": (geometries.get(node_id) or {}).get("geometryConfidence"),
                    "expectedRect": (geometries.get(node_id) or {}).get("expectedRect"),
                    "actualRect": (geometries.get(node_id) or {}).get("actualRect"),
                },
                "nativeControlDecision": control or None,
                "recommendedCorrection": recommendation(target, control, properties),
                "priority": "critical" if region.get("criticality") == "critical" else "high" if mismatch >= 0.35 else "medium",
                "confidence": round(confidence, 3),
                "automaticEligible": confidence >= 0.75 and proposed_mutation is not None,
                "proposedMutation": proposed_mutation,
                "automaticRejectionReasons": automatic_rejection_reasons,
                "prohibitedMutation": "generated-swift-source",
            })

    corrections.sort(key=lambda item: (
        {"critical": 0, "high": 1, "medium": 2}.get(item["priority"], 3),
        -finite(item["metrics"].get("mismatchRatio")),
    ))
    fidelity = finite((bundle.get("summary") or {}).get("fidelityPercent"))
    previous_fidelity = finite((previous or {}).get("summary", {}).get("sourceFidelityPercent"), -1)
    improvement = round(fidelity - previous_fidelity, 4) if previous_fidelity >= 0 else None
    stop_reasons = []
    if iteration > MAX_AUTOMATIC_ITERATIONS:
        stop_reasons.append("maximum-automatic-iterations-reached")
    if improvement is not None and improvement < MIN_IMPROVEMENT_PERCENT:
        stop_reasons.append("fidelity-improvement-below-threshold")
    if not corrections:
        stop_reasons.append("no-attributable-node-level-corrections")
    automatic_count = sum(bool(item["automaticEligible"]) for item in corrections)
    if corrections and automatic_count == 0:
        stop_reasons.append("no-safe-machine-applicable-corrections")
    next_action = "human-review" if stop_reasons and corrections else "complete" if not corrections else "apply-plan-and-regenerate"
    return {
        "schemaVersion": "visual-correction-plan-1.0",
        "policy": {
            "nativeControlPreference": "system-first-visual-fit-gated",
            "mutationOwnership": "ui-ir-and-derived-contracts-only",
            "maxAutomaticIterations": MAX_AUTOMATIC_ITERATIONS,
            "minimumFidelityImprovementPercent": MIN_IMPROVEMENT_PERCENT,
            "maximumAutomaticTranslationPoints": MAX_AUTOMATIC_TRANSLATION_POINTS,
            "maximumAutomaticSizePoints": MAX_AUTOMATIC_SIZE_POINTS,
            "maximumAutomaticSizeRatio": MAX_AUTOMATIC_SIZE_RATIO,
            "customFallback": "only-after-system-control-fit-is-insufficient",
        },
        "iteration": iteration,
        "sourceManifest": bundle.get("manifest"),
        "corrections": corrections,
        "summary": {
            "sourceFidelityPercent": fidelity,
            "previousFidelityPercent": previous_fidelity if previous_fidelity >= 0 else None,
            "improvementPercent": improvement,
            "correctionCount": len(corrections),
            "automaticEligibleCount": automatic_count,
            "systemControlCorrectionCount": sum(
                item["mutationTarget"] == "native-control-configuration"
                for item in corrections
            ),
            "stopReasons": stop_reasons,
            "nextAction": next_action,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_bundle", type=Path)
    parser.add_argument("--ir", action="append", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--previous-plan", type=Path)
    args = parser.parse_args()
    if args.iteration < 1:
        parser.error("--iteration must be at least 1")
    bundle = load_json(args.review_bundle)
    if bundle.get("schemaVersion") != "visual-review-bundle-2.0":
        parser.error("review bundle must use visual-review-bundle-2.0")
    irs = [load_json(path) for path in args.ir]
    previous = load_json(args.previous_plan) if args.previous_plan else None
    plan = build_plan(bundle, irs, args.iteration, previous)
    plan["sourceReviewBundle"] = str(args.review_bundle.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **plan["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
