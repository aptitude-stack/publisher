"""Gate for verifying metadata readiness before security scanning."""

from __future__ import annotations

from publisher.gates.base import PublisherGate
from publisher.models import PublishContext


class MetadataGate(PublisherGate):
    """Verify that required metadata fields are present and usable."""

    name = "metadata_gate"
    stage_name = "metadata"

    def verify(self, context: PublishContext) -> bool:
        blocking_issues: list[str] = []
        warnings: list[str] = []

        metadata = context.metadata

        if not metadata.name:
            blocking_issues.append("Metadata is missing name.")
        if not metadata.description:
            blocking_issues.append("Metadata is missing description.")
        if not metadata.tags:
            blocking_issues.append("Metadata is missing tags.")
        if metadata.inputs_schema is None:
            blocking_issues.append("Metadata is missing inputs_schema.")
        if metadata.outputs_schema is None:
            blocking_issues.append("Metadata is missing outputs_schema.")

        if metadata.token_estimate is None:
            blocking_issues.append("Metadata did not compute token_estimate.")
        elif metadata.token_estimate < 0:
            blocking_issues.append("Metadata token_estimate must not be negative.")

        if metadata.word_count is None:
            warnings.append("Metadata did not compute word_count.")
        elif metadata.word_count == 0:
            warnings.append("Metadata word_count is zero; the skill content may be empty.")

        if metadata.maturity_score is not None and not 0.0 <= metadata.maturity_score <= 1.0:
            blocking_issues.append("Metadata maturity_score must be between 0.0 and 1.0.")

        if metadata.security_score is not None and not 0.0 <= metadata.security_score <= 1.0:
            blocking_issues.append("Metadata security_score must be between 0.0 and 1.0.")

        passed = not blocking_issues
        context.add_gate_result(
            gate_name=self.name,
            passed=passed,
            blocking_issues=blocking_issues,
            warnings=warnings,
            data={
                "stage_name": self.stage_name,
                "name": metadata.name,
                "token_estimate": metadata.token_estimate,
                "word_count": metadata.word_count,
            },
        )
        context.add_snapshot(
            stage_name=self.name,
            status="passed" if passed else "failed",
            data={
                "name": metadata.name,
                "description": metadata.description,
                "token_estimate": metadata.token_estimate,
                "word_count": metadata.word_count,
                "blocking_issues": blocking_issues,
                "warnings": warnings,
            },
            messages=[
                "Metadata gate verified whether the publish metadata is ready for Security."
            ],
        )
        return passed
