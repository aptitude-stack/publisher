"""Phase 5: validation and error verification placeholder."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class ValidationStage(PublisherStage):
    """Validate Anthropic skill-writing compliance before payload delivery."""

    name = "validation"

    def run(self, context: PublishContext) -> None:
        self._reset_validation_state(context)
        skill_root = self._resolve_skill_root(context)
        skill_file = skill_root / "SKILL.md"

        self._validate_skill_root(context, skill_root)
        self._validate_skill_file_presence(context, skill_file)

        frontmatter: dict[str, Any] = {}
        body = ""
        if skill_file.exists():
            frontmatter, body = self._parse_skill_markdown(context, skill_file)
            self._validate_frontmatter(context, skill_root=skill_root, frontmatter=frontmatter)
            self._validate_body(context, body=body)

        self._validate_pipeline_state(context)
        artifact_path = self._write_validation_artifact(
            context,
            skill_root=skill_root,
            skill_file=skill_file,
            frontmatter=frontmatter,
        )
        context.add_snapshot(
            stage_name=self.name,
            status="completed" if context.validation.passed else "failed",
            data={
                "passed": context.validation.passed,
                "errors": context.validation.errors,
                "warnings": context.validation.warnings,
                "artifact_path": artifact_path,
            },
            messages=[
                "Validation stage checked Anthropic skill structure and frontmatter rules.",
                "Validation result is based on filesystem structure plus publisher pipeline state.",
            ],
        )

    def _reset_validation_state(self, context: PublishContext) -> None:
        """Reset validation outputs before running checks."""
        context.validation.passed = False
        context.validation.errors = []
        context.validation.warnings = []
        context.validation.notes = [
            "Validation enforces Anthropic SKILL.md structure and publisher readiness.",
        ]
        context.validation.checks_run = [
            "skill_root_exists",
            "skill_folder_kebab_case",
            "skill_md_present",
            "readme_absent_in_skill_folder",
            "yaml_frontmatter_present",
            "frontmatter_name_present",
            "frontmatter_name_kebab_case",
            "frontmatter_name_matches_folder",
            "frontmatter_name_reserved_words",
            "frontmatter_description_present",
            "frontmatter_description_length",
            "frontmatter_description_trigger_guidance",
            "frontmatter_no_xml_angle_brackets",
            "compatibility_length_if_present",
            "body_present",
            "body_instructions_heading",
            "body_examples_presence",
            "body_troubleshooting_presence",
            "identity_stage_completed",
            "metadata_stage_completed",
            "security_stage_completed",
        ]

    def _resolve_skill_root(self, context: PublishContext) -> Path:
        """Resolve the skill folder from the provided path."""
        source_path = Path(context.source.file_path)
        if source_path.is_dir():
            return source_path
        if source_path.name == "SKILL.md":
            return source_path.parent
        return source_path.parent

    def _validate_skill_root(self, context: PublishContext, skill_root: Path) -> None:
        """Validate the basic skill directory rules."""
        if not skill_root.exists():
            context.validation.errors.append(
                f"Skill root does not exist: {skill_root}"
            )
            return
        if not skill_root.is_dir():
            context.validation.errors.append(
                f"Skill path must resolve to a directory: {skill_root}"
            )
            return

        folder_name = skill_root.name
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", folder_name):
            context.validation.errors.append(
                "Skill folder name must be kebab-case with lowercase letters, numbers, and hyphens only."
            )

        if (skill_root / "README.md").exists():
            context.validation.errors.append(
                "README.md must not appear inside the skill folder; documentation should be in SKILL.md or references/."
            )

    def _validate_skill_file_presence(self, context: PublishContext, skill_file: Path) -> None:
        """Validate that SKILL.md exists exactly as required."""
        if not skill_file.exists():
            context.validation.errors.append(
                f"Missing required SKILL.md file in skill folder: {skill_file.parent}"
            )
            return
        if skill_file.name != "SKILL.md":
            context.validation.errors.append("Skill file must be named exactly SKILL.md.")

    def _parse_skill_markdown(
        self,
        context: PublishContext,
        skill_file: Path,
    ) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter and markdown body from SKILL.md."""
        content = skill_file.read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            context.validation.errors.append(
                "SKILL.md must start with YAML frontmatter delimited by ---"
            )
            return {}, content

        closing_index = content.find("\n---\n", 4)
        if closing_index == -1:
            context.validation.errors.append(
                "SKILL.md frontmatter must end with a closing --- delimiter."
            )
            return {}, content

        frontmatter_text = content[4:closing_index]
        body = content[closing_index + 5 :]
        frontmatter = self._parse_simple_yaml(frontmatter_text)
        return frontmatter, body

    def _parse_simple_yaml(self, frontmatter_text: str) -> dict[str, Any]:
        """Parse a limited subset of YAML sufficient for the expected Anthropic frontmatter."""
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
                    nested_map[nested_key.strip()] = nested_value.strip()
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
            result[key] = value
        return result

    def _validate_frontmatter(
        self,
        context: PublishContext,
        *,
        skill_root: Path,
        frontmatter: dict[str, Any],
    ) -> None:
        """Validate Anthropic frontmatter requirements."""
        if not frontmatter:
            context.validation.errors.append("SKILL.md must contain parseable YAML frontmatter.")
            return

        name = frontmatter.get("name")
        description = frontmatter.get("description")
        compatibility = frontmatter.get("compatibility")

        if not isinstance(name, str) or not name.strip():
            context.validation.errors.append("Frontmatter must include a non-empty name field.")
        else:
            name = name.strip()
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                context.validation.errors.append(
                    "Frontmatter name must be kebab-case with no spaces or capital letters."
                )
            if name != skill_root.name:
                context.validation.errors.append(
                    "Frontmatter name should match the skill folder name."
                )
            if "claude" in name.lower() or "anthropic" in name.lower():
                context.validation.errors.append(
                    'Frontmatter name must not include the reserved words "claude" or "anthropic".'
                )

        if not isinstance(description, str) or not description.strip():
            context.validation.errors.append(
                "Frontmatter must include a non-empty description field."
            )
        else:
            description = description.strip()
            if len(description) >= 1024:
                context.validation.errors.append(
                    "Frontmatter description must be under 1024 characters."
                )
            if "<" in description or ">" in description:
                context.validation.errors.append(
                    "Frontmatter description must not contain XML angle brackets (< or >)."
                )
            if not self._has_trigger_guidance(description):
                context.validation.errors.append(
                    "Frontmatter description must explain what the skill does and when to use it."
                )

        for key, value in frontmatter.items():
            if isinstance(value, str) and ("<" in value or ">" in value):
                context.validation.errors.append(
                    f'Frontmatter field "{key}" must not contain XML angle brackets (< or >).'
                )

        if compatibility is not None:
            if not isinstance(compatibility, str) or not (1 <= len(compatibility.strip()) <= 500):
                context.validation.errors.append(
                    "Frontmatter compatibility must be a string between 1 and 500 characters when provided."
                )

    def _has_trigger_guidance(self, description: str) -> bool:
        """Heuristic check that the description includes use-when guidance."""
        lowered = description.lower()
        action_markers = ("handles", "creates", "analyzes", "manages", "generates", "helps")
        trigger_markers = ("use when", "when user", "asks for", "mentions", "says")
        return any(marker in lowered for marker in action_markers) and any(
            marker in lowered for marker in trigger_markers
        )

    def _validate_body(self, context: PublishContext, *, body: str) -> None:
        """Validate the SKILL.md body against the recommended structure."""
        if not body.strip():
            context.validation.errors.append("SKILL.md must contain instruction content after frontmatter.")
            return

        lowered = body.lower()
        if "# instructions" not in lowered:
            context.validation.warnings.append(
                'SKILL.md body should include an "Instructions" heading.'
            )
        if "example" not in lowered:
            context.validation.warnings.append(
                "SKILL.md should include at least one example section."
            )
        if "troubleshooting" not in lowered:
            context.validation.warnings.append(
                "SKILL.md should include a troubleshooting section for common failures."
            )

    def _validate_pipeline_state(self, context: PublishContext) -> None:
        """Validate that previous pipeline stages completed successfully enough to publish."""
        if not context.identity.slug or not context.identity.version or not context.identity.intent:
            context.validation.errors.append(
                "Identity stage is incomplete: slug, version, and intent are required."
            )

        required_metadata_missing = []
        if not context.metadata.name:
            required_metadata_missing.append("name")
        if not context.metadata.description:
            required_metadata_missing.append("description")
        if not context.metadata.tags:
            required_metadata_missing.append("tags")
        if not context.metadata.headers:
            required_metadata_missing.append("headers")
        if context.metadata.inputs_schema is None:
            required_metadata_missing.append("inputs_schema")
        if context.metadata.outputs_schema is None:
            required_metadata_missing.append("outputs_schema")
        if required_metadata_missing:
            context.validation.errors.append(
                "Metadata stage is incomplete: missing "
                + ", ".join(required_metadata_missing)
                + "."
            )

        if not context.security.scanned:
            context.validation.errors.append(
                "Security stage must run before validation can pass."
            )
        elif context.security.decision == "block":
            context.validation.errors.append(
                "Security stage blocked the skill due to high-risk findings."
            )
        elif context.security.decision == "review_required":
            context.validation.warnings.append(
                "Security stage requires manual review before publish."
            )

        context.validation.passed = len(context.validation.errors) == 0

    def _write_validation_artifact(
        self,
        context: PublishContext,
        *,
        skill_root: Path,
        skill_file: Path,
        frontmatter: dict[str, Any],
    ) -> str:
        """Persist validation results as a JSON artifact."""
        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "05_validation.json"
        artifact = {
            "passed": context.validation.passed,
            "skill_root": str(skill_root),
            "skill_file": str(skill_file),
            "checks_run": context.validation.checks_run,
            "frontmatter_keys": sorted(frontmatter.keys()),
            "errors": context.validation.errors,
            "warnings": context.validation.warnings,
            "notes": context.validation.notes,
        }
        artifact_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        context.validation.artifact_path = str(artifact_path)
        return str(artifact_path)
