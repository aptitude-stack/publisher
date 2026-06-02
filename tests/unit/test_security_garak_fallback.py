from __future__ import annotations

import subprocess

from publisher.app.pipeline import PublisherPipeline
from publisher.integrations import garak_security
from publisher.integrations.garak_security import run_garak_security_scan
from publisher.integrations import upskill_eval
from publisher.integrations.upskill_eval import run_upskill_evaluation
from publisher.stages.security import SecurityStage


def test_security_stage_marks_garak_unavailable_when_target_env_is_missing(tmp_path, monkeypatch) -> None:
    skill_root = tmp_path / "sample-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        """---
name: sample-skill
description: "Use when testing publisher security fallback behavior."
metadata:
  version: 1.0.0
  intent: create_skill
  tags: [test]
  inputs_schema: {"type":"object"}
  outputs_schema: {"type":"object"}
---

# Instructions

Use this skill for a publisher security fallback test.
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("PUBLISHER_GARAK_COMMAND", raising=False)
    monkeypatch.delenv("GARAK_TARGET_TYPE", raising=False)
    monkeypatch.delenv("GARAK_TARGET_NAME", raising=False)

    context = PublisherPipeline().create_context(file_path=str(skill_root))
    SecurityStage().run(context)

    assert context.security.scanned is False
    assert context.security.score is None
    assert context.security.decision == "block"
    assert context.security.checks_run == ["garak"]
    assert context.security.findings == []
    assert context.security.severity_counts == {
        "low": 0,
        "medium": 0,
        "high": 0,
        "critical": 0,
    }
    assert context.metadata.extra["garak_security"]["status"] == "not_available"
    assert "GARAK_TARGET_TYPE" in context.metadata.extra["garak_security"]["reason"]


def test_garak_nonzero_exit_reports_tool_failure_not_output_summary(tmp_path, monkeypatch) -> None:
    skill_root = tmp_path / "sample-skill"
    skill_root.mkdir()
    artifacts_dir = tmp_path / "artifacts"

    monkeypatch.setenv("PUBLISHER_GARAK_COMMAND", "garak --target_type openai --target_name test")

    def fake_run_command(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["garak"],
            returncode=1,
            stdout=(
                "garak LLM vulnerability scanner v0.15.0\n"
                "Missing credentials. Please set the OPENAI_API_KEY environment variable.\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(garak_security, "run_command", fake_run_command)

    result = run_garak_security_scan(skill_root=skill_root, artifacts_dir=artifacts_dir)

    assert result.status == "failed"
    assert result.score is None
    assert result.findings == []
    assert "Missing credentials" in str(result.reason)


def test_garak_disabled_is_explicit_status(tmp_path, monkeypatch) -> None:
    skill_root = tmp_path / "sample-skill"
    skill_root.mkdir()
    artifacts_dir = tmp_path / "artifacts"

    monkeypatch.setenv("PUBLISHER_GARAK_ENABLED", "false")

    result = run_garak_security_scan(skill_root=skill_root, artifacts_dir=artifacts_dir)

    assert result.status == "disabled"
    assert result.reason == "PUBLISHER_GARAK_ENABLED is false"


def test_upskill_missing_command_is_not_available(tmp_path, monkeypatch) -> None:
    skill_root = tmp_path / "sample-skill"
    skill_root.mkdir()
    artifacts_dir = tmp_path / "artifacts"

    monkeypatch.delenv("PUBLISHER_UPSKILL_COMMAND", raising=False)
    monkeypatch.delenv("UPSKILL_BASE_URL", raising=False)
    monkeypatch.delenv("UPSKILL_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(upskill_eval, "resolve_executable", lambda *args, **kwargs: None)

    result = run_upskill_evaluation(skill_root=skill_root, artifacts_dir=artifacts_dir)

    assert result.status == "not_available"
    assert "install upskill" in str(result.reason)


def test_pipeline_runs_upskill_after_garak_is_unavailable(tmp_path, monkeypatch) -> None:
    skill_root = tmp_path / "sample-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        """---
name: sample-skill
description: "Helps test publisher evaluator reporting; use when validating missing evaluator behavior."
metadata:
  version: 1.0.0
  intent: create_skill
  tags: [test]
  inputs_schema: {"type":"object"}
  outputs_schema: {"type":"object"}
---

# Instructions

Use this skill for a publisher evaluator reporting test.

# Example

Input: a skill folder.
Output: evaluator status.

# Troubleshooting

If an evaluator is missing, report evaluator availability separately from findings.
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("PUBLISHER_LLM_VALIDATION_ENABLED", "false")
    monkeypatch.delenv("PUBLISHER_GARAK_COMMAND", raising=False)
    monkeypatch.delenv("GARAK_TARGET_TYPE", raising=False)
    monkeypatch.delenv("GARAK_TARGET_NAME", raising=False)
    monkeypatch.delenv("PUBLISHER_UPSKILL_COMMAND", raising=False)
    monkeypatch.delenv("UPSKILL_BASE_URL", raising=False)
    monkeypatch.delenv("UPSKILL_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(upskill_eval, "resolve_executable", lambda *args, **kwargs: None)

    context = PublisherPipeline().create_context(file_path=str(skill_root))
    PublisherPipeline().run(context)

    assert context.metadata.extra["garak_security"]["status"] == "not_available"
    assert context.security.findings == []
    assert context.metadata.extra["upskill_evaluation"]["status"] == "not_available"
    assert "performance_exam" in [snapshot.stage_name for snapshot in context.stage_history]
    assert any(
        gate.gate_name == "security_gate" and not gate.passed
        for gate in context.gate_history
    )
    assert context.ranking.publish_decision == "block"
