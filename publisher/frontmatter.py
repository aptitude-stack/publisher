"""Shared SKILL.md frontmatter parsing helpers."""

from __future__ import annotations

from typing import Any

import yaml


def parse_skill_markdown(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter and markdown body from a SKILL.md file."""
    if not content.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter.")

    closing_index = content.find("\n---\n", 4)
    if closing_index == -1:
        raise ValueError("SKILL.md frontmatter must end with a closing --- delimiter.")

    frontmatter_text = content[4:closing_index]
    body = content[closing_index + 5 :]
    return parse_frontmatter(frontmatter_text), body


def parse_frontmatter(frontmatter_text: str) -> dict[str, Any]:
    """Parse frontmatter YAML into a mapping."""
    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"SKILL.md frontmatter must be valid YAML: {exc}") from exc

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping.")
    return parsed
