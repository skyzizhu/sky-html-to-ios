#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_responsive_ios_matrix.py"
SPEC = importlib.util.spec_from_file_location("responsive_ios_matrix", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ResponsiveIOSMatrixTests(unittest.TestCase):
    def test_case_parser_and_initial_manifest(self) -> None:
        case = MODULE.parse_case("393x852:iPhone 16")
        self.assertEqual(case, {"width": 393, "height": 852, "device": "iPhone 16"})
        manifest = {"states": [{"id": "initial"}, {"id": "selected"}], "targetViewport": {"width": 430, "height": 932}}
        result = MODULE.initial_manifest(manifest, 375, 667)
        self.assertEqual(result["states"], [{"id": "initial"}])
        self.assertEqual(result["targetViewport"], {"width": 375, "height": 667})

    def test_device_viewport_validation_requires_real_retina_dimensions(self) -> None:
        matching = MODULE.validate_device_viewport(
            {"originalSize": {"width": 1179, "height": 2556}},
            393,
            852,
        )
        self.assertEqual(matching["status"], "passed")
        self.assertEqual(matching["expectedNativeScale"], 3)

        mismatched = MODULE.validate_device_viewport(
            {"originalSize": {"width": 1179, "height": 2556}},
            320,
            568,
        )
        self.assertEqual(mismatched["status"], "failed")
        self.assertEqual(
            mismatched["reason"],
            "simulator-screenshot-does-not-match-declared-viewport",
        )

    def test_geometry_analysis_exempts_nested_horizontal_scroller(self) -> None:
        manifest = {
            "geometryNodes": [
                {"nodeId": "root", "parentNodeId": None, "scrollAxis": "vertical"},
                {"nodeId": "carousel", "parentNodeId": "root", "scrollAxis": "horizontal"},
                {"nodeId": "carousel.item", "parentNodeId": "carousel", "scrollAxis": "none"},
                {"nodeId": "bad", "parentNodeId": "root", "scrollAxis": "none"},
                {"nodeId": "decor", "parentNodeId": "root", "scrollAxis": "none", "isDecorative": True},
                {"nodeId": "container", "parentNodeId": "root", "scrollAxis": "none", "hasChildren": True},
            ],
            "validationRegions": [{"nodeId": "root"}, {"nodeId": "bad"}],
        }
        geometry = {
            "nodes": [
                {"nodeId": "root", "frame": {"x": 0, "y": 0, "width": 393, "height": 852}},
                {"nodeId": "carousel.item", "frame": {"x": 380, "y": 100, "width": 100, "height": 40}},
                {"nodeId": "bad", "frame": {"x": 370, "y": 300, "width": 80, "height": 40}},
                {"nodeId": "decor", "frame": {"x": 390, "y": 20, "width": 100, "height": 100}},
                {"nodeId": "container", "frame": {"x": 0, "y": 0, "width": 500, "height": 300}},
            ]
        }
        report = MODULE.analyze_geometry(manifest, geometry, 393)
        self.assertEqual(report["status"], "review-required")
        self.assertEqual([item["nodeId"] for item in report["unownedHorizontalOverflowNodes"]], ["bad"])
        carousel = next(item for item in report["horizontalOverflowNodes"] if item["nodeId"] == "carousel.item")
        self.assertTrue(carousel["ownedByHorizontalScroller"])
        self.assertEqual(report["validationGeometryCaptureRate"], 1.0)


if __name__ == "__main__":
    unittest.main()
