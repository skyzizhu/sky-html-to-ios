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
    def test_stacking_context_pseudo_order_and_clip_evidence_are_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.html"
            output = root / "render-tree.json"
            source.write_text("""
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                  html,body{margin:0}
                  main{position:relative;width:393px;height:852px}
                  .card{position:relative;margin:20px;width:200px;height:120px;border-radius:20px;overflow:visible}
                  .card::before{content:"";position:absolute;inset:0;background:red;z-index:-1}
                  .card::after{content:"";position:absolute;right:-8px;top:-8px;width:16px;height:16px;background:blue}
                  .content{position:relative;z-index:2;opacity:.9;transform:translateZ(0)}
                </style>
                <main id="app"><section id="card" class="card"><span id="content" class="content">Text</span></section></main>
            """, encoding="utf-8")
            environment = dict(os.environ)
            environment["NODE_PATH"] = str(NODE_MODULES)
            result = subprocess.run([
                str(NODE), str(SCRIPT), "--html", str(source), "--out", str(output),
                "--selector", "#app", "--width", "393", "--height", "852",
            ], text=True, capture_output=True, check=False, env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["captureConfiguration"]["controlSamplingViewportRestored"])
            card = next(item for item in payload["nodes"] if item.get("domId") == "card")
            content = next(item for item in payload["nodes"] if item.get("domId") == "content")
            before = next(item for item in payload["nodes"] if item.get("tag") == "::before")
            after = next(item for item in payload["nodes"] if item.get("tag") == "::after")
            self.assertFalse(card["paint"]["createsStackingContext"])
            self.assertEqual(card["style"]["overflowX"], "visible")
            self.assertEqual(card["style"]["cornerRadii"][0], "20px")
            self.assertTrue(content["paint"]["createsStackingContext"])
            self.assertIn("positioned-z-index", content["paint"]["stackingContextReasons"])
            self.assertIn("opacity", content["paint"]["stackingContextReasons"])
            self.assertIn("transform", content["paint"]["stackingContextReasons"])
            self.assertEqual(before["paint"]["pseudoPhase"], "before")
            self.assertEqual(after["paint"]["pseudoPhase"], "after")
            self.assertLess(before["paint"]["sourceOrder"], content["paint"]["sourceOrder"])
            self.assertGreater(after["paint"]["sourceOrder"], content["paint"]["sourceOrder"])

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

    def test_checked_state_accent_color_and_appearance_are_measured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.html"
            output = root / "render-tree.json"
            source.write_text("""
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                  main{width:393px;height:852px}
                  input{accent-color:rgb(20,120,240);appearance:auto}
                  input:checked{background:rgb(20,120,240);border-color:rgb(10,80,180)}
                </style>
                <main id="app"><input id="choice" type="checkbox" aria-label="Choice"></main>
            """, encoding="utf-8")
            environment = dict(os.environ)
            environment["NODE_PATH"] = str(NODE_MODULES)
            result = subprocess.run([
                str(NODE), str(SCRIPT), "--html", str(source), "--out", str(output),
                "--selector", "#app", "--width", "393", "--height", "852",
            ], text=True, capture_output=True, check=False, env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            checkbox = next(item for item in payload["nodes"] if item.get("domId") == "choice")
            self.assertEqual(checkbox["style"]["accentColor"], "rgb(20, 120, 240)")
            self.assertEqual(checkbox["style"]["appearance"], "auto")
            self.assertEqual(checkbox["controlStateStyles"]["checked"]["backgroundColor"], "rgb(20, 120, 240)")


if __name__ == "__main__":
    unittest.main()
