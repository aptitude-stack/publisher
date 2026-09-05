"""Phase 0: discover the skill package and parse SKILL.md."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from publisher.domain.models import PublishContext
from publisher.frontmatter import parse_skill_markdown
from publisher.manifest import legacy_aptitude_fields, load_manifest
from publisher.stages.base import PublisherStage


class DiscoveryStage(PublisherStage):
    """Discover the skill folder contents and parse the primary skill file."""

    name = "discovery"

    def run(self, context: PublishContext) -> None:
        skill_root = self._resolve_skill_root(context)
        self._build_skill_inventory(context, skill_root)
        blocking_issues: list[str] = []
        try:
            self._load_skill_content(context, skill_root)
        except FileNotFoundError:
            context.source.parsed_content = {}
            blocking_issues.append(f"SKILL.md was not found under skill root: {skill_root}")
        except ValueError as exc:
            context.source.parsed_content = {}
            blocking_issues.append(str(exc))
        context.add_snapshot(
            stage_name=self.name,
            status="completed" if not blocking_issues else "incomplete",
            data={
                "skill_root": context.inventory.skill_root,
                "skill_markdown_path": context.inventory.skill_markdown_path,
                "companion_markdown_files": context.inventory.companion_markdown_files,
                "script_files": context.inventory.script_files,
                "blocking_issues": blocking_issues,
            },
            messages=[
                "Skill folder discovery completed successfully.",
                "SKILL.md and aptitude.yaml were parsed and stored for downstream stages.",
            ]
            if not blocking_issues
            else [
                "Discovery could not produce a complete skill package.",
                *blocking_issues,
            ],
        )

    def _resolve_skill_root(self, context: PublishContext) -> Path:
        """Resolve the skill directory from the provided path."""
        source_path = Path(context.source.file_path)
        if source_path.is_dir():
            return source_path
        if source_path.name == "SKILL.md":
            return source_path.parent
        return source_path.parent

    def _build_skill_inventory(self, context: PublishContext, skill_root: Path) -> None:
        """Discover the files that belong to the skill package."""
        context.source.file_path = str(skill_root)
        context.source.file_name = skill_root.name

        inventory = context.inventory
        inventory.skill_root = str(skill_root)
        inventory.skill_markdown_path = str(skill_root / "SKILL.md")
        inventory.scripts_dir = str(skill_root / "scripts") if (skill_root / "scripts").is_dir() else None
        inventory.references_dir = (
            str(skill_root / "references") if (skill_root / "references").is_dir() else None
        )
        inventory.assets_dir = str(skill_root / "assets") if (skill_root / "assets").is_dir() else None
        inventory.repo_root = self._resolve_repo_root(skill_root)
        inventory.repo_url = self._resolve_repo_url(skill_root)
        inventory.commit_sha = self._resolve_commit_sha(skill_root)
        inventory.tree_path = self._resolve_tree_path(skill_root, inventory.repo_root)
        inventory.companion_markdown_files = []
        inventory.script_files = []
        inventory.reference_files = []
        inventory.asset_files = []
        inventory.other_files = []
        inventory.notes = [
            "Skill inventory is built from the full folder, not from a single file.",
            "SKILL.md is the primary entry file and aptitude.yaml is the Aptitude metadata sidecar.",
        ]
        if inventory.repo_url:
            inventory.notes.append("Repository URL was discovered from the local git repository.")
        else:
            inventory.notes.append("No repository URL was discovered for this skill package.")

        for path in sorted(skill_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(skill_root)
            if ".publisher_artifacts" in relative.parts:
                continue
            relative_path = str(relative)
            if relative_path == "SKILL.md":
                continue
            if relative_path.startswith("scripts/"):
                inventory.script_files.append(relative_path)
            elif relative_path.startswith("references/"):
                inventory.reference_files.append(relative_path)
            elif relative_path.startswith("assets/"):
                inventory.asset_files.append(relative_path)
            elif path.suffix.lower() == ".md":
                inventory.companion_markdown_files.append(relative_path)
            else:
                inventory.other_files.append(relative_path)

    def _load_skill_content(self, context: PublishContext, skill_root: Path) -> dict[str, Any]:
        """Load and parse the skill file from Anthropic's SKILL.md structure."""
        skill_file = skill_root / "SKILL.md"
        raw_content = skill_file.read_text(encoding="utf-8")
        context.source.raw_content = raw_content
        context.source.file_name = skill_file.name

        frontmatter, body = parse_skill_markdown(raw_content)
        legacy = legacy_aptitude_fields(frontmatter)
        if legacy:
            raise ValueError("Move Aptitude fields from SKILL.md to aptitude.yaml: " + ", ".join(legacy))
        manifest = load_manifest(skill_root)
        parsed_skill: dict[str, Any] = {
            "skill_root": str(skill_root),
            "skill_file": str(skill_file),
            "frontmatter": frontmatter,
            "manifest": manifest,
            "manifest_file": str(skill_root / "aptitude.yaml"),
            "body": body,
            "inventory": {
                "companion_markdown_files": context.inventory.companion_markdown_files,
                "script_files": context.inventory.script_files,
                "reference_files": context.inventory.reference_files,
                "asset_files": context.inventory.asset_files,
                "other_files": context.inventory.other_files,
            },
            "content": {
                "raw_markdown": body,
                "rendered_summary": None,
            },
        }
        context.source.parsed_content = parsed_skill
        return parsed_skill


    def _resolve_repo_root(self, skill_root: Path) -> str | None:
        """Resolve the enclosing git repository root if the skill is inside one."""
        try:
            result = subprocess.run(
                ["git", "-C", str(skill_root), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None
        repo_root = result.stdout.strip()
        return repo_root or None

    def _resolve_repo_url(self, skill_root: Path) -> str | None:
        """Resolve the origin repository URL from the enclosing git repository."""
        try:
            result = subprocess.run(
                ["git", "-C", str(skill_root), "remote", "get-url", "origin"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None
        repo_url = result.stdout.strip()
        return repo_url or None

    def _resolve_commit_sha(self, skill_root: Path) -> str | None:
        """Resolve the current HEAD commit SHA for provenance."""
        try:
            result = subprocess.run(
                ["git", "-C", str(skill_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None
        commit_sha = result.stdout.strip()
        return commit_sha or None

    def _resolve_tree_path(self, skill_root: Path, repo_root: str | None) -> str | None:
        """Resolve the skill root relative to the git repository root."""
        if repo_root is None:
            return None
        try:
            relative = skill_root.resolve().relative_to(Path(repo_root).resolve())
        except ValueError:
            return None
        return str(relative)
