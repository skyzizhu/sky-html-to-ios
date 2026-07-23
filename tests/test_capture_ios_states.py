#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capture_ios_states.py"
SPEC = importlib.util.spec_from_file_location("capture_ios_states", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CaptureIOSStatesTests(unittest.TestCase):
    def test_xcresult_renamed_attachment_is_matched_by_test_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            export_dir = Path(temporary)
            exported = export_dir / "A1B2C3.png"
            Image.new("RGB", (30, 30), "white").save(exported)
            manifest = [{
                "testIdentifier": "HTMLToIOSVisualStateTests/test_2_after_sheet()",
                "attachments": [{
                    "exportedFileName": exported.name,
                    "suggestedHumanReadableName": "after-sheet_0_RANDOM.png",
                }],
            }]
            resolved = MODULE.find_exported_attachment(export_dir, manifest, "after-sheet", 2)
            self.assertEqual(resolved, exported)

    def test_retina_capture_is_normalized_to_logical_viewport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "retina.png", root / "logical.png"
            Image.new("RGB", (1179, 2556), "white").save(source)
            report = MODULE.normalize(source, destination, 393, 852)
            self.assertEqual(report["originalSize"], {"width": 1179, "height": 2556})
            self.assertEqual(report["outputSize"], {"width": 393, "height": 852})
            self.assertTrue(report["normalized"])
            with Image.open(destination) as image:
                self.assertEqual(image.size, (393, 852))


if __name__ == "__main__":
    unittest.main()
