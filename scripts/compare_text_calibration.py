#!/usr/bin/env python3
"""Compare exported iOS text metrics with browser-derived calibration targets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def delta_frame(expected, actual):
    return {key: round(float(actual.get(key, 0)) - float(expected.get(key, 0)), 3) for key in ("x", "y", "width", "height")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("expected", type=Path)
    parser.add_argument("ios_metrics", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frame-tolerance", type=float, default=1.5)
    parser.add_argument("--baseline-tolerance", type=float, default=1.0)
    args = parser.parse_args()

    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    actual_data = json.loads(args.ios_metrics.read_text(encoding="utf-8"))
    actual_by_id = {item["nodeId"]: item for item in actual_data.get("items") or []}
    results = []
    for item in expected.get("items") or []:
        actual = actual_by_id.get(item["nodeId"])
        if not actual:
            results.append({"nodeId": item["nodeId"], "status": "missing"})
            continue
        frame_delta = delta_frame(item["expected"]["frame"], actual.get("frame") or {})
        line_match = item["expected"].get("lineCount") == actual.get("lineCount")
        frame_ok = all(abs(value) <= args.frame_tolerance for value in frame_delta.values())
        baseline_deltas = {}
        for key in ("firstBaseline", "lastBaseline"):
            expected_value = item["expected"].get(key)
            actual_value = actual.get(key)
            baseline_deltas[key] = round(float(actual_value) - float(expected_value), 3) if expected_value is not None and actual_value is not None else None
        baseline_ok = all(value is None or abs(value) <= args.baseline_tolerance for value in baseline_deltas.values())
        source_was_clipped = item["expected"].get("clippedVertically") or item["expected"].get("clippedHorizontally")
        truncation_ok = source_was_clipped or not actual.get("truncated")
        status = "passed" if frame_ok and line_match and baseline_ok and truncation_ok else "review-required"
        results.append({
            "nodeId": item["nodeId"],
            "status": status,
            "frameDeltaPt": frame_delta,
            "lineCountExpected": item["expected"].get("lineCount"),
            "lineCountActual": actual.get("lineCount"),
            "baselineDeltaPt": baseline_deltas,
            "truncated": actual.get("truncated"),
        })

    result = {
        "schemaVersion": "text-calibration-comparison-1.0",
        "items": results,
        "summary": {
            "passed": sum(item["status"] == "passed" for item in results),
            "reviewRequired": [item["nodeId"] for item in results if item["status"] == "review-required"],
            "missing": [item["nodeId"] for item in results if item["status"] == "missing"],
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **result["summary"]}, ensure_ascii=False, indent=2))
    return 1 if result["summary"]["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
