#!/usr/bin/env python3

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "artboard_state_classifier.cjs"
NODE = shutil.which("node") or next(
    (
        path
        for path in Path.home().glob(".cache/codex-runtimes/*/dependencies/node/bin/node")
        if path.is_file()
    ),
    None,
)


def screen(
    screen_id: str,
    structure: list[str],
    text: list[str],
    *,
    hints: list[str] | None = None,
    positioned_area: float = 0,
) -> dict:
    return {
        "id": screen_id,
        "kind": "virtual-screen-state",
        "includeInNativeConversion": True,
        "virtualStateId": screen_id,
        "rootSelector": f"#{screen_id}",
        "visualSnapshot": {
            "nodeCount": len(structure),
            "structureTokens": structure,
            "textTokens": text,
            "positionedAreaRatio": positioned_area,
            "hints": hints or [],
        },
    }


class ArtboardStateClassifierTests(unittest.TestCase):
    def classify(self, screens: list[dict]) -> dict:
        if NODE is None:
            self.skipTest("Node.js is unavailable")
        script = """
const fs = require("fs");
const classifier = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(classifier.classifyScreenRepresentations(input)));
"""
        completed = subprocess.run(
            [str(NODE), "-e", script, str(MODULE)],
            input=json.dumps(screens),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_menu_and_swipe_artboards_become_states_but_distinct_page_remains(self) -> None:
        base_structure = [
            "0:main::screen",
            "1:header::nav",
            "1:section::content",
            "2:div::row",
            "2:div::row",
            "1:footer::tabs",
        ]
        base_text = ["home", "first item", "second item", "profile"]
        screens = [
            screen("home", base_structure, base_text),
            screen(
                "home-menu",
                [*base_structure, "1:div:menu:menu", "2:button::item", "2:button::item"],
                [*base_text, "edit", "settings"],
                hints=["menu", "scrim"],
                positioned_area=0.62,
            ),
            screen(
                "search",
                ["0:main::screen", "1:header::search", "1:form::results", "2:input:search:"],
                ["search", "no results"],
            ),
            screen(
                "home-swipe",
                [*base_structure, "3:button::trailing-action"],
                [*base_text, "delete"],
                hints=["cell-swipe-actions"],
                positioned_area=0.08,
            ),
        ]
        result = self.classify(screens)
        by_id = {item["id"]: item for item in result["screens"]}
        self.assertTrue(by_id["home"]["includeInNativeConversion"])
        self.assertFalse(by_id["home-menu"]["includeInNativeConversion"])
        self.assertEqual(by_id["home-menu"]["nativeOwnerScreenId"], "home")
        self.assertEqual(by_id["home-menu"]["stateRepresentation"]["kind"], "presentation")
        self.assertEqual(by_id["home-menu"]["stateRepresentation"]["presentationStyle"], "menu")
        self.assertTrue(by_id["search"]["includeInNativeConversion"])
        self.assertFalse(by_id["home-swipe"]["includeInNativeConversion"])
        self.assertEqual(by_id["home-swipe"]["nativeOwnerScreenId"], "home")
        self.assertEqual(by_id["home-swipe"]["stateRepresentation"]["kind"], "local-effect")
        self.assertEqual(by_id["home-swipe"]["stateRepresentation"]["localEffect"], "swipe-actions")
        self.assertEqual(len(result["visualStates"]), 2)

    def test_explicit_state_owner_overrides_similarity_threshold(self) -> None:
        owner = screen("home", ["0:main::screen", "1:section::content"], ["home"])
        variant = screen("menu-artboard", ["0:aside:menu:drawer"], ["settings"])
        variant.update({
            "iosStateOwner": "home",
            "iosStateKind": "menu",
        })
        result = self.classify([owner, variant])
        classified = result["screens"][1]
        self.assertFalse(classified["includeInNativeConversion"])
        self.assertEqual(classified["nativeOwnerScreenId"], "home")
        self.assertTrue(classified["stateRepresentation"]["explicit"])
        self.assertEqual(classified["stateRepresentation"]["confidence"], 1)


if __name__ == "__main__":
    unittest.main()
