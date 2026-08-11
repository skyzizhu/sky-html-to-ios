#!/usr/bin/env python3

from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_ios_from_ir.py"
CONTROL_PLAN_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_native_control_configuration_plan.py"
PRESENTATION_PLAN_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_native_presentation_plan.py"
PAYLOAD = Path("Resources/Payload/HTMLToIOSGeneratedPayload.json")
SWIFTUI_ROOT_FILE = Path("Application/HTMLToIOSGeneratedRoot.swift")
MODELS_FILE = Path("Core/Models/HTMLToIOSGeneratedModels.swift")
RUNTIME_FILE = Path("Core/Runtime/HTMLToIOSGeneratedRuntime.swift")
NAVIGATION_FILE = Path("Core/Navigation/HTMLToIOSGeneratedNavigation.swift")
SCREEN_FACTORY_FILE = Path("Core/Navigation/HTMLToIOSGeneratedScreenFactory.swift")
ASSET_CATALOG = Path("Resources/Assets/HTMLToIOSGeneratedAssets.xcassets")


def node(node_id: str, parent_id: str | None, semantic: str, text: str = "", display: str = "flex") -> dict:
    return {
        "id": node_id,
        "parentId": parent_id,
        "source": {"selector": f"#{node_id}", "domId": node_id, "runtimeId": node_id},
        "semanticType": semantic,
        "layout": {"mode": "flex-column", "rect": {"x": 0, "y": 0, "width": 393, "height": 100}},
        "style": {
            "display": display,
            "fontSize": "16px",
            "fontWeight": "400",
            "color": "rgb(20, 20, 20)",
            "backgroundColor": "transparent",
            "padding": ["0px", "0px", "0px", "0px"],
            "cornerRadii": ["0px", "0px", "0px", "0px"],
            "gap": "8px",
            "textAlign": "start",
        },
        "content": {"text": text or None, "placeholder": None, "accessibilityLabel": None, "isDecorative": False},
    }


def ir(screen_id: str, interaction: dict | None = None, states: list[dict] | None = None) -> dict:
    root_id = f"{screen_id}.root"
    nodes = [node(root_id, None, "container")]
    if interaction and not interaction.get("automatic"):
        nodes.append(node(f"{screen_id}.button", root_id, "button", "Continue"))
        interaction["sourceNodeId"] = f"{screen_id}.button"
        interaction["sourceNodeIds"] = [f"{screen_id}.button"]
    if states:
        nodes.append(node(f"{screen_id}.sheet", root_id, "container", "Sheet", display="none"))
    return {
        "schemaVersion": "1.2",
        "target": {"uiStack": "swiftui"},
        "screens": [{"id": screen_id, "rootNodeId": root_id, "nodes": nodes}],
        "interactions": [interaction] if interaction else [],
        "states": states or [],
    }


def transition(interaction_id: str, action: str, target: str, automatic: bool = False, delay: int = 0) -> dict:
    target_is_screen = target.startswith("page")
    return {
        "id": interaction_id,
        "automatic": automatic,
        "requiresResolution": False,
        "action": action,
        "target": target,
        "payload": {
            "transitions": [{
                "action": action,
                "target": target,
                "targetScreenId": target if target_is_screen else None,
                "targetStateId": None if target_is_screen else target,
                "schedule": {"type": "delay", "ms": delay} if delay else None,
            }]
        },
    }


