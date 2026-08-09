#!/usr/bin/env python3
"""Resolve interactions and motion to one native owner/executor contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def owner(action: str) -> tuple[str, str]:
    if action == "switch-tab":
        return "application", "tab-container"
    if action in {"push", "pop", "pop-to-root", "replace-stack", "back"}:
        return "navigation-stack", "native-navigation"
    if action.startswith("present") or action in {"dismiss", "overlay"}:
        return "screen-host", "native-presentation"
    if action in {"contextual-actions", "swipe-actions"}:
        return "reusable-content", "native-item-actions"
    if action in {"add-child", "remove-child"}:
        return "screen-host", "native-containment"
    return "source-component", "native-control-state"


def resolved_owner_id(
    owner_kind: str, screen_id: str, source_node_id: Any, membership: dict[str, Any],
) -> str:
    if owner_kind == "application":
        return str(membership.get("applicationContainerId") or "main-application")
    if owner_kind == "navigation-stack":
        return str(membership.get("navigationStackId") or "main-navigation")
    if owner_kind == "screen-host":
        return screen_id
    return str(source_node_id or screen_id)


def interaction_transitions(item: dict[str, Any]) -> list[dict[str, Any]]:
    transitions = (item.get("payload") or {}).get("transitions") or []
    return [transition for transition in transitions if isinstance(transition, dict)] or [item]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--application-plan", required=True, type=Path)
    parser.add_argument("--presentation-plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    application = load(args.application_plan)
    presentation = load(args.presentation_plan)
    if application.get("schemaVersion") != "native-application-plan-1.0":
        raise ValueError("--application-plan must use native-application-plan-1.0")
    if presentation.get("schemaVersion") != "native-presentation-plan-1.0":
        raise ValueError("--presentation-plan must use native-presentation-plan-1.0")
    memberships = {str(item.get("screenId") or ""): item for item in application.get("screenMemberships") or []}
    presentation_ids = {
        str(item.get("stateId") or "")
        for screen in presentation.get("screens") or [] for item in screen.get("presentations") or []
    }
    screens = []
    input_screen_ids = {str(load(path)["screens"][0].get("id") or "") for path in args.ir}
    if set(memberships) != input_screen_ids:
        raise ValueError("application plan screen memberships do not match UI IR")
    for path in args.ir:
        document = load(path)
        screen_id = str(document["screens"][0].get("id") or "")
        actions = []
        for index, item in enumerate(document.get("interactions") or []):
            transitions = interaction_transitions(item)
            base_id = str(item.get("id") or f"{screen_id}.interaction.{index + 1}")
            for transition_index, transition in enumerate(transitions):
                action = str(transition.get("action") or item.get("action") or "none")
                owner_kind, executor = owner(action)
                schedule = transition.get("schedule") or {}
                actions.append({
                    "id": base_id if len(transitions) == 1 else f"{base_id}.transition.{transition_index + 1}",
                    "sourceInteractionId": base_id,
                    "sourceTransitionId": transition.get("sourceTransitionId"),
                    "sourceNodeId": item.get("sourceNodeId"),
                    "trigger": str(transition.get("trigger") or item.get("trigger") or "tap"),
                    "action": action,
                    "targetScreenId": transition.get("targetScreenId"),
                    "targetStateId": transition.get("targetStateId"),
                    "owner": owner_kind,
                    "ownerId": resolved_owner_id(
                        owner_kind, screen_id, item.get("sourceNodeId"), memberships.get(screen_id, {}),
                    ),
                    "executor": executor,
                    "delayMilliseconds": int(
                        schedule.get("delayMs") or schedule.get("ms")
                        or transition.get("delayMilliseconds") or item.get("delayMilliseconds") or 0
                    ),
                })
        motions = []
        for index, item in enumerate(document.get("motions") or []):
            motions.append({
                "id": str(item.get("id") or f"{screen_id}.motion.{index + 1}"),
                "sourceNodeId": item.get("sourceNodeId"),
                "owner": "source-component",
                "ownerId": str(item.get("sourceNodeId") or screen_id),
                "executor": "native-property-animation",
                "kind": item.get("kind"),
                "durationMilliseconds": item.get("durationMs", item.get("durationMilliseconds")),
                "delayMilliseconds": item.get("delayMs", item.get("delayMilliseconds")),
                "timingFunction": item.get("timingFunction"),
                "keyframes": item.get("keyframes") or [],
            })
        screens.append({"screenId": screen_id, "actions": actions, "motions": motions})
    plan = {
        "schemaVersion": "native-interaction-motion-plan-1.0",
        "applicationPlanSha256": hashlib.sha256(args.application_plan.read_bytes()).hexdigest(),
        "presentationPlanSha256": hashlib.sha256(args.presentation_plan.read_bytes()).hexdigest(),
        "presentationStateIds": sorted(presentation_ids),
        "screens": screens,
        "invariants": {"oneOwnerPerAction": True, "oneOwnerPerMotion": True, "screenshotsRequired": False},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "actions": sum(len(item["actions"]) for item in screens), "motions": sum(len(item["motions"]) for item in screens)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
