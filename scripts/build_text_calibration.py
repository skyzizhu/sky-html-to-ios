#!/usr/bin/env python3
"""Build text layout targets from browser range metrics for iOS calibration."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SYSTEM_FONTS = {"system-ui", "-apple-system", "blinkmacsystemfont", "sans-serif", "serif", "monospace"}


def number(value, default=None):
    match = re.match(r"^\s*(-?[0-9.]+)", str(value or ""))
    return float(match.group(1)) if match else default


def scaled_rect(rect, root, scale):
    return {
        "x": round((rect["x"] - root["x"]) * scale, 3),
        "y": round((rect["y"] - root["y"]) * scale, 3),
        "width": round(rect["width"] * scale, 3),
        "height": round(rect["height"] * scale, 3),
    }


def family_names(value):
    return [item.strip().strip("'\"").lower() for item in str(value or "").split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("render_tree", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target-width", type=float, default=393)
    parser.add_argument("--root-runtime-id")
    parser.add_argument("--normalization", choices=("auto", "none", "fixed-artboard"), default="auto")
    args = parser.parse_args()

    data = json.loads(args.render_tree.read_text(encoding="utf-8"))
    nodes = data.get("nodes") or []
    node_by_id = {node["runtimeId"]: node for node in nodes}
    root = node_by_id.get(args.root_runtime_id) if args.root_runtime_id else None
    if root is None:
        selector = (data.get("document") or {}).get("rootSelector")
        root = next((node for node in nodes if node.get("selector") == selector), None)
    root = root or next((node for node in nodes if node.get("parentRuntimeId") is None and not node.get("synthetic")), None)
    if not root or root["rect"]["width"] <= 0:
        parser.error("Unable to determine a non-empty calibration root")

    document_width = ((data.get("document") or {}).get("viewport") or {}).get("width", root["rect"]["width"])
    fixed_artboard_signal = document_width > root["rect"]["width"] * 1.7 or any(
        candidate.get("recommendedRootRuntimeId") == root["runtimeId"] and candidate.get("kind") == "device-frame"
        for candidate in data.get("phoneCandidates") or []
    )
    normalize = args.normalization == "fixed-artboard" or args.normalization == "auto" and fixed_artboard_signal
    scale = args.target_width / root["rect"]["width"] if normalize else 1.0

    loaded_fonts = (data.get("document") or {}).get("loadedFonts") or []
    loaded_names = {str(item.get("family", "")).strip("'\"").lower() for item in loaded_fonts if item.get("status") == "loaded"}
    children = {}
    for node in nodes:
        children.setdefault(node.get("parentRuntimeId"), []).append(node)

    items = []
    for node in nodes:
        metrics = node.get("textMetrics")
        if not metrics or not node.get("visible") or not metrics.get("renderedText"):
            continue
        style = node.get("style") or {}
        families = family_names(style.get("fontFamily"))
        primary = families[0] if families else ""
        if primary in SYSTEM_FONTS:
            font_status = "ios-system-font"
        elif primary in loaded_names or metrics.get("fontLoaded") is True:
            font_status = "web-font-loaded-needs-ios-file"
        else:
            font_status = "fallback-risk"
        font_size = number(style.get("fontSize"), 0) or 0
        line_height = number(style.get("lineHeight"))
        letter_spacing = number(style.get("letterSpacing"), 0) or 0
        item_children = children.get(node["runtimeId"], [])
        items.append({
            "nodeId": node["runtimeId"],
            "selector": node.get("selector"),
            "text": metrics["renderedText"],
            "richText": any(child.get("textMetrics") for child in item_children),
            "font": {
                "cssFamilies": families,
                "status": font_status,
                "sourceSizePx": font_size,
                "targetSizePt": round(font_size * scale, 3),
                "weight": style.get("fontWeight"),
                "style": style.get("fontStyle"),
                "sourceLineHeightPx": line_height,
                "targetLineHeightPt": round(line_height * scale, 3) if line_height is not None else None,
                "sourceLetterSpacingPx": letter_spacing,
                "targetLetterSpacingPt": round(letter_spacing * scale, 3),
            },
            "expected": {
                "frame": scaled_rect(node["rect"], root["rect"], scale),
                "lineCount": metrics.get("lineCount"),
                "lineRects": [scaled_rect(rect, root["rect"], scale) for rect in metrics.get("lineRects") or []],
                "firstBaseline": round((metrics["firstBaselineY"] - root["rect"]["y"]) * scale, 3) if metrics.get("firstBaselineY") is not None else None,
                "lastBaseline": round((metrics["lastBaselineY"] - root["rect"]["y"]) * scale, 3) if metrics.get("lastBaselineY") is not None else None,
                "clippedHorizontally": metrics.get("clippedHorizontally", False),
                "clippedVertically": metrics.get("clippedVertically", False),
            },
            "iosMeasurementKey": node["runtimeId"],
        })

    result = {
        "schemaVersion": "text-calibration-1.0",
        "sourceRenderTree": str(args.render_tree.resolve()),
        "rootNodeId": root["runtimeId"],
        "normalization": {
            "policy": "fixed-artboard-scale-once" if normalize else "native-responsive-1-to-1",
            "sourceRootWidthCssPx": root["rect"]["width"],
            "targetBaselineWidthPt": args.target_width,
            "designScale": round(scale, 6),
            "runtimeWholePageScalingAllowed": False,
        },
        "loadedFonts": loaded_fonts,
        "items": items,
        "verificationContract": {
            "requiredIOSFields": ["nodeId", "frame", "lineCount", "firstBaseline", "lastBaseline", "truncated"],
            "lineCountMustMatch": True,
            "defaultFrameTolerancePt": 1.5,
            "defaultBaselineTolerancePt": 1.0,
        },
        "summary": {
            "textNodes": len(items),
            "richTextNodes": sum(item["richText"] for item in items),
            "fontFileRequired": sum(item["font"]["status"] == "web-font-loaded-needs-ios-file" for item in items),
            "fallbackRisks": sum(item["font"]["status"] == "fallback-risk" for item in items),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **result["summary"], **result["normalization"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
