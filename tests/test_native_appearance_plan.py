#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_native_appearance_plan.py"
VALIDATE = ROOT / "scripts" / "validate_native_appearance_plan.py"


class NativeAppearancePlanTests(unittest.TestCase):
    def test_appearance_keeps_asymmetric_geometry_and_typography_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir = root / "ui-ir.json"
            layout = root / "layout.json"
            plan = root / "appearance.json"
            appearance = {
                "cornerRadiiXPt": [4, 8, 12, 16],
                "cornerRadiiYPt": [5, 9, 13, 17],
                "borderWidthsPt": [1, 2, 3, 4],
                "borderColors": ["red", "green", "blue", "black"],
                "borderStyles": ["solid", "solid", "dashed", "solid"],
            }
            ir.write_text(json.dumps({
                "schemaVersion": "1.2", "screens": [{"id": "home", "nodes": [{
                    "id": "home.card", "style": {
                        "fontFamily": "PingFang SC", "fontSize": "16px", "fontWeight": "600",
                        "lineHeight": "24px", "letterSpacing": "0px", "textAlign": "start",
                        "objectFit": "cover", "objectPosition": "25% 50%",
                    },
                }]}],
            }), encoding="utf-8")
            layout.write_text(json.dumps({
                "schemaVersion": "native-layout-plan-1.1",
                "screens": [{"screenId": "home", "nodes": [{"nodeId": "home.card", "appearance": appearance}]}],
            }), encoding="utf-8")
            result = subprocess.run([
                "python3", str(BUILD), "--ir", str(ir), "--native-layout-plan", str(layout), "--out", str(plan),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            node = json.loads(plan.read_text(encoding="utf-8"))["screens"][0]["nodes"][0]
            self.assertEqual(node["cornerRadiiXPt"], [4, 8, 12, 16])
            self.assertEqual(node["cornerRadiiYPt"], [5, 9, 13, 17])
            self.assertEqual(node["borderWidthsPt"], [1, 2, 3, 4])
            self.assertEqual(node["typography"]["metricOwner"], "native-layout-plan")
            self.assertEqual(node["media"]["objectFit"], "cover")
            report = root / "validation.json"
            validated = subprocess.run([
                "python3", str(VALIDATE), "--plan", str(plan),
                "--native-layout-plan", str(layout), "--out", str(report),
            ], text=True, capture_output=True)
            self.assertEqual(validated.returncode, 0, validated.stderr or validated.stdout)


if __name__ == "__main__":
    unittest.main()
