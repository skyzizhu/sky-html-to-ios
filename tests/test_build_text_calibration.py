#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_text_calibration.py"


def text_node(runtime_id: str, y: float, family: str, resolution: dict | None) -> dict:
    metrics = {
        "renderedText": runtime_id,
        "lineCount": 1,
        "lineRects": [{"x": 16, "y": y, "width": 100, "height": 20}],
        "firstBaselineY": y + 15,
        "lastBaselineY": y + 15,
        "fontLoaded": True,
        "clippedHorizontally": False,
        "clippedVertically": False,
    }
    if resolution is not None:
        metrics["fontResolution"] = resolution
    return {
        "runtimeId": runtime_id,
        "parentRuntimeId": "root",
        "selector": f"#{runtime_id}",
        "rect": {"x": 16, "y": y, "width": 100, "height": 20},
        "visible": True,
        "style": {
            "fontFamily": family,
            "fontSize": "16px",
            "fontWeight": "400",
            "fontStyle": "normal",
            "lineHeight": "20px",
            "letterSpacing": "0px",
        },
        "textMetrics": metrics,
    }


class BuildTextCalibrationTests(unittest.TestCase):
    def test_font_resolution_prevents_fallback_stack_from_being_reported_as_loaded_web_font(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            render_tree = root / "render-tree.json"
            output = root / "text-calibration.json"
            payload = {
                "document": {"viewport": {"width": 393}, "rootSelector": "#root", "loadedFonts": []},
                "phoneCandidates": [],
                "nodes": [
                    {
                        "runtimeId": "root",
                        "parentRuntimeId": None,
                        "selector": "#root",
                        "rect": {"x": 0, "y": 0, "width": 393, "height": 852},
                        "visible": True,
                        "style": {},
                        "textMetrics": None,
                    },
                    text_node(
                        "fallback",
                        40,
                        '"Missing Web Font", monospace',
                        {
                            "requestedFamilies": ["missing web font", "monospace"],
                            "resolvedFamily": "monospace",
                            "status": "generic-fallback",
                            "failedFamilies": ["missing web font"],
                            "confidence": 0.9,
                        },
                    ),
                    text_node(
                        "loaded",
                        80,
                        '"Bundled Font", sans-serif',
                        {
                            "requestedFamilies": ["bundled font", "sans-serif"],
                            "resolvedFamily": "bundled font",
                            "status": "loaded-web-font",
                            "failedFamilies": [],
                            "confidence": 1,
                        },
                    ),
                    text_node("legacy", 120, '"Unknown Font", sans-serif', None),
                ],
            }
            render_tree.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                ["python3", str(SCRIPT), str(render_tree), "--out", str(output)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            by_id = {item["nodeId"]: item for item in report["items"]}
            self.assertEqual(by_id["fallback"]["font"]["status"], "browser-generic-fallback")
            self.assertEqual(by_id["fallback"]["font"]["resolvedFamily"], "monospace")
            self.assertEqual(by_id["loaded"]["font"]["status"], "web-font-loaded-needs-ios-file")
            self.assertEqual(by_id["legacy"]["font"]["status"], "fallback-risk")
            self.assertEqual(report["summary"]["fontFileRequired"], 1)
            self.assertEqual(report["summary"]["browserFallbacks"], 1)
            self.assertEqual(report["summary"]["fallbackRisks"], 1)


if __name__ == "__main__":
    unittest.main()
