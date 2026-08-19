from __future__ import annotations

from publisher.app.cli import _report_detail_sections, _report_phase_rows
from publisher.domain.models import PublishContext, SkillSource
from publisher.stages.validation import ValidationStage


def test_description_with_subject_and_use_when_passes_trigger_guidance() -> None:
    description = (
        "FastAPI best practices and conventions. Use when working with FastAPI APIs "
        "and Pydantic models."
    )

    assert ValidationStage()._has_trigger_guidance(description)


def test_report_merges_publish_readiness_into_structure_validation() -> None:
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.validation.passed = True
    context.validation.checks_run = [
        "skill_root_exists",
        "skill_md_present",
        "yaml_frontmatter_present",
        "body_instructions_heading",
        "relationships_frontmatter_shape",
        "llm_skill_contract_validation",
    ]
    context.add_gate_result(gate_name="discovery_gate", passed=True)
    context.add_gate_result(gate_name="validation_gate", passed=True)
    context.add_gate_result(
        gate_name="identity_gate",
        passed=False,
        blocking_issues=["Identity did not extract a version."],
    )

    phase_rows = dict((phase, (grade, reason)) for phase, grade, reason in _report_phase_rows(context))
    sections = dict(_report_detail_sections(context))

    assert phase_rows["Structure"] == ("failed", "Identity did not extract a version.")
    assert "Readiness" not in phase_rows
    assert sections["Structure Validation"] == [
        ("Status", "failed"),
        (
            "Validation coverage",
            "6 checks: skill folder, SKILL.md, frontmatter, instructions, relationships, LLM contract",
        ),
        ("Issue 1", "Identity did not extract a version."),
    ]
    assert "Publish Readiness" not in sections


def test_report_marks_identity_failure_as_structure_failure() -> None:
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.add_gate_result(gate_name="discovery_gate", passed=True)
    context.add_gate_result(
        gate_name="identity_gate",
        passed=False,
        blocking_issues=["Identity did not extract a version."],
    )

    phase_rows = dict((phase, (grade, reason)) for phase, grade, reason in _report_phase_rows(context))

    assert phase_rows["Structure"] == ("failed", "Identity did not extract a version.")


def test_quality_results_exclude_derived_scores_and_final_scores_stay_separate() -> None:
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.validation.passed = True
    context.security.score = 0.5
    context.security.decision = "review_required"
    context.security.findings = [{"severity": "high", "check": "prompt injection"}]
    context.performance_exam.score = 0.7
    context.metadata.maturity_score = 0.4
    context.ranking.publish_decision = "review_required"
    context.metadata.extra["upskill_evaluation"] = {
        "status": "failed",
        "reason": "upskill failed",
    }

    sections = dict(_report_detail_sections(context))
    risk = dict(sections["Risk Validation"])
    quality = dict(sections["Quality Evaluation"])
    final_scores = dict(sections["Final Scores"])

    assert risk["Safety score"] == "5.0 / 10.0"
    assert risk["Summary"] == "Safety score 5.0 / 10.0. high: prompt injection"
    assert "Issue" not in risk
    assert quality["Performance score"] == "7.0 / 10.0"
    assert "Maturity score" not in quality
    assert "Overall score" not in quality
    assert "Publish decision" not in quality
    assert quality["Summary"] == "Performance score 7.0 / 10.0. upskill failed"
    assert "Issue" not in quality
    assert final_scores == {
        "Security score": "5.0 / 10.0",
        "Maturity score": "4.0 / 10.0",
        "Publish decision": "review_required",
    }


def test_quality_labels_upskill_verdict_as_summary_and_keeps_actionable_suggestions() -> None:
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.performance_exam.score = 0.0
    context.metadata.extra["upskill_evaluation"] = {
        "status": "scored",
        "recommendations": [
            "skill may not be beneficial",
            "add a troubleshooting example",
        ],
    }

    quality = dict(dict(_report_detail_sections(context))["Quality Evaluation"])

    assert quality["Summary"] == "skill may not be beneficial"
    assert quality["Suggestion 1"] == "add a troubleshooting example"
    assert "Suggestion 2" not in quality


def test_inconclusive_upskill_is_reported_for_review() -> None:
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.metadata.extra["upskill_evaluation"] = {
        "status": "inconclusive",
        "reason": "upskill generated tests produced unusable comparative evidence",
    }

    quality = dict(dict(_report_detail_sections(context))["Quality Evaluation"])
    phases = {phase: (grade, reason) for phase, grade, reason in _report_phase_rows(context)}

    assert quality["Summary"] == (
        "Performance score not scored. "
        "upskill generated tests produced unusable comparative evidence"
    )
    assert phases["Quality"][0] == "review_required"
