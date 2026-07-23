#!/usr/bin/env python3
"""Pair HTML/iOS state screenshots and create multimodal review artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def resolve_image_capability(requested: str) -> tuple[str, str]:
    if requested in {"available", "unavailable"}:
        return requested, "cli"
    configured = os.environ.get("CODEX_AGENT_IMAGE_CAPABILITY", "").strip().lower()
    if configured in {"available", "true", "yes", "1"}:
        return "available", "environment"
    if configured in {"unavailable", "false", "no", "0"}:
        return "unavailable", "environment"
    return "unknown", "unresolved"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--html-dir", type=Path, required=True)
    parser.add_argument("--ios-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=int, default=24)
    parser.add_argument("--max-mismatch-ratio", type=float, default=0.08)
    parser.add_argument("--max-mean-difference", type=float, default=18.0)
    parser.add_argument("--max-critical-region-mismatch", type=float, default=0.16)
    parser.add_argument("--max-text-edge-mismatch", type=float, default=0.30)
    parser.add_argument("--advisory", action="store_true", help="Write failures without returning a non-zero quality-gate exit code")
    parser.add_argument(
        "--multimodal-capability",
        choices=("auto", "available", "unavailable"),
        default="auto",
        help="Use actual image-inspection capability; never infer this from a model name.",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    visual_diff = Path(__file__).with_name("visual_diff.py")
    results = []
    for state in manifest.get("states") or []:
        state_id = state["id"]
        html_image = args.html_dir / f"{state_id}.png"
        ios_image = args.ios_dir / f"{state_id}.png"
        if not html_image.exists() or not ios_image.exists():
            results.append({
                "id": state_id,
                "required": state.get("required", True),
                "status": "missing",
                "html": str(html_image.resolve()),
                "ios": str(ios_image.resolve()),
            })
            continue
        state_out = args.out_dir / state_id
        command = [
            sys.executable, str(visual_diff), str(html_image), str(ios_image),
            "--out-dir", str(state_out), "--threshold", str(args.threshold),
            "--regions-json", str(args.manifest),
        ]
        for mask in manifest.get("comparisonMasks") or []:
            rect = mask.get("rect") if isinstance(mask, dict) else mask
            if isinstance(rect, list) and len(rect) == 4:
                command.extend(["--mask", ",".join(str(round(float(value))) for value in rect)])
        completed = subprocess.run(command, check=True, text=True, capture_output=True)
        report = json.loads(completed.stdout)
        diagnostics = report.get("diagnostics") or {}
        failures = []
        if report["resizedCurrent"]:
            failures.append({"gate": "exact-size", "actual": report["originalCurrentSize"], "expected": report["referenceSize"]})
        if report["mismatchRatio"] > args.max_mismatch_ratio:
            failures.append({"gate": "global-mismatch-ratio", "actual": report["mismatchRatio"], "maximum": args.max_mismatch_ratio})
        if report["meanAbsoluteDifference"] > args.max_mean_difference:
            failures.append({"gate": "mean-absolute-difference", "actual": report["meanAbsoluteDifference"], "maximum": args.max_mean_difference})
        if diagnostics.get("maxCriticalRegionMismatchRatio", 0) > args.max_critical_region_mismatch:
            failures.append({"gate": "critical-region-mismatch", "actual": diagnostics["maxCriticalRegionMismatchRatio"], "maximum": args.max_critical_region_mismatch})
        if diagnostics.get("maxTextEdgeMismatchRatio", 0) > args.max_text_edge_mismatch:
            failures.append({"gate": "text-edge-mismatch", "actual": diagnostics["maxTextEdgeMismatchRatio"], "maximum": args.max_text_edge_mismatch})
        passed = not failures
        fidelity = max(0.0, 100.0 * (1.0 - (
            report["mismatchRatio"] * 0.45
            + min(report["meanAbsoluteDifference"] / 255.0, 1.0) * 0.25
            + min(diagnostics.get("maxCriticalRegionMismatchRatio", 0), 1.0) * 0.20
            + min(diagnostics.get("maxTextEdgeMismatchRatio", 0), 1.0) * 0.10
        )))
        if report["resizedCurrent"]:
            fidelity = min(fidelity, 95.0)
        results.append({
            "id": state_id,
            "required": state.get("required", True),
            "status": "passed" if passed else "failed-threshold",
            "gateFailures": failures,
            "fidelityPercent": round(fidelity, 4),
            "exactPixelMatch": not report["resizedCurrent"] and report["mismatchRatio"] == 0 and report["meanAbsoluteDifference"] == 0,
            "report": report,
        })

    missing_required = [item["id"] for item in results if item["required"] and item["status"] == "missing"]
    review_required = [item["id"] for item in results if item["status"] == "failed-threshold"]
    required_failures = [item["id"] for item in results if item["required"] and item["status"] == "failed-threshold"]
    required_states = [item for item in results if item["required"]]
    fidelity_percent = round(
        sum(float(item.get("fidelityPercent") or 0) for item in required_states) / max(1, len(required_states)),
        4,
    )
    image_capability, capability_source = resolve_image_capability(args.multimodal_capability)
    if missing_required:
        multimodal_status = "blocked-missing-screenshots"
    elif not review_required:
        multimodal_status = "not-needed"
    elif image_capability == "available":
        multimodal_status = "pending-agent-review"
    elif image_capability == "unavailable":
        multimodal_status = "not-run"
    else:
        multimodal_status = "capability-check-required"
    bundle = {
        "schemaVersion": "visual-review-bundle-2.0",
        "manifest": str(args.manifest.resolve()),
        "threshold": args.threshold,
        "maxMismatchRatio": args.max_mismatch_ratio,
        "thresholds": {
            "maxMeanDifference": args.max_mean_difference,
            "maxCriticalRegionMismatch": args.max_critical_region_mismatch,
            "maxTextEdgeMismatch": args.max_text_edge_mismatch,
        },
        "capabilityGate": {
            "requested": args.multimodal_capability,
            "imageInspection": image_capability,
            "source": capability_source,
            "policy": "capability-based-not-model-name",
            "multimodalReviewStatus": multimodal_status,
        },
        "states": results,
        "summary": {
            "passed": sum(item["status"] == "passed" for item in results),
            "reviewRequired": review_required,
            "missingRequired": missing_required,
            "requiredFailures": required_failures,
            "qualityGate": "passed" if not missing_required and not required_failures else "failed",
            "targetFidelityPercent": 100.0,
            "fidelityPercent": fidelity_percent,
            "exactFidelityAchieved": bool(required_states) and not missing_required and all(item.get("exactPixelMatch") for item in required_states),
            "readyForAgentReview": not missing_required and bool(review_required) and image_capability == "available",
            "multimodalReviewStatus": multimodal_status,
        },
    }
    bundle_path = args.out_dir / "review-bundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prompt = f"""# Multimodal Visual Review

