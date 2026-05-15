"""Gate for verifying security scan readiness before validation."""

from __future__ import annotations

from publisher.gates.base import PublisherGate
from publisher.models import PublishContext


class SecurityGate(PublisherGate):
    """Verify that security scan ran successfully and did not block publish."""

    name = "security_gate"
    stage_name = "security"

    _allowed_decisions = {"allow", "review_required", "block"}
    _allowed_severities = {"low", "medium", "high", "critical"}

    def verify(self, context: PublishContext) -> bool:
        blocking_issues: list[str] = []
        warnings: list[str] = []

        security = context.security

        if not security.scanned:
            blocking_issues.append("Security scan did not run.")

        if security.score is None:
            blocking_issues.append("Security scan did not produce a score.")

        if security.decision not in self._allowed_decisions:
            blocking_issues.append("Security decision is missing or invalid.")

        for finding in security.findings:
            severity = finding.get("severity")
            if severity not in self._allowed_severities:
                blocking_issues.append("Security findings contain an invalid severity value.")
                break

        if security.decision == "block":
            blocking_issues.append("Security scan blocked the skill from being published.")

        passed = not blocking_issues
        context.add_gate_result(
            gate_name=self.name,
            passed=passed,
            blocking_issues=blocking_issues,
            warnings=warnings,
            data={
                "stage_name": self.stage_name,
                "score": security.score,
                "decision": security.decision,
                "severity_counts": security.severity_counts,
            },
        )
        context.add_snapshot(
            stage_name=self.name,
            status="passed" if passed else "failed",
            data={
                "score": security.score,
                "decision": security.decision,
                "severity_counts": security.severity_counts,
                "blocking_issues": blocking_issues,
                "warnings": warnings,
            },
            messages=[
                "Security gate verified whether the security result allows the pipeline to continue."
            ],
        )
        return passed
