#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_skill.py"
SPEC = importlib.util.spec_from_file_location("validate_skill", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ValidateSkillTests(unittest.TestCase):
    def write_skill(self, root: Path, frontmatter: str, body: str = "# Skill\n") -> None:
        (root / "SKILL.md").write_text(
            f"---\n{frontmatter}\n---\n\n{body}",
            encoding="utf-8",
        )

    def test_accepts_valid_skill_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_skill(root, "name: example-skill\ndescription: A focused example skill.")
            self.assertEqual(MODULE.validate_skill(root), [])

    def test_rejects_invalid_name_missing_description_and_unknown_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_skill(root, "name: Example Skill\nunknown: value")
            errors = MODULE.validate_skill(root)
            self.assertTrue(any("Unexpected" in item for item in errors))
            self.assertTrue(any("hyphen-case" in item for item in errors))
            self.assertTrue(any("description" in item for item in errors))

    def test_rejects_skill_body_over_progressive_disclosure_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_skill(
                root,
                "name: example-skill\ndescription: A focused example skill.",
                "\n".join(f"line {index}" for index in range(510)),
            )
            self.assertTrue(any("500" in item for item in MODULE.validate_skill(root)))


if __name__ == "__main__":
    unittest.main()
