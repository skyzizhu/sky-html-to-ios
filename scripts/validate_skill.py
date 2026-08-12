#!/usr/bin/env python3
"""Validate this Codex Skill without third-party Python packages."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ALLOWED_KEYS = {"name", "description", "license", "allowed-tools", "metadata"}
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_LINES = 500


def scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def top_level_frontmatter(content: str) -> tuple[dict[str, str], str | None]:
    if not content.startswith("---\n"):
        return {}, "SKILL.md must start with YAML frontmatter."
    closing = content.find("\n---", 4)
    if closing < 0:
        return {}, "SKILL.md frontmatter is not closed."
    values: dict[str, str] = {}
    for line in content[4:closing].splitlines():
        if not line.strip() or line[:1].isspace():
            continue
        match = re.fullmatch(r"([A-Za-z0-9_-]+):(?:\s*(.*))?", line)
        if not match:
            return {}, f"Invalid top-level frontmatter line: {line}"
        values[match.group(1)] = scalar(match.group(2) or "")
    return values, None


def validate_skill(skill_root: Path) -> list[str]:
    errors: list[str] = []
    skill_path = skill_root / "SKILL.md"
    if not skill_path.is_file():
        return ["SKILL.md not found."]
    content = skill_path.read_text(encoding="utf-8")
    frontmatter, parse_error = top_level_frontmatter(content)
    if parse_error:
        return [parse_error]
    unexpected = sorted(set(frontmatter) - ALLOWED_KEYS)
    if unexpected:
        errors.append("Unexpected frontmatter keys: " + ", ".join(unexpected))
    name = frontmatter.get("name", "").strip()
    description = frontmatter.get("description", "").strip()
    if not name:
        errors.append("Missing non-empty name in SKILL.md frontmatter.")
    elif not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        errors.append("Skill name must use lowercase hyphen-case.")
    elif len(name) > MAX_SKILL_NAME_LENGTH:
        errors.append(f"Skill name exceeds {MAX_SKILL_NAME_LENGTH} characters.")
    if not description:
        errors.append("Missing non-empty description in SKILL.md frontmatter.")
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"Skill description exceeds {MAX_DESCRIPTION_LENGTH} characters.")
    elif "<" in description or ">" in description:
        errors.append("Skill description cannot contain angle brackets.")
    line_count = len(content.splitlines())
    if line_count > MAX_SKILL_LINES:
        errors.append(
            f"SKILL.md has {line_count} lines; keep it at or below {MAX_SKILL_LINES} "
            "and move details into references/."
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    errors = validate_skill(args.skill_root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Skill is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
