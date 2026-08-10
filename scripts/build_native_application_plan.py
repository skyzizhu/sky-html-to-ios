#!/usr/bin/env python3
"""Build one global native application shell from validated UI IR screens."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-application-plan-1.0"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != "1.2" or not value.get("screens"):
        raise ValueError(f"{path}: expected validated UI IR 1.2")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_tabs(screens: list[dict[str, Any]], screen_ids: set[str]) -> dict[str, Any] | None:
    candidates = [screen.get("tabContainer") for screen in screens if isinstance(screen.get("tabContainer"), dict)]
    if not candidates:
        return None
    signatures = [json.dumps(
        {key: value for key, value in candidate.items() if key != "initialTabId"},
        ensure_ascii=False, sort_keys=True,
    ) for candidate in candidates]
    if len(set(signatures)) != 1:
        raise ValueError("screen tabContainer structures disagree")
    source = candidates[0]
    items = []
    for index, item in enumerate(source.get("items") or []):
        target = str(item.get("targetScreenId") or "")
        if target not in screen_ids:
            raise ValueError(f"tab target screen does not exist: {target!r}")
        items.append({
            "id": str(item.get("id") or target or f"tab-{index + 1}"),
            "title": str(item.get("title") or target),
            "targetScreenId": target,
            "icon": item.get("icon") or "circle",
            "selectedIcon": item.get("selectedIcon"),
            "badge": str(item.get("badge")) if item.get("badge") not in {None, ""} else None,
            "role": str(item.get("role") or "normal"),
        })
    if len(items) < 2:
        raise ValueError("a native tab container requires at least two valid items")
    initial = str(source.get("initialTabId") or items[0]["id"])
    if initial not in {item["id"] for item in items}:
        raise ValueError(f"initial tab does not exist: {initial!r}")
    return {
        "id": str(source.get("id") or "main-tabs"),
        "initialTabId": initial,
        "reselectBehavior": str(source.get("reselectBehavior") or "keep"),
        "visibility": str(source.get("visibility") or "automatic"),
        "items": items,
    }


def interaction_transitions(item: dict[str, Any]) -> list[dict[str, Any]]:
    transitions = (item.get("payload") or {}).get("transitions") or []
    return [transition for transition in transitions if isinstance(transition, dict)] or [item]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--ui-stack", required=True, choices=("swiftui", "uikit"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    documents = [load(path) for path in args.ir]
    screens = [screen for document in documents for screen in document.get("screens") or []]
    ids = [str(screen.get("id") or "") for screen in screens]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("UI IR screens require unique non-empty IDs")
    tabs = normalized_tabs(screens, set(ids))
    initial_screen = ids[0]
    stack_by_screen: dict[str, str] = {}
    navigation_stacks = []
    memberships = []
    if tabs:
        initial_item = next(item for item in tabs["items"] if item["id"] == tabs["initialTabId"])
        initial_screen = initial_item["targetScreenId"]
        for item in tabs["items"]:
            stack_id = f"{item['id']}-navigation"
            stack_by_screen[item["targetScreenId"]] = stack_id
            navigation_stacks.append({"id": stack_id, "tabId": item["id"], "rootScreenId": item["targetScreenId"]})
    else:
        navigation_stacks.append({"id": "main-navigation", "tabId": None, "rootScreenId": initial_screen})
    routes = []
    for document in documents:
        owner = str(document["screens"][0].get("id") or "")
        for item in document.get("interactions") or []:
            for transition_index, transition in enumerate(interaction_transitions(item)):
                action = str(transition.get("action") or item.get("action") or "")
                target = str(transition.get("targetScreenId") or transition.get("target") or item.get("target") or "")
                if action in {
                    "push", "replace-stack", "replace-root", "set-flow-state",
                    "replace-flow-state", "switch-tab", "select-tab",
                } and target in set(ids):
                    routes.append({
                        "interactionId": item.get("id"),
                        "transitionIndex": transition_index,
                        "sourceScreenId": owner,
                        "sourceNodeId": item.get("sourceNodeId"),
                        "action": action,
                        "targetScreenId": target,
                    })
    default_stack = navigation_stacks[0]["id"]
    tab_by_screen = {
        item["targetScreenId"]: item["id"] for item in (tabs or {}).get("items") or []
    }
    for screen_id in ids:
        memberships.append({
            "screenId": screen_id,
            "applicationContainerId": "main-application",
            "tabId": tab_by_screen.get(screen_id),
            "navigationStackId": stack_by_screen.get(screen_id, default_stack),
        })
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "uiStack": args.ui_stack,
        "inputs": [{"path": str(path.resolve()), "sha256": digest(path)} for path in args.ir],
        "applicationContainer": {
            "id": "main-application",
            "kind": "tab-navigation" if tabs else "navigation",
            "ownership": "generated-or-existing-project-router",
            "swiftUIType": "TabView + NavigationStack per tab" if tabs else "NavigationStack",
            "uiKitType": "UITabBarController + UINavigationController per tab" if tabs else "UINavigationController",
        },
        "initialScreenId": initial_screen,
        "tabContainer": tabs,
        "navigationStacks": navigation_stacks,
        "screenMemberships": memberships,
        "routes": routes,
        "invariants": {
            "singleApplicationContainer": True,
            "oneNavigationStackPerTab": True,
            "screenMembershipIsUnique": True,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "screens": len(ids), "tabs": len((tabs or {}).get("items") or [])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
