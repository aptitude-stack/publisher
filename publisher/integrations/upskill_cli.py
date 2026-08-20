"""Compatibility launcher for Upskill 0.2.1."""

from __future__ import annotations

import os
from pathlib import Path

from upskill import cli

_ORIGINAL_LOAD_AGENTS = cli.FastAgent.load_agents


def load_agents(fast: cli.FastAgent, path: str | Path) -> list[str]:
    """Replace Upskill 0.2.1's hardcoded test-generation model before startup."""
    loaded = _ORIGINAL_LOAD_AGENTS(fast, path)
    model = os.environ.get("PUBLISHER_UPSKILL_TEST_GEN_MODEL")
    if model:
        fast.agents["test_gen"]["config"].model = model
    return loaded


def main() -> None:
    cli.FastAgent.load_agents = load_agents
    cli.main()


if __name__ == "__main__":
    main()
