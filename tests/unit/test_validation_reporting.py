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


def test_report_separates_publish_readiness_from_structure_validation() -> None:
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.validation.passed = True
    context.add_gate_result(gate_name="discovery_gate", passed=True)
    context.add_gate_result(gate_name="validation_gate", passed=True)
    context.add_gate_result(
        gate_name="identity_gate",
        passed=False,
        blocking_issues=["Identity did not extract a version."],
    )

    phase_rows = dict((phase, (grade, reason)) for phase, grade, reason in _report_phase_rows(context))
    sections = dict(_report_detail_sections(context))

    assert phase_rows["Structure"][0] == "passed"
    assert phase_rows["Readiness"] == ("failed", "Identity did not extract a version.")
    assert sections["Structure Validation"] == [("Status", "passed")]
    assert sections["Publish Readiness"] == [
        ("Status", "failed"),
        ("Issue", "Identity did not extract a version."),
    ]


def test_report_marks_structure_not_evaluated_when_identity_stops_pipeline() -> None:
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.add_gate_result(gate_name="discovery_gate", passed=True)
    context.add_gate_result(
        gate_name="identity_gate",
        passed=False,
        blocking_issues=["Identity did not extract a version."],
    )

    phase_rows = dict((phase, (grade, reason)) for phase, grade, reason in _report_phase_rows(context))

    assert phase_rows["Structure"] == ("not evaluated", "No structure checks were run.")
