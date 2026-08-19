from __future__ import annotations

import os
from pathlib import Path
import tomllib

import pytest

from publisher.app.cli import (
    BatchUploadResult,
    _build_parser,
    _batch_progress,
    _load_env_file,
    _print_pipeline_report,
    _publisher_cli_version,
    _run_admin_batch_upload,
    main,
)
from publisher.domain.models import PublishContext, SkillSource
from publisher.registry.client import ExistingSkill, ExistingSkillVersion


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


def test_publish_cli_defaults_to_the_public_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("APTITUDE_REGISTRY_URL", "APTITUDE_SERVER_BASE_URL", "APP_PORT"):
        monkeypatch.delenv(name, raising=False)

    args = _build_parser().parse_args(["publish", "skills/example"])

    assert args.registry_url == "https://api.aptitude-registry.dev"


def test_mcp_subcommand_runs_stdio_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr("publisher.app.cli._load_local_env_defaults", lambda: None)
    monkeypatch.setattr(
        "publisher.interfaces.mcp.main.main",
        lambda: calls.append(True),
    )

    assert main(["mcp"]) == 0
    assert calls == [True]


def test_menu_subcommand_is_not_registered() -> None:
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["menu"])


def test_local_env_fills_empty_values_without_overriding_shell_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "")
    _load_env_file(env_file)
    assert os.environ["OPENAI_API_KEY"] == "from-dotenv"

    monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
    _load_env_file(env_file)
    assert os.environ["OPENAI_API_KEY"] == "from-shell"


def test_pipeline_report_defaults_to_a_three_phase_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A normal scan must not dump implementation-stage diagnostics."""
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.validation.passed = True
    context.security.score = 1.0
    context.security.decision = "allow"
    context.metadata.maturity_score = 0.4
    context.metadata.extra["upskill_evaluation"] = {
        "status": "failed",
        "reason": "upskill exited with status 1",
    }
    context.ranking.label = "review"
    context.ranking.publish_decision = "allow"
    context.add_gate_result(gate_name="discovery_gate", passed=True)
    context.add_gate_result(gate_name="security_gate", passed=True)
    context.add_gate_result(gate_name="validation_gate", passed=True)
    context.add_gate_result(
        gate_name="performance_exam_gate",
        passed=False,
        blocking_issues=["Upskill evaluation did not produce scored performance evidence."],
    )

    _print_pipeline_report(context)

    output = capsys.readouterr().out
    assert "Phase" in output
    assert "Structure" in output
    assert "Risk" in output
    assert "Quality" in output
    assert "upskill exited with status 1" in output
    assert "Upskill evaluation did not produce scored performance evidence." not in output
    assert "Stages" not in output
    assert "Gate Results" not in output
    assert "Security score" not in output


def test_pipeline_report_verbose_keeps_only_phase_summaries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verbose output must reveal phase detail, not the old pipeline trace."""
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.validation.passed = True
    context.security.score = 1.0
    context.security.decision = "allow"
    context.performance_exam.score = 0.7
    context.metadata.maturity_score = 0.8
    context.ranking.total_score = 0.75
    context.ranking.label = "review"
    context.metadata.extra["upskill_evaluation"] = {
        "status": "failed",
        "reason": "upskill exited with status 1",
    }
    context.add_gate_result(gate_name="discovery_gate", passed=True)
    context.add_gate_result(gate_name="security_gate", passed=True)
    context.add_gate_result(gate_name="validation_gate", passed=True)

    _print_pipeline_report(context, verbose=True)

    output = capsys.readouterr().out
    assert "Phase      Grade" not in output
    assert "Structure Validation" in output
    assert "Risk Validation" in output
    assert "Quality Evaluation" in output
    assert "Final Scores" in output
    assert "LLM Guard status" in output
    assert "Upskill status" in output
    assert "Safety score" in output
    assert "Security score" in output
    assert "Performance score" in output
    assert "Maturity score" in output
    assert "Overall score" not in output
    assert "10.0 / 10.0" in output
    assert "8.0 / 10.0" in output
    assert "Reason" in output
    assert "upskill exited with status 1" in output
    assert "Stages" not in output
    assert "Gate Results" not in output


def test_inspect_parser_supports_verbose_phase_summaries() -> None:
    parser = _build_parser()

    args = parser.parse_args(["inspect", "skills/example"])

    assert args.verbose is True


def test_inspect_parser_can_disable_verbose_phase_summaries() -> None:
    parser = _build_parser()

    args = parser.parse_args(["inspect", "skills/example", "--no-verbose"])

    assert args.verbose is False


def test_pipeline_report_summarizes_the_first_security_finding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.validation.passed = True
    context.security.score = 0.6
    context.security.decision = "review_required"
    context.security.findings = [{"severity": "high", "check": "prompt injection"}]

    _print_pipeline_report(context)

    assert "high: prompt injection" in capsys.readouterr().out


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
    assert args.scan_profile == "fast"
    assert args.trust_tier == "verified"
    assert args.artifact_origin == "verified"


def test_admin_batch_upload_parser_accepts_full_scan_profile() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["admin-batch-upload", "skills/a", "--scan-profile", "full"]
    )

    assert args.scan_profile == "full"


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
    monkeypatch.setattr(
        "publisher.app.cli._upload_one_batch_skill",
        lambda *args, **kwargs: pytest.fail("batch upload should fail before workers"),
    )

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
    assert "Scan profile" in output
    assert "Trust tier" in output
    assert "Origin" in output
    assert "fast" in output
    assert "verified" in output
    assert "Evaluation Summary" not in output
    assert "Stages" not in output


