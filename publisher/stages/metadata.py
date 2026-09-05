"""Phase 2: prepare metadata for publish."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from publisher.integrations.github_api import fetch_repository_signals
from publisher.domain.models import PublishContext
from publisher.stages.base import PublisherStage


class MetadataStage(PublisherStage):
    """Prepare the metadata block that will later go into the payload."""

    name = "metadata"

    def run(self, context: PublishContext) -> None:
        metadata_payload = self._load_metadata_payload(context)
        self._populate_metadata(context, metadata_payload)
        context.add_snapshot(
            stage_name=self.name,
            status="completed",
            data={
                "name": context.metadata.name,
                "description": context.metadata.description,
                "tags": context.metadata.tags,
                "word_count": context.metadata.word_count,
            },
            messages=[
                "Name and description were extracted from SKILL.md; Aptitude metadata was extracted from aptitude.yaml.",
                "Word count was calculated from the skill content.",
            ],
        )

    def _load_metadata_payload(self, context: PublishContext) -> dict[str, Any]:
        """Build the publish metadata view from SKILL.md and aptitude.yaml."""
        frontmatter = context.source.parsed_content.get("frontmatter", {})
        if not isinstance(frontmatter, dict):
            return {}

        manifest = context.source.parsed_content.get("manifest", {})
        if not isinstance(manifest, dict):
            manifest = {}

        payload: dict[str, Any] = {
            "name": frontmatter.get("name"),
            "description": frontmatter.get("description"),
            "tags": manifest.get("tags", []),
            "inputs_schema": manifest.get("inputs_schema"),
            "outputs_schema": manifest.get("outputs_schema"),
            "token_estimate": manifest.get("token_estimate"),
            "maturity_score": manifest.get("maturity_score"),
            "security_score": manifest.get("security_score"),
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
        context.metadata.inputs_schema = self._extract_dict(metadata_payload, "inputs_schema")
        context.metadata.outputs_schema = self._extract_dict(metadata_payload, "outputs_schema")
        context.metadata.token_estimate = self._estimate_tokens(context)
        context.metadata.maturity_score = self._extract_float(metadata_payload, "maturity_score")
        context.metadata.security_score = self._extract_float(metadata_payload, "security_score")
        context.metadata.word_count = self._count_words(context)

        context.metadata.notes = [
            "Name and description are extracted from SKILL.md; Aptitude metadata is extracted from aptitude.yaml.",
            "Token estimate starts as a publisher content heuristic and is replaced by Upskill measured tokens when available.",
            "Word count is a publisher-side field and is not part of the server contract.",
        ]
        context.metadata.extra.update(
            {
                "source_file": context.inventory.skill_markdown_path,
                "skill_root": context.inventory.skill_root,
                "repo_root": context.inventory.repo_root,
                "repo_url": context.inventory.repo_url,
                "source_file_name": context.source.file_name,
                "companion_markdown_files": context.inventory.companion_markdown_files,
                "script_files": context.inventory.script_files,
                "reference_files": context.inventory.reference_files,
                "asset_files": context.inventory.asset_files,
                "compatibility": metadata_payload.get("compatibility"),
                "license": metadata_payload.get("license"),
                "declared_token_estimate": declared_token_estimate,
                "token_estimate_source": "publisher_content_heuristic",
                "server_supported_fields": [
                    "name",
                    "description",
                    "tags",
                    "inputs_schema",
                    "outputs_schema",
                    "token_estimate",
                    "maturity_score",
                    "security_score",
                ],
                "author_required_fields": [
                    "name",
                    "description",
                    "tags",
                    "inputs_schema",
                    "outputs_schema",
                ],
                "publisher_generated_fields": [
                    "token_estimate",
                    "word_count",
                ],
            }
        )
        context.metadata.extra["repo_signals"] = fetch_repository_signals(
            context.inventory.repo_url
        )


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
