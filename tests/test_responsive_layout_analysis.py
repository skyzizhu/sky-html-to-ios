#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze_responsive_layout.cjs"
NODE = Path(os.environ.get("HTML_TO_IOS_NODE", "/Users/skyzizhu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"))
NODE_MODULES = "/Users/skyzizhu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"


@unittest.skipUnless(NODE.is_file() and Path(NODE_MODULES).is_dir(), "bundled Node/Playwright runtime unavailable")
class ResponsiveLayoutAnalysisTests(unittest.TestCase):
    def analyze(self, html: str) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.html"
            output = root / "layout.json"
            source.write_text(html, encoding="utf-8")
            environment = dict(os.environ)
            environment["NODE_PATH"] = NODE_MODULES
            result = subprocess.run([
                str(NODE), str(SCRIPT), "--html", str(source), "--out", str(output),
                "--widths", "320,375,393,430", "--height", "852", "--baseline-width", "393",
            ], text=True, capture_output=True, check=False, env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(output.read_text(encoding="utf-8"))

    def test_responsive_document_is_accepted_at_mobile_viewports(self) -> None:
        result = self.analyze("""
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
              html,body{margin:0} main{min-height:900px;padding:16px;box-sizing:border-box}
              .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
              @media(max-width:600px){.grid{grid-template-columns:1fr}}
            </style><main><div class="grid"><section>A</section><section>B</section></div></main>
        """)
        classification = result["sourceClassification"]
        self.assertEqual(classification["kind"], "responsive-document")
        self.assertEqual(classification["conversionStatus"], "automatic")
        self.assertFalse(classification["hasMaterialHorizontalOverflow"])

    def test_desktop_min_width_is_blocked_instead_of_scaled_to_phone(self) -> None:
        result = self.analyze("""
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>html,body{margin:0}.desktop{min-width:1000px;height:900px;display:grid;grid-template-columns:240px 1fr}</style>
            <main class="desktop"><nav>Navigation</nav><section>Desktop content</section></main>
        """)
        classification = result["sourceClassification"]
        self.assertEqual(classification["kind"], "desktop-only")
        self.assertEqual(classification["conversionStatus"], "blocked-needs-scope-or-redesign-consent")
        self.assertTrue(classification["hasMaterialHorizontalOverflow"])


if __name__ == "__main__":
    unittest.main()
