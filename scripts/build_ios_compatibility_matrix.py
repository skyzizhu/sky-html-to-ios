#!/usr/bin/env python3
"""Build the target-version, device, size-class, and source-evidence matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ios-compatibility-matrix-1.0"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def version(value: Any) -> tuple[int, ...]:
    return tuple(int(item) for item in re.findall(r"\d+", str(value or ""))) or (0,)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-report", type=Path, required=True)
    parser.add_argument("--api-fallback-plan", type=Path, required=True)
    parser.add_argument("--ir", type=Path, action="append", default=[])
    parser.add_argument("--responsive-analysis", type=Path, action="append", default=[])
    parser.add_argument("--ui-stack", choices=("swiftui", "uikit"), required=True)
    parser.add_argument("--minimum-ios", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    sdk = load(args.sdk_report)
    fallback = load(args.api_fallback_plan)
    irs = [load(path) for path in args.ir]
    analyses = [load(path) for path in args.responsive_analysis]
    widths = sorted({
        int(width)
        for analysis in analyses
        for width in analysis.get("sampleWidthsPt") or []
        if isinstance(width, (int, float))
    })
    target_viewports = [
        (ir.get("target") or {}).get("viewportPt") or {}
        for ir in irs
    ]
    baseline_width = int(next((item.get("width") for item in target_viewports if item.get("width")), 393))
    baseline_height = int(next((item.get("height") for item in target_viewports if item.get("height")), 852))
    if not widths:
        widths = [320, 375, baseline_width, 430]
    source_kinds = sorted({
        str((analysis.get("sourceClassification") or {}).get("kind") or "unknown")
        for analysis in analyses
    })
    ambiguous_nodes = sum(int((analysis.get("summary") or {}).get("ambiguousNodes") or 0) for analysis in analyses)
    runtime_baseline = str((fallback.get("runtimeBaseline") or {}).get("minimumIOS") or "16.0")
    baseline_supported = version(args.minimum_ios) >= version(runtime_baseline)

    phone_heights = {320: 568, 375: 667, 393: 852, 430: 932}
    phone_profiles = []
    for profile_id, phone_width in (
        ("compact-phone", 320),
        ("phone-375", 375),
        ("baseline-phone", baseline_width),
        ("large-phone", 430),
    ):
        source_evidence = phone_width in widths
        phone_profiles.append({
            "id": profile_id,
            "viewport": {"width": phone_width, "height": phone_heights.get(phone_width, baseline_height)},
            "orientation": "portrait",
            "horizontalSizeClass": "compact",
            "verticalSizeClass": "regular",
            "sourceEvidence": source_evidence,
            "validation": "source-analyzed" if source_evidence else "pending-source-analysis",
        })

    profiles = phone_profiles + [
        {
            "id": "landscape-phone",
            "viewport": {"width": baseline_height, "height": baseline_width},
            "orientation": "landscape",
            "horizontalSizeClass": "compact",
            "verticalSizeClass": "compact",
            "sourceEvidence": False,
            "validation": "pending-runtime-validation",
        },
        {
            "id": "ipad-split-compact",
            "viewport": {"width": 507, "height": 1024},
            "orientation": "portrait",
            "horizontalSizeClass": "compact",
            "verticalSizeClass": "regular",
            "sourceEvidence": any(width >= 507 for width in widths),
            "validation": "pending-runtime-validation",
        },
        {
            "id": "ipad-regular",
            "viewport": {"width": 834, "height": 1210},
            "orientation": "portrait",
            "horizontalSizeClass": "regular",
            "verticalSizeClass": "regular",
            "sourceEvidence": any(width >= 768 for width in widths),
            "validation": "pending-runtime-validation",
        },
    ]
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "uiStack": args.ui_stack,
        "minimumIOS": args.minimum_ios,
        "installedSDK": (sdk.get("sdk") or {}).get("version"),
        "runtimeBaseline": runtime_baseline,
        "sdkReport": {"path": str(args.sdk_report.resolve()), "sha256": digest(args.sdk_report)},
        "apiFallbackPlan": {"path": str(args.api_fallback_plan.resolve()), "sha256": digest(args.api_fallback_plan)},
        "source": {
            "classifications": source_kinds,
            "sampleWidthsPt": widths,
            "ambiguousNodeCount": ambiguous_nodes,
        },
        "profiles": profiles,
        "layoutPolicy": {
            "usesAutoLayout": True,
            "wholePageScalingAllowed": False,
            "safeAreaSubtractedFromContainerDimensions": False,
            "horizontalOverflowRequiresOwner": True,
            "regularWidthRequiresExplicitValidation": True,
        },
        "summary": {
            "status": "passed" if baseline_supported else "failed",
            "runtimeBaselineSatisfied": baseline_supported,
            "sourceAnalyzedProfileCount": sum(item["validation"] == "source-analyzed" for item in profiles),
            "pendingRuntimeProfileIDs": [item["id"] for item in profiles if item["validation"] == "pending-runtime-validation"],
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **result["summary"]}, indent=2))
    return 0 if baseline_supported else 1


if __name__ == "__main__":
    raise SystemExit(main())
