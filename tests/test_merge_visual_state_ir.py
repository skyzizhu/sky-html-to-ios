#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "merge_visual_state_ir.py"
SPEC = importlib.util.spec_from_file_location("merge_visual_state_ir", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def node(
    node_id: str,
    parent_id: str | None,
    semantic: str,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str = "",
) -> dict:
    return {
        "id": node_id,
        "parentId": parent_id,
        "source": {"selector": f"#{node_id}", "tag": "div", "runtimeId": node_id},
        "semanticType": semantic,
        "layout": {
            "mode": "flow",
            "rect": {"x": x, "y": y, "width": width, "height": height},
            "sourceRectCssPx": {"x": x, "y": y, "width": width, "height": height},
            "position": "static",
            "scrollAxis": "none",
            "scrollMetrics": {
                "horizontalAllowed": False, "verticalAllowed": False,
                "overflowsHorizontally": False, "overflowsVertically": False,
                "scrollWidth": width, "scrollHeight": height,
                "clientWidth": width, "clientHeight": height,
            },
        },
        "style": {
            "display": "block", "position": "static", "backgroundColor": "transparent",
            "backgroundImage": "none", "color": "rgb(0, 0, 0)", "opacity": "1",
        },
        "content": {"text": text or None, "runs": [], "isDecorative": False},
        "state": {"initiallyVisible": True, "enabled": True},
        "nativeMapping": {
            "swiftUI": "VStack", "uiKit": "UIView", "styleStrategy": "native-default",
            "confidence": 1, "rationale": ["fixture"],
            "availability": {
                "swiftUI": {"status": "available"},
                "uiKit": {"status": "available"},
            },
        },
        "support": "native",
    }


def ir(screen_id: str, nodes: list[dict], *, state_kind: str | None = None) -> dict:
    result = {
        "schemaVersion": "1.2",
        "target": {"uiStack": "swiftui"},
        "screens": [{
            "id": screen_id,
            "rootNodeId": nodes[0]["id"],
            "nodes": nodes,
        }],
        "states": [],
        "assets": [],
    }
    if state_kind:
        result["states"] = [{
            "id": "home.state.1",
            "ownerScreenId": "home",
            "kind": state_kind,
            "targetNodeIds": [],
            "confidence": 1,
        }]
    return result


class MergeVisualStateIRTests(unittest.TestCase):
    def base_nodes(self) -> list[dict]:
        return [
            node("home.root", None, "container", 0, 0, 393, 852),
            node("home.title", "home.root", "heading", 20, 20, 100, 28, "Home"),
            node("home.row", "home.root", "list-item", 20, 80, 353, 56, "First item"),
        ]

    def test_presentation_addition_becomes_detached_state_container(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="overlay")
        variant_nodes = [
            node("variant.root", None, "container", 0, 0, 393, 852),
            node("variant.title", "variant.root", "heading", 20, 20, 100, 28, "Home"),
            node("variant.row", "variant.root", "list-item", 20, 80, 353, 56, "First item"),
            node("variant.menu", "variant.root", "menu", 180, 60, 180, 160),
            node("variant.menu.edit", "variant.menu", "button", 196, 76, 148, 44, "Edit"),
        ]
        delta = MODULE.merge_state(owner, ir("home-menu", variant_nodes), "home.state.1")
        state = owner["states"][0]
        self.assertEqual(len(state["targetNodeIds"]), 1)
        self.assertTrue(state["targetNodeIds"][0].endswith(".container"))
        self.assertEqual([item["kind"] for item in delta["operations"]].count("insert-subtree"), 1)
        self.assertEqual(delta["nativeStrategy"], "detached-presentation")
        copied_menu = next(
            item for item in owner["screens"][0]["nodes"]
            if item["id"].endswith("variant.menu")
        )
        self.assertEqual(copied_menu["parentId"], state["targetNodeIds"][0])
        self.assertFalse(copied_menu["state"]["initiallyVisible"])

    def test_local_addition_uses_generic_expansion_without_case_specific_rules(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="local-state")
        variant_nodes = [
            node("state.root", None, "container", 0, 0, 393, 852),
            node("state.title", "state.root", "heading", 20, 20, 100, 28, "Home"),
            node("state.row", "state.root", "list-item", 20, 80, 353, 56, "First item"),
            node("state.extra", "state.row", "button", 285, 80, 88, 56, "Action"),
        ]
        delta = MODULE.merge_state(owner, ir("home-alternate", variant_nodes), "home.state.1")
        state = owner["states"][0]
        self.assertEqual(state["kind"], "expansion")
        self.assertEqual(state["targetNodeIds"], ["home.row"])
        self.assertEqual(delta["operations"][0]["kind"], "insert-subtree")
        self.assertEqual(delta["nativeStrategy"], "conditional-subtree")

    def test_removal_and_property_changes_become_runnable_subtree_replacement(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="local-state")
        variant_nodes = [
            node("changed.root", None, "container", 0, 0, 393, 852),
            node("changed.title", "changed.root", "heading", 20, 20, 100, 28, "Home"),
        ]
        variant_nodes[1]["style"]["color"] = "rgb(255, 0, 0)"
        owner["screens"][0]["nodes"][2]["iosHints"] = {"state-removable": "true"}
        delta = MODULE.merge_state(owner, ir("home-changed", variant_nodes), "home.state.1")
        replacement = next(
            item for item in delta["operations"]
            if item["kind"] == "replace-subtree" and item.get("reason") == "property-or-layout-change"
        )
        self.assertEqual(replacement["targetNodeId"], "home.title")
        self.assertEqual(replacement["changes"]["style"]["color"], "rgb(255, 0, 0)")
        self.assertIn("remove-subtree", {item["kind"] for item in delta["operations"]})
        self.assertEqual(owner["states"][0]["kind"], "expansion")
        self.assertEqual(owner["states"][0]["targetNodeIds"], ["home.root"])

    def test_overlapping_removed_and_added_subtrees_become_replacement(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="local-state")
        variant_nodes = [
            node("next.root", None, "container", 0, 0, 393, 852),
            node("next.title", "next.root", "heading", 20, 20, 100, 28, "Home"),
            node("next.result", "next.root", "custom", 20, 80, 353, 56, "Replacement"),
        ]
        delta = MODULE.merge_state(owner, ir("home-replaced", variant_nodes), "home.state.1")
        replacement = next(item for item in delta["operations"] if item["kind"] == "replace-subtree")
        self.assertEqual(replacement["targetNodeId"], "home.row")
        self.assertTrue(replacement["generatedRootNodeId"].endswith("next.result"))
        self.assertEqual(delta["nativeStrategy"], "subtree-replacement")

    def test_matching_does_not_modify_input_representation(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="overlay")
        variant = ir("variant", copy.deepcopy(self.base_nodes()))
        before = copy.deepcopy(variant)
        MODULE.merge_state(owner, variant, "home.state.1")
        self.assertEqual(variant, before)

    def test_state_key_matches_logical_node_across_large_geometry_change(self) -> None:
        owner_nodes = self.base_nodes()
        owner_nodes[2]["iosHints"] = {"state-key": "content.primary"}
        variant_nodes = copy.deepcopy(owner_nodes)
        variant_nodes[0]["id"] = "variant.root"
        variant_nodes[1]["id"] = "variant.title"
        variant_nodes[1]["parentId"] = "variant.root"
        variant_nodes[2]["id"] = "variant.content"
        variant_nodes[2]["parentId"] = "variant.root"
        variant_nodes[2]["layout"]["rect"].update({"x": 240, "y": 500, "width": 120})
        matches = MODULE.match_nodes(owner_nodes, variant_nodes)
        self.assertEqual(matches["variant.content"], "home.row")

    def test_only_inserted_root_is_hidden_and_descendants_remain_renderable(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="local-state")
        variant_nodes = self.base_nodes() + [
            node("state.panel", "home.root", "container", 20, 160, 353, 120),
            node("state.panel.icon", "state.panel", "icon", 36, 176, 24, 24),
            node("state.panel.label", "state.panel", "label", 72, 176, 160, 24, "Details"),
        ]
        MODULE.merge_state(owner, ir("home-expanded", variant_nodes), "home.state.1")
        generated = {
            item["id"]: item
            for item in owner["screens"][0]["nodes"]
            if item["id"].startswith("state.home.state.1.")
        }
        self.assertFalse(generated["state.home.state.1.state.panel"]["state"]["initiallyVisible"])
        self.assertTrue(generated["state.home.state.1.state.panel.icon"]["state"]["initiallyVisible"])
        self.assertTrue(generated["state.home.state.1.state.panel.label"]["state"]["initiallyVisible"])

    def test_each_state_compares_only_against_the_base_owner_tree(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="overlay")
        owner["states"].append({
            "id": "home.state.2",
            "ownerScreenId": "home",
            "kind": "local-state",
            "targetNodeIds": [],
            "confidence": 1,
        })
        first_variant = ir("first", self.base_nodes() + [
            node("first.menu", "home.root", "menu", 180, 60, 180, 160),
        ])
        second_variant = ir("second", self.base_nodes() + [
            node("second.badge", "home.row", "label", 300, 96, 48, 24, "New"),
        ])
        MODULE.merge_state(owner, first_variant, "home.state.1")
        second_delta = MODULE.merge_state(owner, second_variant, "home.state.2")
        removed_ids = {
            item.get("targetNodeId")
            for item in second_delta["operations"]
            if item["kind"] == "remove-subtree"
        }
        self.assertFalse(any(str(item).startswith("state.home.state.1.") for item in removed_ids))

    def test_missing_interaction_source_is_not_inferred_as_state_removal(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="local-state")
        owner["interactions"] = [{
            "id": "show-state",
            "sourceNodeId": "home.title",
            "payload": {
                "transitions": [{
                    "targetStateId": "home.state.1",
                    "action": "toggle-state",
                }],
            },
        }]
        variant = ir("variant", self.base_nodes()[:2])
        delta = MODULE.merge_state(owner, variant, "home.state.1")
        removed_ids = {
            item.get("targetNodeId")
            for item in delta["operations"]
            if item["kind"] == "remove-subtree"
        }
        self.assertNotIn("home.row", removed_ids)
        self.assertIn("home.row", delta["suppressedRemovalNodeIds"])
        self.assertTrue(owner["stateDeltaReviews"][0]["requiresHumanReview"])

    def test_swipe_delta_infers_contextual_target_from_geometry(self) -> None:
        owner = ir("home", self.base_nodes(), state_kind="swipe-actions")
        owner["interactions"] = [{
            "id": "reveal",
            "sourceNodeId": "home.title",
            "trigger": "swipe",
            "payload": {
                "transitions": [{
                    "targetStateId": "home.state.1",
                    "action": "reveal-swipe-actions",
                }],
            },
        }]
        variant_nodes = self.base_nodes() + [
            node("state.delete", "home.root", "button", 285, 80, 88, 56, "Delete"),
        ]
        delta = MODULE.merge_state(owner, ir("home-actions", variant_nodes), "home.state.1")
        self.assertEqual(delta["nativeStrategy"], "contextual-item-actions")
        self.assertEqual(delta["contextualTargetNodeId"], "home.row")
        self.assertGreater(delta["contextualTargetConfidence"], 0.5)
        self.assertEqual(
            delta["contextualActionRootNodeIds"],
            ["state.home.state.1.state.delete"],
        )


if __name__ == "__main__":
    unittest.main()
