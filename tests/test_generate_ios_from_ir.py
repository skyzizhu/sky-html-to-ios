#!/usr/bin/env python3

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_ios_from_ir.py"
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
    ) -> subprocess.CompletedProcess[str]:
        command = ["python3", str(SCRIPT)]
        for path in paths:
            command.extend(["--ir", str(path)])
        command.extend(["--out-dir", str(out_dir), "--ui-stack", ui_stack])
        if naming_plan:
            command.extend(["--naming-plan", str(naming_plan)])
        if architecture_plan:
            command.extend(["--architecture-plan", str(architecture_plan)])
        if out_dir.parts[-2:] != ("Generated", "HTMLToIOS"):
            command.append("--allow-nonstandard-output")
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if expect_success and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

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
                        },
                        "reusableContent": {"sections": [], "usesReuse": True},
                        "leafComponents": [],
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
            self.assertIn("let usesOuterScroll = screen.contentContainer.kind == \"scroll-view\"", runtime)

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

            option_nodes = []
            for parent, prefix in ((segmented, "segment"), (select, "choice")):
                for index, title in enumerate(("First", "Second")):
                    option = node(
                        f"controls.{prefix}-{index}",
                        parent["id"],
                        "option",
                        title,
                    )
                    option["state"] = {"selected": index == 1}
                    option_nodes.append(option)

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
                *option_nodes,
            ])
            path = root / "controls.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            swiftui_dir = root / "swiftui"
            self.run_generator([path], swiftui_dir)
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
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            for expected in (
                "Slider(",
                "Stepper(",
                ".pickerStyle(.segmented)",
                ".pickerStyle(.menu)",
                "DatePicker(",
                "ColorPicker(",
                "ProgressView(",
                "HTMLToIOSCheckboxToggleStyle",
                "HTMLToIOSRadioToggleStyle",
            ):
                self.assertIn(expected, swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            for expected in (
                "UISlider()",
                "UIStepper()",
                "UISegmentedControl(items:",
                "UIDatePicker()",
                "UIColorWell()",
                "UIProgressView(progressViewStyle:",
                "UIMenu(children:",
                'spec.semantic == "radio" ? "circle" : "square"',
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
                "(style.flexGrow ?? 0) > 0 || (constrainsPreferredWidth && (style.widthFraction ?? 0) > 0.88)",
                runtime_text,
            )
            self.assertNotIn(".frame(minWidth: minWidth, idealWidth: idealWidth)\n            .frame(maxWidth:", runtime_text)
            self.assertIn("hiddenNodeIDs = nextHiddenNodeIDs", runtime_text)
            self.assertNotIn("UIViewRepresentable", runtime_text)
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
            uikit_root = (uikit_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("generatedState.perform(action)", uikit_root)
            self.assertIn("generatedState.sizeOverrides[presentation.node.id]?.height", uikit_root)

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
                "backgroundImage": "radial-gradient(circle, rgb(155, 138, 255), rgb(58, 43, 204))",
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

            self.assertEqual(generated_rail["style"]["scrollAxis"], "horizontal")
            self.assertEqual(generated_item["style"]["fixedWidth"], 88)
            self.assertEqual(generated_item["style"]["fixedHeight"], 40)
            self.assertTrue(generated_item["style"]["preservesIntrinsicWidth"])
            self.assertEqual(generated_label["style"]["textLineLimit"], 1)
            self.assertTrue(generated_label["style"]["preservesIntrinsicWidth"])
            self.assertEqual(generated_icon_box["style"]["fixedWidth"], 40)
            self.assertEqual(generated_icon_box["style"]["fixedHeight"], 40)
            self.assertEqual(generated_icon_box["style"]["aspectRatio"], 1)
            self.assertEqual(generated_row["style"]["minHeight"], 72)
            self.assertEqual(generated_orb["style"]["fixedWidth"], 104)
            self.assertEqual(generated_orb["style"]["fixedHeight"], 104)
            self.assertEqual(generated_orb["style"]["aspectRatio"], 1)
            self.assertEqual(generated_orb["style"]["cornerRadius"], 52)

            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("ScrollView(.vertical)", swiftui_runtime)
            self.assertIn("private var scrollContainer: some View", swiftui_runtime)
            self.assertIn(".lineLimit(style.textLineLimit)", swiftui_runtime)
            self.assertIn("HTMLToIOSAspectRatioModifier", swiftui_runtime)
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
            self.assertEqual(generated_ring["children"][0]["assetName"], "html_page3_ring")
            self.assertEqual(generated_ring["style"]["fixedWidth"], 88)
            self.assertEqual(generated_ring["style"]["fixedHeight"], 88)
            self.assertEqual(
                [item["id"] for item in generated_ring["overlayChildren"]],
                [value["id"]],
            )
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
            self.assertIn(".offset(y: proxy.safeAreaInsets.bottom)", runtime)
            self.assertIn("presentationDetents", root_source)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('screen.bottomBarPlacement == "safe-area-inset" ? (generatedBottomBar?.bounds.height ?? 0) : 0', uikit_runtime)
            self.assertIn('screen.bottomBarPlacement == "viewport-overlay"\n                        ? view.bottomAnchor', uikit_runtime)

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
            self.assertIn("spec.style.baselineAligned == true ? .firstBaseline : .center", uikit_runtime)

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
            })
            artwork_dot["content"]["isDecorative"] = True

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
                "rect": {"x": 260, "y": 280, "width": 140, "height": 140},
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
            payload["motions"] = [{
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
            }]
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
            self.assertEqual(generated_mixed_card["axis"], "vertical")
            self.assertIsNone(generated_mixed_card["style"]["fixedWidth"])
            self.assertIsNone(generated_mixed_card["style"]["fixedHeight"])
            self.assertEqual([item["id"] for item in generated_mixed_card["children"]], [mixed_label["id"]])
            self.assertEqual(
                [item["id"] for item in generated_mixed_card["overlayChildren"]],
                [mixed_glow["id"]],
            )
            self.assertEqual(generated_grid["axis"], "grid")
            self.assertEqual(generated_grid["style"]["gridColumnCount"], 4)
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('if spec.axis == "grid" {', swiftui_runtime)
            self.assertIn("LazyVGrid(columns: gridColumns", swiftui_runtime)
            self.assertIn("ForEach(spec.overlayChildren)", swiftui_runtime)
            self.assertIn("private struct HTMLToIOSOverlayClipModifier: ViewModifier", swiftui_runtime)
            self.assertIn("if style.clipsContent == true", swiftui_runtime)
            self.assertIn("private struct HTMLToIOSMarginModifier: ViewModifier", swiftui_runtime)
            self.assertIn(".modifier(HTMLToIOSMarginModifier(style: spec.style))", swiftui_runtime)
            self.assertIn("endRadius: radialEndRadius(proxy.size)", swiftui_runtime)
            self.assertIn("sqrt(size.width * size.width + size.height * size.height) / 2", swiftui_runtime)
            self.assertIn(".offset(x: style.offsetX ?? 0, y: style.offsetY ?? 0)", swiftui_runtime)
            self.assertIn("private struct HTMLToIOSMotionModifier: ViewModifier", swiftui_runtime)
            self.assertIn("HTMLToIOSLaunchConfiguration.motionProgress", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('let content = spec.axis == "grid" ? makeGrid(spec) : makeStack(spec)', uikit_runtime)
            self.assertIn("private func makeGrid(_ spec: HTMLToIOSNodeSpec) -> UIStackView", uikit_runtime)
            self.assertIn("row.distribution = .fillEqually", uikit_runtime)
            self.assertIn("private func makeOverlay(_ spec: HTMLToIOSNodeSpec) -> UIView", uikit_runtime)
            self.assertIn("private func attachOverlayChildren(_ spec: HTMLToIOSNodeSpec, to parent: UIView)", uikit_runtime)
            self.assertIn("childSpec.style.offsetX ?? 0", uikit_runtime)
            self.assertIn("let renderedView = wrapInMargins(view, spec: spec)", uikit_runtime)
            self.assertIn("private func wrapInMargins(_ view: UIView, spec: HTMLToIOSNodeSpec) -> UIView", uikit_runtime)
            self.assertIn("private func applyMotion(_ spec: HTMLToIOSNodeSpec, to view: UIView)", uikit_runtime)
            self.assertIn('CABasicAnimation(keyPath: "transform.rotation")', uikit_runtime)

    def test_system_safe_area_preserves_source_content_origin_without_resizing_scroll_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = ir("home")
            root_node = payload["screens"][0]["nodes"][0]
            root_node["layout"]["rect"].update({"height": 852})
            status = node("home.statusbar", root_node["id"], "container")
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
            self.assertIn("if let sourceStatusBarHeight = screen.sourceStatusBarHeight", runtime)
            self.assertIn(".padding(.top, sourceStatusBarHeight)", runtime)
            self.assertIn(".ignoresSafeArea(.container, edges: .top)", runtime)
            self.assertIn(".safeAreaInset(edge: .top, spacing: 0)", runtime)
            uikit_dir = root / "uikit-out"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("scroll.contentInsetAdjustmentBehavior = screen.safeArea.contentInsetAdjustment == \"never\" ? .never : .automatic", uikit_runtime)
            self.assertIn("scroll.topAnchor.constraint(equalTo: view.topAnchor)", uikit_runtime)
            self.assertIn("scroll.bottomAnchor.constraint(equalTo: view.bottomAnchor)", uikit_runtime)
            self.assertIn("let topCalibration = CGFloat(sourceStatusBarHeight) - view.safeAreaInsets.top", uikit_runtime)
            self.assertIn("scroll.contentInset.top = topCalibration", uikit_runtime)
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
            self.assertIn(".minimumScaleFactor(0.7)", swiftui_runtime)
            self.assertIn(".allowsTightening(true)", swiftui_runtime)
            self.assertIn("Color.clear.frame(width: gap, height: 0)", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("spec.contentItems.forEach", uikit_runtime)
            self.assertIn("contentItemText(item, spec: spec)", uikit_runtime)
            self.assertIn("addContentGap(item, to: stack, axis: spec.axis)", uikit_runtime)
            self.assertIn("label.adjustsFontSizeToFitWidth = true", uikit_runtime)
            self.assertIn("label.allowsDefaultTighteningForTruncation = true", uikit_runtime)

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
            self.assertNotIn("\n", "".join(run["text"] for run in generated_score["richTextRuns"]))
            self.assertTrue(generated_score["style"]["baselineAligned"])
            self.assertEqual(generated_score["style"]["firstBaselineOffset"], 24)
            self.assertEqual(generated_score["style"]["lastBaselineOffset"], 24)
            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("HStack(alignment: .firstTextBaseline, spacing: 0)", runtime)
            self.assertIn("if spec.style.baselineAligned == true { return .firstTextBaseline }", runtime)
            self.assertIn('if value >= 800 { return .heavy }', runtime)
            self.assertIn('case "monospaced": return .monospaced', runtime)

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

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("CAGradientLayer", uikit_runtime)
            self.assertIn("html-to-ios-border", uikit_runtime)
            self.assertIn("HTMLToIOSUIKitState", uikit_runtime)
            self.assertIn("toggle-selection", uikit_runtime)
            self.assertIn("scroll.backgroundColor = view.backgroundColor", uikit_runtime)

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
