#!/usr/bin/env python3
"""Audit system-control coverage from SDK availability through both native generators."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from system_control_catalog import SYSTEM_CONTROLS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-report", required=True, type=Path)
    parser.add_argument("--skill-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    sdk = json.loads(args.sdk_report.read_text(encoding="utf-8"))
    uikit_symbols = (sdk.get("symbols") or {}).get("uikit") or {}
    mapping_text = (args.skill_root / "scripts/build_ui_ir.py").read_text(encoding="utf-8")
    architecture_text = (args.skill_root / "scripts/build_native_architecture_plan.py").read_text(encoding="utf-8")
    generator_text = (args.skill_root / "scripts/generate_ios_from_ir.py").read_text(encoding="utf-8")
    controls = []
    for item in SYSTEM_CONTROLS:
        semantic = str(item["semantic"])
        uikit = str(item["uikit"])
        sdk_item = uikit_symbols.get(uikit) or {}
        recognition = f'"{semantic}"' in mapping_text
        architecture = f'"{semantic}"' in architecture_text
        uikit_execution = uikit in generator_text and f'"{semantic}"' in generator_text
        swiftui_execution = str(item["swiftUI"]) in generator_text and f'"{semantic}"' in generator_text
        available = sdk_item.get("status") in {"available", "available-review-version", "requires-fallback"}
        complete = all((available, recognition, architecture, uikit_execution, swiftui_execution))
        controls.append({
            **item,
            "sdk": sdk_item,
            "coverage": {
                "sdkAvailable": available,
                "htmlOrContractRecognition": recognition,
                "architecturePlanning": architecture,
                "swiftUIExecution": swiftui_execution,
                "uiKitExecution": uikit_execution,
            },
            "status": "supported" if complete else "incomplete",
        })
    incomplete = [item for item in controls if item["status"] != "supported"]
    report = {
        "schemaVersion": "system-control-coverage-1.0",
        "sdk": sdk.get("sdk"),
        "minimumIOS": sdk.get("minimumIOS"),
        "status": "passed" if not incomplete else "failed",
        "controls": controls,
        "summary": {"controlCount": len(controls), "supportedCount": len(controls) - len(incomplete), "incompleteCount": len(incomplete)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"] | {"status": report["status"], "out": str(args.out.resolve())}, indent=2))
    return 0 if not incomplete else 1


if __name__ == "__main__":
    raise SystemExit(main())