Capability gate: `{image_capability}`. Review status: `{multimodal_status}`.

Only perform this stage when the current Agent can actually inspect image inputs or open the generated PNG files. Do not infer capability from a model name. If capability is `unknown`, test the available image inspection tool first and rebuild this bundle with `--multimodal-capability available` or `unavailable`.

Review each state marked `failed-threshold`. Open its `comparison.png`, `overlay.png`, `regions.png`, and `report.json`. Start from `diagnostics.worstSemanticRegions`, preserving nodeId and category. Return JSON containing: stateId, severity, category, rect, relatedNodeId, observation, recommendedFix, and confidence. Distinguish structural layout errors from acceptable text antialiasing, shadows, blur, and dynamic system chrome. Multimodal review may explain a deterministic failure but cannot silently convert a required failure to passed; the screenshot must be regenerated and the gate rerun. Do not approve a required state whose screenshot is missing. When capability is unavailable, retain the deterministic reports and mark multimodal review `not-run`.
"""
    (args.out_dir / "agent-review-instructions.md").write_text(prompt, encoding="utf-8")
    print(json.dumps({"out": str(bundle_path.resolve()), **bundle["summary"]}, ensure_ascii=False, indent=2))
    if missing_required:
        return 1
    if required_failures and not args.advisory:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
