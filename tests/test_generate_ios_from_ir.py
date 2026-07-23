#!/usr/bin/env python3

from __future__ import annotations

import json
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
    ) -> subprocess.CompletedProcess[str]:
        command = ["python3", str(SCRIPT)]
        for path in paths:
            command.extend(["--ir", str(path)])
        command.extend(["--out-dir", str(out_dir), "--ui-stack", ui_stack])
        if naming_plan:
            command.extend(["--naming-plan", str(naming_plan)])
        if out_dir.parts[-2:] != ("Generated", "HTMLToIOS"):
            command.append("--allow-nonstandard-output")
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if expect_success and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

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
            self.assertIn("constrainsPreferredWidth: spec.children.isEmpty || isNativeControl", runtime_text)
            self.assertIn("enforcesPreferredWidth: isNativeControl", runtime_text)
            self.assertIn("(enforcesPreferredWidth || style.resistsCompression == true)", runtime_text)
            self.assertIn("constrainsPreferredWidth && (style.widthFraction ?? 0) > 0.88 ? .infinity : nil", runtime_text)
            self.assertNotIn(".frame(minWidth: minWidth, idealWidth: idealWidth)\n            .frame(maxWidth:", runtime_text)
            self.assertIn("hiddenNodeIDs = nextHiddenNodeIDs", runtime_text)
            self.assertNotIn("UIViewRepresentable", runtime_text)
            self.assertNotIn("sizeThatFits(_ proposal:", runtime_text)
            self.assertIn(".fixedSize(horizontal: style.preservesIntrinsicWidth == true, vertical: false)", runtime_text)
            self.assertNotIn("accessibilityElement(children: .contain)", runtime_text)
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
            row["style"]["flexDirection"] = "row"
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
            self.assertTrue(generated_item["style"]["preservesIntrinsicWidth"])
            self.assertEqual(generated_label["style"]["textLineLimit"], 1)
            self.assertTrue(generated_label["style"]["preservesIntrinsicWidth"])
            self.assertEqual(generated_icon_box["style"]["fixedWidth"], 40)
            self.assertEqual(generated_icon_box["style"]["fixedHeight"], 40)
            self.assertEqual(generated_icon_box["style"]["aspectRatio"], 1)
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
            self.assertIn("directionalLockEnabled = true", uikit_runtime)
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
            self.assertEqual(generated_ring["axis"], "overlay")
            self.assertEqual(generated_ring["children"][0]["assetName"], "html_page3_ring")
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
                "bottomBar": {"nodeId": bottom["id"], "kind": "bottom-action-bar", "confidence": 0.9},
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
            self.assertIn("presentationDetents", root_source)

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
            popover = next(item for item in payload["screens"][0]["nodes"] if item["id"] == "home.sheet")
            popover["layout"]["rect"] = {"x": 24, "y": 580, "width": 345, "height": 190}
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
            self.assertEqual(presentation["sourceRect"], [24, 580, 345, 190])
            self.assertEqual(presentation["node"]["style"]["opacity"], 1)
            self.assertEqual(presentation["node"]["style"]["fixedWidth"], 345)
            self.assertEqual(presentation["node"]["style"]["fixedHeight"], 190)
            root_source = (swiftui_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("systemPopoverIsPresented", root_source)
            self.assertIn("customPopoverOverlay", root_source)
            self.assertIn(".position(x: centerX, y: centerY)", root_source)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_root = (uikit_dir / NAVIGATION_FILE).read_text(encoding="utf-8")
            self.assertIn("HTMLToIOSGeneratedCustomOverlayController", uikit_root)
            self.assertIn("presentation.usesCustomOverlay", uikit_root)
            self.assertIn("panel.leadingAnchor.constraint", uikit_root)

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

            payload["screens"][0]["nodes"].extend([artwork, artwork_child, grid_action])
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
            generated_grid = next(item for item in generated_root["children"] if item["id"] == grid_action["id"])
            self.assertEqual(generated_artwork["style"]["preferredHeight"], 224)
            self.assertEqual(generated_artwork["children"][0]["style"]["fixedWidth"], 253)
            self.assertEqual(generated_artwork["children"][0]["style"]["fixedHeight"], 224)
            self.assertEqual(generated_grid["axis"], "grid")
            self.assertEqual(generated_grid["style"]["gridColumnCount"], 4)
            swiftui_runtime = (swiftui_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('if spec.axis == "grid" {', swiftui_runtime)
            self.assertIn("LazyVGrid(columns: gridColumns", swiftui_runtime)

            uikit_dir = root / "uikit"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn('let content = spec.axis == "grid" ? makeGrid(spec) : makeStack(spec)', uikit_runtime)
            self.assertIn("private func makeGrid(_ spec: HTMLToIOSNodeSpec) -> UIStackView", uikit_runtime)
            self.assertIn("row.distribution = .fillEqually", uikit_runtime)

    def test_system_safe_area_owns_status_bar_without_source_height_compensation(self) -> None:
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
            self.assertIsNone(generated["screens"][0]["sourceStatusBarHeight"])
            self.assertEqual(generated["screens"][0]["safeArea"]["owner"], "system")
            self.assertFalse(generated["screens"][0]["safeArea"]["subtractFromContainerDimensions"])
            runtime = (out_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("screen.safeArea.owner != \"system\"", runtime)
            self.assertIn(".safeAreaInset(edge: .top, spacing: 0)", runtime)
            uikit_dir = root / "uikit-out"
            self.run_generator([path], uikit_dir, ui_stack="uikit")
            uikit_runtime = (uikit_dir / RUNTIME_FILE).read_text(encoding="utf-8")
            self.assertIn("scroll.contentInsetAdjustmentBehavior = screen.safeArea.contentInsetAdjustment == \"never\" ? .never : .automatic", uikit_runtime)
            self.assertIn("scroll.topAnchor.constraint(equalTo: view.topAnchor)", uikit_runtime)
            self.assertIn("scroll.bottomAnchor.constraint(equalTo: view.bottomAnchor)", uikit_runtime)
            self.assertNotIn("scroll.topAnchor.constraint(equalTo: top.bottomAnchor)", uikit_runtime)
            self.assertNotIn("scroll.bottomAnchor.constraint(equalTo: bottom.topAnchor)", uikit_runtime)

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
            arrow = node("home.arrow", row["id"], "text", "→")
            text_node = node("home.copy", row["id"], "text", "Suggested copy")
            row["content"]["runs"] = [
                {"kind": "node", "text": "→", "nodeId": arrow["id"]},
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
            self.assertEqual([run["text"] for run in generated_row["richTextRuns"]], ["→", "Suggested copy"])

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


if __name__ == "__main__":
    unittest.main()
