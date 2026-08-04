#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract_render_tree.cjs"
NODE = Path(os.environ.get(
    "HTML_TO_IOS_NODE",
    "/Users/skyzizhu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node",
))
NODE_MODULES = Path(
    "/Users/skyzizhu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
)


@unittest.skipUnless(NODE.is_file() and NODE_MODULES.is_dir(), "bundled Node/Playwright runtime unavailable")
class ExtractControlVisualStatesTests(unittest.TestCase):
    def test_authored_relative_lengths_survive_computed_style_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.html"
            output = root / "render-tree.json"
            source.write_text("""
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                  html,body{margin:0}
                  main{width:393px;height:852px}
                  .panel{width:calc(50% - 8px);min-width:40%;height:80px}
                  #unmatched, .panel{width:55%}
                  @media (min-width:390px){main .panel{width:60%}}
                </style>
                <main id="app"><div id="panel" class="panel"></div></main>
            """, encoding="utf-8")
            environment = dict(os.environ)
            environment["NODE_PATH"] = str(NODE_MODULES)
            result = subprocess.run([
                str(NODE), str(SCRIPT), "--html", str(source), "--out", str(output),
                "--selector", "#app", "--width", "393", "--height", "852",
            ], text=True, capture_output=True, check=False, env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            panel = next(item for item in payload["nodes"] if item.get("domId") == "panel")
            self.assertTrue(panel["style"]["width"].endswith("px"))
            self.assertEqual(panel["style"]["authoredLayout"]["width"]["value"], "60%")
            self.assertEqual(panel["style"]["authoredLayout"]["minWidth"]["value"], "40%")

    def test_pressed_and_focused_styles_are_measured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.html"
            output = root / "render-tree.json"
            source.write_text("""
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                  html,body{margin:0}
                  main{width:393px;height:852px}
                  button{margin:40px;width:180px;height:48px;color:white;background:rgb(220,30,50);border:1px solid transparent;border-radius:12px}
                  button:focus{border:3px solid rgb(30,200,120)}
                  button:active{background:rgb(40,80,220);opacity:.72;transform:scale(.96)}
                  button:disabled{color:rgb(120,120,130);background:rgb(230,230,235);opacity:.55}
                </style>
                <main id="app"><button id="action" onclick="document.body.dataset.clicked='yes'">Continue</button></main>
            """, encoding="utf-8")
            environment = dict(os.environ)
            environment["NODE_PATH"] = str(NODE_MODULES)
            result = subprocess.run([
                str(NODE), str(SCRIPT), "--html", str(source), "--out", str(output),
                "--selector", "#app", "--width", "393", "--height", "852",
            ], text=True, capture_output=True, check=False, env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            button = next(item for item in payload["nodes"] if item.get("domId") == "action")
            states = button["controlStateStyles"]
            self.assertEqual(states["focused"]["borderTopWidth"], "3px")
            self.assertEqual(states["pressed"]["backgroundColor"], "rgb(40, 80, 220)")
            self.assertEqual(states["pressed"]["opacity"], "0.72")
            self.assertEqual(states["disabled"]["backgroundColor"], "rgb(230, 230, 235)")
            self.assertEqual(states["disabled"]["opacity"], "0.55")


if __name__ == "__main__":
    unittest.main()
