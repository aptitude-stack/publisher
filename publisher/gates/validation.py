"""Gate for verifying validation readiness before ranking."""

from __future__ import annotations

from publisher.gates.base import PublisherGate, explain_gate_result
from publisher.domain.models import PublishContext


class ValidationGate(PublisherGate):
    """Verify that validation completed successfully enough to continue."""

    name = "validation_gate"
    stage_name = "validation"

    def verify(self, context: PublishContext) -> bool:
        blocking_issues: list[str] = []
        warnings: list[str] = list(context.validation.warnings)

        validation = context.validation

        if not validation.checks_run:
            blocking_issues.append("Validation did not record any executed checks.")

        if validation.errors:
            blocking_issues.extend(validation.errors)

        if not validation.passed and not validation.errors:
            blocking_issues.append("Validation did not pass, but no blocking errors were recorded.")

        passed = not blocking_issues
        explanation = explain_gate_result(
            passed=passed,
            passed_message="Validation passed: the SKILL.md contract checks completed without blocking errors.",
            blocking_issues=blocking_issues,
            warnings=warnings,
        )
        context.add_gate_result(
            gate_name=self.name,
            passed=passed,
            explanation=explanation,
            blocking_issues=blocking_issues,
            warnings=warnings,
            data={
                "stage_name": self.stage_name,
                "passed": validation.passed,
                "error_count": len(validation.errors),
                "warning_count": len(validation.warnings),
            },
        )
        context.add_snapshot(
            stage_name=self.name,
            status="passed" if passed else "failed",
            data={
                "passed": validation.passed,
                "blocking_issues": blocking_issues,
                "warnings": warnings,
            },
            messages=[
                "Validation gate verified whether the skill passed structural and Anthropic guideline checks.",
                explanation,
            ],
        )
        return passed
