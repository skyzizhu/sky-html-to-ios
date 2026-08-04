#!/usr/bin/env python3
"""Validate the structural invariants of a sky-html-to-ios UI IR file."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


ALLOWED_STACKS = {"swiftui", "uikit"}
ALLOWED_CHROME = {"native", "custom", "none"}
ALLOWED_SUPPORT = {"native", "native-fallback", "placeholder", "unsupported"}
ALLOWED_SEMANTIC_TYPES = {
    "container", "header", "footer", "navigation", "navigation-bar", "tab-bar", "tab-control",
    "scroll", "grid", "divider", "spacer", "text", "heading", "label", "image", "icon",
    "decoration",
    "video", "audio", "canvas-artwork", "map", "embedded-content", "unsupported-web-content",
    "button", "icon-button", "link", "form", "text-input", "secure-input", "search-input",
    "number-input", "date-input", "text-area", "file-input", "checkbox", "switch", "radio", "radio-group",
    "segmented-control", "select", "multi-select", "option", "option-group", "slider", "stepper", "color-picker",
    "list", "list-item", "sectioned-list", "data-table", "table-row", "table-header", "table-cell", "carousel",
    "progress", "meter", "disclosure", "disclosure-trigger", "form-group", "tab-item", "menu-item",
    "modal", "sheet", "alert", "toast", "menu", "loading", "overlay", "custom",
}
ALLOWED_STYLE_STRATEGIES = {
    "native-default", "plain-native-semantics-custom-appearance", "project-component",
    "custom-native-view", "custom-native-control", "custom-view-controller", "native-fallback", "unsupported",
}
ALLOWED_ACTIONS = {
    "push", "pop", "pop-to-root", "replace-stack", "back", "show-primary", "show-detail",
    "present", "present-sheet", "present-fullscreen", "present-popover", "present-alert",
    "present-confirmation", "present-menu", "dismiss", "overlay", "add-child", "remove-child",
    "switch-tab", "page-next", "page-previous", "toggle-state", "update-value", "scroll-to",
    "open-url", "submit", "set-flow-state", "unknown",
}
ALLOWED_AVAILABILITY_STATUS = {"pending-verification", "available", "available-review-version", "requires-fallback", "deprecated", "unavailable", "review-required"}
ALLOWED_SCROLL_AXES = {"none", "horizontal", "vertical", "both"}
ALLOWED_DATA_STATE_ROLES = {"loading", "content", "empty", "error"}
ALLOWED_PAGINATION = {"none", "page", "cursor", "infinite"}
ALLOWED_CONTROL_DECISIONS = {
    "system-control", "system-control-with-native-wrapper", "native-composition",
    "system-view", "system-container", "native-view", "project-component", "unsupported",
}
ALLOWED_VISUAL_CORRECTION_PROPERTIES = {"x", "y", "width", "height"}


def validate_rect(rect, location, errors):
    if not isinstance(rect, dict):
        errors.append(f"{location}.rect must be an object")
        return
    for key in ("x", "y", "width", "height"):
        value = rect.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            errors.append(f"{location}.rect.{key} must be a finite number")
    for key in ("width", "height"):
        value = rect.get(key)
        if isinstance(value, (int, float)) and value < 0:
            errors.append(f"{location}.rect.{key} must be non-negative")


def validate(data):
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["IR root must be an object"], warnings
    if data.get("schemaVersion") != "1.2":
        errors.append("schemaVersion must be '1.2'")
    for key in ("source", "target"):
        if not isinstance(data.get(key), dict):
            errors.append(f"{key} must be an object")
    target = data.get("target", {})
    if target.get("uiStack") not in ALLOWED_STACKS:
        errors.append("target.uiStack must be 'swiftui' or 'uikit'")
    correction_history = data.get("visualCorrectionHistory")
    if correction_history is not None:
        if not isinstance(correction_history, list):
            errors.append("visualCorrectionHistory must be an array")
        else:
            for index, entry in enumerate(correction_history):
                where = f"visualCorrectionHistory[{index}]"
                if not isinstance(entry, dict):
                    errors.append(f"{where} must be an object")
                    continue
                if entry.get("schemaVersion") != "ui-ir-visual-correction-application-1.0":
                    errors.append(f"{where}.schemaVersion is invalid")
                if not isinstance(entry.get("sourceIR"), str) or not entry.get("sourceIR"):
                    errors.append(f"{where}.sourceIR must be a non-empty string")
                source_hash = entry.get("sourceIRSha256")
                if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
                    errors.append(f"{where}.sourceIRSha256 must be a lowercase SHA-256")
                for key in ("iteration", "appliedCount", "rejectedCount"):
                    value = entry.get(key)
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        errors.append(f"{where}.{key} must be a non-negative integer")
    screens = data.get("screens")
    if not isinstance(screens, list) or not screens:
        errors.append("screens must be a non-empty array")
        screens = []

    all_ids: set[str] = set()
    screen_ids: set[str] = set()
    interaction_refs: list[tuple[str, str]] = []
    asset_refs: list[tuple[str, str]] = []
    for s_index, screen in enumerate(screens):
        where = f"screens[{s_index}]"
        if not isinstance(screen, dict):
            errors.append(f"{where} must be an object")
            continue
        screen_id = screen.get("id")
        if not isinstance(screen_id, str) or not screen_id:
            errors.append(f"{where}.id must be a non-empty string")
        elif screen_id in screen_ids:
            errors.append(f"Duplicate screen id: {screen_id}")
        else:
            screen_ids.add(screen_id)
        chrome = screen.get("systemChrome", {})
        if not isinstance(chrome, dict):
            errors.append(f"{where}.systemChrome must be an object")
        else:
            for key in ("statusBar", "navigationBar", "homeIndicator"):
                if chrome.get(key) not in ALLOWED_CHROME:
                    errors.append(f"{where}.systemChrome.{key} has invalid value")
        nodes = screen.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            errors.append(f"{where}.nodes must be a non-empty array")
            continue
        source_coverage = screen.get("sourceCoverage")
        if source_coverage is not None:
            if not isinstance(source_coverage, dict):
                errors.append(f"{where}.sourceCoverage must be an object")
            else:
                for key in (
                    "rootSubtreeNodeCount", "routeScopedNodeCount", "mappedNodeCount",
                    "excludedByRouteCount", "excludedNonVisualOrUnsupportedTagCount",
                ):
                    value = source_coverage.get(key)
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        errors.append(f"{where}.sourceCoverage.{key} must be a non-negative integer")
                ratio = source_coverage.get("mappedRatio")
                if (
                    not isinstance(ratio, (int, float))
                    or isinstance(ratio, bool)
                    or not 0 <= ratio <= 1
                ):
                    errors.append(f"{where}.sourceCoverage.mappedRatio must be between 0 and 1")
                mapped = source_coverage.get("mappedNodeCount")
                excluded = source_coverage.get("excludedNonVisualOrUnsupportedTagCount")
                scoped = source_coverage.get("routeScopedNodeCount")
                if all(isinstance(value, int) and not isinstance(value, bool) for value in (mapped, excluded, scoped)):
                    if mapped + excluded != scoped:
                        errors.append(
                            f"{where}.sourceCoverage mapped and excluded counts must equal routeScopedNodeCount"
                        )
        local_ids: set[str] = set()
        local_parents: dict[str, str | None] = {}
        for n_index, node in enumerate(nodes):
            nwhere = f"{where}.nodes[{n_index}]"
            if not isinstance(node, dict):
                errors.append(f"{nwhere} must be an object")
                continue
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id:
                errors.append(f"{nwhere}.id must be a non-empty string")
                continue
            if node_id in all_ids:
                errors.append(f"Duplicate node id: {node_id}")
            all_ids.add(node_id)
            local_ids.add(node_id)
            parent_id = node.get("parentId")
            local_parents[node_id] = parent_id if isinstance(parent_id, str) and parent_id else None
            if parent_id is not None:
                if not isinstance(parent_id, str) or not parent_id:
                    errors.append(f"{nwhere}.parentId must be null or a non-empty string")
            scroll_axis = None
            scroll_metrics = None
            layout = node.get("layout")
            if not isinstance(layout, dict):
                errors.append(f"{nwhere}.layout must be an object")
            else:
                validate_rect(layout.get("rect"), f"{nwhere}.layout", errors)
                scroll_axis = layout.get("scrollAxis")
                if scroll_axis is not None and scroll_axis not in ALLOWED_SCROLL_AXES:
                    errors.append(f"{nwhere}.layout.scrollAxis has invalid value: {scroll_axis}")
                scroll_metrics = layout.get("scrollMetrics")
                if scroll_metrics is not None:
                    if not isinstance(scroll_metrics, dict):
                        errors.append(f"{nwhere}.layout.scrollMetrics must be an object")
                    else:
                        for key in ("scrollWidth", "scrollHeight", "clientWidth", "clientHeight"):
                            value = scroll_metrics.get(key)
                            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                                errors.append(f"{nwhere}.layout.scrollMetrics.{key} must be a non-negative finite number")
                        for key in (
                            "horizontalAllowed", "verticalAllowed",
                            "overflowsHorizontally", "overflowsVertically",
                        ):
                            if not isinstance(scroll_metrics.get(key), bool):
                                errors.append(f"{nwhere}.layout.scrollMetrics.{key} must be a boolean")
            if not isinstance(node.get("style"), dict):
                errors.append(f"{nwhere}.style must be an object")
            if not isinstance(node.get("content"), dict):
                errors.append(f"{nwhere}.content must be an object")
            if not isinstance(node.get("state"), dict):
                errors.append(f"{nwhere}.state must be an object")
            calibration = node.get("calibration")
            if calibration is not None:
                if not isinstance(calibration, dict):
                    errors.append(f"{nwhere}.calibration must be an object")
                else:
                    visual_corrections = calibration.get("visualCorrections")
                    if visual_corrections is not None:
                        if not isinstance(visual_corrections, list):
                            errors.append(f"{nwhere}.calibration.visualCorrections must be an array")
                        else:
                            for correction_index, correction in enumerate(visual_corrections):
                                cwhere = f"{nwhere}.calibration.visualCorrections[{correction_index}]"
                                if not isinstance(correction, dict):
                                    errors.append(f"{cwhere} must be an object")
                                    continue
                                operations = correction.get("operations")
                                if not isinstance(operations, list) or not operations:
                                    errors.append(f"{cwhere}.operations must be a non-empty array")
                                    continue
                                for operation_index, operation in enumerate(operations):
                                    owhere = f"{cwhere}.operations[{operation_index}]"
                                    if not isinstance(operation, dict):
                                        errors.append(f"{owhere} must be an object")
                                        continue
                                    if operation.get("property") not in ALLOWED_VISUAL_CORRECTION_PROPERTIES:
                                        errors.append(f"{owhere}.property is invalid")
                                    values = [operation.get(key) for key in ("before", "after", "amount")]
                                    if any(
                                        not isinstance(value, (int, float))
                                        or isinstance(value, bool)
                                        or not math.isfinite(value)
                                        for value in values
                                    ):
                                        errors.append(f"{owhere} values must be finite numbers")
                                    elif abs((values[0] + values[2]) - values[1]) > 0.01:
                                        errors.append(f"{owhere}.after must equal before plus amount")
            text_behavior = node.get("textBehavior")
            if text_behavior is not None:
                if not isinstance(text_behavior, dict):
                    errors.append(f"{nwhere}.textBehavior must be an object")
                else:
                    if text_behavior.get("role") not in {"input", "display"}:
                        errors.append(f"{nwhere}.textBehavior.role has invalid value")
                    if text_behavior.get("nativeControl") not in {"label", "text-field", "text-view"}:
                        errors.append(f"{nwhere}.textBehavior.nativeControl has invalid value")
                    for key in ("editable", "readOnly", "selectable", "multiline", "scrollable", "secure"):
                        if not isinstance(text_behavior.get(key), bool):
                            errors.append(f"{nwhere}.textBehavior.{key} must be a boolean")
                    placeholder_style = text_behavior.get("placeholderStyle")
                    if placeholder_style is not None and not isinstance(placeholder_style, dict):
                        errors.append(f"{nwhere}.textBehavior.placeholderStyle must be an object")
            data_binding = node.get("dataBinding")
            if data_binding is not None:
                if not isinstance(data_binding, dict):
                    errors.append(f"{nwhere}.dataBinding must be an object")
                else:
                    state_role = data_binding.get("stateRole")
                    if state_role is not None and state_role not in ALLOWED_DATA_STATE_ROLES:
                        errors.append(f"{nwhere}.dataBinding.stateRole has invalid value")
                    if data_binding.get("pagination") not in ALLOWED_PAGINATION:
                        errors.append(f"{nwhere}.dataBinding.pagination has invalid value")
                    if data_binding.get("ownership") not in {"external", "local-prototype-state"}:
                        errors.append(f"{nwhere}.dataBinding.ownership has invalid value")
                    for key in ("requiresViewModel", "snapshotIsSampleData"):
                        if not isinstance(data_binding.get(key), bool):
                            errors.append(f"{nwhere}.dataBinding.{key} must be a boolean")
                    if data_binding.get("ownership") == "external" and not data_binding.get("sourceID"):
                        errors.append(f"{nwhere}.dataBinding.sourceID is required for external ownership")
            control_states = node.get("controlVisualStates")
            if control_states is not None:
                if not isinstance(control_states, dict):
                    errors.append(f"{nwhere}.controlVisualStates must be an object")
                else:
                    for key, value in control_states.items():
                        if key not in {"pressed", "focused", "disabled", "selected"}:
                            errors.append(f"{nwhere}.controlVisualStates has invalid state: {key}")
                        if not isinstance(value, dict):
                            errors.append(f"{nwhere}.controlVisualStates.{key} must be an object")
            semantic_type = node.get("semanticType")
            if semantic_type not in ALLOWED_SEMANTIC_TYPES:
                errors.append(f"{nwhere}.semanticType has invalid value: {semantic_type}")
            elif semantic_type == "carousel" and scroll_axis != "horizontal":
                errors.append(f"{nwhere} carousel must declare layout.scrollAxis='horizontal'")
            elif semantic_type == "scroll" and scroll_axis in {None, "none"}:
                errors.append(f"{nwhere} scroll container must declare an active layout.scrollAxis")
            if parent_id is None and scroll_axis == "both":
                warnings.append(
                    f"{nwhere} root uses two-axis scrolling; confirm the source is a true two-dimensional surface."
                )
            if (
                parent_id is None
                and isinstance(scroll_metrics, dict)
                and scroll_metrics.get("overflowsHorizontally") is True
                and scroll_axis != "horizontal"
                and scroll_axis != "both"
            ):
                warnings.append(
                    f"{nwhere} root has measured horizontal overflow outside its declared axis; "
                    "locate the oversized descendant instead of enabling page-level horizontal scrolling."
                )
            mapping = node.get("nativeMapping")
            if not isinstance(mapping, dict):
                errors.append(f"{nwhere}.nativeMapping must be an object")
            else:
                for key in ("swiftUI", "uiKit"):
                    if not isinstance(mapping.get(key), str) or not mapping.get(key):
                        errors.append(f"{nwhere}.nativeMapping.{key} must be a non-empty string")
                if mapping.get("styleStrategy") not in ALLOWED_STYLE_STRATEGIES:
                    errors.append(f"{nwhere}.nativeMapping.styleStrategy has invalid value")
                confidence = mapping.get("confidence")
                if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                    errors.append(f"{nwhere}.nativeMapping.confidence must be between 0 and 1")
                if not isinstance(mapping.get("rationale"), list) or not mapping.get("rationale"):
                    errors.append(f"{nwhere}.nativeMapping.rationale must be a non-empty array")
                availability = mapping.get("availability")
                if not isinstance(availability, dict):
                    errors.append(f"{nwhere}.nativeMapping.availability must be an object")
                else:
                    for stack in ("swiftUI", "uiKit"):
                        stack_availability = availability.get(stack)
                        if not isinstance(stack_availability, dict):
                            errors.append(f"{nwhere}.nativeMapping.availability.{stack} must be an object")
                        elif stack_availability.get("status") not in ALLOWED_AVAILABILITY_STATUS:
                            errors.append(f"{nwhere}.nativeMapping.availability.{stack}.status has invalid value")
                control_decision = mapping.get("nativeControlDecision")
                if control_decision is not None:
                    if not isinstance(control_decision, dict):
                        errors.append(f"{nwhere}.nativeMapping.nativeControlDecision must be an object")
                    else:
                        if control_decision.get("policy") != "system-first-visual-fit-gated":
                            errors.append(f"{nwhere}.nativeMapping.nativeControlDecision.policy is invalid")
                        if control_decision.get("decision") not in ALLOWED_CONTROL_DECISIONS:
                            errors.append(f"{nwhere}.nativeMapping.nativeControlDecision.decision is invalid")
                        for key in ("systemCandidate", "requiresCustomControl", "preserveSystemSemantics"):
                            if not isinstance(control_decision.get(key), bool):
                                errors.append(f"{nwhere}.nativeMapping.nativeControlDecision.{key} must be a boolean")
                        for key in ("blockers", "fallbackChain", "evidence", "interactionActions", "interactionTriggers"):
                            if not isinstance(control_decision.get(key), list):
                                errors.append(f"{nwhere}.nativeMapping.nativeControlDecision.{key} must be an array")
            if node.get("interactionRef") is not None:
                interaction_refs.append((node_id, node.get("interactionRef")))
            interaction_refs_value = node.get("interactionRefs")
            if interaction_refs_value is not None:
                if not isinstance(interaction_refs_value, list) or not all(isinstance(item, str) for item in interaction_refs_value):
                    errors.append(f"{nwhere}.interactionRefs must be an array of strings")
                else:
                    interaction_refs.extend((node_id, item) for item in interaction_refs_value)
            if node.get("assetRef") is not None:
                asset_refs.append((node_id, node.get("assetRef")))
            if node.get("support") not in ALLOWED_SUPPORT:
                errors.append(f"{nwhere}.support has invalid value")
        root_id = screen.get("rootNodeId")
        if root_id not in local_ids:
            errors.append(f"{where}.rootNodeId does not reference a node in this screen")
        elif local_parents.get(root_id) is not None:
            errors.append(f"{where}.rootNodeId must have parentId null")
        for node_id, parent_id in local_parents.items():
            if parent_id is not None and parent_id not in local_ids:
                errors.append(f"Node {node_id} references a parent outside its screen: {parent_id}")
        for node_id in local_ids:
            seen: set[str] = set()
            current = node_id
            while current is not None and current in local_parents:
                if current in seen:
                    errors.append(f"Parent cycle detected at node {current}")
                    break
                seen.add(current)
                current = local_parents[current]

    states = data.get("states", [])
    if not isinstance(states, list):
        errors.append("states must be an array")
        states = []
    state_ids: set[str] = set()
    for index, state in enumerate(states):
        where = f"states[{index}]"
        if not isinstance(state, dict):
            errors.append(f"{where} must be an object")
            continue
        state_id = state.get("id")
        if not isinstance(state_id, str) or not state_id:
            errors.append(f"{where}.id must be a non-empty string")
        elif state_id in state_ids:
            errors.append(f"Duplicate state id: {state_id}")
        else:
            state_ids.add(state_id)
        if state.get("ownerScreenId") not in screen_ids:
            errors.append(f"{where}.ownerScreenId references missing screen")
        target_node_ids = state.get("targetNodeIds") or []
        if not isinstance(target_node_ids, list) or not all(isinstance(item, str) for item in target_node_ids):
            errors.append(f"{where}.targetNodeIds must be an array of strings")
        else:
            for node_id in target_node_ids:
                if node_id not in all_ids:
                    errors.append(f"{where}.targetNodeIds references missing node {node_id}")
        state_delta = state.get("stateDelta")
        if state_delta is not None:
            if not isinstance(state_delta, dict):
                errors.append(f"{where}.stateDelta must be an object")
            else:
                if state_delta.get("schemaVersion") != "visual-state-delta-1.0":
                    errors.append(f"{where}.stateDelta.schemaVersion is invalid")
                if state_delta.get("nativeStrategy") not in {
                    "detached-presentation", "contextual-item-actions", "conditional-subtree",
                    "conditional-removal", "subtree-replacement", "property-variant",
                    "composite-delta",
                }:
                    errors.append(f"{where}.stateDelta.nativeStrategy is invalid")
                delta_confidence = state_delta.get("confidence")
                if (
                    not isinstance(delta_confidence, (int, float))
                    or isinstance(delta_confidence, bool)
                    or not 0 <= delta_confidence <= 1
                ):
                    errors.append(f"{where}.stateDelta.confidence must be between 0 and 1")
                contextual_target = state_delta.get("contextualTargetNodeId")
                if contextual_target is not None and contextual_target not in all_ids:
                    errors.append(f"{where}.stateDelta.contextualTargetNodeId references missing node {contextual_target}")
                contextual_roots = state_delta.get("contextualActionRootNodeIds") or []
                if not isinstance(contextual_roots, list):
                    errors.append(f"{where}.stateDelta.contextualActionRootNodeIds must be an array")
                else:
                    for contextual_root in contextual_roots:
                        if contextual_root not in all_ids:
                            errors.append(
                                f"{where}.stateDelta.contextualActionRootNodeIds references missing node {contextual_root}"
                            )
                operations = state_delta.get("operations")
                if not isinstance(operations, list):
                    errors.append(f"{where}.stateDelta.operations must be an array")
                else:
                    allowed_delta_operations = {
                        "insert-subtree", "remove-subtree", "replace-subtree", "update-node",
                    }
                    for operation_index, operation in enumerate(operations):
                        operation_where = f"{where}.stateDelta.operations[{operation_index}]"
                        if not isinstance(operation, dict):
                            errors.append(f"{operation_where} must be an object")
                            continue
                        if operation.get("kind") not in allowed_delta_operations:
                            errors.append(f"{operation_where}.kind is invalid")
                        for key in ("targetNodeId", "targetParentNodeId", "generatedRootNodeId"):
                            node_id = operation.get(key)
                            if node_id is not None and node_id not in all_ids:
                                errors.append(f"{operation_where}.{key} references missing node {node_id}")
        confidence = state.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            errors.append(f"{where}.confidence must be between 0 and 1")

    interaction_ids: set[str] = set()
    prerequisite_refs: list[tuple[str, str]] = []
    for index, interaction in enumerate(data.get("interactions", [])):
        where = f"interactions[{index}]"
        if not isinstance(interaction, dict):
            errors.append(f"{where} must be an object")
            continue
        iid = interaction.get("id")
        if not isinstance(iid, str) or not iid:
            errors.append(f"{where}.id must be a non-empty string")
        elif iid in interaction_ids:
            errors.append(f"Duplicate interaction id: {iid}")
        else:
            interaction_ids.add(iid)
        source = interaction.get("sourceNodeId")
        source_scope = interaction.get("sourceScope")
        if source is None and source_scope not in {"screen", "application", "document", "window"}:
            errors.append(f"{where} needs sourceNodeId or a valid sourceScope")
        elif source is not None and source not in all_ids:
            errors.append(f"{where}.sourceNodeId references missing node {source}")
        source_nodes = interaction.get("sourceNodeIds")
        if source_nodes is not None:
            if not isinstance(source_nodes, list) or not all(isinstance(item, str) for item in source_nodes):
                errors.append(f"{where}.sourceNodeIds must be an array of strings")
            else:
                for node_id in source_nodes:
                    if node_id not in all_ids:
                        errors.append(f"{where}.sourceNodeIds references missing node {node_id}")
        confidence = interaction.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"{where}.confidence must be between 0 and 1")
        if interaction.get("action") not in ALLOWED_ACTIONS:
            errors.append(f"{where}.action has invalid value")
        if interaction.get("presentation") is not None and not isinstance(interaction.get("presentation"), dict):
            errors.append(f"{where}.presentation must be null or an object")
        if interaction.get("containment") is not None and not isinstance(interaction.get("containment"), dict):
            errors.append(f"{where}.containment must be null or an object")
        prerequisites = interaction.get("prerequisiteInteractionIds") or []
        if not isinstance(prerequisites, list) or not all(isinstance(item, str) for item in prerequisites):
            errors.append(f"{where}.prerequisiteInteractionIds must be an array of strings")
        else:
            prerequisite_refs.extend((iid, item) for item in prerequisites)
        transitions = (interaction.get("payload") or {}).get("transitions") or []
        if not isinstance(transitions, list):
            errors.append(f"{where}.payload.transitions must be an array")
        else:
            for t_index, transition in enumerate(transitions):
                twhere = f"{where}.payload.transitions[{t_index}]"
                if not isinstance(transition, dict):
                    errors.append(f"{twhere} must be an object")
                    continue
                if transition.get("action") not in ALLOWED_ACTIONS:
                    errors.append(f"{twhere}.action has invalid value")
                if transition.get("targetStateId") is not None and transition.get("targetStateId") not in state_ids:
                    owner = transition.get("targetStateOwnerScreenId")
                    if not isinstance(owner, str) or owner in screen_ids:
                        errors.append(f"{twhere}.targetStateId references missing state")
        if interaction.get("requiresResolution") is True:
            warnings.append(f"{where} still requires native ownership resolution before code generation.")

    for owner, prerequisite in prerequisite_refs:
        if prerequisite not in interaction_ids:
            errors.append(f"Interaction {owner} references missing prerequisite {prerequisite}")

    assets = data.get("assets", [])
    if not isinstance(assets, list):
        errors.append("assets must be an array")
        assets = []
    motions = data.get("motions", [])
    if not isinstance(motions, list):
        errors.append("motions must be an array")
        motions = []
    motion_ids: set[str] = set()
    for index, motion in enumerate(motions):
        where = f"motions[{index}]"
        if not isinstance(motion, dict):
            errors.append(f"{where} must be an object")
            continue
        motion_id = motion.get("id")
        if not isinstance(motion_id, str) or not motion_id:
            errors.append(f"{where}.id must be a non-empty string")
        elif motion_id in motion_ids:
            errors.append(f"Duplicate motion id: {motion_id}")
        else:
            motion_ids.add(motion_id)
        if motion.get("sourceNodeId") not in all_ids:
            errors.append(f"{where}.sourceNodeId references missing node")
        if not isinstance(motion.get("durationMs"), (int, float)) or motion.get("durationMs") < 0:
            errors.append(f"{where}.durationMs must be non-negative")
        if motion.get("support") not in {"native", "native-fallback", "unsupported"}:
            errors.append(f"{where}.support has invalid value")
    visual_states = data.get("visualStates", [])
    if not isinstance(visual_states, list) or not visual_states:
        errors.append("visualStates must be a non-empty array")
    else:
        visual_state_ids: set[str] = set()
        for index, state in enumerate(visual_states):
            where = f"visualStates[{index}]"
            if not isinstance(state, dict):
                errors.append(f"{where} must be an object")
                continue
            state_id = state.get("id")
            if not isinstance(state_id, str) or not state_id:
                errors.append(f"{where}.id must be a non-empty string")
            elif state_id in visual_state_ids:
                errors.append(f"Duplicate visual state id: {state_id}")
            else:
                visual_state_ids.add(state_id)
            sequence = state.get("interactionSequence")
            if sequence is not None:
                if not isinstance(sequence, list) or not all(isinstance(item, str) for item in sequence):
                    errors.append(f"{where}.interactionSequence must be an array of strings")
                else:
                    for interaction_id in sequence:
                        if interaction_id not in interaction_ids:
                            errors.append(f"{where}.interactionSequence references missing interaction {interaction_id}")
    asset_ids = {asset.get("id") for asset in assets if isinstance(asset, dict)}
    for node_id, asset_ref in asset_refs:
        if asset_ref not in asset_ids:
            errors.append(f"Node {node_id} references missing asset {asset_ref}")
    for node_id, interaction_ref in interaction_refs:
        if interaction_ref not in interaction_ids:
            errors.append(f"Node {node_id} references missing interaction {interaction_ref}")
    warnings_value = data.get("warnings", [])
    if not isinstance(warnings_value, list):
        errors.append("warnings must be an array")
    else:
        for index, warning in enumerate(warnings_value):
            if not isinstance(warning, dict):
                errors.append(f"warnings[{index}] must be an object")
    if not errors and not data.get("interactions"):
        warnings.append("IR contains no interactions; confirm that the source is truly static.")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ir", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.ir.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)], "warnings": []}, indent=2))
        return 2
    errors, warnings = validate(data)
    print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
