"""Phase 1: build slug, version, and intent."""

from __future__ import annotations

from typing import Any

from publisher.domain.models import PublishContext
from publisher.stages.base import PublisherStage


class IdentityStage(PublisherStage):
    """Build publish identity information for the server contract."""

    name = "identity"

    def run(self, context: PublishContext) -> None:
        parsed_skill = context.source.parsed_content
        self._populate_identity_from_skill(context, parsed_skill)
        missing_fields = self._collect_missing_fields(context)
        self._record_identity_notes(context, missing_fields)
        context.add_snapshot(
            stage_name=self.name,
            status="completed" if not missing_fields else "incomplete",
            data={
                "slug": context.identity.slug,
                "version": context.identity.version,
                "intent": context.identity.intent,
                "skill_root": context.inventory.skill_root,
                "skill_markdown_path": context.inventory.skill_markdown_path,
                "missing_fields": missing_fields,
            },
            messages=[
                "Identity values resolved successfully.",
                "Slug was extracted from SKILL.md; version and intent were extracted from aptitude.yaml.",
            ],
        )

    def _populate_identity_from_skill(
        self,
        context: PublishContext,
        parsed_skill: dict[str, Any],
    ) -> None:
        """Extract slug from SKILL.md and version/intent from aptitude.yaml."""
        frontmatter = parsed_skill.get("frontmatter", {})
        manifest = parsed_skill.get("manifest", {})
        if not isinstance(manifest, dict):
            manifest = {}
        context.identity.slug = (
            context.source.slug_override or self._extract_string(frontmatter, "name")
        )
        context.identity.version = (
            context.source.version_override or self._extract_string(manifest, "version")
        )
        context.identity.intent = (
            context.source.intent_override or self._extract_string(manifest, "intent")
        )

    def _collect_missing_fields(self, context: PublishContext) -> list[str]:
        """Find missing required identity fields."""
        missing_fields: list[str] = []
        if not context.identity.slug:
            missing_fields.append("slug")
        if not context.identity.version:
            missing_fields.append("version")
        if not context.identity.intent:
            missing_fields.append("intent")
        return missing_fields

    def _record_identity_notes(
        self,
        context: PublishContext,
        missing_fields: list[str],
    ) -> None:
        """Document how the identity stage behaves."""
        context.identity.notes.append(
            "Slug is extracted from SKILL.md; version and intent are extracted from aptitude.yaml."
        )
        if missing_fields:
            context.identity.notes.append(
                "Missing required identity fields: " + ", ".join(missing_fields)
            )
        else:
            context.identity.notes.append("All required identity fields were provided.")


    def _extract_string(self, payload: dict[str, object], key: str) -> str | None:
        """Return a stripped string value if it exists."""
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None
