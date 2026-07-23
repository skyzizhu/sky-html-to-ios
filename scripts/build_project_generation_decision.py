#!/usr/bin/env python3
"""Decide language, UI stack, and default verification policy for a target module."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SKIP_DIRS = {".git", ".build", "build", "DerivedData", "Pods", "Carthage", "node_modules", "xcuserdata", "Generated"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-state", required=True, choices=("empty-no-ios-project", "swift-package-only", "existing-xcode"))
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--target")
    parser.add_argument("--requested-ui-stack", choices=("swiftui", "uikit"))
    parser.add_argument("--verification-mode", choices=("auto", "ask", "build", "visual", "none"), default="auto")
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args()


def source_files(root: Path, suffixes: set[str]):
    if not root.is_dir():
        return
    count = 0
    for path in root.rglob("*"):
        if count >= 2500:
            break
        if path.is_file() and path.suffix.lower() in suffixes and not any(part in SKIP_DIRS for part in path.parts):
            count += 1
            yield path


def inspect_module(root: Path | None) -> dict[str, Any]:
    swift_files = list(source_files(root, {".swift"})) if root else []
    objc_files = list(source_files(root, {".m", ".mm"})) if root else []
    swiftui_score = 0
    uikit_score = 0
    evidence: list[str] = []
    for path in swift_files:
        text = path.read_text(encoding="utf-8", errors="ignore")[:800_000]
        swiftui_score += 3 * len(re.findall(r"(?m)^\s*import\s+SwiftUI\b", text))
        swiftui_score += 2 * len(re.findall(r"\b(?:struct|class)\s+\w+[^\n{]*:\s*View\b", text))
        uikit_score += 3 * len(re.findall(r"(?m)^\s*import\s+UIKit\b", text))
        uikit_score += 2 * len(re.findall(r"\b(?:UIViewController|UIView|UITableView|UICollectionView)\b", text))
    if swiftui_score:
        evidence.append(f"Target module SwiftUI score: {swiftui_score}.")
    if uikit_score:
        evidence.append(f"Target module UIKit score: {uikit_score}.")
    if objc_files:
        evidence.append(f"Target module contains {len(objc_files)} Objective-C implementation files.")

    if swift_files and objc_files:
        language = "mixed-swift-objective-c"
    elif swift_files:
        language = "swift"
    elif objc_files:
        language = "objective-c"
    else:
        language = "swift" if root is None else "unknown"

    if swiftui_score and swiftui_score >= max(4, uikit_score * 1.35):
        recommendation, confidence = "swiftui", min(0.98, 0.70 + (swiftui_score - uikit_score) / max(swiftui_score, 1) * 0.25)
    elif uikit_score and uikit_score >= max(4, swiftui_score * 1.35):
        recommendation, confidence = "uikit", min(0.98, 0.70 + (uikit_score - swiftui_score) / max(uikit_score, 1) * 0.25)
    elif swiftui_score or uikit_score:
        recommendation, confidence = "mixed", 0.45
    else:
        recommendation, confidence = "unknown", 0.0
    return {
        "language": language,
        "swiftFiles": len(swift_files),
        "objectiveCFiles": len(objc_files),
        "swiftUIScore": swiftui_score,
        "uiKitScore": uikit_score,
        "recommendedUIStack": recommendation,
        "confidence": round(confidence, 3),
        "evidence": evidence,
    }


def main() -> int:
    args = parse_args()
    creating = args.project_state in {"empty-no-ios-project", "swift-package-only"}
    module = inspect_module(args.source_root.expanduser().resolve() if args.source_root else None)
    requested = args.requested_ui_stack
    recommendation = str(module["recommendedUIStack"])
    if requested:
        selected = requested
        source = "explicit-request"
        requires_selection = False
    elif creating:
        selected = None
        source = "new-project-user-selection-required"
        requires_selection = True
    elif recommendation in {"swiftui", "uikit"} and float(module["confidence"]) >= 0.70:
        selected = recommendation
        source = "target-module-detection"
        requires_selection = False
    else:
        selected = None
        source = "ambiguous-target-module"
        requires_selection = True

    if args.verification_mode == "auto":
        verification = "visual" if creating else "ask"
        verification_source = "new-project-default" if creating else "existing-project-confirmation-required"
    else:
        verification = args.verification_mode
        verification_source = "explicit-request"

    result = {
        "schemaVersion": "project-generation-decision-1.0",
        "projectState": args.project_state,
        "target": args.target,
        "sourceRoot": str(args.source_root.resolve()) if args.source_root else None,
        "language": module["language"],
        "moduleInspection": module,
        "uiStack": {
            "requested": requested,
            "selected": selected,
            "source": source,
            "requiresUserSelection": requires_selection,
            "choices": ["swiftui", "uikit"],
            "labels": {"swiftui": "SwiftUI", "uikit": "UIKit + Swift"},
        },
        "verification": {
            "requested": args.verification_mode,
            "resolved": verification,
            "source": verification_source,
            "launchesApplication": verification == "visual",
        },
        "requiresSwiftIntegrationReview": module["language"] == "objective-c",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "uiStack": selected, "requiresUserSelection": requires_selection, "verification": verification}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
