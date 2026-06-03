from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from publisher.app.cli import _build_parser, _publisher_cli_version, main


def test_publisher_cli_version_is_available() -> None:
    assert _publisher_cli_version() == _pyproject_version()


def test_root_version_flag_reports_publisher_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_parser()
    expected_version = _pyproject_version()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"aptitude-publisher {expected_version}"


def test_root_command_launches_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_run_menu() -> int:
        called.append(True)
        return 0

    monkeypatch.setattr("publisher.app.menu.run_menu", fake_run_menu)

    assert main([]) == 0
    assert called == [True]


def test_root_help_still_prints_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert "usage: aptitude-publisher" in capsys.readouterr().out


def _pyproject_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(data["project"]["version"])
