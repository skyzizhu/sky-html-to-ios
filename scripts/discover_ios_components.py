#!/usr/bin/env python3
"""Index reusable SwiftUI/UIKit components, tokens, routers, and assets."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SKIP_DIRS = {".git", ".build", "build", "DerivedData", "Pods", "Carthage", "node_modules", "xcuserdata"}
TYPE_PATTERN = re.compile(
    r"(?m)^\s*(?:(open|public|package|internal|fileprivate|private)\s+)?(?:final\s+)?"
    r"(class|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^\{\n]+))?"
)


def swift_files(root: Path):
    for path in root.rglob("*.swift"):
        if not any(part in SKIP_DIRS for part in path.parts):
            yield path


def discover_paths(root: Path, pattern: str):
    return [path for path in root.rglob(pattern) if not any(part in SKIP_DIRS for part in path.parts)]


def classify(name: str, bases: list[str], text: str) -> tuple[str | None, int, list[str]]:
    joined = " ".join(bases)
    evidence = []
    if "UIViewController" in joined or name.endswith("ViewController"):
        return "uikit-view-controller", 90, ["UIViewController inheritance/name"]
    if any(base in joined for base in ("UITableViewCell", "UICollectionViewCell")):
        return "uikit-cell", 92, ["reusable cell inheritance"]
    if "UIControl" in joined:
        return "uikit-control", 92, ["UIControl inheritance"]
    if re.search(r"\bUIView\b", joined):
        return "uikit-view", 82, ["UIView inheritance"]
    if re.search(r"\bView\b", joined) and "var body" in text:
        score = 84
        evidence.append("SwiftUI View conformance and body")
        if re.search(r"\b(public|package)\s+init\b", text): score += 6; evidence.append("externally reusable initializer")
        return "swiftui-view", score, evidence
    if "ButtonStyle" in joined:
        return "swiftui-style", 88, ["ButtonStyle conformance"]
    if "ViewModifier" in joined:
        return "swiftui-modifier", 88, ["ViewModifier conformance"]
    if re.search(r"Router|Coordinator|Navigator", name, re.IGNORECASE):
        return "navigation", 78, ["router/coordinator naming"]
    if re.search(r"Theme|DesignSystem|Palette|Typography|Token", name, re.IGNORECASE):
        return "design-token", 75, ["design-system naming"]
    return None, 0, []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Project root is not a directory: {root}")

    components = []
    for path in swift_files(root):
        text = path.read_text(encoding="utf-8", errors="ignore")[:1_000_000]
        imports = re.findall(r"(?m)^\s*import\s+([A-Za-z0-9_]+)", text)
        for match in TYPE_PATTERN.finditer(text):
            access, declaration, name, inheritance = match.groups()
            bases = [item.strip() for item in (inheritance or "").split(",") if item.strip()]
            kind, score, evidence = classify(name, bases, text[match.start(): match.start() + 12_000])
            if not kind:
                continue
            components.append({
                "name": name,
                "kind": kind,
                "declaration": declaration,
                "access": access or "internal",
                "inheritsOrConforms": bases,
                "file": str(path.relative_to(root)),
                "imports": imports,
                "reusabilityScore": min(score, 100),
                "evidence": evidence,
            })

    assets = []
    for catalog in root.rglob("*.xcassets"):
        if any(part in SKIP_DIRS for part in catalog.parts):
            continue
        for item in catalog.rglob("Contents.json"):
            if item.parent == catalog:
                continue
            suffix = item.parent.suffix.lower()
            kind = {".colorset": "color", ".imageset": "image", ".symbolset": "symbol", ".dataset": "data"}.get(suffix, suffix.lstrip(".") or "asset")
            assets.append({"name": item.parent.stem, "kind": kind, "path": str(item.parent.relative_to(root))})

    token_signals = []
    for component in components:
        if component["kind"] == "design-token":
            token_signals.append({"name": component["name"], "file": component["file"]})

    xcode_projects = discover_paths(root, "*.xcodeproj")
    xcode_workspaces = discover_paths(root, "*.xcworkspace")
    swift_packages = discover_paths(root, "Package.swift")
    if xcode_projects or xcode_workspaces:
        project_state = "existing-xcode"
    elif swift_packages:
        project_state = "swift-package-only"
    else:
        project_state = "empty-no-ios-project"

    result = {
        "schemaVersion": "ios-component-index-1.0",
        "root": str(root),
        "projectState": project_state,
        "components": sorted(components, key=lambda item: (-item["reusabilityScore"], item["name"])),
        "assets": sorted(assets, key=lambda item: (item["kind"], item["name"])),
        "designTokenSignals": token_signals,
        "summary": {
            "components": len(components),
            "assets": len(assets),
            "swiftUIViews": sum(item["kind"] == "swiftui-view" for item in components),
            "uikitViews": sum(item["kind"].startswith("uikit-") for item in components),
            "navigationTypes": sum(item["kind"] == "navigation" for item in components),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **result["summary"], "projectState": result["projectState"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
