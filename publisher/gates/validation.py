"""Gate for verifying validation readiness before ranking."""

from __future__ import annotations

from pathlib import Path

from publisher.gates.base import PublisherGate
from publisher.models import PublishContext


class ValidationGate(PublisherGate):
    """Verify that validation completed successfully enough to continue."""

    name = "validation_gate"
    stage_name = "validation"

    def verify(self, context: PublishContext) -> bool:
        blocking_issues: list[str] = []
        warnings: list[str] = list(context.validation.warnings)

        validation = context.validation

        if validation.artifact_path is None:
            blocking_issues.append("Validation did not write an artifact.")
        elif not Path(validation.artifact_path).is_file():
            blocking_issues.append(
                f"Validation artifact was recorded but not found: {validation.artifact_path}"
            )

        if not validation.checks_run:
            blocking_issues.append("Validation did not record any executed checks.")

        if validation.errors:
            blocking_issues.extend(validation.errors)

        if not validation.passed and not validation.errors:
            blocking_issues.append("Validation did not pass, but no blocking errors were recorded.")

        passed = not blocking_issues
        context.add_gate_result(
            gate_name=self.name,
            passed=passed,
            blocking_issues=blocking_issues,
            warnings=warnings,
            data={
                "stage_name": self.stage_name,
                "passed": validation.passed,
                "artifact_path": validation.artifact_path,
                "error_count": len(validation.errors),
                "warning_count": len(validation.warnings),
            },
        )
        context.add_snapshot(
            stage_name=self.name,
            status="passed" if passed else "failed",
            data={
                "passed": validation.passed,
                "artifact_path": validation.artifact_path,
                "blocking_issues": blocking_issues,
                "warnings": warnings,
            },
            messages=[
                "Validation gate verified whether the skill passed structural and Anthropic guideline checks."
            ],
        )
        return passed