def test_admin_batch_upload_uses_fast_scan_environment_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = _build_parser()
    args = parser.parse_args(["admin-batch-upload", "skills/a", "--dry-run"])
    captured: dict[str, str | None] = {}

    def fake_upload_one(index, skill_path, args):
        captured["threshold"] = os.environ.get(
            "PUBLISHER_LLM_GUARD_PROMPT_INJECTION_THRESHOLD"
        )
        captured["max_chars"] = os.environ.get("PUBLISHER_LLM_GUARD_MAX_TEXT_CHARS")
        captured["default_tests"] = os.environ.get("UPSKILL_USE_DEFAULT_TESTS")
        captured["timeout"] = os.environ.get("PUBLISHER_UPSKILL_TIMEOUT_SECONDS")
        return BatchUploadResult(
            index=index,
            path=skill_path,
            slug="skill-a",
            version="0.1.0",
            status="ready",
            message="bundle 1 bytes",
        )

    monkeypatch.setattr("publisher.app.cli._upload_one_batch_skill", fake_upload_one)

    assert _run_admin_batch_upload(args) == 0
    assert captured == {
        "threshold": "0.90",
        "max_chars": "40000",
        "default_tests": "true",
        "timeout": "120",
    }


def test_publish_requires_token_before_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("publisher.app.cli._load_local_env_defaults", lambda: None)
    monkeypatch.delenv("APTITUDE_PUBLISH_TOKEN", raising=False)
    monkeypatch.delenv("APTITUDE_INTEGRATION_PUBLISH_TOKEN", raising=False)
    monkeypatch.delenv("PUBLISH_TOKEN", raising=False)
    monkeypatch.setattr(
        "publisher.app.cli._run_pipeline",
        lambda *args, **kwargs: pytest.fail("publish should fail before the pipeline"),
    )

    assert main(["publish", "skills/a"]) == 1

    output = capsys.readouterr().out
    assert "Missing publish token" in output
    assert "APTITUDE_PUBLISH_TOKEN" in output
    assert "APTITUDE_INTEGRATION_PUBLISH_TOKEN" in output
    assert "PUBLISH_TOKEN" in output


def test_publish_blocks_existing_create_slug_before_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skill_dir = _write_skill(tmp_path, name="python-patterns", intent="create_skill")
    monkeypatch.setattr("publisher.app.cli._load_local_env_defaults", lambda: None)
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-token")
    monkeypatch.setattr(
        "publisher.app.cli.get_existing_skill",
        lambda **kwargs: ExistingSkill(
            slug="python-patterns",
            versions=(ExistingSkillVersion(version="1.0.0"),),
        ),
    )
    monkeypatch.setattr(
        "publisher.app.cli._run_pipeline",
        lambda *args, **kwargs: pytest.fail("publish should fail before the pipeline"),
    )

    assert main(["publish", str(skill_dir)]) == 1

    output = capsys.readouterr().out
    assert "Existing Slug Check" in output
    assert "this slug already exists" in output


def test_admin_batch_blocks_existing_create_slug_before_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skill_dir = _write_skill(tmp_path, name="python-patterns", intent="create_skill")
    monkeypatch.setattr("publisher.app.cli._load_local_env_defaults", lambda: None)
    monkeypatch.setenv("APTITUDE_ADMIN_TOKEN", "admin-token")
    monkeypatch.setattr(
        "publisher.app.cli.get_existing_skill",
        lambda **kwargs: ExistingSkill(
            slug="python-patterns",
            versions=(ExistingSkillVersion(version="1.0.0"),),
        ),
    )
    monkeypatch.setattr(
        "publisher.app.cli._run_pipeline",
        lambda *args, **kwargs: pytest.fail("batch upload should fail before the pipeline"),
    )

    assert main(["admin-batch-upload", str(skill_dir)]) == 1

    output = capsys.readouterr().out
    assert "Admin Batch Upload" in output
    assert "blocked" in output
    assert "slug already exists" in output


def test_admin_batch_upload_progress_bar_is_persistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProgress:
        def __init__(self, *args, **kwargs):
            captured["transient"] = kwargs.get("transient")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def add_task(self, description, *, total):
            captured["description"] = description
            captured["total"] = total
            return "task"

        def update(self, task, *, description, advance):
            captured["update_task"] = task
            captured["update_description"] = description
            captured["advance"] = advance

    monkeypatch.setattr("publisher.app.cli.Progress", FakeProgress)

    with _batch_progress(total=2) as progress:
        progress.advance(status="uploaded")

    assert captured == {
        "transient": False,
        "description": "Running batch upload",
        "total": 2,
        "update_task": "task",
        "update_description": "Processed 1/2: uploaded",
        "advance": 1,
    }


def _pyproject_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _write_skill(tmp_path: Path, *, name: str, intent: str) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: "Use when testing publisher preflight behavior."
metadata:
  version: 0.1.0
  intent: {intent}
---

# {name}

Use this skill for publisher unit tests.
""",
        encoding="utf-8",
    )
    return skill_dir
