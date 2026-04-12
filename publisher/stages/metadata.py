"""Phase 2: prepare metadata for publish."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class MetadataStage(PublisherStage):
    """Prepare the metadata block that will later go into the payload."""

    name = "metadata"

    def run(self, context: PublishContext) -> None:
        metadata_payload = self._load_metadata_payload(context)
        self._populate_metadata(context, metadata_payload)
        missing_fields = self._collect_missing_fields(context)
        artifact_path = self._write_metadata_artifact(context)
        context.add_snapshot(
            stage_name=self.name,
            status="completed" if not missing_fields else "incomplete",
            data={
                "name": context.metadata.name,
                "description": context.metadata.description,
                "tags": context.metadata.tags,
                "word_count": context.metadata.word_count,
                "artifact_path": artifact_path,
                "missing_fields": missing_fields,
            },
            messages=[
                "Metadata values were extracted from the skill file.",
                "Word count was calculated from the skill content.",
            ],
        )

    def _load_metadata_payload(self, context: PublishContext) -> dict[str, Any]:
        """Build the publish metadata view from SKILL.md frontmatter."""
        frontmatter = context.source.parsed_content.get("frontmatter", {})
        if not isinstance(frontmatter, dict):
            return {}

        extra_metadata = frontmatter.get("metadata", {})
        if not isinstance(extra_metadata, dict):
            extra_metadata = {}

        payload: dict[str, Any] = {
            "name": frontmatter.get("name"),
            "description": frontmatter.get("description"),
            "tags": extra_metadata.get("tags", []),
            "headers": extra_metadata.get("headers"),
            "inputs_schema": extra_metadata.get("inputs_schema"),
            "outputs_schema": extra_metadata.get("outputs_schema"),
            "token_estimate": extra_metadata.get("token_estimate"),
            "maturity_score": extra_metadata.get("maturity_score"),
            "security_score": extra_metadata.get("security_score"),
            "compatibility": frontmatter.get("compatibility"),
            "license": frontmatter.get("license"),
        }
        return payload

    def _populate_metadata(
        self,
        context: PublishContext,
        metadata_payload: dict[str, Any],
    ) -> None:
        """Extract the server-supported metadata fields from the skill file."""
        declared_token_estimate = self._extract_int(metadata_payload, "token_estimate")
        context.metadata.name = self._extract_string(metadata_payload, "name")
        context.metadata.description = self._extract_string(metadata_payload, "description")
        context.metadata.tags = self._extract_string_list(metadata_payload, "tags")
        context.metadata.headers = self._extract_dict(metadata_payload, "headers") or {}
        context.metadata.inputs_schema = self._extract_dict(metadata_payload, "inputs_schema")
        context.metadata.outputs_schema = self._extract_dict(metadata_payload, "outputs_schema")
        context.metadata.token_estimate = self._estimate_tokens(context)
        context.metadata.maturity_score = self._extract_float(metadata_payload, "maturity_score")
        context.metadata.security_score = self._extract_float(metadata_payload, "security_score")
        context.metadata.word_count = self._count_words(context)

        context.metadata.notes = [
            "Metadata values are extracted from SKILL.md inside the skill folder.",
            "Token estimate is calculated automatically from the skill content.",
            "Word count is a publisher-side field and is not part of the server contract.",
        ]
        context.metadata.extra.update(
            {
                "source_file": context.inventory.skill_markdown_path,
                "skill_root": context.inventory.skill_root,
                "source_file_name": context.source.file_name,
                "companion_markdown_files": context.inventory.companion_markdown_files,
                "script_files": context.inventory.script_files,
                "reference_files": context.inventory.reference_files,
                "asset_files": context.inventory.asset_files,
                "compatibility": metadata_payload.get("compatibility"),
                "license": metadata_payload.get("license"),
                "declared_token_estimate": declared_token_estimate,
                "server_supported_fields": [
                    "name",
                    "description",
                    "tags",
                    "headers",
                    "inputs_schema",
                    "outputs_schema",
                    "token_estimate",
                    "maturity_score",
                    "security_score",
                ],
            }
        )

    def _collect_missing_fields(self, context: PublishContext) -> list[str]:
        """Find required metadata fields that are still missing."""
        missing_fields: list[str] = []
        if not context.metadata.name:
            missing_fields.append("metadata.name")
        if not context.metadata.description:
            missing_fields.append("metadata.description")
        if not context.metadata.tags:
            missing_fields.append("metadata.tags")
        if not context.metadata.headers:
            missing_fields.append("metadata.headers")
        if context.metadata.inputs_schema is None:
            missing_fields.append("metadata.inputs_schema")
        if context.metadata.outputs_schema is None:
            missing_fields.append("metadata.outputs_schema")
        if missing_fields:
            context.metadata.notes.append(
                "Missing required metadata fields: " + ", ".join(missing_fields)
            )
        else:
            context.metadata.notes.append("All required metadata fields were provided.")
        return missing_fields

    def _write_metadata_artifact(self, context: PublishContext) -> str:
        """Persist the stage 2 result as a JSON artifact."""
        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "02_metadata.json"
        artifact = {
            "name": context.metadata.name,
            "description": context.metadata.description,
            "tags": context.metadata.tags,
            "headers": context.metadata.headers,
            "inputs_schema": context.metadata.inputs_schema,
            "outputs_schema": context.metadata.outputs_schema,
            "token_estimate": context.metadata.token_estimate,
            "word_count": context.metadata.word_count,
            "maturity_score": context.metadata.maturity_score,
            "security_score": context.metadata.security_score,
            "notes": context.metadata.notes,
        }
        artifact_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        context.metadata.artifact_path = str(artifact_path)
        return str(artifact_path)

    def _count_words(self, context: PublishContext) -> int:
        """Count words from the skill body, falling back to the raw skill file."""
        text_source = self._skill_text_for_metrics(context)
        return len(re.findall(r"\b\w+\b", text_source, flags=re.UNICODE))

    def _estimate_tokens(self, context: PublishContext) -> int:
        """Estimate token usage from the skill content using a deterministic heuristic."""
        text_source = self._skill_text_for_metrics(context)
        if not text_source.strip():
            return 0

        character_estimate = len(text_source) / 4
        word_estimate = self._count_words(context) * 1.3
        return max(1, int(round(max(character_estimate, word_estimate))))

    def _skill_text_for_metrics(self, context: PublishContext) -> str:
        """Return the main skill text used for publisher-side token and word metrics."""
        parsed_content = context.source.parsed_content
        content_payload = parsed_content.get("content")
        text_source = ""
        if isinstance(content_payload, dict):
            raw_markdown = content_payload.get("raw_markdown")
            if isinstance(raw_markdown, str):
                text_source = raw_markdown

        if not text_source:
            body = parsed_content.get("body")
            if isinstance(body, str):
                text_source = body

        if not text_source:
            text_source = context.source.raw_content or ""

        companion_content = self._load_companion_markdown(context)
        if companion_content:
            text_source = text_source + "\n\n" + companion_content
        return text_source

    def _load_companion_markdown(self, context: PublishContext) -> str:
        """Load additional markdown files from the skill folder for metrics."""
        skill_root = Path(context.inventory.skill_root or "")
        contents: list[str] = []
        for relative_path in context.inventory.companion_markdown_files:
            candidate = skill_root / relative_path
            if candidate.exists():
                contents.append(candidate.read_text(encoding="utf-8"))
        return "\n\n".join(contents)

    def _extract_string(self, payload: dict[str, Any], key: str) -> str | None:
        """Return a stripped string field if present."""
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    def _extract_string_list(self, payload: dict[str, Any], key: str) -> list[str]:
        """Return a cleaned list of strings."""
        value = payload.get(key)
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            cleaned.append(stripped)
        return cleaned

    def _extract_dict(self, payload: dict[str, Any], key: str) -> dict[str, Any] | None:
        """Return a dict field if present."""
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        return None

    def _extract_int(self, payload: dict[str, Any], key: str) -> int | None:
        """Return an integer field if present."""
        value = payload.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        return None

    def _extract_float(self, payload: dict[str, Any], key: str) -> float | None:
        """Return a numeric field as float if present."""
        value = payload.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None
