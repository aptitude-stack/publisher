from datetime import datetime, timezone
import json

import pytest

from publisher.assessment import build_assessment
from publisher.domain.models import PublishContext, SkillSource
from publisher.stages.delivery import DeliveryStage
from publisher.stages.performance_exam import PerformanceExamStage


@pytest.mark.parametrize("warning", [
    "Inspect file:///Users/private/skill.md",
    "Path:/private/tmp/skill.md",
    "Inspect `C:\\Users\\private name\\skill.md`",
    'Inspect "/Users/private name/skill.md"',
    r"Inspect \\private-server\share\skill.md",
])
def test_absolute_path_formats_are_removed(warning):
    context = PublishContext(source=SkillSource(file_path="unused"))
    context.validation.warnings = [warning]
    message = build_assessment(context)["maturity"]["warnings"][0]
    assert "[path redacted]" in message
    assert "private" not in message


def test_public_assessment_uses_existing_results_and_redacts(monkeypatch):
    context = PublishContext(source=SkillSource(file_path="/Users/private/skill"))
    context.validation.passed = True
    context.validation.warnings = ["Missing examples", "Missing examples"]
    context.performance_exam.score = 0.8
    context.performance_exam.test_case_count = 3
    context.performance_exam.models_tested = ["test-model"]
    context.metadata.extra["upskill_evaluation"] = {
        "status": "scored", "validation_warnings": ["Missing examples", "Few tests"],
        "command": "private-command", "artifact_dir": "/private/tmp/eval",
    }
    PerformanceExamStage()._apply_maturity_score(context)
    context.security.scanned = True
    context.security.decision = "review_required"
    context.security.checks_run = ["llm_guard:Secrets"]
    context.security.findings = [{
        "check": "llm_guard:Secrets", "severity": "medium",
        "reason": 'Review "C:\\Users\\private name\\file.txt" and /Users/private/skill with secret-value',
        "evidence": "raw-private-evidence", "field": "/private/file",
    }]
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    summary = build_assessment(context, now=now)
    assert summary["assessed_at"] == "2026-09-06T00:00:00Z"
    assert summary["maturity"]["validation_score"] == 0.8
    assert summary["maturity"]["upskill_score"] == 0.8
    assert summary["maturity"]["warnings"] == ["Missing examples", "Few tests"]
    assert summary["security"]["decision"] == "review_required"
    assert set(summary["security"]["findings"][0]) == {"check", "severity", "explanation"}
    raw = json.dumps(summary)
    for private in ("secret-value", "Users", "private name", "raw-private-evidence", "private-command", "artifact_dir"):
        assert private not in raw
    DeliveryStage().run(context)
    assert context.delivery_payload.metadata["assessment"]["maturity"] == summary["maturity"]


def test_summary_preserves_missing_evidence_and_reports_truncation():
    context = PublishContext(source=SkillSource(file_path="unused"))
    context.validation.warnings = [f"warning {i}" for i in range(102)]
    context.validation.warnings[0] += "x" * 1100
    context.performance_exam.models_tested = [f"model-{i}" for i in range(101)]
    context.security.checks_run = [f"check-{i}" for i in range(103)]
    context.security.findings = [
        {"check": "check", "severity": "low", "reason": "warning"}
        for _ in range(104)
    ]
    summary = build_assessment(context)
    assert summary["maturity"]["upskill_score"] is None
    assert summary["maturity"]["upskill_status"] == "unavailable"
    assert summary["maturity"]["warnings_omitted"] == 2
    assert len(summary["maturity"]["warnings"][0]) == 1000
    assert summary["maturity"]["models_omitted"] == 1
    assert summary["security"]["checks_omitted"] == 3
    assert summary["security"]["findings_omitted"] == 4
    assert summary["security"]["scanned"] is False
    assert summary["security"]["decision"] is None
