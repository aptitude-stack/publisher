"""Phase 1: build slug, version, and intent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class IdentityStage(PublisherStage):
    """Build publish identity information for the server contract."""

    name = "identity"

    def run(self, context: PublishContext) -> None:
        parsed_skill = self._load_skill_content(context)
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
                "missing_fields": missing_fields,
            },
            messages=[
                "Identity artifact created successfully.",
                "Identity values were extracted from the skill file.",
            ],
        )

    def _load_skill_content(self, context: PublishContext) -> dict[str, Any]:
        """Load and parse the skill file from Anthropic's SKILL.md structure."""
        skill_root = self._resolve_skill_root(context)
        skill_file = skill_root / "SKILL.md"
        raw_content = skill_file.read_text(encoding="utf-8")
        context.source.raw_content = raw_content
        context.source.file_name = skill_file.name
        context.source.file_path = str(skill_root)

        frontmatter, body = self._parse_skill_markdown(raw_content)
        parsed_skill: dict[str, Any] = {
            "skill_root": str(skill_root),
            "skill_file": str(skill_file),
            "frontmatter": frontmatter,
            "body": body,
            "content": {
                "raw_markdown": body,
                "rendered_summary": None,
            },
        }
        context.source.parsed_content = parsed_skill
        return parsed_skill

    def _resolve_skill_root(self, context: PublishContext) -> Path:
        """Resolve the skill directory from the provided path."""
        source_path = Path(context.source.file_path)
        if source_path.is_dir():
            return source_path
        if source_path.name == "SKILL.md":
            return source_path.parent
        return source_path.parent

    def _parse_skill_markdown(self, content: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter and markdown body from SKILL.md."""
        if not content.startswith("---\n"):
            raise ValueError("SKILL.md must start with YAML frontmatter.")

        closing_index = content.find("\n---\n", 4)
        if closing_index == -1:
            raise ValueError("SKILL.md frontmatter must end with a closing --- delimiter.")

        frontmatter_text = content[4:closing_index]
        body = content[closing_index + 5 :]
        return self._parse_simple_yaml(frontmatter_text), body

    def _parse_simple_yaml(self, frontmatter_text: str) -> dict[str, Any]:
        """Parse the frontmatter subset used by the current publisher."""
        result: dict[str, Any] = {}
        current_nested_key: str | None = None
        for raw_line in frontmatter_text.splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            if raw_line.startswith("  ") and current_nested_key:
                stripped = raw_line.strip()
                if ":" not in stripped:
                    continue
                nested_key, nested_value = stripped.split(":", 1)
                nested_map = result.setdefault(current_nested_key, {})
                if isinstance(nested_map, dict):
                    nested_map[nested_key.strip()] = self._coerce_scalar(nested_value.strip())
                continue

            current_nested_key = None
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not value:
                result[key] = {}
                current_nested_key = key
                continue
            result[key] = self._coerce_scalar(value)
        return result

    def _coerce_scalar(self, value: str) -> Any:
        """Convert simple YAML scalar strings into Python values when obvious."""
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
        if value.startswith("{") and value.endswith("}"):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        if re_fullmatch := __import__("re").fullmatch(r"-?\d+", value):
            return int(re_fullmatch.group(0))
        if __import__("re").fullmatch(r"-?\d+\.\d+", value):
            return float(value)
        if value.lower() in {"true", "false"}:
            return value.lower() == "true"
        return value.strip("'\"")

    def _populate_identity_from_skill(
        self,
        context: PublishContext,
        parsed_skill: dict[str, Any],
    ) -> None:
        """Extract slug, version, and intent from the parsed skill file."""
        frontmatter = parsed_skill.get("frontmatter", {})
        metadata = frontmatter.get("metadata", {}) if isinstance(frontmatter, dict) else {}
        context.identity.slug = self._extract_string(frontmatter, "name")
        context.identity.version = self._extract_string(metadata, "version")
        context.identity.intent = self._extract_string(metadata, "intent")

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
        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "01_identity.json"
        artifact = {
            "slug": context.identity.slug,
            "version": context.identity.version,
            "intent": context.identity.intent,
            "source_file": context.source.file_path,
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
