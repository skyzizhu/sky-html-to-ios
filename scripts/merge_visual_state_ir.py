#!/usr/bin/env python3
"""Merge repeated-artboard UI IRs into one owner screen as generic state deltas."""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
from pathlib import Path


def compact(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def rect(node: dict) -> dict:
    return (node.get("layout") or {}).get("rect") or {}


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def geometry_similarity(left: dict, right: dict) -> float:
    left_rect, right_rect = rect(left), rect(right)
    scale = max(
        number(left_rect.get("width")), number(left_rect.get("height")),
        number(right_rect.get("width")), number(right_rect.get("height")), 24,
    )
    distance = sum(
        abs(number(left_rect.get(key)) - number(right_rect.get(key)))
        for key in ("x", "y", "width", "height")
    ) / scale
    return max(0.0, 1.0 - distance / 2.5)


def intersection_ratio(left: dict, right: dict) -> float:
    left_rect, right_rect = rect(left), rect(right)
    left_x, left_y = number(left_rect.get("x")), number(left_rect.get("y"))
    right_x, right_y = number(right_rect.get("x")), number(right_rect.get("y"))
    overlap_width = max(
        min(left_x + number(left_rect.get("width")), right_x + number(right_rect.get("width")))
        - max(left_x, right_x),
        0,
    )
    overlap_height = max(
        min(left_y + number(left_rect.get("height")), right_y + number(right_rect.get("height")))
        - max(left_y, right_y),
        0,
    )
    overlap = overlap_width * overlap_height
    left_area = max(number(left_rect.get("width")) * number(left_rect.get("height")), 1)
    right_area = max(number(right_rect.get("width")) * number(right_rect.get("height")), 1)
    return overlap / min(left_area, right_area)


def infer_contextual_target(owner_nodes: list[dict], action_roots: list[dict]) -> tuple[str | None, float]:
    semantic_weight = {
        "list-item": 4.0,
        "cell": 4.0,
        "row": 4.0,
        "text": 2.2,
        "label": 2.2,
        "container": 1.2,
    }
    ranked = []
    for candidate in owner_nodes:
        if not candidate.get("parentId"):
            continue
        overlap = max((intersection_ratio(candidate, action) for action in action_roots), default=0)
        if overlap <= 0:
            continue
        candidate_rect = rect(candidate)
        area = max(number(candidate_rect.get("width")) * number(candidate_rect.get("height")), 1)
        compactness = 1 / max(math.log10(area + 10), 1)
        semantic = str(candidate.get("semanticType") or "")
        action_width = max((number(rect(action).get("width")) for action in action_roots), default=0)
        row_like_container = (
            semantic == "container"
            and 44 <= number(candidate_rect.get("height")) <= 180
            and number(candidate_rect.get("width")) >= action_width * 1.25
        )
        score = overlap * 8 + (3.6 if row_like_container else semantic_weight.get(semantic, 0)) + compactness
        ranked.append((score, overlap, str(candidate["id"])))
    if not ranked:
        return None, 0
    ranked.sort(reverse=True)
    return ranked[0][2], round(min(ranked[0][0] / 12, 1), 3)


def node_score(owner: dict, variant: dict, parent_matches: dict[str, str]) -> float:
    owner_state_key = compact((owner.get("iosHints") or {}).get("state-key"))
    variant_state_key = compact((variant.get("iosHints") or {}).get("state-key"))
    if owner_state_key and variant_state_key:
        if owner_state_key != variant_state_key:
            return -math.inf
        return 20 + geometry_similarity(owner, variant) * 4
    if owner.get("semanticType") != variant.get("semanticType"):
        return -math.inf
    owner_content, variant_content = owner.get("content") or {}, variant.get("content") or {}
    owner_text, variant_text = compact(owner_content.get("text")), compact(variant_content.get("text"))
    variant_parent = str(variant.get("parentId") or "")
    matched_parent = parent_matches.get(variant_parent)
    owner_parent = str(owner.get("parentId") or "")
    if variant_parent and matched_parent and matched_parent != owner_parent:
        return -math.inf
    score = 4 + geometry_similarity(owner, variant) * 4
    if owner_text and owner_text == variant_text:
        score += 5
    elif owner_text and variant_text:
        score -= 2
    elif not owner_text and not variant_text:
        score += 1
    owner_source, variant_source = owner.get("source") or {}, variant.get("source") or {}
    if owner_source.get("tag") == variant_source.get("tag"):
        score += 1
    if variant_parent and matched_parent == owner_parent:
        score += 3
    return score


def match_nodes(owner_nodes: list[dict], variant_nodes: list[dict]) -> dict[str, str]:
    owner_root = next((node for node in owner_nodes if not node.get("parentId")), owner_nodes[0])
    variant_root = next((node for node in variant_nodes if not node.get("parentId")), variant_nodes[0])
    matches = {str(variant_root["id"]): str(owner_root["id"])}
    used = {str(owner_root["id"])}
    pending = [node for node in variant_nodes if str(node["id"]) not in matches]
    while pending:
        progressed = False
        for variant in list(pending):
            variant_parent = str(variant.get("parentId") or "")
            if variant_parent and variant_parent not in matches:
                continue
            ranked = sorted(
                (
                    (node_score(owner, variant, matches), str(owner["id"]))
                    for owner in owner_nodes
                    if str(owner["id"]) not in used
                ),
                reverse=True,
            )
            if ranked and ranked[0][0] >= 8:
                matches[str(variant["id"])] = ranked[0][1]
                used.add(ranked[0][1])
            pending.remove(variant)
            progressed = True
        if not progressed:
            break
    return matches


def changed_fields(owner: dict, variant: dict) -> dict:
    changes = {}
    owner_content, variant_content = owner.get("content") or {}, variant.get("content") or {}
    if compact(owner_content.get("text")) != compact(variant_content.get("text")):
        changes["content"] = copy.deepcopy(variant_content)
    style_keys = (
        "backgroundColor", "backgroundImage", "color", "opacity", "borderRadius",
        "borderTopColor", "borderTopWidth", "boxShadow", "transform",
    )
    style_changes = {
        key: (variant.get("style") or {}).get(key)
        for key in style_keys
        if (owner.get("style") or {}).get(key) != (variant.get("style") or {}).get(key)
    }
    if style_changes:
        changes["style"] = style_changes
    if geometry_similarity(owner, variant) < 0.96:
        changes["layoutRect"] = copy.deepcopy(rect(variant))
    return changes


def merge_assets(owner: dict, variant: dict, state_id: str, copied_nodes: list[dict]) -> None:
    owner_assets = owner.setdefault("assets", [])
    owner_by_id = {str(asset.get("id")): asset for asset in owner_assets}
    remap = {}
    for asset in variant.get("assets") or []:
        asset_id = str(asset.get("id") or "")
        if not asset_id:
            continue
        if asset_id not in owner_by_id:
            owner_assets.append(copy.deepcopy(asset))
            owner_by_id[asset_id] = asset
            continue
        if owner_by_id[asset_id] == asset:
            continue
        new_id = f"{state_id}.{asset_id}"
        cloned = copy.deepcopy(asset)
        cloned["id"] = new_id
        owner_assets.append(cloned)
        remap[asset_id] = new_id
    for node in copied_nodes:
        asset_ref = node.get("assetRef")
        if asset_ref in remap:
            node["assetRef"] = remap[asset_ref]


def synthetic_container(owner_root: dict, state_id: str) -> dict:
    container = copy.deepcopy(owner_root)
    container.update({
        "id": f"state.{state_id}.container",
        "parentId": str(owner_root["id"]),
        "source": {
            "selector": None, "tag": "div", "domId": None, "runtimeId": None,
            "synthetic": "state-delta-container", "encapsulation": None,
        },
        "semanticType": "container",
        "content": {
            "text": None, "runs": [], "placeholder": None, "value": None,
            "accessibilityLabel": None, "lines": None, "lineRects": [],
            "lineTexts": [], "isDecorative": False,
        },
        "state": {**(owner_root.get("state") or {}), "initiallyVisible": False},
        "textBehavior": None,
        "dataBinding": None,
        "controlVisualStates": None,
        "assetRef": None,
        "interactionRef": None,
        "interactionRefs": [],
        "support": "native-fallback",
        "iosHints": {"state-owner": state_id},
    })
    container["layout"] = {
        **(container.get("layout") or {}),
        "mode": "overlay",
        "position": "absolute",
        "overflowX": "visible",
        "overflowY": "visible",
        "scrollAxis": "none",
    }
    container["style"] = {
        **(container.get("style") or {}),
        "display": "block",
        "position": "absolute",
        "backgroundColor": "transparent",
        "backgroundImage": "none",
        "opacity": "1",
        "pointerEvents": "auto",
    }
    return container


def merge_state(owner: dict, variant: dict, state_id: str) -> dict:
    owner_screen = owner["screens"][0]
    variant_screen = variant["screens"][0]
    all_owner_nodes = owner_screen.get("nodes") or []
    owner_nodes = [
        node for node in all_owner_nodes
        if not (node.get("iosHints") or {}).get("state-owner")
    ]
    variant_nodes = variant_screen.get("nodes") or []
    owner_by_id = {str(node["id"]): node for node in owner_nodes}
    variant_by_id = {str(node["id"]): node for node in variant_nodes}
    matches = match_nodes(owner_nodes, variant_nodes)
    reverse_matches = {owner_id: variant_id for variant_id, owner_id in matches.items()}
    unmatched_variant = set(variant_by_id) - set(matches)
    unmatched_owner = set(owner_by_id) - set(reverse_matches)
    additions = [
        node_id for node_id in unmatched_variant
        if str(variant_by_id[node_id].get("parentId") or "") not in unmatched_variant
    ]
    removals = [
        node_id for node_id in unmatched_owner
        if str(owner_by_id[node_id].get("parentId") or "") not in unmatched_owner
    ]
    state = next((item for item in owner.get("states") or [] if item.get("id") == state_id), None)
    if state is None:
        raise ValueError(f"Owner IR has no state {state_id!r}")
    is_presentation = str(state.get("kind") or "") in {
        "sheet", "full-screen", "fullscreen", "full-screen-overlay",
        "popover", "popover-overlay", "overlay", "dialog",
    }
    variant_children: dict[str, list[str]] = {}
    for node in variant_nodes:
        variant_children.setdefault(str(node.get("parentId") or ""), []).append(str(node["id"]))

    def descendants(node_id: str) -> set[str]:
        result = {node_id}
        pending = list(variant_children.get(node_id) or [])
        while pending:
            child_id = pending.pop()
            if child_id in result:
                continue
            result.add(child_id)
            pending.extend(variant_children.get(child_id) or [])
        return result

    updates = []
    for variant_id, owner_id in matches.items():
        if variant_id == str(variant_screen.get("rootNodeId")):
            continue
        changes = changed_fields(owner_by_id[owner_id], variant_by_id[variant_id])
        if changes:
            updates.append({
                "variantNodeId": variant_id,
                "targetNodeId": owner_id,
                "changes": changes,
            })

    updated_variant_ids = {item["variantNodeId"] for item in updates}
    filtered_update_roots = []
    for item in updates:
        current = str(variant_by_id[item["variantNodeId"]].get("parentId") or "")
        nested = False
        while current:
            if current in updated_variant_ids:
                nested = True
                break
            current = str(variant_by_id.get(current, {}).get("parentId") or "")
        if not nested:
            filtered_update_roots.append(item)
    update_roots = filtered_update_roots

    update_subtree_ids = set()
    for item in update_roots:
        update_subtree_ids.update(descendants(item["variantNodeId"]))
    unmatched_additions_in_updates = unmatched_variant & update_subtree_ids
    owner_children: dict[str, list[str]] = {}
    for node in owner_nodes:
        owner_children.setdefault(str(node.get("parentId") or ""), []).append(str(node["id"]))

    def owner_descendants(node_id: str) -> set[str]:
        result = {node_id}
        pending = list(owner_children.get(node_id) or [])
        while pending:
            child_id = pending.pop()
            if child_id in result:
                continue
            result.add(child_id)
            pending.extend(owner_children.get(child_id) or [])
        return result

    unmatched_removals_in_updates = {
        descendant_id
        for item in update_roots
        for descendant_id in owner_descendants(item["targetNodeId"])
        if descendant_id in unmatched_owner
    }
    clone_ids = unmatched_variant | update_subtree_ids
    prefix = f"state.{state_id}."
    copied_by_original: dict[str, dict] = {}
    for original_id in clone_ids:
        cloned = copy.deepcopy(variant_by_id[original_id])
        cloned["id"] = f"{prefix}{original_id}"
        cloned["iosHints"] = {
            **(cloned.get("iosHints") or {}),
            "state-owner": state_id,
        }
        copied_by_original[original_id] = cloned
    owner_root = next((node for node in owner_nodes if not node.get("parentId")), owner_nodes[0])
    container = synthetic_container(owner_root, state_id) if is_presentation and additions else None
    for original_id, cloned in copied_by_original.items():
        original_parent = str(variant_by_id[original_id].get("parentId") or "")
        if original_parent in copied_by_original:
            cloned["parentId"] = copied_by_original[original_parent]["id"]
        elif container is not None:
            cloned["parentId"] = container["id"]
        else:
            cloned["parentId"] = matches.get(original_parent, str(owner_root["id"]))
    copied_nodes = list(copied_by_original.values())
    merge_assets(owner, variant, state_id, copied_nodes)
    interaction_id_map = {}
    copied_interactions = []
    for interaction in variant.get("interactions") or []:
        source_ids = [
            str(item)
            for item in (interaction.get("sourceNodeIds") or [interaction.get("sourceNodeId")])
            if item
        ]
        mapped_source_ids = [
            copied_by_original[item]["id"]
            for item in source_ids
            if item in copied_by_original
        ]
        if not mapped_source_ids:
            continue
        cloned = copy.deepcopy(interaction)
        original_interaction_id = str(interaction.get("id") or f"interaction-{len(copied_interactions) + 1}")
        cloned["id"] = f"{prefix}{original_interaction_id}"
        cloned["sourceNodeId"] = mapped_source_ids[0]
        if "sourceNodeIds" in cloned:
            cloned["sourceNodeIds"] = mapped_source_ids
        interaction_id_map[original_interaction_id] = cloned["id"]
        copied_interactions.append(cloned)
    owner.setdefault("interactions", []).extend(copied_interactions)
    for cloned in copied_nodes:
        reference = cloned.get("interactionRef")
        cloned["interactionRef"] = interaction_id_map.get(str(reference)) if reference else None
        cloned["interactionRefs"] = [
            interaction_id_map[str(item)]
            for item in cloned.get("interactionRefs") or []
            if str(item) in interaction_id_map
        ]
    if container is not None:
        all_owner_nodes.append(container)
    all_owner_nodes.extend(copied_nodes)

    additions = [item for item in additions if item not in unmatched_additions_in_updates]
    replacement_pairs: dict[str, str] = {}
    available_removals = set(removals) - unmatched_removals_in_updates
    for addition_id in additions:
        generated_parent = str(copied_by_original[addition_id].get("parentId") or "")
        ranked_removals = sorted(
            (
                (geometry_similarity(owner_by_id[removal_id], variant_by_id[addition_id]), removal_id)
                for removal_id in available_removals
                if str(owner_by_id[removal_id].get("parentId") or "") == generated_parent
            ),
            reverse=True,
        )
        if ranked_removals and ranked_removals[0][0] >= 0.65:
            replacement_pairs[addition_id] = ranked_removals[0][1]
            available_removals.remove(ranked_removals[0][1])
    protected_interaction_sources = {
        str(source_id)
        for interaction in owner.get("interactions") or []
        for source_id in (
            interaction.get("sourceNodeIds")
            or [interaction.get("sourceNodeId")]
        )
        if source_id
    }
    protected_interaction_scope = set(protected_interaction_sources)
    for source_id in protected_interaction_sources:
        current = str(owner_by_id.get(source_id, {}).get("parentId") or "")
        while current:
            protected_interaction_scope.add(current)
            current = str(owner_by_id.get(current, {}).get("parentId") or "")
    transition_actions = {
        str(transition.get("action") or "").lower()
        for interaction in owner.get("interactions") or []
        for transition in (interaction.get("payload") or {}).get("transitions") or []
        if transition.get("targetStateId") == state_id
    }
    visual_effect = compact(((state.get("visualRepresentation") or {}).get("localEffect")))
    removal_intent = bool(
        any(re.search(r"remove|delete|hide|dismiss", item) for item in transition_actions)
        or re.search(r"remove|delete|dismiss", visual_effect)
    )
    explicit_removals = {
        node_id
        for node_id in available_removals
        if compact((owner_by_id[node_id].get("iosHints") or {}).get("state-removable"))
        in {"1", "true", "yes", state_id.lower()}
    }
    protected_missing_sources = sorted(available_removals & protected_interaction_scope)
    removal_candidates = available_removals - protected_interaction_scope
    accepted_removals = (
        removal_candidates
        if removal_intent
        else explicit_removals
    )
    suppressed_removals = sorted(removal_candidates - accepted_removals)
    operations = [
        {
            "kind": "insert-subtree",
            "sourceNodeId": item,
            "generatedRootNodeId": copied_by_original[item]["id"],
            "targetParentNodeId": copied_by_original[item]["parentId"],
        }
        for item in additions
        if item not in replacement_pairs
    ]
    operations.extend({
        "kind": "replace-subtree",
        "targetNodeId": removal_id,
        "sourceNodeId": addition_id,
        "generatedRootNodeId": copied_by_original[addition_id]["id"],
        "targetParentNodeId": copied_by_original[addition_id]["parentId"],
    } for addition_id, removal_id in replacement_pairs.items())
    operations.extend({
        "kind": "replace-subtree",
        "targetNodeId": item["targetNodeId"],
        "sourceNodeId": item["variantNodeId"],
        "generatedRootNodeId": copied_by_original[item["variantNodeId"]]["id"],
        "targetParentNodeId": copied_by_original[item["variantNodeId"]]["parentId"],
        "changes": item["changes"],
        "reason": "property-or-layout-change",
    } for item in update_roots)
    operations.extend({"kind": "remove-subtree", "targetNodeId": item} for item in accepted_removals)
    generated_root_ids = {
        str(item.get("generatedRootNodeId"))
        for item in operations
        if item.get("kind") in {"insert-subtree", "replace-subtree"}
        and item.get("generatedRootNodeId")
    }
    for cloned in copied_nodes:
        if cloned["id"] in generated_root_ids:
            cloned["state"] = {**(cloned.get("state") or {}), "initiallyVisible": False}
    operation_kinds = {item["kind"] for item in operations}
    triggers = {
        str(interaction.get("trigger") or "")
        for interaction in owner.get("interactions") or []
        for transition in (interaction.get("payload") or {}).get("transitions") or []
        if transition.get("targetStateId") == state_id
    }
    inserted_roots = [variant_by_id[item] for item in additions]
    contextual_target_id = None
    contextual_target_confidence = 0.0
    if is_presentation:
        native_strategy = "detached-presentation"
    elif triggers & {"swipe", "pan", "drag"} and inserted_roots and all(
        item.get("semanticType") in {"button", "icon-button", "link", "menu-item", "container"}
        for item in inserted_roots
    ):
        native_strategy = "contextual-item-actions"
        contextual_target_id, contextual_target_confidence = infer_contextual_target(
            owner_nodes,
            inserted_roots,
        )
    elif operation_kinds == {"insert-subtree"}:
        native_strategy = "conditional-subtree"
    elif operation_kinds == {"remove-subtree"}:
        native_strategy = "conditional-removal"
    elif "replace-subtree" in operation_kinds:
        native_strategy = "subtree-replacement"
    else:
        native_strategy = "composite-delta"
    conditional_parent_ids = sorted({
        str(item.get("targetParentNodeId"))
        for item in operations
        if item.get("kind") in {"insert-subtree", "replace-subtree"}
        and item.get("targetParentNodeId")
    })
    state["targetNodeIds"] = (
        [container["id"]]
        if container is not None
        else conditional_parent_ids
        if conditional_parent_ids
        else sorted(accepted_removals)
    )
    if not is_presentation and generated_root_ids:
        state["kind"] = "expansion"
    state["stateDelta"] = {
        "schemaVersion": "visual-state-delta-1.0",
        "representationScreenId": variant_screen.get("id"),
        "matchedNodeCount": len(matches),
        "ownerNodeCount": len(owner_nodes),
        "representationNodeCount": len(variant_nodes),
        "operations": operations,
        "nativeStrategy": native_strategy,
        "triggers": sorted(trigger for trigger in triggers if trigger),
        "contextualTargetNodeId": contextual_target_id,
        "contextualTargetConfidence": contextual_target_confidence,
        "contextualActionRootNodeIds": sorted(
            str(item.get("generatedRootNodeId"))
            for item in operations
            if native_strategy == "contextual-item-actions"
            and item.get("kind") == "insert-subtree"
            and item.get("generatedRootNodeId")
        ),
        "suppressedRemovalNodeIds": suppressed_removals,
        "protectedInteractionSourceNodeIds": protected_missing_sources,
        "confidence": round(len(matches) / max(min(len(owner_by_id), len(variant_by_id)), 1), 3),
    }
    requires_review = (
        state["stateDelta"]["confidence"] < 0.65
        or bool(suppressed_removals)
        or (native_strategy == "contextual-item-actions" and not contextual_target_id)
    )
    review = {
        "stateId": state_id,
        "ownerScreenId": owner_screen.get("id"),
        "representationScreenId": variant_screen.get("id"),
        "decision": "merge-with-review" if requires_review else "merge",
        "requiresHumanReview": requires_review,
        "confidence": state["stateDelta"]["confidence"],
        "nativeStrategy": native_strategy,
        "contextualTargetNodeId": contextual_target_id,
        "operationCounts": {
            kind: sum(item.get("kind") == kind for item in operations)
            for kind in ("insert-subtree", "remove-subtree", "replace-subtree")
        },
        "suppressedRemovalNodeIds": suppressed_removals,
        "protectedInteractionSourceNodeIds": protected_missing_sources,
        "reasons": [
            f"matched {len(matches)} of {min(len(owner_by_id), len(variant_by_id))} comparable nodes",
            f"selected native strategy {native_strategy}",
            *(
                [f"suppressed {len(suppressed_removals)} ambiguous removals"]
                if suppressed_removals else []
            ),
        ],
        "recommendedHints": (
            ["data-ios-state-owner", "data-ios-state-key", "data-ios-state-removable"]
            if requires_review else []
        ),
    }
    owner.setdefault("stateDeltaReviews", []).append(review)
    if state["stateDelta"]["confidence"] < 0.65:
        owner.setdefault("warnings", []).append({
            "code": "LOW_STATE_DELTA_CONFIDENCE",
            "severity": "warning",
            "nodeId": None,
            "message": f"State {state_id} shares too few stable nodes with its owner.",
            "fallback": "Review the generated state delta or add stable semantic and data-ios-node-id evidence.",
        })
    return state["stateDelta"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", type=Path, required=True)
    parser.add_argument("--state", action="append", default=[], help="STATE_ID=UI_IR_PATH")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    owner = json.loads(args.owner.read_text(encoding="utf-8"))
    summaries = []
    for value in args.state:
        state_id, separator, path = value.partition("=")
        if not separator or not state_id or not path:
            parser.error("--state must use STATE_ID=UI_IR_PATH")
        variant = json.loads(Path(path).read_text(encoding="utf-8"))
        summaries.append({"stateId": state_id, **merge_state(owner, variant, state_id)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(owner, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "states": summaries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
