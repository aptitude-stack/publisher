"""Phase 5: validate the Anthropic SKILL.md file contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from publisher.domain.models import PublishContext
from publisher.frontmatter import parse_skill_markdown
from publisher.integrations.llm_validation import run_llm_skill_validation
from publisher.manifest import legacy_aptitude_fields, load_manifest
from publisher.relationships import normalize_relationships
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
            manifest = self._load_manifest(context, skill_root)
            self._validate_frontmatter(
                context,
                skill_root=skill_root,
                frontmatter=frontmatter,
                manifest=manifest,
            )
            self._validate_body(context, body=body)
            self._validate_with_llm(context, skill_root=skill_root, skill_file=skill_file)

        context.validation.passed = len(context.validation.errors) == 0
        context.add_snapshot(
            stage_name=self.name,
            status="completed" if context.validation.passed else "failed",
            data={
                "passed": context.validation.passed,
                "errors": context.validation.errors,
                "warnings": context.validation.warnings,
            },
            messages=[
                "Validation stage checked Anthropic skill structure and frontmatter rules.",
                "Validation result is based only on the skill folder and SKILL.md contract.",
            ],
        )

    def _reset_validation_state(self, context: PublishContext) -> None:
        """Reset validation outputs before running checks."""
        context.validation.passed = False
        context.validation.errors = []
        context.validation.warnings = []
        context.validation.notes = [
            "Validation enforces Anthropic SKILL.md structure only.",
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
            "aptitude_manifest_present",
            "aptitude_manifest_shape",
            "legacy_aptitude_frontmatter_absent",
            "relationships_manifest_shape",
            "relationships_local_targets_warn_if_missing",
            "body_present",
            "body_instructions_heading",
            "body_examples_presence",
            "body_troubleshooting_presence",
            "llm_skill_contract_validation",
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
        try:
            return parse_skill_markdown(content)
        except ValueError as exc:
            context.validation.errors.append(
                str(exc)
            )
            return {}, content

    def _validate_frontmatter(
        self,
        context: PublishContext,
        *,
        skill_root: Path,
        frontmatter: dict[str, Any],
        manifest: dict[str, Any],
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

        for field in legacy_aptitude_fields(frontmatter):
            context.validation.errors.append(
                f"Legacy Aptitude field {field!r} must be moved from SKILL.md frontmatter to aptitude.yaml."
            )

        try:
            relationships = normalize_relationships(manifest.get("relationships"))
        except ValueError as exc:
            context.validation.errors.append(f"aptitude.yaml relationships are invalid: {exc}")
        else:
            self._validate_relationship_targets(
                context,
                skill_root=skill_root,
                relationships=relationships,
            )

    def _load_manifest(self, context: PublishContext, skill_root: Path) -> dict[str, Any]:
        """Load the required metadata sidecar and retain it for downstream stages."""
        try:
            manifest = load_manifest(skill_root)
        except ValueError as exc:
            context.validation.errors.append(str(exc))
            manifest = {}
        context.source.parsed_content["manifest"] = manifest
        context.source.parsed_content["manifest_file"] = str(skill_root / "aptitude.yaml")
        return manifest

    def _validate_relationship_targets(
        self,
        context: PublishContext,
        *,
        skill_root: Path,
        relationships: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Warn when relationship target skills are not in the local repository tree."""
        if not any(relationships.values()):
            return

        existing_slugs = self._discover_local_skill_slugs(context, skill_root)
        for family, items in relationships.items():
            for item in items:
                slug = item["slug"]
                if slug not in existing_slugs:
                    context.validation.warnings.append(
                        f"Relationship target {slug} is not present in the local skill repository "
                        f"(relationships.{family})."
                    )

    def _discover_local_skill_slugs(
        self,
        context: PublishContext,
        skill_root: Path,
    ) -> set[str]:
        """Return skill slugs found under the enclosing repo, or the skill catalog root."""
        search_root = (
            Path(context.inventory.repo_root)
            if context.inventory.repo_root
            else skill_root.parent
        )
        slugs: set[str] = set()

        for skill_file in search_root.rglob("SKILL.md"):
            if ".publisher_artifacts" in skill_file.parts:
                continue
            slugs.add(skill_file.parent.name)
            try:
                frontmatter, _body = parse_skill_markdown(
                    skill_file.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            name = frontmatter.get("name")
            if isinstance(name, str) and name.strip():
                slugs.add(name.strip())
        return slugs

    def _has_trigger_guidance(self, description: str) -> bool:
        """Heuristic check that the description includes use-when guidance."""
        lowered = description.lower()
        trigger_markers = ("use when", "when user", "asks for", "mentions", "says")
        return any(marker in lowered for marker in trigger_markers)

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

    def _validate_with_llm(
        self,
        context: PublishContext,
        *,
        skill_root: Path,
        skill_file: Path,
    ) -> None:
        """Run optional token-backed semantic validation of SKILL.md."""
        result = run_llm_skill_validation(skill_root=skill_root, skill_file=skill_file)
        context.validation.notes.append(f"LLM validation status: {result.status}.")
        if result.model:
            context.validation.notes.append(f"LLM validation model: {result.model}.")
        if result.reason:
            context.validation.notes.append(f"LLM validation reason: {result.reason}.")
        context.validation.errors.extend(f"LLM: {item}" for item in result.errors)
        context.validation.warnings.extend(f"LLM: {item}" for item in result.warnings)
        context.validation.notes.extend(f"LLM: {item}" for item in result.notes)