class GenerateIOSFromIRTests(unittest.TestCase):
    def run_generator(
        self,
        paths: list[Path],
        out_dir: Path,
        expect_success: bool = True,
        ui_stack: str = "swiftui",
        naming_plan: Path | None = None,
        architecture_plan: Path | None = None,
        control_configuration_plan: Path | None = None,
        presentation_plan: Path | None = None,
        interaction_motion_plan: Path | None = None,
        compatibility_matrix: Path | None = None,
        api_fallback_plan: Path | None = None,
        native_layout_plan: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["python3", str(SCRIPT)]
        for path in paths:
            command.extend(["--ir", str(path)])
        command.extend(["--out-dir", str(out_dir), "--ui-stack", ui_stack])
        if naming_plan:
            command.extend(["--naming-plan", str(naming_plan)])
        if architecture_plan:
            command.extend(["--architecture-plan", str(architecture_plan)])
        if control_configuration_plan:
            command.extend(["--control-configuration-plan", str(control_configuration_plan)])
        if presentation_plan:
            command.extend(["--presentation-plan", str(presentation_plan)])
        if interaction_motion_plan:
            command.extend(["--interaction-motion-plan", str(interaction_motion_plan)])
        if compatibility_matrix:
            command.extend(["--compatibility-matrix", str(compatibility_matrix)])
        if api_fallback_plan:
            command.extend(["--api-fallback-plan", str(api_fallback_plan)])
        if native_layout_plan:
            command.extend(["--native-layout-plan", str(native_layout_plan)])
        if out_dir.parts[-2:] != ("Generated", "HTMLToIOS"):
            command.append("--allow-nonstandard-output")
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if expect_success and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def test_css_pill_radius_is_reduced_to_the_native_box(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            pill = node("home.pill", "home.root", "text", "Badge")
            pill["layout"]["rect"] = {"x": 0, "y": 0, "width": 120, "height": 24}
            pill["style"]["cornerRadii"] = ["999px"] * 4
            pill["style"]["backgroundImage"] = (
                "linear-gradient(90deg, rgb(120, 100, 255), rgb(60, 210, 255))"
            )
            payload["screens"][0]["nodes"].append(pill)
            source, output = root / "ui-ir.json", root / "generated"
            source.write_text(json.dumps(payload), encoding="utf-8")
            self.run_generator([source], output, ui_stack="uikit")
            generated = json.loads((output / PAYLOAD).read_text(encoding="utf-8"))
            generated_pill = generated["screens"][0]["root"]["children"][0]
            self.assertEqual(generated_pill["style"]["cornerRadius"], 12)
            self.assertEqual(generated_pill["style"]["cornerRadii"], [12, 12, 12, 12])
            runtime = (output / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("backgroundHostedTextViewIfNeeded", runtime)
            self.assertIn("A CSS", runtime)
            self.assertIn("label.attributedText = text", runtime)
            self.assertIn("restoreOwnedRichTextIfNeeded", runtime)
            self.assertNotIn("view.subviews.forEach { applyControlForeground", runtime)

    def test_native_layout_point_gaps_are_not_scaled_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            payload["target"]["scale"] = 2
            root_node = payload["screens"][0]["nodes"][0]
            first = node("home.first", root_node["id"], "text", "First")
            second = node("home.second", root_node["id"], "text", "Second")
            payload["screens"][0]["nodes"].extend([first, second])
            source = root / "home.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            plan = root / "native-layout-plan.json"
            plan.write_text(json.dumps({
                "schemaVersion": "native-layout-plan-1.1",
                "screens": [{
                    "screenId": "home",
                    "rootNodeId": root_node["id"],
                    "contentContainer": {"kind": "static"},
                    "containers": [{
                        "containerNodeId": root_node["id"],
                        "layoutAlgorithm": "stack",
                        "axis": "vertical",
                        "orderedChildNodeIds": [first["id"], second["id"]],
                        "paintOrderNodeIds": [first["id"], second["id"]],
                        "gapPt": 10,
                        "rowGapPt": 10,
                        "columnGapPt": 10,
                        "alignment": "normal",
                        "distribution": "normal",
                        "wraps": False,
                        "reverse": False,
                        "childSizing": [
                            {"nodeId": first["id"], "gapBeforePt": None},
                            {"nodeId": second["id"], "gapBeforePt": 12, "flexibleGapBefore": False},
                        ],
                    }],
                    "nodes": [],
                    "collectionLayouts": [],
                    "compoundControls": [],
                    "stateLayouts": [],
                }],
            }), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([source], out_dir, native_layout_plan=plan)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_root = generated["screens"][0]["root"]
            self.assertIsNone(generated_root["style"]["fixedHeight"])
            self.assertEqual(generated_root["style"]["spacing"], 10)
            self.assertEqual(generated_root["style"]["rowSpacing"], 10)
            self.assertEqual(generated_root["style"]["columnSpacing"], 10)
            self.assertEqual(generated_root["contentItems"][1]["gapBefore"], 12)

    def test_grid_auto_placement_preserves_source_order_when_items_have_different_y(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("grid")
            root_node = payload["screens"][0]["nodes"][0]
            root_node["style"]["display"] = "grid"
            root_node["layout"]["mode"] = "grid"
            title = node("grid.title", root_node["id"], "text", "Title")
            icon = node("grid.icon", root_node["id"], "button", "Icon")
            title["layout"]["rect"] = {"x": 0, "y": 8, "width": 320, "height": 24}
            icon["layout"]["rect"] = {"x": 333, "y": 0, "width": 40, "height": 40}
            for child in (title, icon):
                child["layout"]["sourceRectCssPx"] = dict(child["layout"]["rect"])
            root_node["content"]["runs"] = [
                {"kind": "node", "nodeId": title["id"], "domIndex": 0, "rect": title["layout"]["rect"]},
                {"kind": "node", "nodeId": icon["id"], "domIndex": 1, "rect": icon["layout"]["rect"]},
            ]
            payload["screens"][0]["nodes"].extend([title, icon])
            source = root / "grid.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([source], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            self.assertEqual(
                [item.get("childID") for item in generated["screens"][0]["root"]["contentItems"]],
                [title["id"], icon["id"]],
            )

    def test_interaction_motion_plan_is_lowered_into_generated_action_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home", transition("open-details", "push", "page-details"))
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            plan = root / "interaction-motion.json"
            plan.write_text(json.dumps({
                "schemaVersion": "native-interaction-motion-plan-1.0",
                "screens": [{
                    "screenId": "home",
                    "actions": [{
                        "id": "open-details", "sourceInteractionId": "open-details",
                        "sourceNodeId": "home.button", "owner": "navigation-stack",
                        "ownerId": "main-navigation", "executor": "native-navigation",
                    }],
                    "motions": [],
                }],
            }), encoding="utf-8")
            out = root / "generated"
            self.run_generator([path], out, interaction_motion_plan=plan)
            generated = json.loads((out / PAYLOAD).read_text(encoding="utf-8"))
            action = generated["screens"][0]["root"]["children"][0]["action"]
            self.assertEqual(action["nativeOwner"], "navigation-stack")
            self.assertEqual(action["nativeOwnerID"], "main-navigation")
            self.assertEqual(action["nativeExecutor"], "native-navigation")

    def test_contextual_state_delta_generates_native_swipe_actions_for_both_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path = root / "home.json"
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            row = node("home.row", root_node["id"], "list-item", "Document")
            action_container = node("state.home.actions.container", root_node["id"], "group")
            action_wrapper = node(
                "state.home.actions.wrapper", action_container["id"], "group"
            )
            action_node = node(
                "state.home.actions.delete", action_wrapper["id"], "button", "Delete"
            )
            action_container["state"] = {"initiallyVisible": False}
            action_wrapper["state"] = {"initiallyVisible": False}
            action_node["state"] = {"initiallyVisible": False}
            for action_part in (action_container, action_wrapper, action_node):
                action_part["iosHints"] = {"state-owner": "home.actions"}
            action_node["style"]["backgroundColor"] = "rgb(255, 59, 48)"
            payload["screens"][0]["nodes"].extend(
                [row, action_container, action_wrapper, action_node]
            )
            interaction = transition("reveal-actions", "reveal-swipe-actions", "home.actions")
            interaction["trigger"] = "swipe"
            interaction["sourceNodeId"] = "home.row"
            interaction["sourceNodeIds"] = ["home.row"]
            payload["interactions"] = [interaction]
            payload["states"] = [{
                "id": "home.actions",
                "ownerScreenId": "home",
                "kind": "expansion",
                "targetNodeIds": [root_node["id"]],
                "confidence": 1,
                "stateDelta": {
                    "schemaVersion": "visual-state-delta-1.0",
                    "nativeStrategy": "contextual-item-actions",
                    "confidence": 1,
                    "operations": [{
                        "kind": "insert-subtree",
                        "generatedRootNodeId": action_container["id"],
                        "targetParentNodeId": root_node["id"],
                    }],
                    "contextualTargetNodeId": "home.row",
                    "contextualTargetConfidence": 1,
                    "contextualActionRootNodeIds": [action_container["id"]],
                    "suppressedRemovalNodeIds": [],
                    "triggers": ["swipe"],
                },
            }]
            ir_path.write_text(json.dumps(payload), encoding="utf-8")

            for ui_stack in ("swiftui", "uikit"):
                out_dir = root / ui_stack / "Generated" / "HTMLToIOS"
                self.run_generator([ir_path], out_dir, ui_stack=ui_stack)
                generated_payload = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
                generated_row = generated_payload["screens"][0]["root"]["children"][0]
                self.assertEqual(generated_row["id"], "home.row")
                self.assertEqual(generated_row["contextualActions"][0]["title"], "Delete")
                runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
                if ui_stack == "swiftui":
                    self.assertIn("HTMLToIOSContextualActionsModifier", runtime)
                    self.assertIn(".swipeActions(", runtime)
                else:
                    self.assertIn("HTMLToIOSClosureSwipeGestureRecognizer", runtime)
                    self.assertIn("installContextualActions", runtime)

    def test_six_layer_architecture_drives_native_container_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path = root / "home.json"
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            list_node = node("home.list", root_node["id"], "list")
            payload["screens"][0]["nodes"].append(list_node)
            for index in range(6):
                payload["screens"][0]["nodes"].append(
                    node(f"home.row.{index}", list_node["id"], "list-item", f"Row {index}")
                )
            payload["screens"][0]["nodes"].append(
                node("home.primary-action", root_node["id"], "button", "Continue")
            )
            ir_path.write_text(json.dumps(payload), encoding="utf-8")
            architecture = {
                "schemaVersion": "native-architecture-plan-1.1",
                "invariants": {
                    "safeAreaNeverSubtractedFromWidthOrHeight": True,
                    "sixLayerArchitectureComplete": True,
                },
                "screens": [{
                    "screenId": "home",
                    "safeArea": {"owner": "system", "subtractFromContainerDimensions": False},
                    "scroll": {"contentInsetAdjustment": "automatic", "subtractSafeAreaFromFrame": False},
                    "layers": {
                        "applicationContainer": {"kind": "navigation"},
                        "screenContainer": {"kind": "screen"},
                        "screenRegions": {},
                        "contentContainer": {
                            "nodeId": "home.list", "kind": "table-view", "scrollAxis": "vertical",
                            "usesCellReuse": True,
                            "nodeStrategies": [{"nodeId": "home.list", "kind": "table-view"}],
                            "layoutRelations": [{
                                "containerNodeId": "home.list",
                                "axis": "vertical",
                                "sourceChildNodeIds": [f"home.row.{index}" for index in range(6)],
                                "orderedChildNodeIds": [f"home.row.{index}" for index in range(6)],
                                "reordersSourceChildren": False,
                                "alignment": "stretch",
                                "distribution": "normal",
                                "wraps": False,
                                "gap": 8,
                                "childSizing": [{
                                    "nodeId": f"home.row.{index}",
                                    "widthPolicy": "flexible",
                                    "heightPolicy": "intrinsic",
                                    "measuredWidth": 393,
                                    "measuredHeight": 100,
                                    "aspectRatio": 3.93,
                                    "flexGrow": 1,
                                    "flexShrink": 1,
                                    "resistsHorizontalCompression": False,
                                } for index in range(6)],
                            }, {
                                "containerNodeId": root_node["id"],
                                "axis": "vertical",
                                "sourceChildNodeIds": ["home.list", "home.primary-action"],
                                "orderedChildNodeIds": ["home.list", "home.primary-action"],
                                "reordersSourceChildren": False,
                                "alignment": "stretch",
                                "distribution": "normal",
                                "wraps": False,
                                "gap": 12,
                                "childSizing": [{
                                    "nodeId": "home.primary-action",
                                    "widthPolicy": "fixed",
                                    "heightPolicy": "fixed",
                                    "measuredWidth": 180,
                                    "measuredHeight": 44,
                                    "aspectRatio": 4.0909,
                                    "flexGrow": 0,
                                    "flexShrink": 0,
                                    "resistsHorizontalCompression": True,
                                }],
                            }],
                        },
                        "reusableContent": {
                            "sections": [{
                                "id": "home.section.0",
                                "sourceNodeId": "home.list",
                                "kind": "list",
                                "scrollAxis": "vertical",
                                "itemNodeIds": [f"home.row.{index}" for index in range(6)],
                                "itemCount": 6,
                                "itemTemplateNodeId": "home.row.0",
                                "usesReuse": True,
                                "headerNodeId": None,
                                "footerNodeId": None,
                            }],
                            "usesReuse": True,
                        },
                        "leafComponents": [{
                            "nodeId": "home.primary-action",
                            "semanticType": "button",
                            "category": "control",
                            "swiftUIType": "Button",
                            "uiKitType": "UIButton",
                            "styleStrategy": "native-default",
                            "nativeControlDecision": {
                                "policy": "system-first-visual-fit-gated",
                                "decision": "system-control",
                                "systemCandidate": True,
                                "requiresCustomControl": False,
                            },
                            "systemControlPreferred": True,
                            "requiresCustomControl": False,
                            "interactive": True,
                            "accessibilityIdentifier": "home.primary-action",
                            "confidence": 0.98,
                            "reasons": ["html-tag:button"],
                            "generateType": True,
                            "generationReasons": ["stable-interactive-control"],
                        }],
                    },
                }],
            }
            architecture_path = root / "architecture.json"
            architecture_path.write_text(json.dumps(architecture), encoding="utf-8")
            out = root / "Generated" / "HTMLToIOS"
            self.run_generator(
                [ir_path], out, ui_stack="uikit", architecture_plan=architecture_path,
            )
            generated = json.loads((out / PAYLOAD).read_text(encoding="utf-8"))["screens"][0]
            generated_list = generated["root"]["children"][0]
            self.assertEqual(generated["contentContainer"]["kind"], "table-view")
            self.assertEqual(generated_list["nativeContainerKind"], "table-view")
            runtime = (out / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("HTMLToIOSGeneratedTableView", runtime)
            self.assertIn("HTMLToIOSGeneratedCompositionalCollectionView", runtime)
            self.assertIn("UICollectionViewCompositionalLayout", runtime)
            self.assertIn('spec.nativeContainerKind == "table-view"', runtime)
            self.assertIn("screen.contentContainer.nodeId == screen.root.id", runtime)
            self.assertIn("let hasNestedScrollOwner = screen.contentContainer.kind == \"scroll-view\"", runtime)
            self.assertIn("hasNestedScrollOwner", runtime)
            self.assertIn("view.setContentHuggingPriority(.defaultLow, for: axis)", runtime)
            self.assertTrue((out / "Home/Models/HTMLToIOSHomeUIContract.swift").is_file())
            layout_contract = out / "Home/Models/HTMLToIOSHomeLayoutContract.swift"
            self.assertTrue(layout_contract.is_file())
            self.assertIn("orderedChildNodeIDs", layout_contract.read_text(encoding="utf-8"))
            self.assertTrue((out / "Home/Sections/HTMLToIOSHomeSection1View.swift").is_file())
            self.assertTrue((out / "Home/Cells/HTMLToIOSHomeSection1TableViewCell.swift").is_file())
            self.assertTrue((out / "Home/Views/HTMLToIOSHomeLeafPrimaryActionView.swift").is_file())
            controller = (out / "Home/Controllers/HTMLToIOSHomeViewController.swift").read_text(encoding="utf-8")
            self.assertIn("configureTypedComponents", controller)
            self.assertIn("registerTableCell", controller)
            self.assertIn("HTMLToIOSHomeLeafPrimaryActionView", controller)
            uikit_leaf = (out / "Home/Views/HTMLToIOSHomeLeafPrimaryActionView.swift").read_text(encoding="utf-8")
            self.assertIn("final class HTMLToIOSHomeLeafPrimaryActionView: UIView", uikit_leaf)
            self.assertIn("let content = renderer.makeView", uikit_leaf)
            self.assertIn("widthAnchor.constraint(equalToConstant: 180.0)", uikit_leaf)
            self.assertIn("heightAnchor.constraint(equalToConstant: 44.0)", uikit_leaf)
            self.assertIn("setContentCompressionResistancePriority(.required", uikit_leaf)

            swiftui_out = root / "SwiftUI" / "Generated" / "HTMLToIOS"
            self.run_generator(
                [ir_path], swiftui_out, ui_stack="swiftui", architecture_plan=architecture_path,
            )
            self.assertTrue((swiftui_out / "Home/Sections/HTMLToIOSHomeSection1View.swift").is_file())
            item_path = swiftui_out / "Home/Cells/HTMLToIOSHomeSection1ItemView.swift"
            self.assertTrue(item_path.is_file())
            self.assertTrue((swiftui_out / "Home/Views/HTMLToIOSHomeLeafPrimaryActionView.swift").is_file())
            swiftui_leaf = (swiftui_out / "Home/Views/HTMLToIOSHomeLeafPrimaryActionView.swift").read_text(encoding="utf-8")
            self.assertIn("width: CGFloat(180.0)", swiftui_leaf)
            self.assertIn("height: CGFloat(44.0)", swiftui_leaf)
            self.assertIn(".fixedSize(horizontal: false", swiftui_leaf)
            self.assertIn(".layoutPriority(2)", swiftui_leaf)
            item_view = item_path.read_text(encoding="utf-8")
            self.assertIn("let registry: HTMLToIOSTypedViewRegistry", item_view)
            self.assertIn("bypassTypedNodeID: spec.id", item_view)
            content = (swiftui_out / "Home/Views/HTMLToIOSHomeContentView.swift").read_text(encoding="utf-8")
            self.assertIn("HTMLToIOSTypedViewRegistry", content)
            self.assertIn("HTMLToIOSHomeSection1ItemView", content)
            self.assertIn("HTMLToIOSHomeLeafPrimaryActionView", content)
            if shutil.which("xcrun"):
                sdk = subprocess.run(
                    ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"],
                    text=True,
                    capture_output=True,
                )
                if sdk.returncode == 0:
                    for ui_stack, generated_dir in (("uikit", out), ("swiftui", swiftui_out)):
                        sources = sorted(str(path) for path in generated_dir.rglob("*.swift"))
                        completed = subprocess.run(
                            [
                                "xcrun", "--sdk", "iphonesimulator", "swiftc",
                                "-target", "arm64-apple-ios16.0-simulator", "-typecheck", *sources,
                            ],
                            text=True,
                            capture_output=True,
                        )
                        self.assertEqual(
                            completed.returncode,
                            0,
                            f"{ui_stack} typed architecture:\n{completed.stdout}{completed.stderr}",
                        )

    def test_generated_swiftui_and_uikit_sources_typecheck_when_xcode_is_available(self) -> None:
        if shutil.which("xcrun") is None:
            self.skipTest("xcrun is unavailable")
        sdk = subprocess.run(
            ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"],
            text=True,
            capture_output=True,
        )
        if sdk.returncode != 0:
            self.skipTest("iPhone Simulator SDK is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "home.json"
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            field = node("home.query", root_node["id"], "text-input")
            field["textBehavior"] = {
                "role": "input", "nativeControl": "text-field", "editable": True,
                "readOnly": False, "selectable": True, "multiline": False,
                "scrollable": False, "secure": False, "maxLength": 40,
                "autofocus": True, "returnKey": "search",
                "autocapitalization": "none", "autocorrection": False,
            }
            notes = node("home.notes", root_node["id"], "text-area")
            notes["textBehavior"] = {
                "role": "input", "nativeControl": "text-view", "editable": True,
                "readOnly": False, "selectable": True, "multiline": True,
                "scrollable": True, "secure": False, "maxLength": 200,
            }
            payload["screens"][0]["nodes"].extend([field, notes])
            path.write_text(json.dumps(payload), encoding="utf-8")
            for ui_stack in ("swiftui", "uikit"):
                out_dir = root / ui_stack / "Generated" / "HTMLToIOS"
                self.run_generator([path], out_dir, ui_stack=ui_stack)
                sources = sorted(str(item) for item in out_dir.rglob("*.swift"))
                completed = subprocess.run(
                    [
                        "xcrun", "--sdk", "iphonesimulator", "swiftc",
                        "-target", "arm64-apple-ios16.0-simulator", "-typecheck", *sources,
                    ],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, f"{ui_stack}:\n{completed.stdout}{completed.stderr}")

    def test_pure_text_baseline_calibration_does_not_hard_code_range_height(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("baseline")
            root_node = payload["screens"][0]["nodes"][0]
            title = node("baseline.title", root_node["id"], "heading", "Measured title")
            title["content"].update({
                "lines": 1,
                "firstBaselineY": 24,
                "lastBaselineY": 24,
                "lineRects": [{"x": 0, "y": 0, "width": 112, "height": 31}],
                "fontResolution": {
                    "status": "system-local",
                    "resolvedFamily": "Helvetica",
                    "failedFamilies": [],
                },
            })
            payload["screens"][0]["nodes"].append(title)
            path = root / "baseline.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir)
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            swiftui_payload = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            swiftui_title = swiftui_payload["screens"][0]["root"]["children"][0]
            self.assertEqual(swiftui_title["style"]["firstBaselineOffset"], 24)
            self.assertIsNone(swiftui_title["style"]["fixedHeight"])
            self.assertIn("let nativeFirstBaseline = nativeFont.ascender", swiftui_runtime)
            self.assertIn(".offset(y: baselineAdjustment)", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("hasReliableFontMetrics(spec)", uikit_runtime)
            self.assertIn("baselineOffset += min(max(rawAdjustment", uikit_runtime)
            self.assertIn("owner.layoutMarginsGuide.widthAnchor", uikit_runtime)
            self.assertIn("owner.layoutMarginsGuide.heightAnchor", uikit_runtime)
            self.assertIn("Establish the parent's CSS content box before child percentage", uikit_runtime)
            self.assertIn("makeStack(spec, appliesPadding: false)", uikit_runtime)
            self.assertIn('gradient.name == "html-to-ios-gradient"', uikit_runtime)
            self.assertIn('gradientMask.name = "html-to-ios-gradient-corner-mask"', uikit_runtime)
            self.assertIn('layer.name == "html-to-ios-gradient" || layer.name == "html-to-ios-control-state-gradient"', uikit_runtime)

    def test_common_html_controls_emit_native_control_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("controls")
            root_node = payload["screens"][0]["nodes"][0]

            slider = node("controls.slider", root_node["id"], "slider")
            slider["state"] = {"min": "1", "max": "9", "step": "2", "value": "5", "inputType": "range"}
            stepper = node("controls.stepper", root_node["id"], "stepper", "Count")
            stepper["state"] = {"min": "0", "max": "10", "step": "1", "value": "3", "inputType": "number"}
            segmented = node("controls.segmented", root_node["id"], "segmented-control", "Mode")
            select = node("controls.select", root_node["id"], "select", "Choice")
            date_input = node("controls.date", root_node["id"], "date-input")
            date_input["state"] = {"value": "2026-07-26T09:30", "inputType": "datetime-local"}
            color_input = node("controls.color", root_node["id"], "color-picker")
            color_input["state"] = {"value": "#12A36D", "inputType": "color"}
            progress = node("controls.progress", root_node["id"], "progress")
            progress["state"] = {"min": "0", "max": "8", "value": "6"}
            checkbox = node("controls.checkbox", root_node["id"], "checkbox", "Remember")
            radio = node("controls.radio", root_node["id"], "radio", "Primary")
            file_input = node("controls.file", root_node["id"], "file-input", "Choose file")
            switch = node("controls.switch", root_node["id"], "switch", "Enabled")
            switch["state"] = {"checked": True}
            switch_thumb = node("controls.switch-thumb", switch["id"], "decoration")
            switch_thumb["style"]["backgroundColor"] = "rgb(255, 255, 255)"
            search_input = node("controls.search-input", root_node["id"], "search-input")
            search_bar = node("controls.search-bar", root_node["id"], "search-bar")
            wheel_picker = node("controls.wheel", root_node["id"], "wheel-picker")
            activity = node("controls.activity", root_node["id"], "activity-indicator")
            page_control = node("controls.pages", root_node["id"], "page-control")
            page_control["state"] = {"pageCount": "4", "currentPage": "1"}
            paste_control = node("controls.paste", root_node["id"], "paste-control", "Paste")
            refresh_control = node("controls.refresh", root_node["id"], "refresh-control")
            calendar = node("controls.calendar", root_node["id"], "calendar-view")
            calendar["state"] = {"calendarSelection": "multi-date"}

            option_nodes = []
            for parent, prefix in ((segmented, "segment"), (select, "choice"), (wheel_picker, "wheel")):
                for index, title in enumerate(("First", "Second")):
                    option = node(
                        f"controls.{prefix}-{index}",
                        parent["id"],
                        "option",
                        title,
                    )
                    if parent is segmented:
                        option["state"] = {}
                        if index == 1:
                            option["source"]["selector"] += ".selected"
                    else:
                        option["state"] = {"selected": index == 1}
                    option_nodes.append(option)
            stepper_options = []
            for index, title in enumerate(("-", "3", "+")):
                option = node(f"controls.stepper-{index}", stepper["id"], "button" if index != 1 else "text", title)
                stepper_options.append(option)
            page_options = []
            for index in range(4):
                option = node(f"controls.page-{index}", page_control["id"], "container")
                if index == 1:
                    option["source"]["selector"] += ".active"
                option["style"]["backgroundColor"] = "rgb(0, 122, 255)" if index == 1 else "rgb(200, 204, 212)"
                page_options.append(option)

            payload["screens"][0]["nodes"].extend([
                slider,
                stepper,
                segmented,
                select,
                date_input,
                color_input,
                progress,
                checkbox,
                radio,
                file_input,
                switch,
                search_input,
                search_bar,
                wheel_picker,
                activity,
                page_control,
                paste_control,
                refresh_control,
                calendar,
                switch_thumb,
                *option_nodes,
                *stepper_options,
                *page_options,
            ])
            path = root / "controls.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            control_plan = root / "native-control-configuration-plan.json"
            result = subprocess.run([
                "python3", str(CONTROL_PLAN_SCRIPT), "--ir", str(path), "--out", str(control_plan),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir, control_configuration_plan=control_plan)
            generated = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_nodes = {
                item["id"]: item
                for item in generated["screens"][0]["root"]["children"]
            }
            self.assertEqual(generated_nodes[slider["id"]]["controlConfig"]["minimum"], 1)
            self.assertEqual(generated_nodes[slider["id"]]["controlConfig"]["maximum"], 9)
            self.assertEqual(
                [item["title"] for item in generated_nodes[segmented["id"]]["controlConfig"]["options"]],
                ["First", "Second"],
            )
            self.assertEqual(
                [item["selected"] for item in generated_nodes[segmented["id"]]["controlConfig"]["options"]],
                [False, True],
            )
            self.assertEqual(generated_nodes[page_control["id"]]["controlConfig"]["pageCount"], 4)
            self.assertEqual(generated_nodes[page_control["id"]]["controlConfig"]["currentPage"], 1)
            self.assertEqual(generated_nodes[page_control["id"]]["controlConfig"]["fillTint"], "rgb(0, 122, 255)")
            self.assertEqual(generated_nodes[page_control["id"]]["controlConfig"]["trackTint"], "rgb(200, 204, 212)")
            self.assertTrue(generated_nodes[switch["id"]]["isInitiallySelected"])
            self.assertEqual(generated_nodes[switch["id"]]["controlConfig"]["thumbTint"], "rgb(255, 255, 255)")
            self.assertEqual(generated_nodes[calendar["id"]]["controlConfig"]["calendarSelection"], "multi-date")
            self.assertEqual(generated_nodes[slider["id"]]["controlConfig"]["contentInsets"], [0, 0, 0, 0])
            self.assertEqual(generated_nodes[slider["id"]]["controlConfig"]["itemSpacing"], 8)
            self.assertIn("normal", generated_nodes[slider["id"]]["controlConfig"]["stateAppearances"])
            for control in (
                switch, search_input, search_bar, wheel_picker, activity,
                page_control, paste_control, refresh_control, calendar,
            ):
                self.assertEqual(generated_nodes[control["id"]]["semantic"], control["semanticType"])
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            for expected in (
                "Slider(",
                "Stepper(",
                ".labelsHidden()",
                ".pickerStyle(.segmented)",
                ".pickerStyle(.menu)",
                "DatePicker(",
                "ColorPicker(",
                "ProgressView(",
                "HTMLToIOSCheckboxToggleStyle",
                "HTMLToIOSRadioToggleStyle",
                "Toggle(",
                "HTMLToIOSSearchBarRepresentable(",
                ".pickerStyle(.wheel)",
                "HTMLToIOSPageControlRepresentable(",
                "PasteButton(payloadType: String.self)",
                "HTMLToIOSCalendarRepresentable(",
                ".refreshable",
                "HTMLToIOSOptionalTintModifier",
                "HTMLToIOSNativeIntrinsicSizeModifier",
                "nativeControlAppearance",
            ):
                self.assertIn(expected, swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit", control_configuration_plan=control_plan)
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            for expected in (
                "HTMLToIOSMeasuredSlider()",
                "override func thumbRect(forBounds bounds:",
                "UIStepper()",
                "valueLabel?.text",
                "UISegmentedControl(items:",
                "UIDatePicker()",
                "UIColorWell()",
                "UIProgressView(progressViewStyle:",
                "UIMenu(children:",
                'spec.semantic == "radio" ? "circle" : "square"',
                "UISwitch()",
                "UISearchTextField()",
                "UISearchBar(frame: .zero)",
                "HTMLToIOSGeneratedPickerView()",
                "UIActivityIndicatorView(style:",
                "UIPageControl()",
                "UIPasteControl(configuration:",
                "UIRefreshControl()",
                "UICalendarView()",
                "slider.minimumTrackTintColor",
                "slider.setThumbImage(image, for: .normal)",
                "segmented.selectedSegmentTintColor",
                "pageControl.currentPageIndicatorTintColor",
                "applyNativeControlStateAppearance",
                "nativeControlStateName",
                "setContentCompressionResistancePriority(.required",
                'contract.widthKind == "fixed"',
                'contract.heightKind == "fixed"',
            ):
                self.assertIn(expected, uikit_runtime)

    def test_multi_page_payload_and_modified_file_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page1 = ir("page1", transition("tap-next", "push", "page2"))
            page2 = ir("page2", transition("auto-finish", "push", "page3", automatic=True, delay=550))
            page3 = ir("page3", transition("go-home", "pop-to-root", "page1"))
            paths = []
            for index, payload in enumerate((page1, page2, page3), start=1):
                path = root / f"page{index}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths.append(path)

            out_dir = root / "Generated" / "HTMLToIOS"
            self.run_generator(paths, out_dir)
            generated_payload = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            self.assertEqual([screen["id"] for screen in generated_payload["screens"]], ["page1", "page2", "page3"])
            self.assertEqual(generated_payload["screens"][1]["automaticActions"][0]["delayMilliseconds"], 550)

            runtime_text = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("Color(htmlToIOS: screen.root.style.background)", runtime_text)
            self.assertIn("screen.root.primaryScrollContent ?? screen.root", runtime_text)
            self.assertIn(".accessibilityIdentifier(screen.root.id)", runtime_text)
            self.assertNotIn(".padding(.bottom, screen.bottomBar?.style.preferredHeight ?? 0)", runtime_text)
            self.assertIn(".safeAreaInset(edge: .bottom, spacing: 0)", runtime_text)
            self.assertIn("screen.safeArea.owner == \"system\"", runtime_text)
            self.assertIn("private struct HTMLToIOSRichTextView: View", runtime_text)
            self.assertIn("Text(attributedText)", runtime_text)
            self.assertIn("private struct HTMLToIOSFrameModifier: ViewModifier", runtime_text)
            self.assertIn("let preferredWidth = constrainsPreferredWidth ? CGFloat(style.preferredWidth ?? 0) : 0", runtime_text)
            self.assertIn("constrainsPreferredWidth: isMeasuredText || spec.children.isEmpty || isNativeControl", runtime_text)
            self.assertIn("enforcesPreferredWidth: isNativeControl", runtime_text)
            self.assertIn("(enforcesPreferredWidth || style.resistsCompression == true)", runtime_text)
            self.assertIn(
                "(style.flexGrow ?? 0) > 0 || (style.widthFraction ?? 0) > 0.72",
                runtime_text,
            )
            self.assertNotIn(".frame(minWidth: minWidth, idealWidth: idealWidth)\n            .frame(maxWidth:", runtime_text)
            self.assertIn("hiddenNodeIDs = nextHiddenNodeIDs", runtime_text)
            self.assertNotIn("WKWebView", runtime_text)
            self.assertIn("HTMLToIOSSearchBarRepresentable: UIViewRepresentable", runtime_text)
            self.assertIn("HTMLToIOSPageControlRepresentable: UIViewRepresentable", runtime_text)
            self.assertIn("HTMLToIOSCalendarRepresentable: UIViewRepresentable", runtime_text)
            self.assertNotIn("sizeThatFits(_ proposal:", runtime_text)
            self.assertIn("vertical: (style.expectedTextLines ?? 1) > 1", runtime_text)
            self.assertIn("HTMLToIOSLaunchConfiguration.geometryCaptureEnabled", runtime_text)
            self.assertIn("accessibilityElement(children: .contain)", runtime_text)
            models_text = (out_dir / MODELS_FILE).read_text(encoding="utf-8")
            navigation_text = (out_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("-HTMLToIOSInitialRoute", models_text)
            self.assertIn("HTMLToIOSLaunchConfiguration.initialRoute", navigation_text)

            runtime = out_dir / RUNTIME_FILE
            runtime.write_text(runtime.read_text(encoding="utf-8") + "\n// User edit\n", encoding="utf-8")
            for _ in range(2):
                result = self.run_generator(paths, out_dir)
                report = json.loads(result.stdout)
                self.assertEqual(report["fileStatuses"][str(RUNTIME_FILE)], "preserved-user-modified")
                self.assertIn("// User edit", runtime.read_text(encoding="utf-8"))
            self.assertTrue((out_dir.with_name("HTMLToIOS.conflicts") / f"{RUNTIME_FILE}.generated").exists())

    def test_dynamic_repeated_content_variants_preserve_native_item_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("dynamic")
            root_node = payload["screens"][0]["nodes"][0]
            tab = node("dynamic.tab", root_node["id"], "button", "Category")
            panel = node("dynamic.panel", root_node["id"], "container")
            panel["layout"]["rect"] = {"x": 20, "y": 300, "width": 353, "height": 180}
            panel["style"]["height"] = "180px"
            grid = node("dynamic.grid", panel["id"], "container")
            grid["layout"]["rect"] = {"x": 32, "y": 350, "width": 329, "height": 90}
            grid["style"]["height"] = "90px"
            grid["layout"]["mode"] = "grid"
            grid["style"]["gridTemplateColumns"] = "repeat(3, 1fr)"
            template = node("dynamic.item", grid["id"], "text", "A")
            payload["screens"][0]["nodes"].extend([tab, panel, grid, template])
            payload["states"] = [
                {"id": "category-selection", "kind": "selection", "targetNodeIds": [tab["id"]]},
                {"id": "emoji-popover", "kind": "popover-overlay", "targetNodeIds": [panel["id"]]},
            ]
            payload["interactions"] = [{
                "id": "interaction.dynamic.category",
                "sourceNodeId": tab["id"],
                "sourceNodeIds": [tab["id"]],
                "automatic": False,
                "action": "toggle-state",
                "target": "category-selection",
                "payload": {
                    "transitions": [{
                        "action": "toggle-state",
                        "target": "category-selection",
                        "targetScreenId": None,
                        "targetStateId": "category-selection",
                        "schedule": None,
                    }],
                    "contentVariants": [{
                        "sourceNodeId": tab["id"],
                        "targetNodeId": grid["id"],
                        "mode": "replace-children",
                        "targetRectBeforeCssPx": {"x": 32, "y": 350, "width": 329, "height": 90},
                        "targetRectAfterCssPx": {"x": 32, "y": 350, "width": 329, "height": 210},
                        "scrollAxisAfter": "vertical",
                        "items": [
                            {"text": "B", "textLeaves": ["B"]},
                            {"text": "C", "textLeaves": ["C"]},
                        ],
                    }],
                },
            }]
            path = root / "dynamic.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir)
            generated = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_tab = generated["screens"][0]["root"]["children"][0]
            variant = generated_tab["action"]["contentVariant"]
            self.assertEqual(variant["targetNodeID"], grid["id"])
            self.assertEqual([item["textValues"] for item in variant["items"]], [["B"], ["C"]])
            self.assertEqual(variant["sizeOverrides"], [
                {"nodeID": grid["id"], "width": None, "height": 210.0},
                {"nodeID": panel["id"], "width": None, "height": 300.0},
            ])
            self.assertEqual(variant["scrollAxisOverride"], "vertical")
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("contentOverrides[variant.targetNodeID] = variant.items", swiftui_runtime)
            self.assertIn("sizeOverride: store.sizeOverrides[spec.id]", swiftui_runtime)
            self.assertIn("store.scrollAxisOverrides[spec.id]", swiftui_runtime)
            swiftui_navigation = (swiftui_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("store.sizeOverrides[presentation.node.id]?.height", swiftui_navigation)
            self.assertIn("dynamicContentItem", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("makeDynamicView", uikit_runtime)
            self.assertIn("state.contentOverrides[spec.id]", uikit_runtime)
            self.assertIn("state.sizeOverrides[spec.id]", uikit_runtime)
            self.assertIn("state.scrollAxisOverrides[spec.id]", uikit_runtime)
            self.assertIn("spec.id != outerScrollOwnerNodeID", uikit_runtime)
            self.assertIn("content.isUserInteractionEnabled = false", uikit_runtime)
            self.assertIn("stack.isUserInteractionEnabled = false", uikit_runtime)
            self.assertIn("view = actionHostedViewIfNeeded(view, spec: spec)", uikit_runtime)
            self.assertIn("var view: UIView", uikit_runtime)
            self.assertIn("view is UILabel || view is UIStackView || view is UIImageView", uikit_runtime)
            self.assertIn("view.isUserInteractionEnabled = false", uikit_runtime)
            uikit_root = (uikit_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("generatedState.perform(action)", uikit_root)
            self.assertIn("generatedState.sizeOverrides[presentation.node.id]?.height", uikit_root)

    def test_layout_only_state_variant_updates_and_restores_native_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("expandable")
            root_node = payload["screens"][0]["nodes"][0]
            toggle = node("expandable.toggle", root_node["id"], "button", "Toggle")
            panel = node("expandable.panel", root_node["id"], "container")
            panel["layout"]["rect"] = {"x": 20, "y": 80, "width": 353, "height": 54}
            panel["style"]["height"] = "54px"
            payload["screens"][0]["nodes"].extend([toggle, panel])
            payload["states"] = [{
                "id": "panel-expanded",
                "kind": "expansion",
                "targetNodeIds": [panel["id"]],
            }]
            payload["interactions"] = [{
                "id": "interaction.dynamic.expand",
                "sourceNodeId": toggle["id"],
                "sourceNodeIds": [toggle["id"]],
                "automatic": False,
                "action": "toggle-state",
                "target": "panel-expanded",
                "payload": {
                    "transitions": [{
                        "action": "toggle-state",
                        "target": "panel-expanded",
                        "targetScreenId": None,
                        "targetStateId": "panel-expanded",
                        "schedule": None,
                    }],
                    "contentVariants": [{
                        "sourceNodeId": toggle["id"],
                        "targetNodeId": panel["id"],
                        "mode": "layout-only",
                        "targetRectBeforeCssPx": {"x": 20, "y": 80, "width": 353, "height": 54},
                        "targetRectAfterCssPx": {"x": 20, "y": 80, "width": 353, "height": 307},
                        "scrollAxisAfter": "none",
                        "items": [],
                    }],
                },
            }]
            path = root / "expandable.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            for stack in ("swiftui", "uikit"):
                out_dir = root / stack
                self.run_generator([path], out_dir, ui_stack=stack)
                generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
                action = generated["screens"][0]["root"]["children"][0]["action"]
                self.assertEqual(action["contentVariant"]["items"], [])
                self.assertEqual(action["contentVariant"]["sizeOverrides"], [{
                    "nodeID": panel["id"], "width": None, "height": 307.0,
                }])
                runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
                self.assertIn("let reversesVariant", runtime)
                self.assertIn("sizeOverrides.removeValue(forKey: $0.nodeID)", runtime)

    def test_axis_isolation_intrinsic_item_width_and_compact_square_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("generic")
            root_node = payload["screens"][0]["nodes"][0]
            root_node["layout"]["scrollAxis"] = "vertical"
            root_node["style"].update({"overflowX": "hidden", "overflowY": "auto"})

            rail = node("generic.rail", root_node["id"], "carousel")
            rail["layout"].update({
                "mode": "flex-row",
                "scrollAxis": "horizontal",
                "rect": {"x": 20, "y": 80, "width": 353, "height": 56},
            })
            rail["style"].update({
                "flexDirection": "row",
                "flexWrap": "nowrap",
                "overflowX": "auto",
                "overflowY": "hidden",
            })
            item = node("generic.item", rail["id"], "container")
            item["layout"].update({
                "mode": "flex-row",
                "rect": {"x": 20, "y": 88, "width": 88, "height": 40},
            })
            item["style"].update({"flexDirection": "row", "backgroundColor": "rgb(245, 245, 248)"})
            label = node("generic.label", item["id"], "text", "Single line label")
            label["layout"]["rect"] = {"x": 28, "y": 98, "width": 72, "height": 20}
            label["style"].update({"whiteSpace": "nowrap", "textOverflow": "clip"})
            label["content"]["lines"] = 1

            row = node("generic.row", root_node["id"], "container")
            row["layout"].update({
                "mode": "flex-row",
                "rect": {"x": 20, "y": 180, "width": 353, "height": 72},
            })
            row["style"].update({
                "flexDirection": "row",
                "borderWidths": ["1px"] * 4,
                "borderStyles": ["solid"] * 4,
                "borderColors": ["rgb(220, 220, 225)"] * 4,
            })
            icon_box = node("generic.icon-box", row["id"], "container")
            icon_box["layout"]["rect"] = {"x": 20, "y": 196, "width": 40, "height": 40}
            icon_box["style"].update({
                "backgroundColor": "rgb(230, 235, 245)",
                "cornerRadii": ["10px"] * 4,
            })
            icon = node("generic.icon", icon_box["id"], "icon")
            icon["layout"]["rect"] = {"x": 30, "y": 206, "width": 20, "height": 20}
            icon["assetRef"] = "asset.icon"

            orb = node("generic.orb", root_node["id"], "grid")
            orb["layout"].update({
                "mode": "grid",
                "rect": {"x": 144, "y": 280, "width": 104, "height": 104},
            })
            orb["style"].update({
                "display": "grid",
                "backgroundImage": "radial-gradient(circle at 35% 30%, rgb(155, 138, 255), rgb(58, 43, 204))",
                "cornerRadii": ["50%"] * 4,
                "gridTemplateColumns": "104px",
            })
            orb_icon = node("generic.orb-icon", orb["id"], "icon")
            orb_icon["layout"]["rect"] = {"x": 175, "y": 311, "width": 42, "height": 42}
            orb_icon["assetRef"] = "asset.icon"

            payload["screens"][0]["nodes"].extend([rail, item, label, row, icon_box, icon, orb, orb_icon])
            payload["assets"] = [{
                "id": "asset.icon",
                "kind": "inline-svg",
                "source": "inline-svg",
                "markup": '<svg viewBox="0 0 20 20"><path d="M2 10h16"/></svg>',
                "iosName": "html_generic_icon",
            }]
            path = root / "generic.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir)
            generated = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_root = generated["screens"][0]["root"]
            generated_rail = next(child for child in generated_root["children"] if child["id"] == rail["id"])
            generated_row = next(child for child in generated_root["children"] if child["id"] == row["id"])
            generated_item = generated_rail["children"][0]
            generated_label = generated_item["children"][0]
            generated_icon_box = generated_row["children"][0]
            generated_orb = next(child for child in generated_root["children"] if child["id"] == orb["id"])
            self.assertEqual(generated_orb["style"]["gradientCenterX"], 0.35)
            self.assertEqual(generated_orb["style"]["gradientCenterY"], 0.3)

            self.assertEqual(generated_rail["style"]["scrollAxis"], "horizontal")
            self.assertEqual(generated_item["style"]["fixedWidth"], 88)
            self.assertEqual(generated_item["style"]["fixedHeight"], 40)
            self.assertTrue(generated_item["style"]["preservesIntrinsicWidth"])
            self.assertEqual(generated_label["style"]["textLineLimit"], 1)
            self.assertTrue(generated_label["style"]["preservesIntrinsicWidth"])
            self.assertEqual(generated_icon_box["style"]["fixedWidth"], 40)
            self.assertEqual(generated_icon_box["style"]["fixedHeight"], 40)
            self.assertEqual(generated_icon_box["style"]["aspectRatio"], 1)
            self.assertAlmostEqual(generated_icon_box["style"]["widthFraction"], 40 / 353)
            self.assertAlmostEqual(generated_label["style"]["widthFraction"], 72 / 88)
            self.assertEqual(generated_row["style"]["minHeight"], 72)
            self.assertEqual(generated_orb["style"]["fixedWidth"], 104)
            self.assertEqual(generated_orb["style"]["fixedHeight"], 104)
            self.assertEqual(generated_orb["style"]["aspectRatio"], 1)
            self.assertEqual(generated_orb["style"]["cornerRadius"], 52)

            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("ScrollView(.vertical)", swiftui_runtime)
            self.assertIn("private var scrollContainer: some View", swiftui_runtime)
            self.assertIn(
                "let preservesIntrinsicWidth: Bool?",
                (swiftui_dir / MODELS_FILE).read_text(encoding="utf-8"),
            )
            self.assertIn(".lineLimit(style.textLineLimit)", swiftui_runtime)
            self.assertIn("HTMLToIOSAspectRatioModifier", swiftui_runtime)
            self.assertIn("if spec.children.isEmpty", swiftui_runtime)
            self.assertIn("ZStack(alignment: gridItemAlignment)", swiftui_runtime)
            self.assertIn("style.fixedWidth.map { CGFloat($0) }", swiftui_runtime)
            self.assertIn("let typography = content", swiftui_runtime)
            self.assertNotIn("map(CGFloat.init)", swiftui_runtime)
            self.assertIn(".clipped()", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("isDirectionalLockEnabled = true", uikit_runtime)
            self.assertIn("final class HTMLToIOSUIKitState", uikit_runtime)
            self.assertNotIn("private final class HTMLToIOSUIKitState", uikit_runtime)
            self.assertIn("alwaysBounceHorizontal = false", uikit_runtime)
            self.assertIn("label.numberOfLines = spec.style.textLineLimit ?? 0", uikit_runtime)
            self.assertIn("if spec.style.textLineLimit == 1 { return .byClipping }", uikit_runtime)
            self.assertIn("return .byWordWrapping", uikit_runtime)
            self.assertIn("widthAnchor.constraint(equalToConstant:", uikit_runtime)
            self.assertIn("heightAnchor.constraint(equalToConstant:", uikit_runtime)
            self.assertIn("makeScrollContainer", uikit_runtime)

    def test_unresolved_interaction_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            interaction = transition("unresolved", "push", "page2")
            interaction["requiresResolution"] = True
            path = root / "page1.json"
            path.write_text(json.dumps(ir("page1", interaction)), encoding="utf-8")
            result = self.run_generator([path], root / "out", expect_success=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unresolved interactions", result.stderr)

    def test_collapsed_expansion_content_is_generated_as_conditionally_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("page1")
            root_id = "page1.root"
            panel = node("page1.panel", root_id, "container")
            panel["state"] = {"initiallyVisible": False}
            panel["style"]["overflowY"] = "hidden"
            panel["layout"]["rect"]["height"] = 0
            option = node("page1.option", "page1.panel", "button", "Option")
            payload["screens"][0]["nodes"].extend([panel, option])
            payload["states"] = [{
                "id": "state-expanded",
                "kind": "expansion",
                "targetNodeIds": [root_id],
            }]
            path = root / "page1.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            out_dir = root / "Generated" / "HTMLToIOS"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_panel = generated["screens"][0]["root"]["children"][0]
            self.assertEqual(generated_panel["id"], "page1.panel")
            self.assertEqual(generated_panel["visibleWhenStateID"], "state-expanded")
            self.assertEqual(generated_panel["children"][0]["id"], "page1.option")

    def test_native_navigation_and_tab_containers_are_generated_for_both_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = ir("home")
            profile = ir("profile")
            home["screens"][0]["navigation"] = {
                "style": "native",
                "title": "Home",
                "titleMode": "large",
                "scrollEdgeAppearance": "transparent",
                "backButton": "system",
            }
            home["screens"][0]["tabContainer"] = {
                "id": "main-tabs",
                "initialTabId": "home-tab",
                "reselectBehavior": "pop-to-root",
                "visibility": "hide-on-push",
                "items": [
                    {"id": "home-tab", "title": "Home", "targetScreenId": "home", "icon": "house", "selectedIcon": "house.fill", "role": "normal"},
                    {"id": "profile-tab", "title": "Profile", "targetScreenId": "profile", "icon": "person", "badge": "2", "role": "normal"},
                ],
            }
            paths = []
            for name, payload in (("home", home), ("profile", profile)):
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths.append(path)

            swiftui_dir = root / "swiftui"
            self.run_generator(paths, swiftui_dir)
            generated = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            self.assertEqual(generated["tabContainer"]["initialTabId"], "home-tab")
            self.assertEqual(generated["screens"][0]["navigation"]["titleMode"], "large")
            self.assertFalse((swiftui_dir / "HTMLToIOSGeneratedRuntime.swift").exists())
            self.assertTrue((swiftui_dir / RUNTIME_FILE).is_file())
            self.assertTrue((swiftui_dir / MODELS_FILE).is_file())
            swiftui_root = (swiftui_dir / SWIFTUI_ROOT_FILE).read_text(encoding="utf-8")
            swiftui_navigation = (swiftui_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("HTMLToIOSGeneratedNavigationContainer", swiftui_root)
            self.assertIn("TabView(selection:", swiftui_navigation)
            self.assertIn("tabPathBinding", swiftui_navigation)
            self.assertTrue((swiftui_dir / "Home/Screens/HTMLToIOSHomeScreen.swift").is_file())
            self.assertTrue((swiftui_dir / "Home/Views/HTMLToIOSHomeContentView.swift").is_file())
            self.assertTrue((swiftui_dir / "Profile/Screens/HTMLToIOSProfileScreen.swift").is_file())
            self.assertIn("HTMLToIOSHomeScreen", (swiftui_dir / SCREEN_FACTORY_FILE).read_text(encoding="utf-8"))
            self.assertIn("tabScrollToTopNonce", swiftui_runtime)
            self.assertIn("tabBarVisibility(for:", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator(paths, uikit_dir, ui_stack="uikit")
            uikit_root = (uikit_dir / SWIFTUI_ROOT_FILE).read_text(encoding="utf-8")
            uikit_navigation = (uikit_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("HTMLToIOSGeneratedCoordinator", uikit_root)
            self.assertIn("UITabBarController", uikit_navigation)
            self.assertIn("tabNavigationControllers", uikit_navigation)
            self.assertIn("popToRootViewController", uikit_navigation)
            self.assertIn('case "scroll-to-top"', uikit_navigation)
            self.assertIn("firstScrollView", uikit_navigation)
            self.assertTrue((uikit_dir / "Home/Controllers/HTMLToIOSHomeViewController.swift").is_file())
            self.assertTrue((uikit_dir / "Home/Views/HTMLToIOSHomeContentView.swift").is_file())
            self.assertTrue((uikit_dir / "Profile/Controllers/HTMLToIOSProfileViewController.swift").is_file())

    def test_nonstandard_output_directory_requires_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "home.json"
            path.write_text(json.dumps(ir("home")), encoding="utf-8")
            result = subprocess.run(
                ["python3", str(SCRIPT), "--ir", str(path), "--out-dir", str(root / "GeneratedCode"), "--ui-stack", "swiftui"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Generated/HTMLToIOS", result.stderr)

    def test_explicit_module_and_screen_prefix_group_related_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = ir("home")
            home_detail = ir("home-detail")
            article_list = ir("article-list")
            article_list["screens"][0]["moduleId"] = "content-library"
            paths = []
            for name, payload in (("home", home), ("home-detail", home_detail), ("article-list", article_list)):
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths.append(path)

            out_dir = root / "Generated" / "HTMLToIOS"
            self.run_generator(paths, out_dir)
            self.assertTrue((out_dir / "Home/Screens/HTMLToIOSHomeScreen.swift").is_file())
            self.assertTrue((out_dir / "Home/Screens/HTMLToIOSHomeDetailScreen.swift").is_file())
            self.assertTrue((out_dir / "Home/Views/HTMLToIOSHomeDetailContentView.swift").is_file())
            self.assertTrue((out_dir / "ContentLibrary/Screens/HTMLToIOSArticleListScreen.swift").is_file())
            manifest = json.loads((out_dir / ".html-to-ios-generation.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["screenModules"]["home-detail"], "home")
            self.assertEqual(manifest["screenModules"]["article-list"], "content-library")

    def test_naming_plan_prefixes_generated_page_files_and_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "home.json"
            path.write_text(json.dumps(ir("home")), encoding="utf-8")
            naming_plan = root / "native-naming-plan.json"
            naming_plan.write_text(json.dumps({
                "schemaVersion": "native-naming-plan-1.0",
                "prefix": "Sky",
                "source": "new-project-default",
            }), encoding="utf-8")
            out_dir = root / "Generated" / "HTMLToIOS"
            result = self.run_generator([path], out_dir, naming_plan=naming_plan)
            report = json.loads(result.stdout)
            self.assertEqual(report["namePrefix"], "Sky")
            screen_file = out_dir / "Home/Screens/SkyHomeScreen.swift"
            content_file = out_dir / "Home/Views/SkyHomeContentView.swift"
            self.assertTrue(screen_file.is_file())
            self.assertTrue(content_file.is_file())
            self.assertIn("struct SkyHomeScreen", screen_file.read_text(encoding="utf-8"))
            self.assertIn("SkyHomeScreen", (out_dir / SCREEN_FACTORY_FILE).read_text(encoding="utf-8"))

    def test_naming_plan_rejects_existing_type_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "home.json"
            path.write_text(json.dumps(ir("home")), encoding="utf-8")
            naming_plan = root / "native-naming-plan.json"
            naming_plan.write_text(json.dumps({
                "schemaVersion": "native-naming-plan-1.0",
                "prefix": "ABC",
                "source": "existing-module-dominant-prefix",
                "existingTypeNames": ["ABCHomeScreen"],
            }), encoding="utf-8")
            result = self.run_generator(
                [path], root / "Generated" / "HTMLToIOS",
                expect_success=False,
                naming_plan=naming_plan,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("collide with existing target types", result.stderr)

    def test_asset_catalog_is_rebuilt_and_legacy_catalog_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            payload["assets"] = [{
                "id": "asset.logo",
                "kind": "inline-svg",
                "iosName": "html_home_logo",
                "source": "inline-svg",
                "localPath": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3Crect width='8' height='8' fill='red'/%3E%3C/svg%3E",
            }]
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "Generated" / "HTMLToIOS"

            self.run_generator([path], out_dir)
            self.assertTrue((out_dir / ASSET_CATALOG / "html_home_logo.imageset").is_dir())

            legacy = out_dir / "HTMLToIOSGeneratedAssets.xcassets"
            legacy.mkdir()
            (legacy / "Contents.json").write_text('{"legacy":true}\n', encoding="utf-8")
            result = self.run_generator([path], out_dir)
            report = json.loads(result.stdout)
            self.assertEqual(report["assetMigration"]["status"], "preserved-legacy-catalog-in-conflicts")
            self.assertFalse(legacy.exists())
            self.assertTrue((out_dir.with_name("HTMLToIOS.conflicts") / "Legacy" / legacy.name).is_dir())

            payload["assets"] = []
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.run_generator([path], out_dir)
            self.assertFalse((out_dir / ASSET_CATALOG).exists())

    def test_fixed_artboard_scales_design_tokens_and_binds_expansion_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("page1", transition("expand", "toggle-state", "state-expand"))
            payload["target"]["scale"] = 1.25
            root_node = payload["screens"][0]["nodes"][0]
            root_node["style"].update({
                "fontSize": "12px",
                "lineHeight": "18px",
                "letterSpacing": "0.4px",
                "padding": ["2px", "4px", "6px", "8px"],
                "margin": ["1px", "2px", "3px", "4px"],
                "cornerRadii": ["10px"] * 4,
                "gap": "normal",
            })
            button = payload["screens"][0]["nodes"][1]
            panel = node("page1.panel", "page1.root", "container")
            panel["layout"]["rect"]["height"] = 0
            panel["style"]["overflowY"] = "hidden"
            panel["style"]["maxHeight"] = "0px"
            panel["style"]["cornerRadii"] = ["10px"] * 4
            payload["screens"][0]["nodes"].append(panel)
            payload["states"] = [{
                "id": "state-expand",
                "kind": "expansion",
                "targetNodeIds": ["page1.root"],
            }]
            button["parentId"] = "page1.root"

            path = root / "page1.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_root = generated["screens"][0]["root"]
            style = generated_root["style"]
            self.assertEqual(style["fontSize"], 15)
            self.assertEqual(style["lineHeight"], 22.5)
            self.assertEqual(style["letterSpacing"], 0.5)
            self.assertEqual(style["padding"], [2.5, 5, 7.5, 10])
            self.assertEqual(style["margin"], [1.25, 2.5, 3.75, 5])
            self.assertEqual(style["cornerRadius"], 0)
            self.assertEqual(style["spacing"], 0)
            generated_panel = next(child for child in generated_root["children"] if child["id"] == "page1.panel")
            self.assertEqual(generated_panel["style"]["cornerRadius"], 12.5)
            self.assertEqual(generated_panel["visibleWhenStateID"], "state-expand")

    def test_svg_assets_overlay_layout_and_grid_column_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("page3")
            root_node = payload["screens"][0]["nodes"][0]
            ring = node("page3.ring", root_node["id"], "container")
            ring["layout"]["mode"] = "flow"
            ring["layout"]["rect"].update({"width": 88, "height": 88})
            svg = node("page3.ring-svg", ring["id"], "icon")
            svg["layout"]["position"] = "static"
            svg["layout"]["rect"].update({"width": 88, "height": 88})
            svg["assetRef"] = "asset.ring"
            value = node("page3.ring-value", ring["id"], "text", "82分")
            value["layout"]["position"] = "absolute"
            value["layout"]["rect"].update({"width": 88, "height": 88})
            stats = node("page3.stats", root_node["id"], "grid")
            stats["layout"]["mode"] = "grid"
            stats["style"]["display"] = "grid"
            stats["style"]["gridTemplateColumns"] = "80px 80px 80px"
            root_node["children"] = []
            payload["screens"][0]["nodes"].extend([ring, svg, value, stats])
            payload["assets"] = [{
                "id": "asset.ring",
                "kind": "inline-svg",
                "source": "inline-svg",
                "markup": '<svg viewBox="0 0 88 88"><circle cx="44" cy="44" r="38"/></svg>',
                "iosName": "html_page3_ring",
            }]

            path = root / "page3.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_root = generated["screens"][0]["root"]
            generated_ring = next(child for child in generated_root["children"] if child["id"] == ring["id"])
            generated_stats = next(child for child in generated_root["children"] if child["id"] == stats["id"])
            self.assertEqual(generated_ring["axis"], "horizontal")
            self.assertEqual(generated_ring["children"], [])
            self.assertEqual(generated_ring["style"]["fixedWidth"], 88)
            self.assertEqual(generated_ring["style"]["fixedHeight"], 88)
            self.assertEqual(
                [item["id"] for item in generated_ring["overlayChildren"]],
                [svg["id"], value["id"]],
            )
            self.assertEqual(generated_ring["overlayChildren"][0]["assetName"], "html_page3_ring")
            self.assertEqual(generated_stats["style"]["gridColumnCount"], 3)
            self.assertTrue((out_dir / ASSET_CATALOG / "html_page3_ring.imageset" / "html_page3_ring.svg").is_file())

    def test_page_regions_background_asset_and_sheet_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            open_sheet = transition("open-filters", "present-sheet", "filters-sheet")
            open_sheet["presentation"] = {
                "style": "page-sheet",
                "detents": ["medium", "large"],
                "grabberVisible": True,
                "interactiveDismissDisabled": True,
            }
            payload = ir("home", open_sheet, states=[{
                "id": "filters-sheet",
                "kind": "sheet",
                "targetNodeIds": ["home.sheet"],
            }])
            root_node = payload["screens"][0]["nodes"][0]
            sheet = next(item for item in payload["screens"][0]["nodes"] if item["id"] == "home.sheet")
            sheet["style"]["opacity"] = "0"
            sheet_child = node("home.sheet.child", sheet["id"], "text", "Child")
            sheet_child["style"]["opacity"] = "0.4"
            payload["screens"][0]["nodes"].append(sheet_child)
            top = node("home.top", root_node["id"], "navigation")
            top["layout"]["rect"].update({"y": 0, "height": 56})
            bottom = node("home.bottom", root_node["id"], "footer")
            bottom["layout"]["rect"].update({"y": 752, "height": 100})
            payload["screens"][0]["nodes"].extend([top, bottom])
            payload["screens"][0]["regions"] = {
                "topBar": {"nodeId": top["id"], "kind": "custom-navigation-bar", "confidence": 0.9},
                "bottomBar": {"nodeId": bottom["id"], "kind": "bottom-action-bar", "placement": "viewport-overlay", "confidence": 0.9},
            }
            payload["screens"][0]["systemChrome"] = {"navigationBar": "custom"}

            background_file = root / "background.png"
            background_file.write_bytes(b"not-a-real-png-but-valid-generator-input")
            root_node["assetRef"] = "asset.background"
            payload["assets"] = [{
                "id": "asset.background",
                "kind": "css-background",
                "source": background_file.as_uri(),
                "localPath": str(background_file),
                "iosName": "html_home_background",
                "renderMode": "cover",
                "position": "center top",
                "repeat": "no-repeat",
            }]

            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            screen = generated["screens"][0]
            self.assertEqual(screen["topBar"]["id"], "home.top")
            self.assertEqual(screen["bottomBar"]["id"], "home.bottom")
            self.assertIsNone(screen["topBar"]["style"]["preferredWidth"])
            self.assertIsNone(screen["bottomBar"]["style"]["preferredWidth"])
            self.assertEqual(screen["topBar"]["style"]["widthFraction"], 1.0)
            self.assertEqual(screen["bottomBar"]["style"]["widthFraction"], 1.0)
            self.assertEqual(screen["bottomBarPlacement"], "viewport-overlay")
            self.assertFalse(screen["showsNavigationBar"])
            self.assertEqual(screen["root"]["backgroundAssetName"], "html_home_background")
            self.assertEqual(screen["root"]["style"]["backgroundContentMode"], "cover")
            self.assertEqual(screen["presentations"][0]["detents"], ["medium", "large"])
            self.assertTrue(screen["presentations"][0]["interactiveDismissDisabled"])
            self.assertEqual(screen["presentations"][0]["node"]["style"]["opacity"], 1)
            self.assertEqual(screen["presentations"][0]["node"]["children"][0]["style"]["opacity"], 0.4)
            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            root_source = (out_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("safeAreaInset(edge: .top", runtime)
            self.assertIn('screen.bottomBarPlacement == "viewport-overlay"', runtime)
            self.assertIn("GeometryReader { proxy in", runtime)
            self.assertIn("proxy.safeAreaInsets.bottom + CGFloat(screen.fixedArtboardCropInsets?[2] ?? 0)", runtime)
            self.assertIn("presentationDetents", root_source)
            self.assertIn("HTMLToIOSScrollOffsetPreferenceKey", runtime)
            self.assertIn('screen.topBarBehavior == "collapse"', runtime)
            self.assertIn('screen.topBarBehavior == "hide-on-scroll"', runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('screen.bottomBarPlacement == "safe-area-inset" ? (generatedBottomBar?.bounds.height ?? 0) : 0', uikit_runtime)
            self.assertIn('screen.bottomKeyboardAvoidance == "keyboard-layout-guide"', uikit_runtime)
            self.assertIn('? view.keyboardLayoutGuide.topAnchor', uikit_runtime)
            self.assertIn('screen.bottomBarPlacement == "viewport-overlay"', uikit_runtime)
            self.assertIn('CGFloat(screen.fixedArtboardCropInsets?[2] ?? 0)', uikit_runtime)
            self.assertIn("func scrollViewDidScroll(_ scrollView: UIScrollView)", uikit_runtime)
            self.assertIn('case "appearance-change":', uikit_runtime)
            self.assertIn("navigationController?.hidesBarsOnSwipe", uikit_runtime)

    def test_presentation_actions_focus_and_scroll_handoff_are_generated_for_both_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            open_sheet = transition("open-actions", "present-sheet", "actions-sheet")
            open_sheet["presentation"] = {
                "style": "page-sheet",
                "detents": ["medium", "large"],
                "grabberVisible": True,
            }
            payload = ir("home", open_sheet, states=[{
                "id": "actions-sheet",
                "kind": "sheet",
                "targetNodeIds": ["home.sheet"],
            }])
            sheet = next(item for item in payload["screens"][0]["nodes"] if item["id"] == "home.sheet")
            sheet["layout"]["scrollAxis"] = "vertical"
            sheet["style"]["opacity"] = "0"
            delete = node("home.sheet.delete", sheet["id"], "button", "Delete")
            cancel = node("home.sheet.cancel", sheet["id"], "button", "Cancel")
            payload["screens"][0]["nodes"].extend([delete, cancel])
            for source_node_id, action_id in ((delete["id"], "delete-action"), (cancel["id"], "cancel-action")):
                action = transition(action_id, "dismiss-sheet", "actions-sheet")
                action["sourceNodeId"] = source_node_id
                action["sourceNodeIds"] = [source_node_id]
                payload["interactions"].append(action)

            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            presentation_plan = root / "native-presentation-plan.json"
            subprocess.run(
                ["python3", str(PRESENTATION_PLAN_SCRIPT), "--ir", str(path), "--out", str(presentation_plan)],
                check=True,
                capture_output=True,
                text=True,
            )

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir, presentation_plan=presentation_plan)
            generated = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            presentation = generated["screens"][0]["presentations"][0]
            self.assertEqual(presentation["sourceNodeID"], "home.button")
            self.assertEqual(
                [item["action"]["action"] for item in presentation["actions"]],
                ["dismiss-sheet", "dismiss-sheet"],
            )
            swiftui_root = (swiftui_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("store.perform(action.action)", swiftui_root)
            self.assertIn("restorePresentationFocus(presentation)", swiftui_root)
            self.assertIn("canTransferScrollGesture(to: presentation)", swiftui_root)
            self.assertIn("presentation.scrollOwnership == \"presentation-content\"", swiftui_root)
            self.assertIn("HTMLToIOSNodeScrollOffsetPreferenceKey", swiftui_runtime)
            self.assertIn("replacePrimaryPresentation()", swiftui_runtime)
            self.assertIn("focusRequestNodeID", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit", presentation_plan=presentation_plan)
            uikit_root = (uikit_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("action.action?.action != \"dismiss\"", uikit_root)
            self.assertIn("restorePresentationFocus(presentation.sourceNodeID)", uikit_root)
            self.assertIn("view(withAccessibilityIdentifier:", uikit_root)
            self.assertIn("private var presentationHost", uikit_root)
            self.assertIn("presentationStateIDsInFlight", uikit_root)
            self.assertIn("isPresentationActive(stateID)", uikit_root)
            self.assertIn("controller.popoverPresentationController", uikit_root)
            self.assertIn("scroll.contentOffset.y > -scroll.adjustedContentInset.top + 0.5", uikit_root)

    def test_generator_consumes_compatible_api_fallback_contracts_and_rejects_unknown_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path = root / "home.json"
            ir_path.write_text(json.dumps(ir("home")), encoding="utf-8")
            fallback_path = root / "fallback.json"
            fallback = {
                "schemaVersion": "native-api-fallback-plan-1.0",
                "uiStack": "swiftui",
                "minimumIOS": "16.0",
                "runtimeBaseline": {"minimumIOS": "16.0"},
                "capabilities": [{
                    "id": "keyframe-animation",
                    "required": True,
                    "activeResolution": "fallback",
                    "stacks": {"swiftui": {"fallback": "timeline-sampled-animation"}},
                }],
                "summary": {"blockedCapabilityIDs": [], "fallbackCapabilityIDs": ["keyframe-animation"]},
            }
            fallback_path.write_text(json.dumps(fallback), encoding="utf-8")
            matrix_path = root / "matrix.json"
            matrix_path.write_text(json.dumps({
                "schemaVersion": "ios-compatibility-matrix-1.0",
                "uiStack": "swiftui",
                "minimumIOS": "16.0",
                "summary": {"runtimeBaselineSatisfied": True},
            }), encoding="utf-8")

            out_dir = root / "valid"
            self.run_generator(
                [ir_path], out_dir,
                compatibility_matrix=matrix_path,
                api_fallback_plan=fallback_path,
            )
            generation = json.loads((out_dir / ".html-to-ios-generation.json").read_text(encoding="utf-8"))
            self.assertEqual(generation["activeAPIFallbacks"], ["keyframe-animation"])
            self.assertEqual(generation["compatibilityMatrixSha256"], hashlib.sha256(matrix_path.read_bytes()).hexdigest())

            fallback["capabilities"][0]["stacks"]["swiftui"]["fallback"] = "unknown-runtime"
            fallback_path.write_text(json.dumps(fallback), encoding="utf-8")
            result = self.run_generator(
                [ir_path], root / "invalid", expect_success=False,
                compatibility_matrix=matrix_path,
                api_fallback_plan=fallback_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("generator does not implement API fallbacks", result.stderr)

    def test_viewport_bar_releases_large_direct_children_without_relaxing_icons(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            screen = payload["screens"][0]
            root_node = screen["nodes"][0]
            bottom = node("home.bottom", root_node["id"], "footer")
            bottom["layout"]["rect"].update({"width": 393, "height": 88})
            left = node("home.bottom.left", bottom["id"], "button", "Export")
            left["layout"]["rect"].update({"width": 168, "height": 48})
            left["style"]["flexShrink"] = "0"
            left["style"]["flexGrow"] = "1"
            icon = node("home.bottom.left.icon", left["id"], "icon")
            icon["layout"]["rect"].update({"width": 20, "height": 20})
            icon["style"]["flexShrink"] = "0"
            right = node("home.bottom.right", bottom["id"], "button", "Retry")
            right["layout"]["rect"].update({"width": 168, "height": 48})
            right["style"]["flexShrink"] = "0"
            right["style"]["flexGrow"] = "1"
            screen["nodes"].extend([bottom, left, icon, right])
            screen["regions"] = {
                "bottomBar": {
                    "nodeId": bottom["id"],
                    "kind": "bottom-action-bar",
                    "placement": "viewport-overlay",
                }
            }

            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            bar = generated["screens"][0]["bottomBar"]
            generated_left = next(item for item in bar["children"] if item["id"] == left["id"])
            generated_icon = generated_left["children"][0]
            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")

            self.assertIsNone(bar["style"]["preferredWidth"])
            self.assertIsNone(generated_left["style"]["fixedWidth"])
            self.assertFalse(generated_left["style"]["preservesIntrinsicWidth"])
            self.assertFalse(generated_left["style"]["resistsCompression"])
            self.assertEqual(generated_left["style"]["flexGrow"], 1)
            self.assertEqual(generated_icon["style"]["preferredWidth"], 20)
            self.assertTrue(generated_icon["style"]["preservesIntrinsicWidth"])
            self.assertIn(
                "enforcesPreferredWidth: isNativeControl && spec.style.preservesIntrinsicWidth == true",
                runtime,
            )
            self.assertIn("(style.flexGrow ?? 0) > 0", runtime)

    def test_custom_popover_overlay_preserves_source_geometry_for_both_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            open_popover = transition("open-emoji", "present-popover", "emoji-popover")
            open_popover["presentation"] = {"style": "popover", "detents": []}
            payload = ir("home", open_popover, states=[{
                "id": "emoji-popover",
                "kind": "popover-overlay",
                "targetNodeIds": ["home.sheet"],
            }])
            payload["screens"][0]["nodes"][0]["layout"]["rect"] = {"x": 100, "y": 200, "width": 393, "height": 852}
            popover = next(item for item in payload["screens"][0]["nodes"] if item["id"] == "home.sheet")
            popover["layout"]["rect"] = {"x": 124, "y": 780, "width": 345, "height": 190}
            popover["style"].update({
                "opacity": "0",
                "backgroundColor": "rgb(255, 255, 255)",
                "cornerRadii": ["18px"] * 4,
            })
            child = node("home.emoji", popover["id"], "button", "Emoji")
            payload["screens"][0]["nodes"].append(child)
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir)
            generated = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            presentation = generated["screens"][0]["presentations"][0]
            self.assertTrue(presentation["usesCustomOverlay"])
            self.assertEqual(presentation["coordinateSpace"], "app-root")
            self.assertEqual(presentation["sourceRect"], [24, 580, 345, 190])
            self.assertEqual(presentation["panelRect"], [24, 580, 345, 190])
            self.assertEqual(presentation["node"]["style"]["opacity"], 1)
            self.assertEqual(presentation["node"]["style"]["fixedWidth"], 345)
            self.assertEqual(presentation["node"]["style"]["fixedHeight"], 190)
            self.assertEqual(presentation["node"]["style"]["offsetX"], 0)
            self.assertEqual(presentation["node"]["style"]["offsetY"], 0)
            root_source = (swiftui_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("systemPopoverIsPresented", root_source)
            self.assertIn("customPopoverOverlay", root_source)
            self.assertIn("let globalFrame = proxy.frame(in: .global)", root_source)
            self.assertIn("- globalFrame.minY", root_source)
            self.assertIn(".position(x: centerX, y: centerY)", root_source)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_root = (uikit_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("HTMLToIOSGeneratedCustomOverlayController", uikit_root)
            self.assertIn("presentation.usesCustomOverlay", uikit_root)
            self.assertIn("sourceLeading.priority = .defaultHigh", uikit_root)
            self.assertIn("panel.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor)", uikit_root)
            self.assertIn("panel.heightAnchor.constraint(equalToConstant: height)", uikit_root)
            self.assertIn('catalog.presentation(stateID)?.node.id ?? "html-to-ios-presentation-\\(stateID)"', uikit_root)
            self.assertIn('spec.style.baselineAligned == true || spec.style.alignItems == "baseline"', uikit_runtime)

    def test_large_overlay_height_and_actionable_grid_are_preserved_for_both_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]

            artwork = node("home.artwork", root_node["id"], "container")
            artwork["layout"].update({
                "mode": "flow",
                "rect": {"x": 70, "y": 80, "width": 253, "height": 224},
            })
            artwork["style"]["position"] = "relative"
            artwork_child = node("home.artwork-child", artwork["id"], "container")
            artwork_child["layout"].update({
                "mode": "absolute",
                "position": "absolute",
                "rect": {"x": 70, "y": 80, "width": 253, "height": 224},
            })
            artwork_child["style"].update({
                "position": "absolute",
                "backgroundColor": "rgb(120, 100, 255)",
            })
            artwork_dot = node("home.artwork-dot", artwork["id"], "decoration")
            artwork_dot["layout"].update({
                "mode": "absolute",
                "position": "absolute",
                "rect": {"x": 191.5, "y": 80, "width": 10, "height": 10},
            })
            artwork_dot["style"].update({
                "position": "absolute",
                "backgroundColor": "rgb(255, 255, 255)",
                "cornerRadii": ["50%"] * 4,
                "opacity": "0",
            })
            artwork_dot["content"]["isDecorative"] = True
            artwork_dot["state"] = {"initiallyVisible": False}

            mixed_card = node("home.mixed-card", root_node["id"], "container")
            mixed_card["layout"].update({
                "mode": "flow",
                "rect": {"x": 24, "y": 310, "width": 345, "height": 120},
            })
            mixed_card["style"].update({
                "position": "relative",
                "display": "flex",
                "flexDirection": "column",
                "overflowX": "hidden",
                "overflowY": "hidden",
                "margin": ["0px", "24px", "0px", "24px"],
                "backgroundColor": "rgb(30, 32, 48)",
            })
            mixed_label = node("home.mixed-label", mixed_card["id"], "text", "Flow content")
            mixed_label["layout"]["rect"] = {"x": 44, "y": 330, "width": 120, "height": 24}
            mixed_glow = node("home.mixed-glow", mixed_card["id"], "decoration")
            mixed_glow["layout"].update({
                "mode": "absolute",
                "position": "absolute",
                "rect": {"x": 34, "y": 320, "width": 160, "height": 80},
            })
            mixed_glow["style"].update({
                "position": "absolute",
                "backgroundImage": "radial-gradient(circle, rgba(120, 100, 255, 0.5), transparent 65%)",
            })
            mixed_glow["content"]["isDecorative"] = True

            grid_action = node("home.grid-action", root_node["id"], "button")
            grid_action["layout"].update({
                "mode": "grid",
                "rect": {"x": 24, "y": 340, "width": 345, "height": 132},
            })
            grid_action["style"].update({
                "display": "grid",
                "gridTemplateColumns": "repeat(4, 1fr)",
                "gap": "4px",
            })
            grid_action["interactionRef"] = "interaction-grid"
            for index in range(7):
                child = node(f"home.grid-item-{index}", grid_action["id"], "text", str(index))
                child["layout"]["rect"] = {
                    "x": 24 + (index % 4) * 86,
                    "y": 340 + (index // 4) * 64,
                    "width": 82,
                    "height": 60,
                }
                payload["screens"][0]["nodes"].append(child)

            payload["screens"][0]["nodes"].extend([
                artwork,
                artwork_child,
                artwork_dot,
                mixed_card,
                mixed_label,
                mixed_glow,
                grid_action,
            ])
            payload["motions"] = [
                {
                    "id": "motion-artwork",
                    "sourceNodeId": artwork["id"],
                    "durationMs": 8000,
                    "delayMs": 0,
                    "iterationCount": "Infinity",
                    "direction": "reverse",
                    "properties": ["transform"],
                    "keyframes": [
                        {"computedOffset": 0, "transform": "none"},
                        {"computedOffset": 1, "transform": "rotate(360deg)"},
                    ],
                },
                {
                    "id": "motion-artwork-dot",
                    "sourceNodeId": artwork_dot["id"],
                    "durationMs": 1200,
                    "delayMs": 100,
                    "iterationCount": "1",
                    "direction": "normal",
                    "properties": ["transform", "opacity"],
                    "keyframes": [
                        {"computedOffset": 0, "transform": "translate(-50%, -50%) scale(0)", "opacity": 0},
                        {"computedOffset": 0.15, "transform": "translate(-50%, -50%) scale(1.4)", "opacity": 1},
                        {
                            "computedOffset": 1,
                            "transform": "translate(calc(-50% - 70px), calc(-50% - 80px)) scale(0)",
                            "opacity": 0,
                        },
                    ],
                },
            ]
            payload["interactions"] = [{
                "id": "interaction-grid",
                "sourceNodeId": grid_action["id"],
                "action": "toggle-state",
                "targetStateId": "grid-state",
            }]
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir)
            generated = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_root = generated["screens"][0]["root"]
            generated_artwork = next(item for item in generated_root["children"] if item["id"] == artwork["id"])
            generated_mixed_card = next(item for item in generated_root["children"] if item["id"] == mixed_card["id"])
            generated_grid = next(item for item in generated_root["children"] if item["id"] == grid_action["id"])
            self.assertEqual(generated_artwork["style"]["preferredHeight"], 224)
            self.assertEqual(generated_artwork["children"][0]["style"]["fixedWidth"], 253)
            self.assertEqual(generated_artwork["children"][0]["style"]["fixedHeight"], 224)
            self.assertEqual(generated_artwork["axis"], "overlay")
            self.assertEqual(generated_artwork["motions"][0]["rotationDegrees"], 360)
            self.assertTrue(generated_artwork["motions"][0]["repeats"])
            self.assertTrue(generated_artwork["motions"][0]["reverses"])
            generated_dot = next(item for item in generated_artwork["children"] if item["id"] == artwork_dot["id"])
            self.assertEqual(generated_dot["style"]["fixedWidth"], 10)
            self.assertEqual(generated_dot["style"]["fixedHeight"], 10)
            self.assertEqual(generated_dot["style"]["offsetX"], 0)
            self.assertEqual(generated_dot["style"]["offsetY"], -107)
            self.assertEqual(generated_dot["motions"][0]["sampleOffsets"], [0, 0.15, 1])
            self.assertEqual(generated_dot["motions"][0]["translationXValues"], [0, 0, -70])
            self.assertEqual(generated_dot["motions"][0]["translationYValues"], [0, 0, -80])
            self.assertEqual(generated_dot["motions"][0]["scaleValues"], [0, 1.4, 0])
            self.assertEqual(generated_dot["motions"][0]["opacityValues"], [0, 1, 0])
            self.assertEqual(generated_mixed_card["style"]["fixedWidth"], 345)
            self.assertEqual(generated_mixed_card["style"]["fixedHeight"], 120)
            self.assertFalse(generated_mixed_card["style"]["clipsOwnContent"])
            self.assertTrue(generated_mixed_card["style"]["clipsContent"])
            self.assertEqual(generated_mixed_card["children"], [])
            self.assertEqual(
                [item["id"] for item in generated_mixed_card["overlayChildren"]],
                [mixed_label["id"], mixed_glow["id"]],
            )
            self.assertEqual(generated_grid["axis"], "grid")
            self.assertEqual(generated_grid["style"]["gridColumnCount"], 4)
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('if spec.axis == "grid" {', swiftui_runtime)
            self.assertIn("LazyVGrid(columns: gridColumns", swiftui_runtime)
            self.assertIn("ForEach(spec.overlayChildren)", swiftui_runtime)
            self.assertIn("private struct HTMLToIOSOverlayClipModifier: ViewModifier", swiftui_runtime)
            self.assertIn("if style.clipsContent == true", swiftui_runtime)
            self.assertIn("if style.clipsContent == true || style.clipsOwnContent == true", swiftui_runtime)
            self.assertNotIn("style.clipsContent == true || (style.cornerRadius ?? 0) > 0", swiftui_runtime)
            self.assertIn("private struct HTMLToIOSMarginModifier: ViewModifier", swiftui_runtime)
            self.assertIn(".modifier(HTMLToIOSMarginModifier(style: spec.style))", swiftui_runtime)
            self.assertIn("endRadius: radialEndRadius(proxy.size)", swiftui_runtime)
            self.assertIn("center: radialCenter", swiftui_runtime)
            self.assertIn("let farthestX = max(centerX, 1 - centerX) * size.width", swiftui_runtime)
            self.assertIn("sqrt(farthestX * farthestX + farthestY * farthestY)", swiftui_runtime)
            self.assertIn(".offset(x: style.offsetX ?? 0, y: style.offsetY ?? 0)", swiftui_runtime)
            self.assertIn("private struct HTMLToIOSMotionModifier: ViewModifier", swiftui_runtime)
            self.assertIn("HTMLToIOSLaunchConfiguration.motionProgress", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('let content = spec.axis == "grid" ? makeGrid(spec) : makeStack(spec, appliesPadding: false)', uikit_runtime)
            self.assertIn("stack.insetsLayoutMarginsFromSafeArea = false", uikit_runtime)
            self.assertIn("private func makeGrid(_ spec: HTMLToIOSNodeSpec) -> UIView", uikit_runtime)
            self.assertIn("return makeOverlay(spec)", uikit_runtime)
            self.assertIn("HTMLToIOSGridPlacementView", uikit_runtime)
            self.assertIn("row.distribution = .fillEqually", uikit_runtime)
            self.assertIn("private func makeOverlay(_ spec: HTMLToIOSNodeSpec) -> UIView", uikit_runtime)
            self.assertIn("private func attachOverlayChildren(_ spec: HTMLToIOSNodeSpec, to parent: UIView)", uikit_runtime)
            self.assertIn("$0.style.nativePaintOrder ?? 0", uikit_runtime)
            self.assertIn("spec.style.clipsContent == true || spec.style.clipsOwnContent == true", uikit_runtime)
            self.assertIn("childSpec.style.offsetX ?? 0", uikit_runtime)
            self.assertIn("let renderedView = wrapInMargins(styledView, spec: spec)", uikit_runtime)
            self.assertIn("private func wrapInMargins(_ view: UIView, spec: HTMLToIOSNodeSpec) -> UIView", uikit_runtime)
            self.assertIn("private func applyMotion(_ spec: HTMLToIOSNodeSpec, to view: UIView)", uikit_runtime)
            self.assertIn('CABasicAnimation(keyPath: "transform.rotation")', uikit_runtime)

    def test_system_safe_area_preserves_source_content_origin_without_resizing_scroll_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            root_node["layout"]["rect"].update({"height": 852})
            status = node("home.chrome", root_node["id"], "container", "9:41  ● ◒ ▰")
            status["source"]["selector"] = "body > main > div.status"
            status["source"]["domId"] = None
            status["source"]["runtimeId"] = "node-status"
            status["layout"]["rect"] = {"x": 0, "y": 0, "width": 393, "height": 52}
            payload["screens"][0]["nodes"].append(status)
            payload["screens"][0]["systemChrome"] = {
                "statusBar": "native", "navigationBar": "none", "homeIndicator": "native",
            }
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            self.assertEqual(generated["screens"][0]["sourceStatusBarHeight"], 52)
            self.assertEqual(generated["screens"][0]["safeArea"]["owner"], "system")
            self.assertFalse(generated["screens"][0]["safeArea"]["subtractFromContainerDimensions"])
            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('if let sourceStatusBarHeight = screen.sourceStatusBarHeight', runtime)
            self.assertIn(".padding(.top, sourceStatusBarHeight)", runtime)
            self.assertIn(".ignoresSafeArea(.container, edges: .top)", runtime)
            self.assertIn(".safeAreaInset(edge: .top, spacing: 0)", runtime)
            uikit_dir = root / "uikit-out"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("let customTopBarOwnsStatusArea = screen.sourceStatusBarHeight == 0", uikit_runtime)
            self.assertIn("scroll.contentInsetAdjustmentBehavior = customTopBarOwnsStatusArea", uikit_runtime)
            self.assertIn('(screen.safeArea.contentInsetAdjustment == "never" ? .never : .automatic)', uikit_runtime)
            self.assertIn("scroll.topAnchor.constraint(equalTo: view.topAnchor)", uikit_runtime)
            self.assertIn("scroll.bottomAnchor.constraint(equalTo: view.bottomAnchor)", uikit_runtime)
            self.assertIn("sourceTopCalibration = CGFloat(sourceStatusBarHeight) - view.safeAreaInsets.top", uikit_runtime)
            self.assertIn("+ sourceTopCalibration", uikit_runtime)
            self.assertIn("let wasAtTop = scroll.contentOffset.y <= -scroll.adjustedContentInset.top + 0.5", uikit_runtime)
            self.assertIn('screen.safeArea.owner == "system" && screen.sourceStatusBarHeight == nil', uikit_runtime)
            self.assertIn("constant: CGFloat(screen.sourceStatusBarHeight ?? 0)", uikit_runtime)
            self.assertIn("CGPoint(x: 0, y: -scroll.adjustedContentInset.top)", uikit_runtime)
            self.assertNotIn("scroll.topAnchor.constraint(equalTo: top.bottomAnchor)", uikit_runtime)
            self.assertNotIn("scroll.bottomAnchor.constraint(equalTo: bottom.topAnchor)", uikit_runtime)

    def test_fixed_artboard_status_bar_height_accounts_for_center_crop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            payload["target"] = {
                "uiStack": "swiftui",
                "viewportPt": {"width": 393, "height": 852},
                "scale": 393 / 318,
            }
            payload.setdefault("source", {})["screenContext"] = {
                "visualRootRect": {"x": 0, "y": 0, "width": 318, "height": 698},
            }
            root_node = payload["screens"][0]["nodes"][0]
            root_node["layout"]["rect"].update({"width": 393, "height": 862.622641509434})
            status = node("home.statusbar", root_node["id"], "container")
            status["layout"]["rect"] = {"x": 0, "y": 0, "width": 393, "height": 51.905660377358494}
            payload["screens"][0]["nodes"].append(status)
            payload["screens"][0]["systemChrome"] = {
                "statusBar": "native", "navigationBar": "none", "homeIndicator": "native",
            }
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            self.assertAlmostEqual(generated["screens"][0]["sourceStatusBarHeight"], 46.5943396226)
            self.assertAlmostEqual(generated["screens"][0]["fixedArtboardCropInsets"][2], 5.311320755)

    def test_fixed_artboard_content_origin_falls_back_to_visual_root_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            payload["target"] = {
                "uiStack": "uikit",
                "viewportPt": {"width": 393, "height": 852},
                "scale": 393 / 318,
            }
            payload.setdefault("source", {})["screenContext"] = {
                "visualRootRect": {"x": 40, "y": 200, "width": 318, "height": 698},
                "contentRootRect": {"x": 40, "y": 242, "width": 318, "height": 520},
            }
            payload["screens"][0]["systemChrome"] = {
                "statusBar": "native", "navigationBar": "none", "homeIndicator": "native",
            }
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir, ui_stack="uikit")
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            self.assertAlmostEqual(generated["screens"][0]["sourceStatusBarHeight"], 46.5943396226)

    def test_symbol_text_is_promoted_to_directional_system_icon(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            arrow = node("home.arrow", root_node["id"], "text", "→")
            arrow["source"]["selector"] = ".suggestion > span.arrow"
            payload["screens"][0]["nodes"].append(arrow)
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_arrow = generated["screens"][0]["root"]["children"][0]
            self.assertEqual(generated_arrow["semantic"], "icon")
            self.assertEqual(generated_arrow["systemImage"], "arrow.right")

    def test_extracted_svg_asset_prevents_approximate_system_symbol_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            upload = node("home.upload", root_node["id"], "icon")
            upload["source"]["selector"] = ".toolbar .upload-icon"
            upload["assetRef"] = "asset.upload"
            payload["screens"][0]["nodes"].append(upload)
            payload["assets"] = [{
                "id": "asset.upload",
                "kind": "inline-svg",
                "source": "inline-svg",
                "markup": '<svg viewBox="0 0 16 16"><path d="M8 1v10M4 5l4-4 4 4"/></svg>',
                "iosName": "html_home_upload",
            }]
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_upload = generated["screens"][0]["root"]["children"][0]
            self.assertEqual(generated_upload["assetName"], "html_home_upload")
            self.assertIsNone(generated_upload["systemImage"])

    def test_noninteractive_inline_text_container_is_flattened_to_rich_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            row = node("home.suggestion", root_node["id"], "container")
            row["layout"]["mode"] = "flex-row"
            arrow = node("home.prefix", row["id"], "text", "建议：")
            text_node = node("home.copy", row["id"], "text", "Suggested copy")
            row["content"]["runs"] = [
                {"kind": "node", "text": "建议：", "nodeId": arrow["id"]},
                {"kind": "node", "text": "Suggested copy", "nodeId": text_node["id"]},
            ]
            payload["screens"][0]["nodes"].extend([row, arrow, text_node])
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_row = generated["screens"][0]["root"]["children"][0]
            self.assertEqual(generated_row["semantic"], "text")
            self.assertEqual(generated_row["children"], [])
            self.assertEqual([run["text"] for run in generated_row["richTextRuns"]], ["建议：", "Suggested copy"])

    def test_compound_control_content_follows_visual_order_without_flattening(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]

            label = node("home.label", root_node["id"], "text", "待检测文案")
            label["layout"].update({
                "mode": "flex-row",
                "rect": {"x": 20, "y": 40, "width": 130, "height": 24},
            })
            label["style"].update({"display": "flex", "flexDirection": "row", "gap": "8px"})
            label["content"]["runs"] = [{
                "kind": "text",
                "text": "待检测文案",
                "nodeId": None,
                "domIndex": 1,
                "rect": {"x": 52, "y": 42, "width": 88, "height": 20},
            }]
            icon = node("home.label-icon", label["id"], "icon")
            icon["layout"]["rect"] = {"x": 20, "y": 40, "width": 24, "height": 24}

            issue_head = node("home.issue-head", root_node["id"], "container")
            issue_head["layout"].update({
                "mode": "flex-row",
                "rect": {"x": 20, "y": 90, "width": 353, "height": 28},
            })
            issue_head["style"].update({"display": "flex", "flexDirection": "row", "gap": "8px"})
            tag = node("home.issue-tag", issue_head["id"], "text", "错别字")
            tag["layout"]["rect"] = {"x": 20, "y": 90, "width": 72, "height": 24}
            tag["style"].update({
                "padding": ["3px", "8px", "3px", "8px"],
                "cornerRadii": ["6px"] * 4,
                "backgroundColor": "rgba(255, 107, 129, 0.12)",
            })
            # Empty styled spans are often classified as text by the extractor.
            severity = node("home.severity", issue_head["id"], "text")
            severity["layout"]["rect"] = {"x": 100, "y": 99, "width": 8, "height": 8}
            severity["style"].update({
                "backgroundColor": "rgb(255, 107, 129)",
                "cornerRadii": ["50%"] * 4,
            })
            position = node("home.position", issue_head["id"], "text", "L1 · P3")
            position["layout"]["rect"] = {"x": 320, "y": 96, "width": 53, "height": 16}
            position["style"]["margin"] = ["0px", "0px", "0px", "204px"]
            issue_head["content"]["runs"] = [
                {"kind": "node", "text": "错别字", "nodeId": tag["id"], "domIndex": 0},
                {"kind": "node", "text": "", "nodeId": severity["id"], "domIndex": 1},
                {"kind": "node", "text": "L1 · P3", "nodeId": position["id"], "domIndex": 2},
            ]

            payload["screens"][0]["nodes"].extend([label, icon, issue_head, tag, severity, position])
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_root = generated["screens"][0]["root"]
            generated_label = next(item for item in generated_root["children"] if item["id"] == label["id"])
            generated_head = next(item for item in generated_root["children"] if item["id"] == issue_head["id"])

            self.assertEqual(
                [(item["kind"], item.get("childID"), item.get("text")) for item in generated_label["contentItems"]],
                [
                    ("child", icon["id"], None),
                    ("text", None, "待检测文案"),
                ],
            )
            self.assertEqual(generated_label["contentItems"][1]["preferredWidth"], 88)
            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("needsTrailingContentSpacer", runtime)
            self.assertTrue(generated_label["contentItems"][1]["singleLine"])
            self.assertEqual(generated_label["contentItems"][1]["gapBefore"], 8)
            self.assertFalse(generated_label["contentItems"][1]["flexibleGapBefore"])
            self.assertEqual(generated_head["semantic"], "container")
            self.assertEqual(
                [item.get("childID") for item in generated_head["contentItems"]],
                [tag["id"], severity["id"], position["id"]],
            )
            self.assertEqual([item["id"] for item in generated_head["children"]], [
                tag["id"], severity["id"], position["id"],
            ])
            self.assertEqual(generated_head["richTextRuns"], [])
            generated_tag = next(item for item in generated_head["children"] if item["id"] == tag["id"])
            generated_severity = next(item for item in generated_head["children"] if item["id"] == severity["id"])
            generated_position = next(item for item in generated_head["children"] if item["id"] == position["id"])
            self.assertEqual(generated_tag["style"]["fixedWidth"], 72)
            self.assertEqual(generated_tag["style"]["fixedHeight"], 24)
            self.assertEqual(generated_severity["style"]["fixedWidth"], 8)
            self.assertEqual(generated_severity["style"]["fixedHeight"], 8)
            self.assertEqual(generated_position["style"]["margin"][3], 0)
            self.assertEqual(generated_head["contentItems"][2]["gapBefore"], 8)
            self.assertTrue(generated_head["contentItems"][2]["flexibleGapBefore"])

            swiftui_runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("let contentItems: [HTMLToIOSContentItemSpec]", (out_dir / MODELS_FILE).read_text(encoding="utf-8"))
            self.assertIn("private var orderedContentItems: some View", swiftui_runtime)
            self.assertIn("contentItemGap(item)", swiftui_runtime)
            self.assertNotIn(".minimumScaleFactor(0.7)", swiftui_runtime)
            self.assertIn(".truncationMode(.tail)", swiftui_runtime)
            self.assertIn("Color.clear.frame(width: gap, height: 0)", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("spec.contentItems.forEach", uikit_runtime)
            self.assertIn("contentItemText(item, spec: spec)", uikit_runtime)
            self.assertIn("addContentGap(item, to: stack, axis: spec.axis)", uikit_runtime)
            self.assertNotIn("label.adjustsFontSizeToFitWidth = true", uikit_runtime)
            self.assertIn("label.lineBreakMode = spec.style.textOverflow", uikit_runtime)
            self.assertNotIn("label.allowsDefaultTighteningForTruncation = true", uikit_runtime)

    def test_explicit_text_lines_and_rich_text_keep_browser_measure_width(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]

            description = node(
                "home.description",
                root_node["id"],
                "text",
                "正在逐句分析 8 个维度 识别错别字、语法、搭配与专名一致性",
                display="block",
            )
            description["layout"]["rect"] = {"x": 64, "y": 100, "width": 264, "height": 44}
            description["style"]["lineHeight"] = "20px"
            description["content"].update({
                "lines": 2,
                "runs": [
                    {
                        "kind": "text",
                        "text": "正在逐句分析 8 个维度",
                        "domIndex": 0,
                        "rect": {"x": 96, "y": 100, "width": 200, "height": 20},
                    },
                    {
                        "kind": "text",
                        "text": "识别错别字、语法、搭配与专名一致性",
                        "domIndex": 1,
                        "rect": {"x": 64, "y": 124, "width": 264, "height": 20},
                    },
                ],
            })

            suggestion = node("home.suggestion", root_node["id"], "text", "建议改为", display="block")
            suggestion["layout"]["rect"] = {"x": 44, "y": 180, "width": 258, "height": 78}
            suggestion["style"]["lineHeight"] = "21px"
            suggestion["content"].update({
                "lines": 3,
                "lineTexts": ["建议改为 「耳戴式」", "保持表达自然", "保持表达自然"],
                "runs": [
                    {"kind": "text", "text": "建议改为 ", "domIndex": 0},
                    {"kind": "node", "text": "「耳戴式」", "nodeId": "home.emphasis", "domIndex": 1},
                    {"kind": "text", "text": " 保持表达自然 保持表达自然", "domIndex": 2},
                ],
            })
            emphasis = node("home.emphasis", suggestion["id"], "text", "「耳戴式」", display="inline")
            emphasis["layout"]["rect"] = {"x": 120, "y": 180, "width": 72, "height": 20}
            emphasis["style"]["fontWeight"] = "700"

            summary = node(
                "home.summary",
                root_node["id"],
                "text",
                "2 处高优 · 3 处中优 · 1 处低优",
                display="block",
            )
            summary["layout"]["rect"] = {"x": 44, "y": 280, "width": 168, "height": 40}
            summary["style"]["lineHeight"] = "20px"
            summary["content"].update({
                "lines": 2,
                "lineTexts": ["2 处高优 · 3 处中优 · 1 处", "低优"],
                "runs": [{
                    "kind": "text",
                    "text": "2 处高优 · 3 处中优 · 1 处低优",
                    "domIndex": 0,
                    "rect": {"x": 44, "y": 280, "width": 168, "height": 40},
                }],
            })

            payload["screens"][0]["nodes"].extend([description, suggestion, emphasis, summary])
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_root = generated["screens"][0]["root"]
            generated_description = next(
                item for item in generated_root["children"] if item["id"] == description["id"]
            )
            generated_suggestion = next(
                item for item in generated_root["children"] if item["id"] == suggestion["id"]
            )
            generated_summary = next(
                item for item in generated_root["children"] if item["id"] == summary["id"]
            )

            self.assertEqual(generated_description["style"]["textMeasureWidth"], 264)
            self.assertEqual(generated_description["style"]["expectedTextLines"], 2)
            self.assertEqual([item["preferredWidth"] for item in generated_description["contentItems"]], [200, 264])
            self.assertTrue(all(item["singleLine"] for item in generated_description["contentItems"]))
            self.assertEqual(generated_description["contentItems"][1]["gapBefore"], 4)
            self.assertEqual(generated_suggestion["style"]["textMeasureWidth"], 258)
            self.assertEqual(generated_suggestion["style"]["expectedTextLines"], 3)
            self.assertEqual(generated_summary["text"], "2 处高优 · 3 处中优 · 1 处\n低优")
            self.assertEqual(
                generated_summary["contentItems"][0]["text"],
                "2 处高优 · 3 处中优 · 1 处\n低优",
            )
            self.assertEqual(generated_summary["style"]["textMeasureWidth"], 168)
            self.assertEqual([run["fontWeight"] for run in generated_suggestion["richTextRuns"]], ["400", "700", "400"])
            self.assertEqual(
                "".join(run["text"] for run in generated_suggestion["richTextRuns"]),
                "建议改为 「耳戴式」\n保持表达自然\n保持表达自然",
            )

            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("let measuredTextWidth = style.textMeasureWidth.map", runtime)
            self.assertIn("private var isMeasuredText: Bool", runtime)
            self.assertIn("htmlToIOSUIFontLineHeight", runtime)
            self.assertIn("let lineBoxLeading = calibratesTextLineBox", runtime)
            self.assertIn("vertical: (style.expectedTextLines ?? 1) > 1", runtime)
            self.assertIn("htmlToIOSFont(size: fontSize", runtime)
            self.assertIn('} else if spec.axis == "vertical" {', runtime)
            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("return runs.map(\\.text).joined()", uikit_runtime)
            self.assertIn("paragraph.minimumLineHeight = targetLineHeight", uikit_runtime)
            self.assertIn("baselineOffset += (targetLineHeight - font.lineHeight) / 2", uikit_runtime)
            self.assertIn("private func nativeFont(size: Double", uikit_runtime)

    def test_overlapping_inline_range_rects_are_one_visual_text_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            score = node("home.score", root_node["id"], "text", "82", display="block")
            score["layout"]["rect"] = {"x": 44, "y": 100, "width": 46, "height": 30}
            score["style"].update({
                "fontSize": "26px",
                "fontWeight": "800",
                "fontFamily": '"JetBrains Mono", monospace',
                "fontStyle": "italic",
            })
            score["content"].update({
                "lines": 2,
                "firstBaselineY": 124,
                "lastBaselineY": 124,
                "lineTexts": ["82", "分"],
                "lineRects": [
                    {"x": 44, "y": 100, "width": 32, "height": 30},
                    {"x": 77, "y": 112, "width": 13, "height": 15},
                ],
                "runs": [
                    {"kind": "text", "text": "82", "domIndex": 0},
                    {"kind": "node", "text": "分", "nodeId": "home.unit", "domIndex": 1},
                ],
            })
            unit = node("home.unit", score["id"], "text", "分", display="inline")
            unit["layout"]["rect"] = {"x": 77, "y": 112, "width": 13, "height": 15}
            unit["style"].update({"fontSize": "13px", "fontWeight": "600"})
            payload["screens"][0]["nodes"].extend([score, unit])

            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_score = next(
                item for item in generated["screens"][0]["root"]["children"] if item["id"] == score["id"]
            )

            self.assertEqual(generated_score["style"]["expectedTextLines"], 1)
            self.assertEqual(generated_score["style"]["fontDesign"], "monospaced")
            self.assertEqual(generated_score["style"]["fontStyle"], "italic")
            self.assertEqual(generated_score["richTextRuns"][0]["fontDesign"], "monospaced")
            self.assertEqual(generated_score["richTextRuns"][0]["fontStyle"], "italic")
            self.assertEqual(generated_score["style"]["textLineLimit"], 1)
            self.assertEqual(generated_score["axis"], "horizontal")
            self.assertIsNone(generated_score["style"]["textMeasureWidth"])
            self.assertEqual([run["text"] for run in generated_score["richTextRuns"]], ["82", "分"])
            self.assertEqual(
                [run["sourceNodeID"] for run in generated_score["richTextRuns"]],
                [score["id"], unit["id"]],
            )
            self.assertNotIn("\n", "".join(run["text"] for run in generated_score["richTextRuns"]))
            self.assertTrue(generated_score["style"]["baselineAligned"])
            self.assertEqual(generated_score["style"]["firstBaselineOffset"], 24)
            self.assertEqual(generated_score["style"]["lastBaselineOffset"], 24)
            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("HStack(alignment: .firstTextBaseline, spacing: 0)", runtime)
            self.assertIn("if spec.style.baselineAligned == true { return .firstTextBaseline }", runtime)
            self.assertIn('if value >= 800 { return .heavy }', runtime)
            self.assertIn('case "monospaced": return .monospaced', runtime)

    def test_responsive_source_does_not_freeze_browser_soft_line_breaks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            payload.setdefault("source", {})["layoutClassification"] = {
                "kind": "responsive-document",
                "conversionStatus": "automatic",
            }
            root_node = payload["screens"][0]["nodes"][0]
            body = node("home.body", root_node["id"], "text", "一段需要响应式换行的正文内容")
            body["layout"]["rect"] = {"x": 20, "y": 40, "width": 180, "height": 44}
            body["content"].update({
                "lines": 2,
                "lineTexts": ["一段需要响应式", "换行的正文内容"],
                "runs": [{"kind": "text", "text": "一段需要响应式换行的正文内容", "domIndex": 0}],
            })
            payload["screens"][0]["nodes"].append(body)
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_body = generated["screens"][0]["root"]["children"][0]
            self.assertEqual(generated_body["text"], "一段需要响应式换行的正文内容")
            self.assertNotIn("\n", "".join(run["text"] for run in generated_body["richTextRuns"]))

    def test_resolved_font_contract_uses_generic_fallback_and_safe_ios_native_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            fallback = node("home.fallback", root_node["id"], "text", "Fallback")
            fallback["style"]["fontFamily"] = '"Missing Font", monospace'
            fallback["content"]["fontResolution"] = {
                "resolvedFamily": "monospace",
                "status": "generic-fallback",
                "failedFamilies": ["missing font"],
            }
            local = node("home.local", root_node["id"], "text", "Local")
            local["style"].update({"fontFamily": "Arial", "fontWeight": "700", "fontStyle": "italic"})
            local["content"]["fontResolution"] = {
                "resolvedFamily": "arial",
                "status": "system-local",
                "failedFamilies": [],
            }
            payload["screens"][0]["nodes"].extend([fallback, local])
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            by_id = {item["id"]: item for item in generated["screens"][0]["root"]["children"]}
            self.assertEqual(by_id[fallback["id"]]["style"]["fontDesign"], "monospaced")
            self.assertEqual(by_id[fallback["id"]]["style"]["fontResolutionStatus"], "generic-fallback")
            self.assertEqual(by_id[fallback["id"]]["style"]["fontFailedFamilies"], ["missing font"])
            self.assertIsNone(by_id[fallback["id"]]["style"]["fontNativeName"])
            self.assertEqual(by_id[local["id"]]["style"]["fontNativeName"], "Arial-BoldItalicMT")
            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("Font.custom(nativeName, fixedSize: size)", runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("UIFont(name: nativeName, size: size)", uikit_runtime)


    def test_computed_flex_direction_overrides_absolute_layout_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            footer = node("home.footer", root_node["id"], "container")
            footer["layout"]["mode"] = "absolute"
            footer["style"].update({"display": "flex", "flexDirection": "row-reverse"})
            first = node("home.first", footer["id"], "button", "First")
            second = node("home.second", footer["id"], "button", "Second")
            payload["screens"][0]["nodes"].extend([footer, first, second])
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_footer = generated["screens"][0]["root"]["children"][0]
            self.assertEqual(generated_footer["axis"], "horizontal")
            self.assertEqual([child["id"] for child in generated_footer["children"]], ["home.second", "home.first"])

    def test_hidden_named_overlay_footer_is_not_promoted_to_bottom_bar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            hidden = node("home.fs-foot", root_node["id"], "footer")
            hidden["source"]["selector"] = "#fullscreen-overlay .fs-foot"
            hidden["layout"].update({
                "position": "absolute",
                "rect": {"x": 0, "y": 778, "width": 393, "height": 74},
            })
            hidden["state"] = {"initiallyVisible": False}
            payload["screens"][0]["nodes"].append(hidden)
            path = root / "home.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            self.run_generator([path], out_dir)
            generated = json.loads((out_dir / PAYLOAD).read_text(encoding="utf-8"))
            self.assertIsNone(generated["screens"][0]["bottomBar"])

    def test_structured_css_styles_and_data_uri_assets_are_shared_by_both_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("styles")
            root_node = payload["screens"][0]["nodes"][0]
            root_node["style"].update({
                "backgroundImage": "linear-gradient(90deg, rgb(255, 0, 0) 0%, rgba(0, 0, 255, 0.5) 100%)",
                "borderWidths": ["2px", "2px", "2px", "2px"],
                "borderColors": ["rgb(10, 20, 30)"] * 4,
                "borderStyles": ["dashed"] * 4,
                "boxShadow": "rgba(0, 0, 0, 0.25) 0px 4px 12px 2px",
                "opacity": "0.75",
                "overflowX": "hidden",
            })
            image = node("styles.image", root_node["id"], "image")
            image["assetRef"] = "asset.data"
            payload["screens"][0]["nodes"].append(image)
            payload["assets"] = [{
                "id": "asset.data",
                "kind": "image",
                "source": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                "iosName": "html_inline_pixel",
            }]
            path = root / "styles.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir)
            generated = json.loads((swiftui_dir / PAYLOAD).read_text(encoding="utf-8"))
            style = generated["screens"][0]["root"]["style"]
            self.assertEqual(style["gradientKind"], "linear")
            self.assertEqual(style["gradientAngle"], 90)
            self.assertEqual(style["gradientLocations"], [0, 1])
            self.assertEqual(style["borderWidth"], 2)
            self.assertEqual(style["borderStyle"], "dashed")
            self.assertEqual(style["opacity"], 0.75)
            self.assertEqual(style["shadowRadius"], 6)
            self.assertTrue(style["clipsContent"])
            self.assertTrue((swiftui_dir / ASSET_CATALOG / "html_inline_pixel.imageset" / "html_inline_pixel.png").is_file())
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("HTMLToIOSBorderModifier", swiftui_runtime)
            self.assertIn("gradientAngle", swiftui_runtime)
            self.assertIn("else if let fixedHeight", swiftui_runtime)
            self.assertIn(".frame(minWidth: minWidth, idealWidth: idealWidth, maxWidth: maxWidth, alignment: alignment)", swiftui_runtime)
            self.assertIn("contentAlignment: contentFrameAlignment", swiftui_runtime)
            self.assertIn("alignment: childSlotAlignment(child)", swiftui_runtime)
            self.assertIn("private var contentHorizontalAlignment: HorizontalAlignment", swiftui_runtime)
            self.assertNotIn("if fixedWidth != nil || fixedHeight != nil", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            uikit_navigation = (uikit_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("CAGradientLayer", uikit_runtime)
            self.assertIn("spec.style.gradientCenterX ?? 0.5", uikit_runtime)
            self.assertIn("let isGradientText = view is UILabel", uikit_runtime)
            self.assertIn("attributedText.addAttribute(.foregroundColor", uikit_runtime)
            self.assertIn("html-to-ios-border", uikit_runtime)
            self.assertIn("HTMLToIOSUIKitState", uikit_runtime)
            self.assertIn("toggle-selection", uikit_runtime)
            self.assertIn("scroll.backgroundColor = view.backgroundColor", uikit_runtime)
            self.assertIn("content.translatesAutoresizingMaskIntoConstraints = false", uikit_runtime)
            self.assertIn("spec.richTextRuns?.isEmpty == false && !hasInteractiveInlineChild", uikit_runtime)
            self.assertIn("usesRichText: false", uikit_runtime)
            self.assertIn('case "start", "flex-start", "top": stack.alignment = .top', uikit_runtime)
            self.assertIn('case "start", "flex-start", "left": stack.alignment = .leading', uikit_runtime)
            self.assertIn("stack.setCustomSpacing(gap, after: previous)", uikit_runtime)
            self.assertIn('screen.safeArea.owner == "system" && screen.sourceStatusBarHeight == nil', uikit_runtime)
            self.assertIn('? view.safeAreaLayoutGuide.topAnchor', uikit_runtime)
            self.assertIn(': view.topAnchor', uikit_runtime)
            self.assertIn("hasNestedScrollOwner", uikit_runtime)
            self.assertIn("content.bottomAnchor.constraint(equalTo: contentBottom)", uikit_runtime)
            self.assertIn("content.bottomAnchor.constraint(lessThanOrEqualTo: contentBottom)", uikit_runtime)
            self.assertIn("configureNavigationBar(for: controller, in: navigation)", uikit_navigation)
            self.assertIn("generatedShowsNavigationBar", uikit_navigation)

    def test_text_behavior_generates_native_editable_and_readonly_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            field = node("home.name", root_node["id"], "text-input")
            field["content"].update({"value": "Sky", "placeholder": "Name"})
            field["textBehavior"] = {
                "role": "input", "nativeControl": "text-field", "editable": False,
                "readOnly": True, "selectable": True, "multiline": False,
                "scrollable": False, "secure": False, "maxLength": 40,
                "fieldID": "profile.name", "autofocus": True, "returnKey": "next",
                "autocapitalization": "words", "autocorrection": False,
                "validation": "required",
                "placeholderStyle": {
                    "fontSize": "14px", "fontWeight": "500",
                    "foreground": "rgba(80, 90, 110, 0.65)",
                    "lineHeight": "20px", "letterSpacing": "0.2px", "opacity": "0.8",
                },
            }
            field["style"]["padding"] = ["10px", "14px", "10px", "14px"]
            field["controlVisualStates"] = {
                "focused": {
                    "color": "rgb(20, 30, 40)", "backgroundColor": "rgb(255, 255, 255)",
                    "backgroundImage": "none",
                    "borderTopColor": "rgb(80, 90, 240)", "borderRightColor": "rgb(80, 90, 240)",
                    "borderBottomColor": "rgb(80, 90, 240)", "borderLeftColor": "rgb(80, 90, 240)",
                    "borderTopWidth": "2px", "borderRightWidth": "2px",
                    "borderBottomWidth": "2px", "borderLeftWidth": "2px",
                    "borderRadius": "10px", "boxShadow": "0 2px 8px rgba(80, 90, 240, 0.25)",
                    "opacity": "1", "transform": "scale(1.01)",
                },
            }
            field["dataBinding"] = {
                "sourceID": "profile", "itemIDKey": "id", "stateRole": "content",
                "pagination": "none", "ownership": "external",
                "requiresViewModel": True, "snapshotIsSampleData": True,
            }
            notes = node("home.notes", root_node["id"], "text-area", "Line one\nLine two")
            notes["textBehavior"] = {
                "role": "input", "nativeControl": "text-view", "editable": True,
                "readOnly": False, "selectable": True, "multiline": True,
                "scrollable": True, "secure": False, "maxLength": 200,
            }
            payload["screens"][0]["nodes"].extend([field, notes])
            path = root / "text-controls.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir)
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('case "search-bar":', swiftui_runtime)
            self.assertIn('Image(systemName: "magnifyingglass")', swiftui_runtime)
            self.assertIn('case "text-field", "input", "search-field", "text-input", "search-input", "number-input":', swiftui_runtime)
            self.assertIn("TextEditor(text:", swiftui_runtime)
            self.assertIn(".textFieldStyle(.plain)", swiftui_runtime)
            self.assertIn("maxLength: spec.textBehavior?.maxLength", swiftui_runtime)
            self.assertIn(".scrollDismissesKeyboard(.interactively)", swiftui_runtime)
            self.assertIn("HTMLToIOSInputPolicyModifier", swiftui_runtime)
            self.assertIn("prompt: inputPrompt", swiftui_runtime)
            self.assertIn(".padding(.horizontal, -5)", swiftui_runtime)
            self.assertIn("HTMLToIOSControlButtonStyle", swiftui_runtime)
            self.assertIn("activeControlVisualStyle", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("let textView = HTMLToIOSManagedTextView()", uikit_runtime)
            self.assertIn("textView.isEditable = spec.textBehavior?.editable == true", uikit_runtime)
            self.assertIn("field.borderStyle = .none", uikit_runtime)
            self.assertIn("scroll.keyboardDismissMode = .interactive", uikit_runtime)
            self.assertIn("field.returnKeyType = returnKeyType", uikit_runtime)
            self.assertIn("let field = HTMLToIOSInsetTextField()", uikit_runtime)
            self.assertIn("field.attributedPlaceholder = attributedPlaceholder(spec)", uikit_runtime)
            self.assertIn("field.contentInsets = contentInsets(spec)", uikit_runtime)
            self.assertIn("field.markedTextRange == nil", uikit_runtime)
            self.assertIn("installControlVisualStates", uikit_runtime)
            self.assertIn("HTMLToIOSStatefulButton", uikit_runtime)
            self.assertIn("htmlToIOSContentInsets", uikit_runtime)
            self.assertNotIn("button.contentEdgeInsets", uikit_runtime)
            generated = json.loads((uikit_dir / PAYLOAD).read_text(encoding="utf-8"))
            generated_field = generated["screens"][0]["root"]["children"][0]
            self.assertEqual(generated_field["textBehavior"]["initialValue"], "Sky")
            self.assertFalse(generated_field["textBehavior"]["editable"])
            self.assertEqual(generated_field["textBehavior"]["fieldID"], "profile.name")
            self.assertEqual(generated_field["textBehavior"]["placeholderStyle"]["fontSize"], 14)
            self.assertEqual(generated_field["textBehavior"]["placeholderStyle"]["fontWeight"], "500")
            self.assertEqual(generated_field["controlVisualStates"]["focused"]["borderWidth"], 2)
            self.assertAlmostEqual(generated_field["controlVisualStates"]["focused"]["scale"], 1.01)
            self.assertEqual(generated_field["dataBinding"]["sourceID"], "profile")
            self.assertTrue(generated_field["dataBinding"]["requiresViewModel"])


if __name__ == "__main__":
    unittest.main()
