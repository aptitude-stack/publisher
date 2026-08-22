from __future__ import annotations

import sys

from publisher.integrations import external_tools
from publisher.integrations.external_tools import resolve_executable, run_command


def test_resolve_executable_finds_windows_venv_scripts_exe(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    skill_root = project / "skills" / "sample"
    scripts_dir = project / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    skill_root.mkdir(parents=True)
    upskill = scripts_dir / "upskill.exe"
    upskill.write_text("", encoding="utf-8")
    upskill.chmod(0o755)

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(external_tools.os, "name", "nt")
    monkeypatch.setattr(external_tools.sys, "executable", str(tmp_path / "python.exe"))

    assert resolve_executable("upskill", start=skill_root) == str(upskill)


def test_run_command_decodes_utf8_output(tmp_path) -> None:
    completed = run_command(
        [sys.executable, "-c", "print('\\u258e')"],
        cwd=tmp_path,
        timeout_seconds=10,
    )

    assert completed.returncode == 0
    assert "\u258e" in completed.stdout
