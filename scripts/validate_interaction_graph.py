#!/usr/bin/env python3
"""Validate an HTML interaction/state graph and its optional ambiguity overrides."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


GRAPH_SCHEMA = "interaction-state-graph-1.0"
OVERRIDES_SCHEMA = "html-to-ios-overrides-1.0"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def unique_ids(items: object, location: str, errors: list[str]) -> set[str]:
    if not isinstance(items, list):
        errors.append(f"{location} must be an array")
        return set()
    result: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{location}[{index}] must be an object")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            errors.append(f"{location}[{index}].id must be a non-empty string")
        elif item_id in result:
            errors.append(f"{location} contains duplicate id {item_id!r}")
        else:
            result.add(item_id)
    return result


def validate_confidence(item: dict, location: str, errors: list[str]) -> None:
    confidence = item.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        errors.append(f"{location}.confidence must be a number from 0 through 1")


def validate_graph(data: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if data.get("schemaVersion") != GRAPH_SCHEMA:
        errors.append(f"schemaVersion must be {GRAPH_SCHEMA!r}")

    screen_ids = unique_ids(data.get("screens"), "screens", errors)
    state_ids = unique_ids(data.get("states"), "states", errors)
    interaction_ids = unique_ids(data.get("interactions"), "interactions", errors)
    transition_ids = unique_ids(data.get("transitions"), "transitions", errors)
    unresolved_ids = unique_ids(data.get("unresolved"), "unresolved", errors)

    for index, state in enumerate(data.get("states") or []):
        if not isinstance(state, dict):
            continue
        location = f"states[{index}]"
        owner = state.get("ownerScreenId")
        if owner is not None and owner not in screen_ids:
            errors.append(f"{location}.ownerScreenId references unknown screen {owner!r}")
        if owner is None:
            warnings.append(f"{location} has no ownerScreenId")
        if not state.get("kind"):
            errors.append(f"{location}.kind is required")
        if not state.get("targetSelector"):
            warnings.append(f"{location} has no targetSelector and needs implementation review")
        validate_confidence(state, location, errors)

    for index, interaction in enumerate(data.get("interactions") or []):
        if not isinstance(interaction, dict):
            continue
        location = f"interactions[{index}]"
        owner = interaction.get("sourceScreenId")
        if owner is not None and owner not in screen_ids:
            errors.append(f"{location}.sourceScreenId references unknown screen {owner!r}")
        if not interaction.get("sourceSelector") and not interaction.get("sourceScope"):
            errors.append(f"{location} needs sourceSelector or sourceScope")
        if not interaction.get("trigger"):
            errors.append(f"{location}.trigger is required")
        validate_confidence(interaction, location, errors)

    for index, transition in enumerate(data.get("transitions") or []):
        if not isinstance(transition, dict):
            continue
        location = f"transitions[{index}]"
        interaction_id = transition.get("interactionId")
        if interaction_id is not None and interaction_id not in interaction_ids:
            errors.append(f"{location}.interactionId references unknown interaction {interaction_id!r}")
        source_screen = transition.get("sourceScreenId")
        target_screen = transition.get("targetScreenId")
        target_state = transition.get("targetStateId")
        if source_screen is not None and source_screen not in screen_ids:
            errors.append(f"{location}.sourceScreenId references unknown screen {source_screen!r}")
        if target_screen is not None and target_screen not in screen_ids:
            errors.append(f"{location}.targetScreenId references unknown screen {target_screen!r}")
        if target_state is not None and target_state not in state_ids:
            errors.append(f"{location}.targetStateId references unknown state {target_state!r}")
        if target_screen is None and target_state is None and transition.get("externalURL") is None:
            errors.append(f"{location} needs targetScreenId, targetStateId, or externalURL")
        if transition.get("trigger") == "automatic" and interaction_id is not None:
            warnings.append(f"{location} is automatic but is also attached to an interaction")
        validate_confidence(transition, location, errors)

    for index, item in enumerate(data.get("unresolved") or []):
        if not isinstance(item, dict):
            continue
        location = f"unresolved[{index}]"
        if item.get("transitionId") and item["transitionId"] not in transition_ids:
            errors.append(f"{location}.transitionId references unknown transition {item['transitionId']!r}")
        if item.get("interactionId") and item["interactionId"] not in interaction_ids:
            errors.append(f"{location}.interactionId references unknown interaction {item['interactionId']!r}")
        if not item.get("question"):
            errors.append(f"{location}.question is required")

    summary = data.get("summary")
    expected = {
        "screens": len(screen_ids),
        "states": len(state_ids),
        "interactions": len(interaction_ids),
        "transitions": len(transition_ids),
        "automaticTransitions": sum(1 for item in data.get("transitions") or [] if isinstance(item, dict) and item.get("trigger") == "automatic"),
        "runtimeVerified": sum(1 for item in data.get("interactions") or [] if isinstance(item, dict) and (item.get("runtimeEvidence") or {}).get("status") == "verified"),
        "unresolved": len(unresolved_ids),
    }
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
    else:
        for key, value in expected.items():
            if summary.get(key) != value:
                errors.append(f"summary.{key} must be {value}, got {summary.get(key)!r}")
    return errors, warnings


def validate_overrides(graph: dict, overrides: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if overrides.get("schemaVersion") != OVERRIDES_SCHEMA:
        errors.append(f"overrides schemaVersion must be {OVERRIDES_SCHEMA!r}")
    fingerprint = graph.get("source", {}).get("fingerprint")
    if not fingerprint:
        warnings.append("graph source fingerprint is missing")
    elif overrides.get("sourceFingerprint") != fingerprint:
        errors.append("overrides sourceFingerprint does not match the interaction graph")

    graph_unresolved = {item.get("id") for item in graph.get("unresolved") or [] if isinstance(item, dict)}
    override_unresolved = unique_ids(overrides.get("unresolved"), "overrides.unresolved", errors)
    resolution_ids = unique_ids(overrides.get("resolutions"), "overrides.resolutions", errors)
    unknown = (override_unresolved | resolution_ids) - graph_unresolved
    if unknown:
        errors.append(f"overrides reference unknown ambiguity ids: {sorted(unknown)}")
    missing = graph_unresolved - override_unresolved - resolution_ids
    if missing:
        warnings.append(f"overrides do not mention ambiguity ids: {sorted(missing)}")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("--overrides", type=Path)
    args = parser.parse_args()

    try:
        graph = load_json(args.graph)
        errors, warnings = validate_graph(graph)
        if args.overrides:
            override_errors, override_warnings = validate_overrides(graph, load_json(args.overrides))
            errors.extend(override_errors)
            warnings.extend(override_warnings)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"valid": False, "errors": [str(error)], "warnings": []}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
