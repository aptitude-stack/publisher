"""Compatibility launcher for Upskill 0.2.1."""

from __future__ import annotations

from typing import Any

from upskill import cli
from upskill.generate import generate_tests as upstream_generate_tests


async def generate_tests(task: str, generator: Any, model: str | None = None) -> Any:
    """Apply the CLI model ignored by Upskill 0.2.1's test generator."""
    if model:
        await generator.set_model(model)
    return await upstream_generate_tests(task, generator, model=model)


def main() -> None:
    cli.generate_tests = generate_tests
    cli.main()


if __name__ == "__main__":
    main()
