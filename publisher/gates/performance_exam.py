"""Gate for verifying Upskill performance evidence before publishing."""

from __future__ import annotations

from publisher.domain.models import PublishContext
from publisher.gates.base import PublisherGate, explain_gate_result


class PerformanceExamGate(PublisherGate):
    """Require complete scored Upskill evidence for a publishable skill."""

    name = "performance_exam_gate"
    stage_name = "performance_exam"

    def verify(self, context: PublishContext) -> bool:
        evidence = context.metadata.extra.get("upskill_evaluation", {})
        status = evidence.get("status") if isinstance(evidence, dict) else None
        blocking_issues: list[str] = []

        if status != "scored":
            blocking_issues.append(
                "Upskill evaluation did not produce scored performance evidence."
            )
        elif context.performance_exam.score is None:
            blocking_issues.append("Upskill evaluation did not produce a performance score.")
        elif context.performance_exam.test_case_count <= 0:
            blocking_issues.append("Upskill evaluation did not run any test cases.")
        elif not context.performance_exam.models_tested:
            blocking_issues.append("Upskill evaluation did not record a model.")

        if isinstance(evidence, dict) and evidence.get("validation_errors"):
            blocking_issues.append("Upskill evaluation recorded provider or validation errors.")

        passed = not blocking_issues
        explanation = explain_gate_result(
            passed=passed,
            passed_message="Upskill passed: complete scored performance evidence is available.",
            blocking_issues=blocking_issues,
            warnings=[],
        )
        context.add_gate_result(
            gate_name=self.name,
            passed=passed,
            explanation=explanation,
            blocking_issues=blocking_issues,
            data={"stage_name": self.stage_name, "status": status},
        )
        context.add_snapshot(
            stage_name=self.name,
            status="passed" if passed else "failed",
            data={"status": status, "blocking_issues": blocking_issues},
            messages=["Performance gate verified Upskill evaluation evidence.", explanation],
        )
        return passed
