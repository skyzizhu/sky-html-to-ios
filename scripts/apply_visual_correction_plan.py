#!/usr/bin/env python3
"""Apply bounded, deterministic visual corrections to a copied UI IR."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SUPPORTED_PLAN_SCHEMA = "visual-correction-plan-1.0"
SUPPORTED_MUTATION_SCHEMA = "ui-ir-bounded-mutation-1.0"
SUPPORTED_PATHS = {f"layout.rect.{prop}" for prop in ("x", "y", "width", "height")}
MAX_OPERATIONS_PER_NODE = 4


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("mutation values must be finite")
    return result


def node_index(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for screen in ir.get("screens") or []:
        for node in screen.get("nodes") or []:
            node_id = node.get("id")
            if node_id:
                nodes[str(node_id)] = node
    return nodes


def reject(correction: dict, reason: str) -> dict[str, Any]:
    return {"correctionId": correction.get("id"), "nodeId": correction.get("nodeId"), "reason": reason}


def apply_plan(plan: dict[str, Any], ir: dict[str, Any], source_ir: Path, plan_path: Path) -> tuple[dict, dict]:
    if plan.get("schemaVersion") != SUPPORTED_PLAN_SCHEMA:
        raise ValueError(f"plan must use {SUPPORTED_PLAN_SCHEMA}")
    if (plan.get("policy") or {}).get("mutationOwnership") != "ui-ir-and-derived-contracts-only":
        raise ValueError("plan mutation ownership must be ui-ir-and-derived-contracts-only")

    corrected = copy.deepcopy(ir)
    nodes = node_index(corrected)
    applied = []
    rejected = []
    mutated_nodes: set[str] = set()
    for correction in plan.get("corrections") or []:
        if not correction.get("automaticEligible"):
            rejected.append(reject(correction, "not-automatic-eligible"))
            continue
        if correction.get("prohibitedMutation") != "generated-swift-source":
            rejected.append(reject(correction, "missing-generated-source-prohibition"))
            continue
        mutation = correction.get("proposedMutation") or {}
        if mutation.get("schemaVersion") != SUPPORTED_MUTATION_SCHEMA or mutation.get("owner") != "ui-ir":
            rejected.append(reject(correction, "unsupported-mutation-contract"))
            continue
        node_id = str(correction.get("nodeId") or "")
        if not node_id or node_id != str(mutation.get("nodeId") or "") or node_id not in nodes:
            rejected.append(reject(correction, "mutation-node-does-not-resolve"))
            continue
        if node_id in mutated_nodes:
            rejected.append(reject(correction, "node-already-mutated-by-higher-priority-correction"))
            continue
        operations = mutation.get("operations") or []
        if not operations or len(operations) > MAX_OPERATIONS_PER_NODE:
            rejected.append(reject(correction, "invalid-operation-count"))
            continue

        node = nodes[node_id]
        rect = (node.get("layout") or {}).get("rect")
        if not isinstance(rect, dict):
            rejected.append(reject(correction, "node-has-no-layout-rect"))
            continue
        staged = []
        invalid_reason = None
        for operation in operations:
            path = str(operation.get("path") or "")
            if path not in SUPPORTED_PATHS or operation.get("operation") != "add":
                invalid_reason = "unsupported-operation"
                break
            prop = path.rsplit(".", 1)[-1]
            before = finite(rect.get(prop))
            expected_before = finite(operation.get("expectedBefore"))
            amount = finite(operation.get("amount"))
            limit = finite(operation.get("limitPoints"))
            if abs(before - expected_before) > 0.05:
                invalid_reason = f"stale-expected-before-{prop}"
                break
            if limit <= 0 or abs(amount) > limit + 1e-6:
                invalid_reason = f"amount-exceeds-plan-limit-{prop}"
                break
            after = before + amount
            if prop in {"width", "height"} and after < 1:
                invalid_reason = f"invalid-result-{prop}"
                break
            staged.append((prop, before, after, amount))
        if invalid_reason:
            rejected.append(reject(correction, invalid_reason))
            continue
        for prop, _before, after, _amount in staged:
            rect[prop] = round(after, 4)
        history = node.setdefault("calibration", {}).setdefault("visualCorrections", [])
        history.append({
            "plan": str(plan_path.resolve()),
            "correctionId": correction.get("id"),
            "iteration": plan.get("iteration"),
            "operations": [
                {"property": prop, "before": round(before, 4), "after": round(after, 4), "amount": round(amount, 4)}
                for prop, before, after, amount in staged
            ],
        })
        mutated_nodes.add(node_id)
        applied.append({
            "correctionId": correction.get("id"),
            "nodeId": node_id,
            "operations": history[-1]["operations"],
        })

    corrected.setdefault("visualCorrectionHistory", []).append({
        "schemaVersion": "ui-ir-visual-correction-application-1.0",
        "sourceIR": str(source_ir.resolve()),
        "sourceIRSha256": sha256(source_ir),
        "plan": str(plan_path.resolve()),
        "iteration": plan.get("iteration"),
        "appliedCount": len(applied),
        "rejectedCount": len(rejected),
    })
    report = {
        "schemaVersion": "visual-correction-application-report-1.0",
        "sourceIR": str(source_ir.resolve()),
        "sourceIRSha256": sha256(source_ir),
        "plan": str(plan_path.resolve()),
        "iteration": plan.get("iteration"),
        "applied": applied,
        "rejected": rejected,
        "summary": {
            "appliedCount": len(applied),
            "rejectedCount": len(rejected),
            "requiresRegeneration": bool(applied),
            "rollback": "use-source-ir",
        },
    }
    return corrected, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--ir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    plan = load_json(args.plan)
    ir = load_json(args.ir)
    corrected, report = apply_plan(plan, ir, args.ir, args.plan)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(corrected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "report": str(args.report.resolve()), **report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
