"""Compatibility launcher for Upskill 0.2.1."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
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
    model = _publisher_upskill_model()
    if model:
        for agent_name in ("test_gen", "evaluator", "skill_gen"):
            if agent_name in fast.agents:
                fast.agents[agent_name]["config"].model = model
    return loaded


@asynccontextmanager
async def fast_agent_context() -> AsyncIterator[object]:
    """Create Upskill agents with the publisher-selected model before startup."""
    model = _publisher_upskill_model()
    fast = cli.FastAgent(
        "upskill",
        ignore_unknown_args=True,
    )

    @fast.agent(model=model)
    async def empty():
        pass

    cards = resources.files("upskill").joinpath("agent_cards")
    with resources.as_file(cards) as cards_path:
        fast.load_agents(cards_path)

    async with fast.run() as agent:
        yield agent


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
    cli._fast_agent_context = fast_agent_context
    cli.generate_tests = generate_tests
    cli.main()


def _publisher_upskill_model() -> str | None:
    return os.environ.get("PUBLISHER_UPSKILL_TEST_GEN_MODEL")


if __name__ == "__main__":
    main()
