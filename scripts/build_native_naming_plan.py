#!/usr/bin/env python3
"""Infer a stable generated page/type prefix from an existing target module."""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-state", required=True, choices=("empty-no-ios-project", "swift-package-only", "existing-xcode"))
    parser.add_argument("--target", required=True)
    parser.add_argument("--component-index", type=Path)
    parser.add_argument("--name-prefix")
    parser.add_argument("--previous-plan", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args()


def normalize_prefix(value: str, fallback: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value or "")
    result = "".join(word[:1].upper() + word[1:] for word in words) or fallback
    if result[0].isdigit():
        result = fallback + result
    return result[:24]


def acronym_prefix(name: str) -> str | None:
    match = re.match(r"^([A-Z]{2,6})(?=[A-Z][a-z]|\d)", name)
    return match.group(1) if match else None


def load_components(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("components") or [])


def main() -> int:
    args = parse_args()
    components = load_components(args.component_index)
    candidates = [value for item in components if (value := acronym_prefix(str(item.get("name") or "")))]
    counts = collections.Counter(candidates)
    examples: list[str] = []
    previous = {}
    if args.previous_plan and args.previous_plan.is_file():
        try:
            previous = json.loads(args.previous_plan.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    if args.name_prefix:
        prefix = normalize_prefix(args.name_prefix, "Sky")
        source, confidence = "explicit-request", 1.0
    elif previous.get("schemaVersion") == "native-naming-plan-1.0" and previous.get("prefix"):
        prefix = normalize_prefix(str(previous["prefix"]), "Sky")
        source, confidence = "previous-generation-plan", 1.0
    elif args.project_state != "existing-xcode":
        prefix = "Sky"
        source, confidence = "new-project-default", 1.0
    elif counts:
        candidate, count = counts.most_common(1)[0]
        total = sum(counts.values())
        if count >= 2 and count / max(total, 1) >= 0.60:
            prefix = candidate
            source, confidence = "existing-module-dominant-prefix", round(count / total, 3)
            examples = [str(item.get("name")) for item in components if str(item.get("name") or "").startswith(prefix)][:8]
        else:
            prefix = normalize_prefix(args.target, "Sky")
            source, confidence = "target-name-fallback", 0.65
    else:
        prefix = normalize_prefix(args.target, "Sky")
        source, confidence = "target-name-fallback", 0.65

    result = {
        "schemaVersion": "native-naming-plan-1.0",
        "prefix": prefix,
        "source": source,
        "confidence": confidence,
        "target": args.target,
        "examples": examples,
        "existingTypeNames": sorted({str(item.get("name")) for item in components if item.get("name")}),
        "rules": {
            "pageFileAndTypePrefix": prefix,
            "generatedRuntimePrefix": "HTMLToIOSGenerated",
            "resourcePrefix": re.sub(r"[^a-z0-9]+", "_", prefix.lower()).strip("_"),
            "accessibilityIdentifiersRemainSourceStable": True,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "prefix": prefix, "source": source, "confidence": confidence}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
