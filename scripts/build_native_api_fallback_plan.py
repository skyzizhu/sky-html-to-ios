#!/usr/bin/env python3
"""Build an executable native API availability and fallback contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-api-fallback-plan-1.0"

CAPABILITIES: list[dict[str, Any]] = [
    {
        "id": "navigation-container",
        "required": "always",
        "swiftui": {"symbol": "NavigationStack", "fallback": None},
        "uikit": {"symbol": "UINavigationController", "fallback": None},
    },
    {
        "id": "scroll-keyboard-dismissal",
        "required": "always",
        "swiftui": {"symbol": "scrollDismissesKeyboard", "fallback": None},
        "uikit": {"symbol": "UIScrollView", "fallback": None},
    },
    {
        "id": "sheet-presentation",
        "required": "presentation:sheet",
        "swiftui": {"symbol": "presentationDetents", "fallback": "custom-overlay-container"},
        "uikit": {"symbol": "UISheetPresentationController", "fallback": "over-full-screen-container"},
    },
    {
        "id": "popover-presentation",
        "required": "presentation:popover",
        "swiftui": {"symbol": "popover", "fallback": "anchored-overlay"},
        "uikit": {"symbol": "UIPopoverPresentationController", "fallback": "anchored-child-controller"},
    },
    {
        "id": "alert-presentation",
        "required": "presentation:alert",
        "swiftui": {"symbol": "alert", "fallback": "accessible-custom-alert"},
        "uikit": {"symbol": "UIAlertController", "fallback": "accessible-custom-alert-controller"},
    },
    {
        "id": "confirmation-presentation",
        "required": "presentation:confirmation",
        "swiftui": {"symbol": "confirmationDialog", "fallback": "accessible-action-sheet-overlay"},
        "uikit": {"symbol": "UIAlertController", "fallback": "accessible-action-sheet-controller"},
    },
    {
        "id": "calendar-control",
        "required": "semantic:calendar-view",
        "swiftui": {"symbol": "UICalendarView", "sdkGroup": "uikit", "fallback": "date-picker-calendar-mode"},
        "uikit": {"symbol": "UICalendarView", "fallback": "UIDatePicker"},
    },
    {
        "id": "paste-control",
        "required": "semantic:paste-control",
        "swiftui": {"symbol": "PasteButton", "fallback": "pasteboard-button"},
        "uikit": {"symbol": "UIPasteControl", "fallback": "pasteboard-button"},
    },
    {
        "id": "color-well",
        "required": "semantic:color-well",
        "swiftui": {"symbol": "ColorPicker", "fallback": "color-picker-bridge"},
        "uikit": {"symbol": "UIColorWell", "fallback": "UIColorPickerViewController"},
    },
    {
        "id": "content-unavailable",
        "required": "semantic:content-unavailable",
        "swiftui": {"symbol": "ContentUnavailableView", "fallback": "semantic-empty-state-stack"},
        "uikit": {"symbol": "UIContentUnavailableView", "fallback": "semantic-empty-state-view"},
    },
    {
        "id": "keyframe-animation",
        "required": "motion:keyframes",
        "swiftui": {"symbol": "keyframeAnimator", "fallback": "timeline-sampled-animation"},
        "uikit": {"symbol": "UIView", "fallback": "property-animator-keyframes"},
    },
]


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def version(value: Any) -> tuple[int, ...]:
    return tuple(int(item) for item in re.findall(r"\d+", str(value or ""))) or (0,)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence(irs: list[dict[str, Any]]) -> set[str]:
    result = {"always"}
    for ir in irs:
        for screen in ir.get("screens") or []:
            for node in screen.get("nodes") or []:
                semantic = str(node.get("semanticType") or "")
                if semantic:
                    result.add(f"semantic:{semantic}")
        for state in ir.get("states") or []:
            kind = str(state.get("kind") or "").lower()
            if kind in {"sheet"}:
                result.add("presentation:sheet")
            if "popover" in kind or kind == "menu":
                result.add("presentation:popover")
            if kind in {"alert", "dialog"}:
                result.add("presentation:alert")
            if kind in {"confirmation", "action-sheet"}:
                result.add("presentation:confirmation")
        if ir.get("motions"):
            result.add("motion:keyframes")
    return result


def resolve(mapping: dict[str, Any], symbol: dict[str, Any], minimum_ios: str) -> dict[str, Any]:
    status = str(symbol.get("status") or "unavailable")
    fallback = mapping.get("fallback")
    introduced = symbol.get("introduced")
    if status in {"available", "available-review-version"} and (
        not introduced or version(minimum_ios) >= version(introduced)
    ):
        resolution = "system-native" if status == "available" else "system-native-review"
    elif fallback:
        resolution = "fallback"
    else:
        resolution = "blocked"
    return {
        "symbol": mapping.get("symbol"),
        "sdkStatus": status,
        "introduced": introduced,
        "deprecated": bool(symbol.get("deprecated")),
        "resolution": resolution,
        "fallback": fallback if resolution == "fallback" else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-report", type=Path, required=True)
    parser.add_argument("--ir", type=Path, action="append", default=[])
    parser.add_argument("--ui-stack", choices=("swiftui", "uikit"), required=True)
    parser.add_argument("--minimum-ios", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    sdk = load(args.sdk_report)
    if str(sdk.get("minimumIOS") or "") != str(args.minimum_ios):
        parser.error("SDK report minimumIOS does not match --minimum-ios")
    irs = [load(path) for path in args.ir]
    required_evidence = evidence(irs)
    capabilities = []
    for capability in CAPABILITIES:
        stacks = {}
        for stack in ("swiftui", "uikit"):
            mapping = capability[stack]
            sdk_group = str(mapping.get("sdkGroup") or stack)
            symbol = ((sdk.get("symbols") or {}).get(sdk_group) or {}).get(mapping["symbol"]) or {}
            stacks[stack] = resolve(mapping, symbol, args.minimum_ios)
        active = stacks[args.ui_stack]
        required = capability["required"] in required_evidence
        capabilities.append({
            "id": capability["id"],
            "required": required,
            "evidence": capability["required"],
            "activeStack": args.ui_stack,
            "activeResolution": active["resolution"],
            "stacks": stacks,
        })

    required_items = [item for item in capabilities if item["required"]]
    blocked = [item["id"] for item in required_items if item["activeResolution"] == "blocked"]
    fallbacks = [item["id"] for item in required_items if item["activeResolution"] == "fallback"]
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "sdkReport": str(args.sdk_report.resolve()),
        "sdkReportSha256": digest(args.sdk_report),
        "installedSDK": (sdk.get("sdk") or {}).get("version"),
        "minimumIOS": args.minimum_ios,
        "uiStack": args.ui_stack,
        "runtimeBaseline": {"minimumIOS": "16.0", "reason": "generated-runtime-contract"},
        "capabilities": capabilities,
        "summary": {
            "requiredCapabilityCount": len(required_items),
            "fallbackCapabilityIDs": fallbacks,
            "blockedCapabilityIDs": blocked,
            "status": "failed" if blocked else "passed-with-fallbacks" if fallbacks else "passed",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **result["summary"]}, indent=2))
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
