#!/usr/bin/env python3
"""Inspect an iOS project without modifying it."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


SKIP_DIRS = {
    ".git", ".build", "build", "DerivedData", "Pods", "Carthage",
    "node_modules", ".swiftpm", "xcuserdata",
}


def walk_files(root: Path, suffixes: set[str] | None = None):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and (suffixes is None or path.suffix.lower() in suffixes):
            yield path


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def discover_paths(root: Path, pattern: str) -> list[Path]:
    return sorted(path for path in root.rglob(pattern) if not any(part in SKIP_DIRS for part in path.parts))


def read_limited(path: Path, limit: int = 3_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def detect_conventions(root: Path) -> list[str]:
    exact = {
        "agents.md", "claude.md", ".cursorrules",
        "copilot-instructions.md",
    }
    results: list[str] = []
    for path in walk_files(root):
        lower = path.name.lower()
        rel = relative(path, root)
        if lower in exact or rel.lower().endswith(".cursor/rules"):
            results.append(rel)
        elif ".cursor/rules/" in rel.lower() and path.suffix.lower() in {".md", ".mdc"}:
            results.append(rel)
        elif rel.lower().endswith(".github/copilot-instructions.md"):
            results.append(rel)
    return sorted(set(results))


def inspect(root: Path) -> dict:
    projects = discover_paths(root, "*.xcodeproj")
    workspaces = discover_paths(root, "*.xcworkspace")
    packages = discover_paths(root, "Package.swift")
    podfiles = discover_paths(root, "Podfile")
    cartfiles = discover_paths(root, "Cartfile")
    asset_catalogs = discover_paths(root, "*.xcassets")

    swift_counts = Counter()
    dependency_hits = Counter()
    sampled_swift_files = 0
    for path in walk_files(root, {".swift"}):
        if sampled_swift_files >= 2500:
            break
        text = read_limited(path, 250_000)
        sampled_swift_files += 1
        swift_counts["swiftui"] += len(re.findall(r"(?m)^\s*import\s+SwiftUI\b", text))
        swift_counts["uikit"] += len(re.findall(r"(?m)^\s*import\s+UIKit\b", text))
        if "import SnapKit" in text:
            dependency_hits["SnapKit"] += 1
        if "import Kingfisher" in text:
            dependency_hits["Kingfisher"] += 1
        if "import SDWebImage" in text or "import SDWebImageSwiftUI" in text:
            dependency_hits["SDWebImage"] += 1

    project_info = []
    deployment_targets: set[str] = set()
    swift_versions: set[str] = set()
    target_names: set[str] = set()
    synchronized = False
    for project in projects:
        pbx = project / "project.pbxproj"
        text = read_limited(pbx)
        synchronized = synchronized or bool(
            re.search(r"PBXFileSystemSynchronizedRootGroup|fileSystemSynchronizedGroups", text)
        )
        deployment_targets.update(re.findall(r"IPHONEOS_DEPLOYMENT_TARGET\s*=\s*([^;\s]+)", text))
        swift_versions.update(re.findall(r"SWIFT_VERSION\s*=\s*([^;\s]+)", text))
        target_names.update(re.findall(r"productName\s*=\s*([^;]+);", text))
        project_info.append({
            "path": relative(project, root),
            "fileSystemSynchronized": bool(
                re.search(r"PBXFileSystemSynchronizedRootGroup|fileSystemSynchronizedGroups", text)
            ),
        })

    scheme_paths = discover_paths(root, "*.xcscheme")
    schemes = sorted({path.stem for path in scheme_paths if "xcuserdata" not in path.parts})

    pod_text = "\n".join(read_limited(path, 500_000) for path in podfiles)
    package_text = "\n".join(read_limited(path, 500_000) for path in packages)
    cart_text = "\n".join(read_limited(path, 500_000) for path in cartfiles)
    dependency_blob = "\n".join((pod_text, package_text, cart_text))
    for name in ("SnapKit", "Kingfisher", "SDWebImage", "Masonry"):
        if re.search(re.escape(name), dependency_blob, re.IGNORECASE):
            dependency_hits[name] += 1

    swiftui = swift_counts["swiftui"]
    uikit = swift_counts["uikit"]
    if swiftui and not uikit:
        recommendation = "swiftui"
    elif uikit and not swiftui:
        recommendation = "uikit"
    elif swiftui > uikit * 1.5:
        recommendation = "swiftui"
    elif uikit > swiftui * 1.5:
        recommendation = "uikit"
    elif swiftui or uikit:
        recommendation = "mixed-inspect-target-module"
    else:
        recommendation = "unknown-new-project-default-swiftui"

    if projects or workspaces:
        project_state = "existing-xcode"
    elif packages:
        project_state = "swift-package-only"
    else:
        project_state = "empty-no-ios-project"

    return {
        "root": str(root),
        "projectState": project_state,
        "canCreateXcodeProject": not bool(projects or workspaces),
        "projects": project_info,
        "workspaces": [relative(path, root) for path in workspaces],
        "swiftPackages": [relative(path, root) for path in packages],
        "dependencyFiles": {
            "podfiles": [relative(path, root) for path in podfiles],
            "cartfiles": [relative(path, root) for path in cartfiles],
        },
        "assetCatalogs": [relative(path, root) for path in asset_catalogs],
        "schemes": schemes,
        "targets": sorted(name.strip('"') for name in target_names),
        "deploymentTargets": sorted(deployment_targets),
        "swiftVersions": sorted(swift_versions),
        "usesFileSystemSynchronizedGroups": synchronized,
        "sourceSignals": {
            "sampledSwiftFiles": sampled_swift_files,
            "swiftUIImports": swiftui,
            "uiKitImports": uikit,
            "recommendedUIStack": recommendation,
        },
        "detectedDependencies": dict(sorted(dependency_hits.items())),
        "conventionFiles": detect_conventions(root),
        "warnings": (["No Xcode project, workspace, or Package.swift found."]
                     if not (projects or workspaces or packages) else
                     ["A Swift package exists, but no Xcode project/workspace was found."]
                     if packages and not (projects or workspaces) else []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Project root is not a directory: {root}")
    result = inspect(root)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
