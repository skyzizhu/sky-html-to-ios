#!/usr/bin/env python3
"""Inspect the installed iPhoneOS SDK for planned UIKit and SwiftUI symbols."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


DEFAULT_UIKIT_SYMBOLS = [
    "UIControl", "UIButton", "UITextField", "UITextView", "UISwitch", "UISlider",
    "UIStepper", "UIDatePicker", "UIPickerView", "UISegmentedControl", "UIColorWell",
    "UIPageControl", "UIProgressView", "UIActivityIndicatorView", "UIRefreshControl",
    "UIScrollView", "UIStackView", "UITableView", "UICollectionView", "UICalendarView",
    "UIContentUnavailableView", "UINavigationController", "UITabBarController",
    "UISplitViewController", "UIPageViewController", "UISearchController",
    "UIAlertController", "UISheetPresentationController", "UIPopoverPresentationController",
    "UIActivityViewController", "UIDocumentPickerViewController", "UIImagePickerController",
    "UIColorPickerViewController", "UIFontPickerViewController", "UIPrintInteractionController",
]

DEFAULT_SWIFTUI_SYMBOLS = [
    "Button", "TextField", "SecureField", "TextEditor", "Toggle", "Picker", "Slider",
    "Stepper", "DatePicker", "ColorPicker", "ProgressView", "ScrollView", "List",
    "Grid", "LazyVGrid", "TabView", "NavigationStack", "NavigationSplitView",
    "ContentUnavailableView", "sheet", "fullScreenCover", "popover", "alert",
    "confirmationDialog", "presentationDetents", "keyframeAnimator", "phaseAnimator", "visualEffect",
]


def run(*command: str) -> str:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    return completed.stdout.strip()


def version_tuple(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)*", value)
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def declaration_context(text: str, match: re.Match) -> tuple[str, str]:
    lines = text.splitlines()
    line_index = text.count("\n", 0, match.start())
    selected = [lines[line_index]]
    for index in range(line_index - 1, max(-1, line_index - 9), -1):
        line = lines[index].strip()
        if not line:
            break
        if "available" in line.lower() or line.startswith(("@_", "UIKIT_EXTERN", "NS_SWIFT", "API_")):
            selected.insert(0, lines[index])
            continue
        break
    return "\n".join(selected), lines[line_index].strip()


def availability(snippet: str) -> dict:
    introduced = None
    for pattern in (
        r"API_AVAILABLE\s*\(\s*ios\(([0-9.]+)\)",
        r"API_DEPRECATED[^\n]*ios\(([0-9.]+)\s*,",
        r"@available\(iOS\s+([0-9.]+)",
        r"@available\(iOS,\s*deprecated,\s*introduced:\s*([0-9.]+)",
        r"@available\(iOSApplicationExtension\s+([0-9.]+)",
    ):
        match = re.search(pattern, snippet)
        if match:
            introduced = match.group(1)
            break
    deprecated = bool(re.search(r"API_DEPRECATED|API_DEPRECATED_WITH_REPLACEMENT|@available\(iOS[^\n]*deprecated", snippet))
    unavailable = bool(re.search(r"API_UNAVAILABLE\s*\([^)]*ios|@available\(iOS[^\n]*unavailable", snippet))
    return {"introduced": introduced, "deprecated": deprecated, "unavailable": unavailable}


def find_symbol(symbol: str, sources: list[tuple[Path, str]], kind: str) -> dict:
    escaped = re.escape(symbol)
    if kind == "uikit":
        pattern = re.compile(rf"@interface\s+{escaped}\b(?!\s*\()")
    elif symbol and symbol[0].islower():
        pattern = re.compile(rf"\bfunc\s+{escaped}\b")
    else:
        pattern = re.compile(rf"\b(?:struct|class|enum|protocol|typealias)\s+{escaped}\b")
    for path, text in sources:
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        choices = []
        for match in matches:
            snippet, declaration = declaration_context(text, match)
            choices.append((availability(snippet), declaration))
        details, declaration = next(
            ((item, declaration) for item, declaration in choices if not item["deprecated"] and not item["unavailable"]),
            choices[0],
        )
        return {
            "found": True,
            "file": str(path),
            "declaration": declaration,
            **details,
        }
    return {"found": False, "file": None, "declaration": None, "introduced": None, "deprecated": False, "unavailable": False}


def assess(item: dict, minimum_ios: str | None) -> str:
    if not item["found"] or item["unavailable"]:
        return "unavailable"
    if item["deprecated"]:
        return "deprecated"
    introduced = version_tuple(item["introduced"])
    minimum = version_tuple(minimum_ios)
    if introduced and minimum and minimum < introduced:
        return "requires-fallback"
    if introduced:
        return "available"
    return "available-review-version"


def load_sources(paths: list[Path]) -> list[tuple[Path, str]]:
    result = []
    for path in paths:
        try:
            result.append((path, path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum-ios")
    parser.add_argument("--uikit-symbols", help="Comma-separated UIKit symbols")
    parser.add_argument("--swiftui-symbols", help="Comma-separated SwiftUI symbols")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    try:
        sdk_path = Path(run("xcrun", "--sdk", "iphoneos", "--show-sdk-path"))
        sdk_version = run("xcrun", "--sdk", "iphoneos", "--show-sdk-version")
        xcode_version = run("xcodebuild", "-version").replace("\n", "; ")
    except (OSError, subprocess.CalledProcessError) as exc:
        parser.error(f"Unable to query Xcode SDK: {exc}")

    headers = sorted((sdk_path / "System/Library/Frameworks/UIKit.framework/Headers").glob("*.h"))
    swift_interfaces = sorted((sdk_path / "System/Library/Frameworks").glob("**/*.swiftinterface"))
    uikit_sources = load_sources(headers)
    swiftui_sources = load_sources([path for path in swift_interfaces if "SwiftUI" in str(path)])
    uikit_symbols = [item.strip() for item in args.uikit_symbols.split(",")] if args.uikit_symbols else DEFAULT_UIKIT_SYMBOLS
    swiftui_symbols = [item.strip() for item in args.swiftui_symbols.split(",")] if args.swiftui_symbols else DEFAULT_SWIFTUI_SYMBOLS

    result = {
        "schemaVersion": "ios-sdk-report-1.0",
        "xcode": xcode_version,
        "sdk": {"name": "iphoneos", "version": sdk_version, "path": str(sdk_path)},
        "minimumIOS": args.minimum_ios,
        "symbols": {"uikit": {}, "swiftui": {}},
    }
    for kind, symbols, sources in (
        ("uikit", uikit_symbols, uikit_sources),
        ("swiftui", swiftui_symbols, swiftui_sources),
    ):
        for symbol in symbols:
            item = find_symbol(symbol, sources, kind)
            item["status"] = assess(item, args.minimum_ios)
            result["symbols"][kind][symbol] = item

    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
        print(json.dumps({"out": str(args.out.resolve()), "sdk": sdk_version, "uikit": len(uikit_symbols), "swiftui": len(swiftui_symbols)}, indent=2))
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
