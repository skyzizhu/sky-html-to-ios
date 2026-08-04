#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/presentation-state-gallery.html"
NODE = next((path for path in Path.home().glob(".cache/codex-runtimes/*/dependencies/node/bin/node") if path.is_file()), None)


class PresentationStateGalleryTests(unittest.TestCase):
    def test_gallery_authoring_contract_is_valid(self) -> None:
        completed = subprocess.run(["python3", str(ROOT / "scripts/validate_html_authoring_contract.py"), str(FIXTURE)], text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["summary"]["errors"], 0)

    def test_browser_extraction_preserves_presentation_and_keyboard_evidence(self) -> None:
        if NODE is None:
            self.skipTest("Bundled Node.js is unavailable")
        environment = {**os.environ, "NODE_PATH": str(NODE.parents[1] / "node_modules")}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            render = root / "render.json"
            ir = root / "ir.json"
            completed = subprocess.run([str(NODE), str(ROOT / "scripts/extract_render_tree.cjs"), "--html", str(FIXTURE), "--out", str(render), "--selector", "#app", "--width", "393", "--height", "852"], env=environment, text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = subprocess.run(["python3", str(ROOT / "scripts/build_ui_ir.py"), str(render), "--out", str(ir), "--screen-id", "gallery", "--root-selector", "#app", "--ui-stack", "swiftui", "--target-width", "393", "--target-height", "852"], text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            nodes = json.loads(ir.read_text(encoding="utf-8"))["screens"][0]["nodes"]
            styles = {(node.get("source") or {}).get("ios", {}).get("presentationStyle") for node in nodes}
            self.assertTrue({"page-sheet", "alert", "popover"}.issubset(styles))
            self.assertTrue(any((node.get("textBehavior") or {}).get("editable") for node in nodes))


if __name__ == "__main__":
    unittest.main()
