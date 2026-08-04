#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_native_architecture_plan.py"


def make_ir(screen_id: str = "home") -> dict:
    root_id = f"{screen_id}.root"
    return {
        "schemaVersion": "1.2",
        "target": {"uiStack": "uikit"},
        "screens": [{
            "id": screen_id,
            "rootNodeId": root_id,
            "navigation": {"style": "custom", "scrollEdgeAppearance": "automatic"},
            "regions": {
                "topBar": {"nodeId": f"{screen_id}.top", "kind": "custom-navigation-bar"},
                "bottomBar": {"nodeId": f"{screen_id}.bottom", "kind": "bottom-action-bar"},
            },
            "nodes": [
                {"id": root_id, "semanticType": "scroll"},
                {"id": f"{screen_id}.top", "semanticType": "navigation"},
                {"id": f"{screen_id}.bottom", "semanticType": "footer"},
                {"id": f"{screen_id}.query", "semanticType": "text-input", "textBehavior": {"role": "input"}},
            ],
        }],
        "interactions": [{"action": "push"}, {"action": "present-sheet"}],
    }


class BuildNativeArchitecturePlanTests(unittest.TestCase):
    def test_dominant_vertical_list_replaces_same_axis_scroll_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = make_ir("results")
            screen = payload["screens"][0]
            root_id = screen["rootNodeId"]
            screen["nodes"][0]["layout"] = {
                "scrollAxis": "vertical",
                "rect": {"x": 0, "y": 0, "width": 393, "height": 852},
            }
            list_id = "results.list"
            screen["nodes"].append({
                "id": list_id, "parentId": root_id, "semanticType": "list",
                "layout": {"scrollAxis": "vertical", "rect": {"x": 0, "y": 0, "width": 393, "height": 852}},
            })
            for index in range(8):
                screen["nodes"].append({
                    "id": f"{list_id}.row.{index}", "parentId": list_id,
                    "semanticType": "list-item",
                })
            ir_path = root / "ui-ir.json"
            output = root / "plan.json"
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--out", str(output), "--ui-stack", "uikit",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = json.loads(output.read_text(encoding="utf-8"))["screens"][0]["layers"]["contentContainer"]
            self.assertEqual(content["nodeId"], list_id)
            self.assertEqual(content["kind"], "table-view")
            self.assertEqual(content["scrollAxis"], "vertical")
            self.assertTrue(content["rejectsSameAxisScrollWrapper"])

    def test_nested_horizontal_collection_does_not_replace_vertical_scroll_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = make_ir("feed")
            screen = payload["screens"][0]
            root_id = screen["rootNodeId"]
            screen["nodes"][0]["layout"] = {
                "scrollAxis": "vertical",
                "rect": {"x": 0, "y": 0, "width": 393, "height": 852},
            }
            carousel_id = "feed.carousel"
            screen["nodes"].append({
                "id": carousel_id, "parentId": root_id, "semanticType": "carousel",
                "layout": {"scrollAxis": "horizontal", "rect": {"x": 16, "y": 100, "width": 361, "height": 80}},
            })
            for index in range(5):
                screen["nodes"].append({
                    "id": f"{carousel_id}.item.{index}", "parentId": carousel_id,
                    "semanticType": "container",
                })
            ir_path = root / "ui-ir.json"
            output = root / "plan.json"
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--out", str(output), "--ui-stack", "uikit",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = json.loads(output.read_text(encoding="utf-8"))["screens"][0]["layers"]["contentContainer"]
            self.assertEqual(content["nodeId"], root_id)
            self.assertEqual(content["kind"], "scroll-view")
            self.assertEqual(content["scrollAxis"], "vertical")
            carousel = next(item for item in content["nodeStrategies"] if item["nodeId"] == carousel_id)
            self.assertEqual(carousel["kind"], "collection-view")
            self.assertTrue(carousel["ownsScrollAxis"])

    def test_multiple_top_level_collections_use_compositional_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = make_ir("dashboard")
            screen = payload["screens"][0]
            root_id = screen["rootNodeId"]
            screen["nodes"][0]["semanticType"] = "container"
            screen["nodes"][0]["layout"] = {"scrollAxis": "none"}
            for section_index in range(2):
                section_id = f"dashboard.section.{section_index}"
                screen["nodes"].append({
                    "id": section_id, "parentId": root_id, "semanticType": "grid",
                    "layout": {"scrollAxis": "none"},
                })
                for item_index in range(4):
                    screen["nodes"].append({
                        "id": f"{section_id}.item.{item_index}",
                        "parentId": section_id,
                        "semanticType": "container",
                    })
            ir_path = root / "ui-ir.json"
            output = root / "plan.json"
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--out", str(output), "--ui-stack", "uikit",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = json.loads(output.read_text(encoding="utf-8"))["screens"][0]["layers"]["contentContainer"]
            self.assertEqual(content["kind"], "compositional-collection")
            root_strategy = next(item for item in content["nodeStrategies"] if item["nodeId"] == root_id)
            self.assertEqual(root_strategy["kind"], "compositional-collection")

    def test_six_layers_classify_reusable_content_and_leaf_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = make_ir()
            screen = payload["screens"][0]
            root_id = screen["rootNodeId"]
            screen["nodes"][0]["semanticType"] = "container"
            screen["nodes"][0]["layout"] = {"scrollAxis": "none"}
            list_id = "home.results"
            screen["nodes"].append({
                "id": list_id, "parentId": root_id, "semanticType": "list",
                "layout": {"scrollAxis": "vertical"},
                "style": {
                    "display": "flex", "flexDirection": "column",
                    "alignItems": "stretch", "justifyContent": "normal", "gap": "12px",
                },
                "nativeMapping": {"confidence": 0.96, "styleStrategy": "custom-native-view", "rationale": ["html-tag:ul"]},
            })
            for index in range(6):
                item_id = f"home.result.{index}"
                screen["nodes"].extend([
                    {
                        "id": item_id, "parentId": list_id, "semanticType": "list-item",
                        "layout": {"rect": {"x": 0, "y": index * 72, "width": 320, "height": 60}},
                        "style": {"width": "320px", "height": "60px", "flexGrow": "0", "flexShrink": "0"},
                        "nativeMapping": {"confidence": 0.94, "styleStrategy": "custom-native-view", "rationale": ["html-tag:li"]},
                    },
                    {
                        "id": f"{item_id}.image", "parentId": item_id, "semanticType": "image",
                        "nativeMapping": {"confidence": 0.99, "styleStrategy": "project-component", "rationale": ["html-tag:img"]},
                    },
                    {
                        "id": f"{item_id}.title", "parentId": item_id, "semanticType": "text",
                        "nativeMapping": {"confidence": 0.99, "styleStrategy": "native-default", "rationale": ["html-tag:span"]},
                    },
                ])
            ir_path = root / "ui-ir.json"
            output = root / "plan.json"
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--out", str(output), "--ui-stack", "uikit",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(plan["schemaVersion"], "native-architecture-plan-1.1")
            self.assertTrue(plan["invariants"]["sixLayerArchitectureComplete"])
            layers = plan["screens"][0]["layers"]
            self.assertEqual(
                set(layers),
                {
                    "applicationContainer", "screenContainer", "screenRegions",
                    "contentContainer", "reusableContent", "leafComponents",
                },
            )
            strategy = next(
                item for item in layers["contentContainer"]["nodeStrategies"]
                if item["nodeId"] == list_id
            )
            self.assertEqual(strategy["kind"], "table-view")
            self.assertTrue(strategy["usesReuse"])
            leaves = {item["nodeId"]: item for item in layers["leafComponents"]}
            self.assertEqual(leaves["home.result.0.image"]["uiKitType"], "UIImageView")
            self.assertEqual(leaves["home.result.0.title"]["uiKitType"], "UILabel")
            self.assertTrue(leaves["home.result.0.image"]["generateType"])
            self.assertIn("project-component", leaves["home.result.0.image"]["generationReasons"])
            self.assertFalse(leaves["home.result.0.title"]["generateType"])
            relation = next(
                item for item in layers["contentContainer"]["layoutRelations"]
                if item["containerNodeId"] == list_id
            )
            self.assertEqual(relation["axis"], "vertical")
            self.assertEqual(relation["gap"], 12)
            self.assertEqual(
                relation["orderedChildNodeIds"],
                [f"home.result.{index}" for index in range(6)],
            )
            self.assertEqual(relation["childSizing"][0]["widthPolicy"], "fixed")
            self.assertTrue(relation["childSizing"][0]["resistsHorizontalCompression"])

    def test_nested_horizontal_collection_is_a_typed_reusable_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = make_ir("feed")
            screen = payload["screens"][0]
            root_id = screen["rootNodeId"]
            carousel_id = "feed.filters"
            screen["nodes"].append({
                "id": carousel_id,
                "parentId": root_id,
                "semanticType": "carousel",
                "layout": {"scrollAxis": "horizontal"},
                "style": {"display": "flex", "flexDirection": "row-reverse", "gap": "10px"},
            })
            for index in range(4):
                screen["nodes"].append({
                    "id": f"{carousel_id}.item.{index}",
                    "parentId": carousel_id,
                    "semanticType": "button",
                    "layout": {"rect": {"x": (3 - index) * 50, "y": 0, "width": 40, "height": 32}},
                })
            ir_path = root / "ui-ir.json"
            output = root / "plan.json"
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--out", str(output), "--ui-stack", "uikit",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            layers = json.loads(output.read_text(encoding="utf-8"))["screens"][0]["layers"]
            strategy = next(
                item for item in layers["contentContainer"]["nodeStrategies"]
                if item["nodeId"] == carousel_id
            )
            section = next(
                item for item in layers["reusableContent"]["sections"]
                if item["sourceNodeId"] == carousel_id
            )
            self.assertEqual(strategy["kind"], "collection-view")
            self.assertTrue(strategy["usesReuse"])
            self.assertEqual(section["kind"], "horizontal-carousel")
            self.assertEqual(section["scrollAxis"], "horizontal")
            self.assertTrue(section["usesReuse"])
            self.assertEqual(section["itemCount"], 4)
            relation = next(
                item for item in layers["contentContainer"]["layoutRelations"]
                if item["containerNodeId"] == carousel_id
            )
            self.assertTrue(relation["reordersSourceChildren"])
            self.assertEqual(
                relation["orderedChildNodeIds"],
                [f"{carousel_id}.item.{index}" for index in reversed(range(4))],
            )
            self.assertEqual(relation["gap"], 10)

    def test_scroll_frame_never_subtracts_safe_area(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path = root / "ui-ir.json"
            behavior_path = root / "scroll.json"
            output = root / "native-architecture-plan.json"
            ir_path.write_text(json.dumps(make_ir()), encoding="utf-8")
            behavior_path.write_text(json.dumps({
                "schemaVersion": "scroll-region-behavior-1.0",
                "screenId": "home",
                "regions": [
                    {"nodeId": "home.top", "edge": "top", "behavior": "scroll-away", "confidence": 0.94, "evidence": ["moved with content"]},
                    {"nodeId": "home.bottom", "edge": "bottom", "behavior": "fixed", "confidence": 0.92, "evidence": ["stable"]},
                ],
            }), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--scroll-behavior", str(behavior_path),
                "--out", str(output), "--ui-stack", "uikit",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(output.read_text(encoding="utf-8"))
            screen = plan["screens"][0]
            self.assertEqual(screen["navigation"]["barBehavior"], "scroll-away")
            self.assertEqual(screen["bottomRegion"]["behavior"], "fixed")
            self.assertEqual(screen["safeArea"]["owner"], "system")
            self.assertFalse(screen["safeArea"]["subtractFromContainerDimensions"])
            self.assertFalse(screen["scroll"]["subtractSafeAreaFromFrame"])
            self.assertEqual(screen["scroll"]["containerWidthPolicy"], "full-parent-bounds")
            self.assertEqual(screen["scroll"]["containerHeightPolicy"], "full-parent-bounds")
            self.assertEqual(screen["scroll"]["contentInsetAdjustment"], "automatic")
            self.assertEqual(screen["controller"]["navigationContainer"], "UINavigationController")
            self.assertEqual(screen["presentations"], ["present-sheet"])
            self.assertTrue(screen["keyboard"]["present"])
            self.assertEqual(screen["keyboard"]["avoidanceOwner"], "scroll-view-controller")
            self.assertEqual(screen["keyboard"]["scrollDismissMode"], "interactive")
            self.assertFalse(screen["keyboard"]["subtractKeyboardFromContainerDimensions"])

    def test_immersive_page_owns_insets_without_dimension_subtraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = make_ir("detail")
            payload["screens"][0]["navigation"]["style"] = "immersive"
            ir_path = root / "ui-ir.json"
            output = root / "plan.json"
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--ir", str(ir_path), "--out", str(output), "--ui-stack", "swiftui",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            screen = json.loads(output.read_text(encoding="utf-8"))["screens"][0]
            self.assertEqual(screen["safeArea"]["owner"], "immersive-content")
            self.assertEqual(screen["scroll"]["contentInsetAdjustment"], "never")
            self.assertFalse(screen["scroll"]["subtractSafeAreaFromFrame"])


if __name__ == "__main__":
    unittest.main()
