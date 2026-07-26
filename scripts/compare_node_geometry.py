#!/usr/bin/env python3
"""Compare HTML validation-region geometry with captured iOS accessibility frames."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


def finite(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def rect_values(value) -> list[float] | None:
    if isinstance(value, list) and len(value) == 4:
        result = [finite(item) for item in value]
    elif isinstance(value, dict):
        result = [finite(value.get(key)) for key in ("x", "y", "width", "height")]
    else:
        return None
    return [float(item) for item in result] if all(item is not None for item in result) else None


def rounded(value: float) -> float:
    return round(value, 3)


def median(values: list[float]) -> float | None:
    return rounded(statistics.median(values)) if values else None


def geometry_confidence(category: str, element_type: int | None) -> str:
    expected_types = {
        "typography": {48},
        "asset": {43},
        "control": {9, 40, 41, 49, 50, 52},
    }
    if category == "viewport":
        return "excluded"
    if element_type in expected_types.get(category, set()):
        return "high"
    return "low"


def horizontally_corresponds(expected_rect: list[float], actual_rect: list[float]) -> bool:
    expected_left, _, expected_width, _ = expected_rect
    actual_left, _, actual_width, _ = actual_rect
    if expected_width <= 0 or actual_width <= 0:
        return False
    overlap = max(
        min(expected_left + expected_width, actual_left + actual_width)
        - max(expected_left, actual_left),
        0,
    )
    overlap_ratio = overlap / min(expected_width, actual_width)
    expected_center = expected_left + expected_width / 2
    actual_center = actual_left + actual_width / 2
    center_tolerance = max(8, expected_width * 0.25)
    return overlap_ratio >= 0.5 or abs(actual_center - expected_center) <= center_tolerance


def anchor_rows(comparisons: list[dict]) -> list[dict]:
    rows: list[list[dict]] = []
    for item in comparisons:
        if not item.get("verticalAggregationEligible"):
            continue
        expected_y = item["expectedRect"][1]
        if not rows or expected_y - rows[-1][-1]["expectedRect"][1] > 4:
            rows.append([item])
        else:
            rows[-1].append(item)
    result = []
    for items in rows:
        result.append({
            "expectedY": median([item["expectedRect"][1] for item in items]),
            "medianYDeltaPt": median([item["delta"]["y"] for item in items]),
            "medianHeightDeltaPt": median([item["delta"]["height"] for item in items]),
            "nodeIds": [item["nodeId"] for item in items],
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("ios_geometry", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    actual_payload = json.loads(args.ios_geometry.read_text(encoding="utf-8"))
    expected = {
        str(item.get("nodeId")): item
        for item in manifest.get("validationRegions") or []
        if item.get("nodeId") and rect_values(item.get("geometryRect") or item.get("rect"))
    }
    actual = {
        str(item.get("nodeId")): item
        for item in actual_payload.get("nodes") or []
        if item.get("nodeId") and rect_values(item.get("frame"))
    }
    requested_geometry = {
        str(item.get("nodeId")): item
        for item in manifest.get("geometryNodes") or []
        if item.get("nodeId")
    }

    comparisons = []
    for node_id, region in expected.items():
        captured = actual.get(node_id)
        if not captured:
            continue
        expected_rect = rect_values(region.get("geometryRect") or region.get("rect"))
        actual_rect = rect_values(captured.get("frame"))
        if not expected_rect or not actual_rect:
            continue
        x_delta = actual_rect[0] - expected_rect[0]
        y_delta = actual_rect[1] - expected_rect[1]
        width_delta = actual_rect[2] - expected_rect[2]
        height_delta = actual_rect[3] - expected_rect[3]
        category = str(region.get("category") or "")
        element_type = int(captured.get("elementType")) if captured.get("elementType") is not None else None
        confidence = geometry_confidence(category, element_type)
        comparisons.append({
            "nodeId": node_id,
            "category": category,
            "semanticType": region.get("semanticType"),
            "elementType": element_type,
            "geometryConfidence": confidence,
            "verticalAggregationEligible": (
                confidence == "high" and horizontally_corresponds(expected_rect, actual_rect)
            ),
            "expectedRect": [rounded(item) for item in expected_rect],
            "actualRect": [rounded(item) for item in actual_rect],
            "delta": {
                "x": rounded(x_delta),
                "y": rounded(y_delta),
                "width": rounded(width_delta),
                "height": rounded(height_delta),
                "bottom": rounded((actual_rect[1] + actual_rect[3]) - (expected_rect[1] + expected_rect[3])),
            },
            "verticalDiagnosticScore": rounded(abs(y_delta) + abs(height_delta) * 0.5),
        })

    comparisons.sort(key=lambda item: item["expectedRect"][1])
    reliable = [item for item in comparisons if item["verticalAggregationEligible"]]
    y_deltas = [item["delta"]["y"] for item in reliable]
    height_deltas = [item["delta"]["height"] for item in reliable]
    third = max(len(reliable) // 3, 1)
    bands = {
        "top": reliable[:third],
        "middle": reliable[third:third * 2],
        "bottom": reliable[third * 2:],
    }
    rows = anchor_rows(comparisons)
    transitions = []
    for previous, current in zip(rows, rows[1:]):
        previous_delta = previous.get("medianYDeltaPt")
        current_delta = current.get("medianYDeltaPt")
        if previous_delta is None or current_delta is None:
            continue
        change = current_delta - previous_delta
        if abs(change) >= 2:
            transitions.append({
                "fromExpectedY": previous["expectedY"],
                "toExpectedY": current["expectedY"],
                "deltaChangePt": rounded(change),
                "fromNodeIds": previous["nodeIds"],
                "toNodeIds": current["nodeIds"],
            })
    summary = {
        "expectedNodeCount": len(expected),
        "capturedNodeCount": len(actual),
        "matchedNodeCount": len(comparisons),
        "reliableMatchedNodeCount": len(reliable),
        "missingNodeIds": sorted(set(expected) - set(actual)),
        "geometryCaptureCoverage": {
            "requestedNodeCount": len(requested_geometry),
            "capturedNodeCount": len(set(requested_geometry) & set(actual)),
            "captureRate": rounded(
                len(set(requested_geometry) & set(actual)) / len(requested_geometry)
            ) if requested_geometry else None,
            "missingNodeIds": sorted(set(requested_geometry) - set(actual)),
            "validationRequestedNodeCount": len(expected),
            "validationCapturedNodeCount": len(set(expected) & set(actual)),
            "validationCaptureRate": rounded(
                len(set(expected) & set(actual)) / len(expected)
            ) if expected else None,
        },
        "medianYDeltaPt": median(y_deltas),
        "medianHeightDeltaPt": median(height_deltas),
        "verticalDriftSpanPt": rounded(max(y_deltas) - min(y_deltas)) if y_deltas else None,
        "bands": {
            name: {
                "count": len(items),
                "medianYDeltaPt": median([item["delta"]["y"] for item in items]),
                "medianHeightDeltaPt": median([item["delta"]["height"] for item in items]),
            }
            for name, items in bands.items()
        },
        "anchorRows": rows,
        "driftTransitions": transitions,
        "worstVerticalNodes": sorted(
            reliable,
            key=lambda item: item["verticalDiagnosticScore"],
            reverse=True,
        )[:12],
    }
    report = {
        "schemaVersion": "node-geometry-comparison-1.0",
        "manifest": str(args.manifest.resolve()),
        "iosGeometry": str(args.ios_geometry.resolve()),
        "stateId": actual_payload.get("stateId"),
        "summary": summary,
        "nodes": comparisons,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
