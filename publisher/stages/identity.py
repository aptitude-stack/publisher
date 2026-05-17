"""Phase 1: build slug, version, and intent."""

from __future__ import annotations

from typing import Any

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class IdentityStage(PublisherStage):
    """Build publish identity information for the server contract."""

    name = "identity"

    def run(self, context: PublishContext) -> None:
        parsed_skill = context.source.parsed_content
        self._populate_identity_from_skill(context, parsed_skill)
        missing_fields = self._collect_missing_fields(context)
        self._record_identity_notes(context, missing_fields)
        artifact_path = self._write_identity_artifact(context)
        context.add_snapshot(
            stage_name=self.name,
            status="completed" if not missing_fields else "incomplete",
            data={
                "slug": context.identity.slug,
                "version": context.identity.version,
                "intent": context.identity.intent,
                "artifact_path": artifact_path,
                "skill_root": context.inventory.skill_root,
                "skill_markdown_path": context.inventory.skill_markdown_path,
                "missing_fields": missing_fields,
            },
            messages=[
                "Identity artifact created successfully.",
                "Identity values were extracted from parsed SKILL.md data.",
            ],
        )

    def _populate_identity_from_skill(
        self,
        context: PublishContext,
        parsed_skill: dict[str, Any],
    ) -> None:
        """Extract slug, version, and intent from the parsed skill file."""
        frontmatter = parsed_skill.get("frontmatter", {})
        metadata = frontmatter.get("metadata", {}) if isinstance(frontmatter, dict) else {}
        context.identity.slug = (
            context.source.slug_override or self._extract_string(frontmatter, "name")
        )
        context.identity.version = (
            context.source.version_override or self._extract_string(metadata, "version")
        )
        context.identity.intent = (
            context.source.intent_override or self._extract_string(metadata, "intent")
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
        context.identity.notes.append("Identity values are extracted from the skill file.")
        if missing_fields:
            context.identity.notes.append(
                "Missing required identity fields: " + ", ".join(missing_fields)
            )
        else:
            context.identity.notes.append("All required identity fields were provided.")

    def _write_identity_artifact(self, context: PublishContext) -> str:
        """Persist the stage 1 result as a JSON artifact for later stages."""
        import json
        from pathlib import Path

        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "01_identity.json"
        artifact = {
            "slug": context.identity.slug,
            "version": context.identity.version,
            "intent": context.identity.intent,
            "source_file": context.inventory.skill_markdown_path,
            "skill_root": context.inventory.skill_root,
            "inventory_artifact": context.inventory.artifact_path,
            "parsed_keys": sorted(context.source.parsed_content.keys()),
            "notes": context.identity.notes,
        }
        artifact_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        context.identity.artifact_path = str(artifact_path)
        return str(artifact_path)

    def _extract_string(self, payload: dict[str, object], key: str) -> str | None:
        """Return a stripped string value if it exists."""
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None
