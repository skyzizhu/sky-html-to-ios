#!/usr/bin/env python3
"""Validate native system-control configuration coverage and geometry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = load(args.plan)
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if plan.get("schemaVersion") != "native-control-configuration-plan-1.0":
        issues.append({"code": "SCHEMA_VERSION_INVALID", "message": "Expected native-control-configuration-plan-1.0."})
    for screen in plan.get("screens") or []:
        screen_id = str(screen.get("screenId") or "")
        if not screen_id:
            issues.append({"code": "SCREEN_ID_MISSING", "message": "Every control plan screen requires an ID."})
        for control in screen.get("controls") or []:
            node_id = str(control.get("nodeId") or "")
            key = (screen_id, node_id)
            if not node_id or key in seen:
                issues.append({"code": "CONTROL_ID_INVALID", "screenId": screen_id, "nodeId": node_id, "message": "Control IDs must be non-empty and unique."})
            seen.add(key)
            if control.get("strategy") not in {"system-control", "system-control-with-wrapper"}:
                issues.append({"code": "CONTROL_STRATEGY_INVALID", "screenId": screen_id, "nodeId": node_id, "message": "Unsupported native control strategy."})
            geometry = control.get("geometry") or {}
            insets = geometry.get("contentInsetsPt") or []
            if len(insets) != 4 or any(not isinstance(item, (int, float)) or item < 0 for item in insets):
                issues.append({"code": "CONTROL_INSETS_INVALID", "screenId": screen_id, "nodeId": node_id, "message": "Control content insets must contain four non-negative values."})
            if (control.get("behavior") or {}).get("usesNativeStateMachine") is not True:
                issues.append({"code": "NATIVE_STATE_MACHINE_DISABLED", "screenId": screen_id, "nodeId": node_id, "message": "System controls must preserve their native state machine."})
            state_appearances = control.get("stateAppearances") or {}
            if "normal" not in state_appearances:
                issues.append({"code": "CONTROL_NORMAL_STATE_MISSING", "screenId": screen_id, "nodeId": node_id, "message": "Every system control requires a normal appearance baseline."})
            declared_states = set((control.get("behavior") or {}).get("stateNames") or [])
            if declared_states != set(state_appearances):
                issues.append({"code": "CONTROL_STATE_SET_MISMATCH", "screenId": screen_id, "nodeId": node_id, "message": "Declared native states must match the state appearance matrix."})
            for state_name, appearance in state_appearances.items():
                opacity = (appearance or {}).get("disabledOpacity")
                if state_name == "disabled" and (not isinstance(opacity, (int, float)) or not 0 <= opacity <= 1):
                    issues.append({"code": "CONTROL_DISABLED_OPACITY_INVALID", "screenId": screen_id, "nodeId": node_id, "message": "Disabled opacity must be between zero and one."})
    report = {
        "schemaVersion": "native-control-configuration-validation-1.0",
        "status": "passed" if not issues else "failed",
        "qualityGate": {"passed": not issues, "requiresScreenshots": False, "requiresMultimodalModel": False},
        "issues": issues,
        "summary": {"screenCount": len(plan.get("screens") or []), "controlCount": len(seen), "errorCount": len(issues)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
