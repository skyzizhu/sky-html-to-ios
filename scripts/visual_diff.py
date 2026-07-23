#!/usr/bin/env python3
"""Create deterministic pixel-difference artifacts for two screenshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageStat


def parse_mask(value: str) -> tuple[int, int, int, int]:
    try:
        x, y, width, height = (int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("mask must be x,y,width,height") from exc
    if width < 0 or height < 0:
        raise argparse.ArgumentTypeError("mask width and height must be non-negative")
    return x, y, width, height


def load_regions(path: Path | None) -> list[dict]:
    if not path:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    regions = data.get("validationRegions") if isinstance(data, dict) else data
    if not isinstance(regions, list):
        raise ValueError("regions JSON must be a list or contain validationRegions")
    return [item for item in regions if isinstance(item, dict) and isinstance(item.get("rect"), list)]


def clamp_rect(rect: list, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    if len(rect) != 4:
        return None
    x, y, width, height = (round(float(value)) for value in rect)
    left, top = max(0, x), max(0, y)
    right, bottom = min(size[0], x + max(0, width)), min(size[1], y + max(0, height))
    return (left, top, right, bottom) if right > left and bottom > top else None


def changed_pixels(image: Image.Image) -> int:
    histogram = image.convert("L").histogram()
    return sum(histogram[1:])


def region_metrics(
    region: dict,
    rgb_diff: Image.Image,
    max_diff: Image.Image,
    edge_xor: Image.Image,
    threshold: int,
) -> dict | None:
    bounds = clamp_rect(region.get("rect") or [], rgb_diff.size)
    if not bounds:
        return None
    local_rgb = rgb_diff.crop(bounds)
    local_max = max_diff.crop(bounds)
    local_edges = edge_xor.crop(bounds)
    pixels = max(1, local_max.width * local_max.height)
    histogram = local_max.histogram()
    mismatch = sum(histogram[threshold + 1:])
    binary = local_max.point(lambda value: 255 if value > threshold else 0)
    local_difference_bounds = binary.getbbox()
    if local_difference_bounds:
        local_difference_bounds = [
            bounds[0] + local_difference_bounds[0],
            bounds[1] + local_difference_bounds[1],
            local_difference_bounds[2] - local_difference_bounds[0],
            local_difference_bounds[3] - local_difference_bounds[1],
        ]
    mean_rgb = ImageStat.Stat(local_rgb).mean
    criticality_weight = {"critical": 1.5, "high": 1.25, "medium": 1.0, "low": 0.75}.get(str(region.get("criticality")), 1.0)
    mismatch_ratio = mismatch / pixels
    return {
        **{key: value for key, value in region.items() if key != "rect"},
        "rect": [bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]],
        "meanAbsoluteDifference": sum(mean_rgb) / 3.0,
        "mismatchRatio": mismatch_ratio,
        "edgeMismatchRatio": changed_pixels(local_edges) / pixels,
        "differenceBounds": local_difference_bounds,
        "diagnosticScore": min(1.0, mismatch_ratio * criticality_weight),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=int, default=24)
    parser.add_argument("--mask", type=parse_mask, action="append", default=[])
    parser.add_argument("--resize-current", action="store_true")
    parser.add_argument("--regions-json", type=Path, help="Visual-state manifest or region list with node-aligned rects")
    args = parser.parse_args()

    reference = Image.open(args.reference).convert("RGBA")
    current = Image.open(args.current).convert("RGBA")
    original_current_size = current.size
    if current.size != reference.size:
        if not args.resize_current:
            parser.error(
                f"Image sizes differ: reference={reference.size}, current={current.size}. "
                "Align captures first or pass --resize-current for diagnostics only."
            )
        current = current.resize(reference.size, Image.Resampling.LANCZOS)

    reference_rgb = reference.convert("RGB")
    current_rgb = current.convert("RGB")
    diff = ImageChops.difference(reference_rgb, current_rgb)
    for x, y, width, height in args.mask:
        diff.paste((0, 0, 0), (x, y, x + width, y + height))

    reference_edges = reference_rgb.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda value: 255 if value > 24 else 0, mode="1")
    current_edges = current_rgb.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda value: 255 if value > 24 else 0, mode="1")
    edge_xor = ImageChops.logical_xor(reference_edges, current_edges).convert("L")
    for x, y, width, height in args.mask:
        edge_xor.paste(0, (x, y, x + width, y + height))

    stat = ImageStat.Stat(diff)
    mean_rgb = stat.mean
    mean_absolute = sum(mean_rgb) / 3.0
    similarity = max(0.0, 1.0 - mean_absolute / 255.0)
    max_channel = diff.convert("RGB").split()
    max_diff = ImageChops.lighter(ImageChops.lighter(max_channel[0], max_channel[1]), max_channel[2])
    histogram = max_diff.histogram()
    threshold = max(0, min(255, args.threshold))
    mismatch_pixels = sum(histogram[threshold + 1:])
    total_pixels = reference.width * reference.height
    mismatch_ratio = mismatch_pixels / total_pixels if total_pixels else 0.0
    luminance_difference = ImageStat.Stat(ImageChops.difference(reference_rgb.convert("L"), current_rgb.convert("L"))).mean[0]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    diff_path = args.out_dir / "diff.png"
    heat_path = args.out_dir / "heatmap.png"
    overlay_path = args.out_dir / "overlay.png"
    comparison_path = args.out_dir / "comparison.png"
    regions_path = args.out_dir / "regions.png"
    report_path = args.out_dir / "report.json"

    diff.save(diff_path)
    enhanced = ImageEnhance.Contrast(max_diff).enhance(4.0)
    heat = Image.new("RGBA", reference.size, (255, 0, 0, 0))
    heat.putalpha(enhanced)
    heat.save(heat_path)
    overlay = Image.alpha_composite(reference, heat)
    overlay.save(overlay_path)

    header = 32
    comparison = Image.new("RGBA", (reference.width * 2, reference.height + header), (250, 250, 250, 255))
    comparison.paste(reference, (0, header))
    comparison.paste(current, (reference.width, header))
    draw = ImageDraw.Draw(comparison)
    draw.text((12, 9), "HTML reference", fill=(20, 20, 20, 255))
    draw.text((reference.width + 12, 9), "iOS current", fill=(20, 20, 20, 255))
    draw.line((reference.width, 0, reference.width, reference.height + header), fill=(160, 160, 160, 255), width=1)
    comparison.save(comparison_path)

    binary = max_diff.point(lambda value: 255 if value > threshold else 0)
    difference_bounds = list(binary.getbbox()) if binary.getbbox() else None
    regions = []
    columns, rows = 4, 8
    for row in range(rows):
        for column in range(columns):
            left = round(column * reference.width / columns)
            top = round(row * reference.height / rows)
            right = round((column + 1) * reference.width / columns)
            bottom = round((row + 1) * reference.height / rows)
            tile = binary.crop((left, top, right, bottom))
            changed = sum(tile.histogram()[1:])
            pixels = max(1, tile.width * tile.height)
            ratio = changed / pixels
            if ratio > 0:
                regions.append({"rect": [left, top, right - left, bottom - top], "mismatchRatio": ratio})
    regions.sort(key=lambda item: item["mismatchRatio"], reverse=True)

    semantic_regions = []
    for item in load_regions(args.regions_json):
        measured = region_metrics(item, diff, max_diff, edge_xor, threshold)
        if measured:
            semantic_regions.append(measured)
    semantic_regions.sort(key=lambda item: item["diagnosticScore"], reverse=True)

    annotated = current.copy()
    annotated_draw = ImageDraw.Draw(annotated)
    for index, item in enumerate([item for item in semantic_regions if item.get("mismatchRatio", 0) > 0][:12], start=1):
        x, y, width, height = item["rect"]
        color = (220, 38, 38, 255) if item.get("criticality") in {"critical", "high"} else (234, 88, 12, 255)
        annotated_draw.rectangle((x, y, x + width, y + height), outline=color, width=2)
        annotated_draw.text((x + 3, y + 3), f"{index}:{item.get('category', 'region')}", fill=color)
    annotated.save(regions_path)

    text_regions = [item for item in semantic_regions if item.get("toleranceProfile") == "text"]
    critical_regions = [item for item in semantic_regions if item.get("criticality") == "critical" and item.get("id") != "screen.viewport"]

    report = {
        "schemaVersion": "visual-diff-report-2.0",
        "reference": str(args.reference.resolve()),
        "current": str(args.current.resolve()),
        "referenceSize": list(reference.size),
        "originalCurrentSize": list(original_current_size),
        "resizedCurrent": original_current_size != reference.size,
        "meanAbsoluteDifferenceRGB": mean_rgb,
        "meanAbsoluteDifference": mean_absolute,
        "meanLuminanceDifference": luminance_difference,
        "simplePixelSimilarity": similarity,
        "threshold": threshold,
        "pixelsAboveThreshold": mismatch_pixels,
        "mismatchRatio": mismatch_ratio,
        "differenceBounds": difference_bounds,
        "topDifferenceRegions": regions[:8],
        "semanticRegions": semantic_regions,
        "diagnostics": {
            "worstSemanticRegions": semantic_regions[:12],
            "textRegionCount": len(text_regions),
            "maxTextMismatchRatio": max((item["mismatchRatio"] for item in text_regions), default=0.0),
            "maxTextEdgeMismatchRatio": max((item["edgeMismatchRatio"] for item in text_regions), default=0.0),
            "criticalRegionCount": len(critical_regions),
            "maxCriticalRegionMismatchRatio": max((item["mismatchRatio"] for item in critical_regions), default=0.0),
            "maxCriticalRegionEdgeMismatchRatio": max((item["edgeMismatchRatio"] for item in critical_regions), default=0.0),
            "dominantCategory": semantic_regions[0].get("category") if semantic_regions else None,
        },
        "masks": [list(mask) for mask in args.mask],
        "artifacts": {
            "diff": str(diff_path.resolve()),
            "heatmap": str(heat_path.resolve()),
            "overlay": str(overlay_path.resolve()),
            "comparison": str(comparison_path.resolve()),
            "regions": str(regions_path.resolve()),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
