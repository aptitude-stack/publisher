"""Gate for verifying that discovery produced a usable skill package."""

from __future__ import annotations

from pathlib import Path

from publisher.gates.base import PublisherGate
from publisher.models import PublishContext


class DiscoveryGate(PublisherGate):
    """Verify that discovery produced the minimum package structure."""

    name = "discovery_gate"
    stage_name = "discovery"

    def verify(self, context: PublishContext) -> bool:
        blocking_issues: list[str] = []
        warnings: list[str] = []

        skill_root = context.inventory.skill_root
        skill_markdown_path = context.inventory.skill_markdown_path
        parsed_content = context.source.parsed_content
        inventory_artifact = context.inventory.artifact_path

        if not skill_root:
            blocking_issues.append("Discovery did not resolve a skill root.")
        elif not Path(skill_root).is_dir():
            blocking_issues.append(f"Resolved skill root is not a directory: {skill_root}")

        if not skill_markdown_path:
            blocking_issues.append("Discovery did not record a SKILL.md path.")
        elif not Path(skill_markdown_path).is_file():
            blocking_issues.append(f"SKILL.md was not found at the recorded path: {skill_markdown_path}")

        if not parsed_content:
            blocking_issues.append("Discovery did not store parsed SKILL.md content.")
        else:
            frontmatter = parsed_content.get("frontmatter")
            body = parsed_content.get("body")
            if not isinstance(frontmatter, dict):
                blocking_issues.append("Parsed SKILL.md frontmatter is missing or invalid.")
            if not isinstance(body, str):
                blocking_issues.append("Parsed SKILL.md body is missing or invalid.")
            elif not body.strip():
                warnings.append("SKILL.md body is empty; later validation may fail.")

        if not inventory_artifact:
            blocking_issues.append("Discovery did not write an inventory artifact.")
        elif not Path(inventory_artifact).is_file():
            blocking_issues.append(
                f"Discovery inventory artifact was recorded but not found: {inventory_artifact}"
            )

        if context.inventory.other_files:
            warnings.append(
                "Discovery found uncategorized files in the skill package; review whether they need explicit handling."
            )

        passed = not blocking_issues
        context.add_gate_result(
            gate_name=self.name,
            passed=passed,
            blocking_issues=blocking_issues,
            warnings=warnings,
            data={
                "stage_name": self.stage_name,
                "skill_root": skill_root,
                "skill_markdown_path": skill_markdown_path,
                "inventory_artifact": inventory_artifact,
            },
        )
        context.add_snapshot(
            stage_name=self.name,
            status="passed" if passed else "failed",
            data={
                "skill_root": skill_root,
                "skill_markdown_path": skill_markdown_path,
                "blocking_issues": blocking_issues,
                "warnings": warnings,
            },
            messages=[
                "Discovery gate verified whether the skill package is ready for Identity."
            ],
        )
        return passed
