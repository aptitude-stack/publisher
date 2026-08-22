"""Helpers for optional external publisher tools."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping


def configured_bool(name: str, *, default: bool) -> bool:
    """Read a boolean-like environment flag."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def render_command(template: str, values: Mapping[str, str]) -> list[str]:
    """Render a shell-like command template without invoking a shell."""
    return [part.format(**values) for part in shlex.split(template)]


def resolve_executable(name: str, *, start: Path) -> str | None:
    """Find a command on PATH or in the nearest local project virtualenv."""
    executable = shutil.which(name)
    if executable:
        return executable

    for candidate in _executable_candidates(Path(sys.executable).parent, name):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    for directory in (start, *start.parents):
        for scripts_dir in (directory / ".venv" / "bin", directory / ".venv" / "Scripts"):
            for candidate in _executable_candidates(scripts_dir, name):
                if candidate.exists() and os.access(candidate, os.X_OK):
                    return str(candidate)
    return None


def _executable_candidates(directory: Path, name: str) -> list[Path]:
    candidates = [directory / name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        candidates.append(directory / f"{name}.exe")
    return candidates


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an external command and capture text output."""
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=dict(env) if env is not None else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
