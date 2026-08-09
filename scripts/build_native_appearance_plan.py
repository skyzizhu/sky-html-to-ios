#!/usr/bin/env python3
"""Extract node rendering concerns into one SwiftUI/UIKit appearance contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--native-layout-plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    layout = load(args.native_layout_plan)
    if layout.get("schemaVersion") != "native-layout-plan-1.1":
        raise ValueError("--native-layout-plan must use native-layout-plan-1.1")
    layout_screens = {str(item.get("screenId") or ""): item for item in layout.get("screens") or []}
    screens = []
    for path in args.ir:
        document = load(path)
        for screen in document.get("screens") or []:
            screen_id = str(screen.get("id") or "")
            layout_nodes = {
                str(item.get("nodeId") or ""): item for item in (layout_screens.get(screen_id) or {}).get("nodes") or []
            }
            nodes = []
            for node in screen.get("nodes") or []:
                node_id = str(node.get("id") or "")
                style = node.get("style") or {}
                appearance = dict((layout_nodes.get(node_id) or {}).get("appearance") or {})
                appearance["typography"] = {
                    "fontFamily": str(style.get("fontFamily") or ""),
                    "fontSize": str(style.get("fontSize") or ""),
                    "fontWeight": str(style.get("fontWeight") or ""),
                    "fontStyle": str(style.get("fontStyle") or "normal"),
                    "lineHeight": str(style.get("lineHeight") or "normal"),
                    "letterSpacing": str(style.get("letterSpacing") or "normal"),
                    "textAlign": str(style.get("textAlign") or "start"),
                    "whiteSpace": str(style.get("whiteSpace") or "normal"),
                    "textOverflow": str(style.get("textOverflow") or "clip"),
                    "metricOwner": "native-layout-plan",
                }
                appearance["media"] = {
                    "objectFit": str(style.get("objectFit") or "fill"),
                    "objectPosition": str(style.get("objectPosition") or "50% 50%"),
                }
                nodes.append({"nodeId": node_id, **appearance})
            screens.append({"screenId": screen_id, "nodes": nodes})
    plan = {
        "schemaVersion": "native-appearance-plan-1.0",
        "layoutPlanSha256": digest(args.native_layout_plan),
        "inputs": [{"path": str(path.resolve()), "sha256": digest(path)} for path in args.ir],
        "invariants": {
            "oneAppearancePerNode": True,
            "perCornerGeometryPreserved": True,
            "perEdgeBordersPreserved": True,
            "typographyMetricsOwnedByLayout": True,
        },
        "screens": screens,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "screens": len(screens), "nodes": sum(len(item["nodes"]) for item in screens)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
