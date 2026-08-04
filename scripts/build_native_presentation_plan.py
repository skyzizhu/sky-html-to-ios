#!/usr/bin/env python3
"""Build an executable native presentation contract from UI IR evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "native-presentation-plan-1.0"
PRESENTATION_KINDS = {
    "sheet", "full-screen", "fullscreen", "full-screen-overlay", "popover",
    "popover-overlay", "overlay", "dialog", "alert", "confirmation", "menu",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: Any, default: float = 0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", str(value or ""))
    return float(match.group(0)) if match else default


def boolean(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return default


def normalized_detents(values: Any) -> list[str]:
    raw = values if isinstance(values, list) else str(values or "").split(",")
    result: list[str] = []
    for item in raw:
        value = str(item).strip().lower()
        if value in {"medium", "large"}:
            result.append(value)
        elif re.fullmatch(r"(?:height|fraction):\s*\d+(?:\.\d+)?", value):
            result.append(value.replace(" ", ""))
    return list(dict.fromkeys(result))


def inferred_kind(state: dict[str, Any], presentation: dict[str, Any]) -> str:
    kind = str(state.get("kind") or "").lower()
    style = str(presentation.get("style") or "").lower()
    if kind in PRESENTATION_KINDS:
        return {"fullscreen": "full-screen", "dialog": "alert"}.get(kind, kind)
    if style in {"alert", "action-sheet", "menu", "popover", "full-screen"}:
        return {"action-sheet": "confirmation"}.get(style, style)
    return "sheet"


def strategy(kind: str, style: str, target_rect: dict[str, Any], root_rect: dict[str, Any]) -> str:
    width = number(target_rect.get("width"))
    height = number(target_rect.get("height"))
    root_width = max(number(root_rect.get("width")), 1)
    root_height = max(number(root_rect.get("height")), 1)
    if kind == "sheet" and style not in {"in-place-overlay", "custom-overlay"}:
        return "system-sheet"
    if kind in {"full-screen", "full-screen-overlay"}:
        return "system-cover" if width >= root_width * 0.9 and height >= root_height * 0.8 else "custom-overlay"
    if kind == "popover":
        return "system-popover"
    if kind == "alert":
        return "system-alert"
    if kind == "confirmation":
        return "system-confirmation"
    if kind == "menu":
        return "system-menu"
    return "custom-overlay"


def transition(kind: str, presentation: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    style = node.get("style") or {}
    raw = str(presentation.get("transition") or style.get("transitionProperty") or "system").lower()
    if raw in {"system", "source-derived", "automatic", "none"}:
        motion = "slide-up" if kind == "sheet" else "scale-fade" if kind in {"popover", "alert", "confirmation", "menu"} else "fade"
    elif "translate" in raw or "slide" in raw:
        motion = "slide-up"
    elif "scale" in raw:
        motion = "scale-fade"
    else:
        motion = "fade"
    duration = int(max(number(presentation.get("durationMilliseconds"), number(style.get("transitionDuration"), 0.28) * 1000), 1))
    return {"kind": motion, "durationMilliseconds": duration, "interactive": kind == "sheet", "reducedMotion": "fade"}


def descendants(nodes: dict[str, dict[str, Any]], root_id: str) -> list[dict[str, Any]]:
    children: dict[str, list[dict[str, Any]]] = {}
    for node in nodes.values():
        children.setdefault(str(node.get("parentId") or ""), []).append(node)
    result: list[dict[str, Any]] = []
    pending = list(children.get(root_id) or [])
    while pending:
        node = pending.pop(0)
        result.append(node)
        pending[0:0] = children.get(str(node.get("id") or ""), [])
    return result


def content_contract(nodes: dict[str, dict[str, Any]], target_id: str) -> dict[str, Any]:
    subtree = [nodes[target_id], *descendants(nodes, target_id)]
    text_nodes = [
        node for node in subtree
        if str((node.get("content") or {}).get("text") or "").strip()
        and str(node.get("semanticType") or "") not in {"button", "icon-button", "menu-item"}
    ]
    heading = next((node for node in text_nodes if str(node.get("semanticType") or "") == "heading"), None)
    title_node = heading or (text_nodes[0] if text_nodes else None)
    title = str(((title_node or {}).get("content") or {}).get("text") or "").strip()
    message = " ".join(
        str((node.get("content") or {}).get("text") or "").strip()
        for node in text_nodes if node is not title_node
    ).strip()
    actions = []
    for node in subtree:
        if str(node.get("semanticType") or "") not in {"button", "icon-button", "menu-item"}:
            continue
        label = str((node.get("content") or {}).get("text") or "").strip()
        if not label:
            continue
        role = "destructive" if re.search(r"delete|remove|clear|destructive|删除|移除|清空", label, re.I) else (
            "cancel" if re.search(r"cancel|close|dismiss|取消|关闭", label, re.I) else "default"
        )
        actions.append({"id": str(node.get("id") or ""), "title": label, "role": role})
    return {"title": title, "message": message, "actions": actions}


def mask_score(node: dict[str, Any], root_rect: dict[str, Any]) -> float:
    source = node.get("source") or {}
    name = " ".join(str(source.get(key) or "") for key in ("selector", "domId", "runtimeId")).lower()
    rect = (node.get("layout") or {}).get("rect") or {}
    area_ratio = number(rect.get("width")) * number(rect.get("height")) / max(number(root_rect.get("width")) * number(root_rect.get("height")), 1)
    return (1 if re.search(r"mask|backdrop|scrim", name) else 0) + (0.5 if area_ratio >= 0.8 else 0)


def build_screen(screen: dict[str, Any], ir: dict[str, Any]) -> dict[str, Any]:
    nodes = {str(item.get("id") or ""): item for item in screen.get("nodes") or []}
    root = nodes.get(str(screen.get("rootNodeId") or "")) or next(iter(nodes.values()), {})
    root_rect = (root.get("layout") or {}).get("rect") or {}
    states = {str(item.get("id") or ""): item for item in ir.get("states") or []}
    contracts: dict[str, dict[str, Any]] = {}
    for interaction in ir.get("interactions") or []:
        candidates = []
        for item in (interaction.get("payload") or {}).get("transitions") or []:
            state_id = str(item.get("targetStateId") or "")
            state = states.get(state_id) or {}
            if not state_id or str(state.get("kind") or "") not in PRESENTATION_KINDS:
                continue
            target_id = str((state.get("targetNodeIds") or [""])[0])
            target = nodes.get(target_id) or {}
            # A transition-only state can describe browser behavior without a
            # visual subtree. It is not an executable native presentation until
            # state merging resolves a concrete owner node.
            if not target_id or not target:
                continue
            candidates.append((state_id, state, target_id, target))
        if not candidates:
            continue
        canonical = min(candidates, key=lambda value: mask_score(value[3], root_rect))
        aliases = [state_id for state_id, _, _, _ in candidates if state_id != canonical[0]]
        for state_id, state, target_id, target in [canonical]:
            source_id = str(interaction.get("sourceNodeId") or ((interaction.get("sourceNodeIds") or [""])[0]))
            source = nodes.get(source_id) or {}
            explicit = dict(interaction.get("presentation") or {})
            ios = (target.get("source") or {}).get("ios") or {}
            style_name = str(explicit.get("style") or ios.get("presentationStyle") or "page-sheet").lower()
            kind = inferred_kind(state, explicit)
            target_rect = (target.get("layout") or {}).get("rect") or {}
            source_rect = (source.get("layout") or {}).get("rect") or {}
            node_style = target.get("style") or {}
            corner_radius = number(node_style.get("borderTopLeftRadius"), number(node_style.get("borderRadius"), 16))
            content = content_contract(nodes, target_id)
            subtree = [target, *descendants(nodes, target_id)]
            has_editable_content = any(bool((node.get("textBehavior") or {}).get("editable")) for node in subtree)
            contracts[state_id] = {
                "stateId": state_id,
                "sourceInteractionId": interaction.get("id"),
                "sourceNodeId": source_id or None,
                "targetNodeId": target_id,
                "aliasStateIds": aliases,
                "kind": kind,
                "style": style_name,
                "strategy": strategy(kind, style_name, target_rect, root_rect),
                "detents": normalized_detents(explicit.get("detents") or ios.get("detents")) or (["large"] if kind == "sheet" else []),
                "grabberVisible": boolean(explicit.get("grabberVisible"), kind == "sheet"),
                "interactiveDismissDisabled": boolean(explicit.get("interactiveDismissDisabled", ios.get("interactiveDismiss")), False),
                "backdrop": {
                    "color": str(explicit.get("backdropColor") or "#000000"),
                    "opacity": min(max(number(explicit.get("backdropOpacity"), 0.32), 0), 1),
                    "dismisses": boolean(explicit.get("backdropDismiss", ios.get("backdropDismiss")), kind not in {"alert"}),
                },
                "panel": {"cornerRadiusPt": max(corner_radius, 0), "clipsContent": True},
                "scrollOwnership": "presentation-content" if str((target.get("layout") or {}).get("scrollAxis") or "none") != "none" else "none",
                "keyboardAvoidance": "system-focus-aware" if has_editable_content else "system",
                "focusRestoration": "source-control",
                "content": content,
                "largestUndimmedDetent": explicit.get("largestUndimmedDetent"),
                "anchor": {
                    "coordinateSpace": "app-root",
                    "sourceRect": [number(source_rect.get(key)) - (number(root_rect.get(key)) if key in {"x", "y"} else 0) for key in ("x", "y", "width", "height")],
                    "permittedArrowDirections": ["up", "down"],
                },
                "transition": transition(kind, explicit, target),
            }
    return {"screenId": str(screen.get("id") or ""), "presentations": list(contracts.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", action="append", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    screens = []
    for path in args.ir:
        ir = load(path)
        screens.extend(build_screen(screen, ir) for screen in ir.get("screens") or [])
    output = {
        "schemaVersion": SCHEMA_VERSION,
        "sources": {"uiIR": [{"path": str(path.resolve()), "sha256": digest(path)} for path in args.ir]},
        "invariants": {
            "preferSystemPresentation": True,
            "onePresentationOwnerPerState": True,
            "safeAreaDimensionsRemainSystemOwned": True,
            "screenshotsRequired": False,
            "multimodalModelRequired": False,
        },
        "screens": screens,
        "summary": {"screenCount": len(screens), "presentationCount": sum(len(item["presentations"]) for item in screens)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), **output["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
