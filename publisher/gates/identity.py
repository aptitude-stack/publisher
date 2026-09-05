"""Gate for verifying publish identity before metadata processing."""

from __future__ import annotations

import re

from publisher.gates.base import PublisherGate, explain_gate_result
from publisher.domain.models import PublishContext
from publisher.versioning import SEMVER_PATTERN


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

        for field, value, limit, required in (
            ("namespace", context.source.namespace, 128, True),
            ("policy_pack_slug", context.source.policy_pack_slug, 128, False),
            ("publisher_identity", context.source.publisher_identity, 200, False),
            ("repo_url", context.inventory.repo_url, 500, False),
            ("tree_path", context.inventory.tree_path, 500, False),
        ):
            if value is None and not required:
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                blocking_issues.append(f"{field} must contain 1 to {limit} characters.")

        if not slug:
            blocking_issues.append("Identity did not extract a slug.")
        elif not isinstance(slug, str) or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,127})", slug
        ):
            blocking_issues.append(
                "Slug must be lowercase and hyphenated, matching the registry identifier pattern."
            )

        if not version:
            blocking_issues.append("Identity did not extract a version.")
        elif not isinstance(version, str) or SEMVER_PATTERN.fullmatch(version) is None:
            blocking_issues.append(
                "Version must follow semantic versioning, e.g. 1.2.3, "
                "1.2.3-codex, or 1.2.3-gpt-6-astra+build.1 (no leading v)."
            )

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
        explanation = explain_gate_result(
            passed=passed,
            passed_message="Identity passed: slug, version, and publish intent are usable.",
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
                "Identity gate verified whether publish identity is ready for Metadata.",
                explanation,
            ],
        )
        return passed
