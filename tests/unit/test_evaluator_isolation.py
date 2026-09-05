from __future__ import annotations

import json
from pathlib import Path
import subprocess

from publisher.integrations import upskill_eval
from publisher.integrations import llm_guard_security
from publisher.integrations.upskill_eval import run_upskill_evaluation


def _write_summary(runs_dir: Path) -> None:
    summary_path = runs_dir / "run" / "batch_summary.json"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        json.dumps(
            {
                "model": "openai.gpt-4.1-mini",
                "results": [
                    {
                        "run_type": "baseline",
                        "assertions_passed": 1,
                        "assertions_total": 1,
                        "stats": {"total_tokens": 40},
                    },
                    {
                        "run_type": "with_skill",
                        "assertions_passed": 1,
                        "assertions_total": 1,
                        "stats": {"total_tokens": 20},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _configure_upskill(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("PUBLISHER_UPSKILL_COMMAND", raising=False)
    monkeypatch.delenv("UPSKILL_TESTS_PATH", raising=False)
    monkeypatch.delenv("UPSKILL_BASE_URL", raising=False)
    monkeypatch.delenv("UPSKILL_MODELS", raising=False)


def test_upskill_runs_in_a_copy_and_cleans_workspace_after_success(tmp_path, monkeypatch) -> None:
    _configure_upskill(monkeypatch)
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    original = "original skill"
    (skill_root / "SKILL.md").write_text(original, encoding="utf-8")
    stale_artifacts = skill_root / ".publisher_artifacts"
    stale_artifacts.mkdir()
    (stale_artifacts / "stale.json").write_text("stale", encoding="utf-8")
    linked_file = tmp_path / "linked-temporary-file.txt"
    linked_file.write_text("copy file contents", encoding="utf-8")
    (skill_root / "linked-file.txt").symlink_to(linked_file)

    captured: dict[str, Path] = {}

    def fake_run(command, **kwargs):
        evaluator_root = Path(kwargs["cwd"])
        captured["cwd"] = evaluator_root
        assert evaluator_root != skill_root
        assert evaluator_root.name == skill_root.name
        assert (evaluator_root / "SKILL.md").read_text(encoding="utf-8") == original
        assert not (evaluator_root / ".publisher_artifacts").exists()
        copied_linked_file = evaluator_root / "linked-file.txt"
        assert copied_linked_file.is_file()
        assert not copied_linked_file.is_symlink()
        assert copied_linked_file.read_text(encoding="utf-8") == "copy file contents"
        (evaluator_root / "SKILL.md").write_text("evaluator mutation", encoding="utf-8")
        runs_dir = Path(command[command.index("--runs-dir") + 1])
        captured["runs_dir"] = runs_dir
        _write_summary(runs_dir)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(upskill_eval, "resolve_executable", lambda *args, **kwargs: "upskill")
    monkeypatch.setattr(upskill_eval, "run_command", fake_run)

    result = run_upskill_evaluation(
        skill_root=skill_root,
        artifacts_dir=skill_root / ".publisher_artifacts",
    )

    assert result.status == "scored"
    assert result.artifact_dir is None
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == original
    assert not captured["cwd"].exists()
    assert not captured["runs_dir"].exists()
    assert (
        skill_root / ".publisher_artifacts" / "stale.json"
    ).read_text(encoding="utf-8") == "stale"
    assert all(str(captured["cwd"].parent) not in item for item in result.command)


def test_upskill_resolves_relative_tests_before_switching_cwd(tmp_path, monkeypatch) -> None:
    _configure_upskill(monkeypatch)
    caller_root = tmp_path / "caller"
    caller_root.mkdir()
    cases_path = caller_root / "cases.json"
    cases_path.write_text(
        '{"cases":[{"input":"test","expected":{"contains":"ok"}}]}',
        encoding="utf-8",
    )
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("skill", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["tests"] = command[command.index("--tests") + 1]
        captured["env_tests"] = kwargs["env"]["UPSKILL_TESTS_PATH"]
        _write_summary(Path(command[command.index("--runs-dir") + 1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.chdir(caller_root)
    monkeypatch.setenv("UPSKILL_TESTS_PATH", "cases.json")
    monkeypatch.setattr(upskill_eval, "resolve_executable", lambda *args, **kwargs: "upskill")
    monkeypatch.setattr(upskill_eval, "run_command", fake_run)

    result = run_upskill_evaluation(skill_root=skill_root, artifacts_dir=tmp_path / "artifacts")

    assert result.status == "scored"
    assert captured["tests"] == str(cases_path.resolve())
    assert captured["env_tests"] == str(cases_path.resolve())


def test_upskill_cleans_workspace_after_timeout_and_redacts_command(tmp_path, monkeypatch) -> None:
    _configure_upskill(monkeypatch)
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("skill", encoding="utf-8")
    captured: dict[str, Path] = {}

    def fake_run(command, **kwargs):
        captured["cwd"] = Path(kwargs["cwd"])
        raise subprocess.TimeoutExpired(
            ["upskill", "--api-key", "test-openai-key"],
            timeout=1,
        )

    monkeypatch.setenv(
        "PUBLISHER_UPSKILL_COMMAND",
        "upskill --api-key test-openai-key {skill_path} --runs-dir {runs_dir}",
    )
    monkeypatch.setattr(upskill_eval, "run_command", fake_run)

    result = run_upskill_evaluation(skill_root=skill_root, artifacts_dir=tmp_path / "artifacts")

    assert result.status == "failed"
    assert result.artifact_dir is None
    assert not captured["cwd"].exists()
    assert "test-openai-key" not in str(result.command)
    assert "test-openai-key" not in (result.reason or "")
    assert str(captured["cwd"]) not in str(result.command)
    assert not (tmp_path / "artifacts").exists()


def test_upskill_cleans_workspace_after_evaluator_error(tmp_path, monkeypatch) -> None:
    _configure_upskill(monkeypatch)
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("skill", encoding="utf-8")
    captured: dict[str, Path] = {}

    def fake_run(command, **kwargs):
        captured["cwd"] = Path(kwargs["cwd"])
        return subprocess.CompletedProcess(command, 2, "", "evaluator failed")

    monkeypatch.setattr(upskill_eval, "resolve_executable", lambda *args, **kwargs: "upskill")
    monkeypatch.setattr(upskill_eval, "run_command", fake_run)

    result = run_upskill_evaluation(skill_root=skill_root, artifacts_dir=tmp_path / "artifacts")

    assert result.status == "failed"
    assert result.reason == "upskill exited with status 2"
    assert not captured["cwd"].exists()
    assert not (tmp_path / "artifacts").exists()


def test_upskill_refuses_workspace_inside_source(tmp_path, monkeypatch) -> None:
    _configure_upskill(monkeypatch)
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("skill", encoding="utf-8")

    class InSourceTemporaryDirectory:
        def __init__(self, *args, **kwargs):
            assert not Path(kwargs["dir"]).resolve().is_relative_to(skill_root.resolve())

        def __enter__(self):
            return str(skill_root / "aptitude-publisher-eval-inside")

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(upskill_eval.tempfile, "gettempdir", lambda: str(skill_root))
    monkeypatch.setattr(upskill_eval.tempfile, "TemporaryDirectory", InSourceTemporaryDirectory)

    result = run_upskill_evaluation(skill_root=skill_root, artifacts_dir=tmp_path / "artifacts")

    assert result.status == "failed"
    assert "outside the skill source" in (result.reason or "")
    assert not (skill_root / "aptitude-publisher-eval-inside").exists()


def test_upskill_rejects_directory_symlinks(tmp_path, monkeypatch) -> None:
    _configure_upskill(monkeypatch)
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("skill", encoding="utf-8")
    linked_root = tmp_path / "linked-root"
    linked_root.mkdir()
    (linked_root / "secret.txt").write_text("do not copy", encoding="utf-8")
    (skill_root / "linked-root").symlink_to(linked_root, target_is_directory=True)

    result = run_upskill_evaluation(skill_root=skill_root, artifacts_dir=tmp_path / "artifacts")

    assert result.status == "failed"
    assert "directory symlink" in (result.reason or "")
    assert not (tmp_path / "artifacts").exists()


def test_llm_guard_does_not_persist_artifacts(tmp_path, monkeypatch) -> None:
    class Scanner:
        def scan(self, text):
            return text, True, 0.0

    monkeypatch.setattr(
        llm_guard_security,
        "_load_llm_guard",
        lambda: (llm_guard_security._scan_prompt, [Scanner()]),
    )

    artifacts_dir = tmp_path / "artifacts"
    result = llm_guard_security.run_llm_guard_security_scan(
        skill_root=tmp_path / "skill",
        artifacts_dir=artifacts_dir,
        field_values={"content.raw_markdown": "safe"},
    )

    assert result.status == "scored"
    assert result.artifact_dir is None
    assert not artifacts_dir.exists()
