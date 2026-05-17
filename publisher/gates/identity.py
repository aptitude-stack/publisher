"""Gate for verifying publish identity before metadata processing."""

from __future__ import annotations

import re

from publisher.gates.base import PublisherGate
from publisher.models import PublishContext


class IdentityGate(PublisherGate):
    """Verify that slug, version, and intent are present and usable."""

    name = "identity_gate"
    stage_name = "identity"

    _allowed_intents = {"create_skill", "publish_version"}

    def verify(self, context: PublishContext) -> bool:
        blocking_issues: list[str] = []
        warnings: list[str] = []

        slug = context.identity.slug
        version = context.identity.version
        intent = context.identity.intent
        frontmatter = context.source.parsed_content.get("frontmatter", {})
        declared_name = frontmatter.get("name") if isinstance(frontmatter, dict) else None

        if not slug:
            blocking_issues.append("Identity did not extract a slug.")
        elif not isinstance(slug, str) or not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})", slug
        ):
            blocking_issues.append(
                "Slug must match the registry identifier pattern and may include dots, underscores, and hyphens."
            )

        if not version:
            blocking_issues.append("Identity did not extract a version.")
        elif not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            blocking_issues.append("Version must follow semantic versioning in the form X.Y.Z.")

        if not intent:
            blocking_issues.append("Identity did not extract an intent.")
        elif intent not in self._allowed_intents:
            blocking_issues.append(
                "Intent must be one of: create_skill, publish_version."
            )

        if slug and isinstance(declared_name, str) and slug != declared_name.strip():
            warnings.append("Slug does not match the raw frontmatter name value.")

        if slug in {"skill", "test", "example"}:
            warnings.append("Slug is very generic and may not be stable enough for a registry identifier.")

        passed = not blocking_issues
        context.add_gate_result(
            gate_name=self.name,
            passed=passed,
            blocking_issues=blocking_issues,
            warnings=warnings,
            data={
                "stage_name": self.stage_name,
                "slug": slug,
                "version": version,
                "intent": intent,
            },
        )
        context.add_snapshot(
            stage_name=self.name,
            status="passed" if passed else "failed",
            data={
                "slug": slug,
                "version": version,
                "intent": intent,
                "blocking_issues": blocking_issues,
                "warnings": warnings,
            },
            messages=[
                "Identity gate verified whether publish identity is ready for Metadata."
            ],
        )
        return passed
