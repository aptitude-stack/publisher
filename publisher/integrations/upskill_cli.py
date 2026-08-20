"""Compatibility launcher for Upskill 0.2.1."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from upskill import cli, generate
from upskill.models import TestCase

_ORIGINAL_LOAD_AGENTS = cli.FastAgent.load_agents
_ORIGINAL_GENERATE_TESTS = cli.generate_tests
_TEST_GENERATION_CONSTRAINT = (
    "Use exactly two short, essential, non-synonymous expected strings per test case."
)


def load_agents(fast: cli.FastAgent, path: str | Path) -> list[str]:
    """Replace Upskill 0.2.1's hardcoded test-generation model before startup."""
    loaded = _ORIGINAL_LOAD_AGENTS(fast, path)
    model = os.environ.get("PUBLISHER_UPSKILL_TEST_GEN_MODEL")
    if model:
        fast.agents["test_gen"]["config"].model = model
    return loaded


async def generate_tests(*args: Any, **kwargs: Any) -> list[TestCase]:
    """Keep generated exact-text checks focused enough to be discriminative."""
    cases = await _ORIGINAL_GENERATE_TESTS(*args, **kwargs)
    for case in cases:
        case.expected.contains = case.expected.contains[:2]
    return cases


def main() -> None:
    if _TEST_GENERATION_CONSTRAINT not in generate.TEST_GENERATION_PROMPT:
        generate.TEST_GENERATION_PROMPT += f"\n\n{_TEST_GENERATION_CONSTRAINT}"
    cli.FastAgent.load_agents = load_agents
    cli.generate_tests = generate_tests
    cli.main()


if __name__ == "__main__":
    main()
