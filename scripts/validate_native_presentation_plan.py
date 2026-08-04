#!/usr/bin/env python3
"""Validate native presentation ownership, geometry, and dismissal contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


STRATEGIES = {"system-sheet", "system-cover", "system-popover", "system-alert", "system-confirmation", "system-menu", "custom-overlay"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    owned_states: set[tuple[str, str]] = set()
    if plan.get("schemaVersion") != "native-presentation-plan-1.0":
        issues.append({"code": "SCHEMA_VERSION_INVALID", "message": "Expected native-presentation-plan-1.0."})
    for screen in plan.get("screens") or []:
        screen_id = str(screen.get("screenId") or "")
        for item in screen.get("presentations") or []:
            state_id = str(item.get("stateId") or "")
            key = (screen_id, state_id)
            if not screen_id or not state_id or key in seen:
                issues.append({"code": "PRESENTATION_OWNER_INVALID", "screenId": screen_id, "stateId": state_id, "message": "Presentation state owners must be non-empty and unique per screen."})
            seen.add(key)
            for owned_state in [state_id, *(item.get("aliasStateIds") or [])]:
                owned_key = (screen_id, str(owned_state))
                if owned_key in owned_states:
                    issues.append({"code": "PRESENTATION_STATE_OWNER_DUPLICATED", "screenId": screen_id, "stateId": owned_state, "message": "Every presentation state and alias must have exactly one native owner."})
                owned_states.add(owned_key)
            if item.get("strategy") not in STRATEGIES:
                issues.append({"code": "PRESENTATION_STRATEGY_INVALID", "screenId": screen_id, "stateId": state_id, "message": "Unknown native presentation strategy."})
            opacity = (item.get("backdrop") or {}).get("opacity")
            if not isinstance(opacity, (int, float)) or not 0 <= opacity <= 1:
                issues.append({"code": "BACKDROP_OPACITY_INVALID", "screenId": screen_id, "stateId": state_id, "message": "Backdrop opacity must be between zero and one."})
            anchor = (item.get("anchor") or {}).get("sourceRect") or []
            if len(anchor) != 4 or any(not isinstance(value, (int, float)) for value in anchor):
                issues.append({"code": "PRESENTATION_ANCHOR_INVALID", "screenId": screen_id, "stateId": state_id, "message": "Anchor sourceRect must contain four numeric values."})
            if item.get("strategy") == "system-sheet" and not item.get("detents"):
                issues.append({"code": "SHEET_DETENTS_MISSING", "screenId": screen_id, "stateId": state_id, "message": "A system sheet requires at least one detent."})
            actions = (item.get("content") or {}).get("actions") or []
            if sum(str(action.get("role") or "") == "cancel" for action in actions) > 1:
                issues.append({"code": "MULTIPLE_CANCEL_ACTIONS", "screenId": screen_id, "stateId": state_id, "message": "A native alert or confirmation may have only one cancel action."})
            if any(str(action.get("role") or "") not in {"default", "cancel", "destructive"} for action in actions):
                issues.append({"code": "PRESENTATION_ACTION_ROLE_INVALID", "screenId": screen_id, "stateId": state_id, "message": "Presentation action roles must map to native action roles."})
    report = {
        "schemaVersion": "native-presentation-validation-1.0",
        "status": "passed" if not issues else "failed",
        "qualityGate": {"passed": not issues, "requiresScreenshots": False, "requiresMultimodalModel": False},
        "issues": issues,
        "summary": {"presentationCount": len(seen), "errorCount": len(issues)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
