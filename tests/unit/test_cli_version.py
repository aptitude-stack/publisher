from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from publisher.app.cli import (
    BatchUploadResult,
    _build_parser,
    _publisher_cli_version,
    main,
)


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


def test_menu_subcommand_is_not_registered() -> None:
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["menu"])


def test_admin_batch_upload_parser_uses_admin_token_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APTITUDE_ADMIN_TOKEN", "admin-token")

    parser = _build_parser()
    args = parser.parse_args(["admin-batch-upload", "skills/a", "skills/b"])

    assert args.command == "admin-batch-upload"
    assert args.skill_paths == ["skills/a", "skills/b"]
    assert args.admin_token == "admin-token"
    assert args.concurrency == 4


def test_admin_batch_upload_rejects_global_identity_overrides() -> None:
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["admin-batch-upload", "skills/a", "--slug", "same-slug"])


def test_admin_batch_upload_requires_token_for_upload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("publisher.app.cli._load_local_env_defaults", lambda: None)
    monkeypatch.delenv("APTITUDE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("APTITUDE_REGISTRY_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("REGISTRY_ADMIN_TOKEN", raising=False)

    assert main(["admin-batch-upload", "skills/a"]) == 1

    output = capsys.readouterr().out
    assert "Missing admin token" in output


def test_admin_batch_upload_prints_summary_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("publisher.app.cli._load_local_env_defaults", lambda: None)
    monkeypatch.setenv("APTITUDE_ADMIN_TOKEN", "admin-token")

    def fake_upload_one(index, skill_path, args):
        return BatchUploadResult(
            index=index,
            path=skill_path,
            slug=f"skill-{index}",
            version="0.1.0",
            status="uploaded",
            http_status=201,
            message="accepted",
        )

    monkeypatch.setattr("publisher.app.cli._upload_one_batch_skill", fake_upload_one)

    assert (
        main(["admin-batch-upload", "skills/a", "skills/b", "--concurrency", "8"])
        == 0
    )

    output = capsys.readouterr().out
    assert "Admin Batch Upload" in output
    assert "Summary" in output
    assert "Running local scans" not in output
    assert "skill-1" in output
    assert "skill-2" in output
    assert "uploaded" in output
    assert "Evaluation Summary" not in output
    assert "Stages" not in output


def _pyproject_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(data["project"]["version"])
