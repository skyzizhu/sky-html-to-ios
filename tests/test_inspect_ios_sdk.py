#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_ios_sdk.py"
SPEC = importlib.util.spec_from_file_location("inspect_ios_sdk", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InspectIOSSDKTests(unittest.TestCase):
    def test_extension_method_inherits_outer_availability(self) -> None:
        source = """
@available(iOS 16.0, macOS 13.0, *)
extension View {
  public func firstModifier() -> some View

  public func presentationDetents() -> some View
}
"""
        item = MODULE.find_symbol("presentationDetents", [(Path("SwiftUI.swiftinterface"), source)], "swiftui")
        self.assertTrue(item["found"])
        self.assertEqual(item["introduced"], "16.0")
        self.assertEqual(MODULE.assess(item, "16.0"), "available")
        self.assertEqual(MODULE.assess(item, "15.0"), "requires-fallback")

    def test_method_level_availability_inside_extension_is_preserved(self) -> None:
        source = """
extension View {
  @available(iOS 17.0, macOS 14.0, *)
  public func visualEffect() -> some View
}
"""
        item = MODULE.find_symbol("visualEffect", [(Path("SwiftUICore.swiftinterface"), source)], "swiftui")
        self.assertEqual(item["introduced"], "17.0")


if __name__ == "__main__":
    unittest.main()
