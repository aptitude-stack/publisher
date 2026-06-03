"""Phase 4: security scan using LLM Guard as the authoritative source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from publisher.integrations.llm_guard_security import run_llm_guard_security_scan
from publisher.domain.models import PublishContext
from publisher.stages.base import PublisherStage


class SecurityStage(PublisherStage):
    """Prepare the security review step around LLM Guard results."""

    name = "security"

    def run(self, context: PublishContext) -> None:
        self._populate_security_template(context)
        llm_guard_result = run_llm_guard_security_scan(
            skill_root=self._resolve_skill_root(context),
            artifacts_dir=Path(context.artifacts_dir or ".publisher_artifacts"),
            field_values=self._collect_field_values(context),
        )
        context.metadata.extra["llm_guard_security"] = {
            "status": llm_guard_result.status,
            "score": llm_guard_result.score,
            "checks_run": llm_guard_result.checks_run,
            "artifact_dir": llm_guard_result.artifact_dir,
            "reason": llm_guard_result.reason,
        }

        context.security.checks_run = llm_guard_result.checks_run or ["llm_guard"]
        if llm_guard_result.status == "scored":
            context.security.notes.append("LLM Guard skill security scan completed.")
            self._finalize_security_results(
                context,
                findings=llm_guard_result.findings,
                authoritative_score=llm_guard_result.score,
            )
        elif llm_guard_result.status == "disabled":
            context.security.notes.append(
                f"LLM Guard security scan was disabled: {llm_guard_result.reason or 'disabled by configuration'}."
            )
            self._finalize_disabled_security_result(context)
        elif llm_guard_result.reason:
            context.security.notes.append(
                f"LLM Guard evaluator {llm_guard_result.status}: {llm_guard_result.reason}."
            )
            self._finalize_unscored_security_result(context, llm_guard_result.reason)
        else:
            context.security.notes.append("LLM Guard security scan did not produce a score.")
            self._finalize_unscored_security_result(context, "llm guard did not produce a scored result")

        artifact_path = self._write_security_artifact(context)
        context.add_snapshot(
            stage_name=self.name,
            status="completed" if context.security.scanned else "failed",
            data={
                "score": context.security.score,
                "findings": context.security.findings,
                "scan_targets": context.security.scan_targets,
                "checks_run": context.security.checks_run,
                "decision": context.security.decision,
                "artifact_path": artifact_path,
            },
            messages=[
                "Security stage used LLM Guard as the authoritative skill security source.",
                "LLM Guard scanned skill text before publish to detect prompt injection, secrets, and hidden text.",
            ],
        )

    def _populate_security_template(self, context: PublishContext) -> None:
        """Prepare the security scanning checklist and reset prior results."""
        context.security.scanned = False
        context.security.score = 1.0
        context.security.scan_targets = self._build_scan_targets()
        context.security.checks_run = ["llm_guard"]
        context.security.severity_counts = {
            "low": 0,
            "medium": 0,
            "high": 0,
            "critical": 0,
        }
        context.security.decision = "allow"
        context.security.notes = [
            "This stage depends on LLM Guard for skill-text security findings, score, and publish decision.",
            "The security source scans the skill package content rather than probing model behavior.",
        ]
        context.security.findings = []

    def _resolve_skill_root(self, context: PublishContext) -> Path:
        """Resolve the skill folder for external security tools."""
        source_path = Path(context.source.file_path)
        if source_path.is_dir():
            return source_path
        if source_path.name == "SKILL.md":
            return source_path.parent
        return source_path.parent

    def _build_scan_targets(self) -> list[str]:
        """Return the text-bearing skill fields that should be scanned for injection."""
        return [
            "content.raw_markdown",
            "content.rendered_summary",
            "metadata.description",
            "metadata.tags",
            "metadata.inputs_schema",
            "metadata.outputs_schema",
            "package.markdown_files",
            "package.script_files",
            "package.reference_files",
            "package.other_text_files",
        ]

    def _collect_field_values(self, context: PublishContext) -> dict[str, str]:
        """Collect normalized text values from the configured scan targets."""
        payload = {
            "content": self._build_content_payload(context),
            "metadata": self._build_metadata_payload(context),
            "package": self._build_package_payload(context),
        }
        return {target: self._extract_field_text(payload, target) for target in context.security.scan_targets}

    def _build_content_payload(self, context: PublishContext) -> dict[str, Any]:
        """Build the content view available before the delivery stage runs."""
        parsed_content = context.source.parsed_content
        content = parsed_content.get("content")
        if isinstance(content, dict):
            return {
                "raw_markdown": content.get("raw_markdown", ""),
                "rendered_summary": content.get("rendered_summary"),
            }
        return {
            "raw_markdown": parsed_content.get("body", ""),
            "rendered_summary": None,
        }

    def _build_metadata_payload(self, context: PublishContext) -> dict[str, Any]:
        """Build the metadata view available before the delivery stage runs."""
        return {
            "name": context.metadata.name,
            "description": context.metadata.description,
            "tags": context.metadata.tags,
            "inputs_schema": context.metadata.inputs_schema,
            "outputs_schema": context.metadata.outputs_schema,
            "token_estimate": context.metadata.token_estimate,
            "maturity_score": context.metadata.maturity_score,
            "security_score": context.metadata.security_score,
        }

    def _build_package_payload(self, context: PublishContext) -> dict[str, Any]:
        """Read text-bearing package files beyond the primary SKILL.md body."""
        skill_root = self._resolve_skill_root(context)
        return {
            "markdown_files": self._read_relative_files(skill_root, context.inventory.companion_markdown_files),
            "script_files": self._read_relative_files(skill_root, context.inventory.script_files),
            "reference_files": self._read_relative_files(skill_root, context.inventory.reference_files),
            "other_text_files": self._read_relative_files(skill_root, context.inventory.other_files),
        }

    def _read_relative_files(self, skill_root: Path, relative_paths: list[str]) -> dict[str, str]:
        contents: dict[str, str] = {}
        for relative_path in relative_paths:
            file_path = skill_root / relative_path
            if not self._is_text_file(file_path):
                continue
            try:
                contents[relative_path] = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        return contents

    def _is_text_file(self, file_path: Path) -> bool:
        if not file_path.is_file():
            return False
        if file_path.stat().st_size > 250_000:
            return False
        return file_path.suffix.lower() in {
            ".md",
            ".txt",
            ".json",
            ".yaml",
            ".yml",
            ".py",
            ".sh",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".toml",
        }

    def _extract_field_text(self, payload: dict[str, Any], dotted_path: str) -> str:
        """Resolve a dotted field path and normalize it into text for scanning."""
        current: Any = payload
        for part in dotted_path.split("."):
            if not isinstance(current, dict):
                return ""
            current = current.get(part)

        if current is None:
            return ""
        if isinstance(current, str):
            return current
        if isinstance(current, list):
            flattened = [item for item in current if isinstance(item, str)]
            return "\n".join(flattened)
        if isinstance(current, dict):
            try:
                return json.dumps(current, ensure_ascii=True, sort_keys=True)
            except TypeError:
                return str(current)
        return str(current)

    def _finalize_security_results(
        self,
        context: PublishContext,
        findings: list[dict[str, Any]],
        authoritative_score: float | None = None,
    ) -> None:
        """Apply the LLM Guard score and findings to the publish decision."""
        context.security.findings = findings
        context.security.scanned = True
        for severity in context.security.severity_counts:
            context.security.severity_counts[severity] = 0

        for item in findings:
            severity = item["severity"]
            if severity in context.security.severity_counts:
                context.security.severity_counts[severity] += 1

        if authoritative_score is not None:
            context.security.score = max(0.0, min(1.0, round(authoritative_score, 2)))
        else:
            context.security.score = None
            context.security.decision = "block"
            return

        if context.security.severity_counts["critical"] > 0:
            context.security.decision = "block"
        elif context.security.severity_counts["high"] > 0:
            context.security.decision = "review_required"
        else:
            context.security.decision = "allow"

    def _finalize_unscored_security_result(
        self,
        context: PublishContext,
        reason: str,
    ) -> None:
        """Record evaluator unavailability as blocking without creating findings."""
        context.security.scanned = False
        context.security.score = None
        context.security.decision = "block"
        for severity in context.security.severity_counts:
            context.security.severity_counts[severity] = 0
        context.security.findings = []
        context.security.notes.append(
            f"LLM Guard is required as the authoritative skill security source: {reason}."
        )

    def _finalize_disabled_security_result(
        self,
        context: PublishContext,
    ) -> None:
        """Allow publishing only when LLM Guard was explicitly disabled."""
        context.security.scanned = True
        context.security.score = 1.0
        context.security.decision = "allow"
        context.security.checks_run = []
        for severity in context.security.severity_counts:
            context.security.severity_counts[severity] = 0
        context.security.findings = []

    def _write_security_artifact(self, context: PublishContext) -> str:
        """Persist the phase 4 security results as a JSON artifact."""
        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "04_security.json"
        artifact = {
            "scan_targets": context.security.scan_targets,
            "checks_run": context.security.checks_run,
            "score": context.security.score,
            "severity_counts": context.security.severity_counts,
            "decision": context.security.decision,
            "findings": context.security.findings,
            "llm_guard": context.metadata.extra.get("llm_guard_security"),
            "notes": context.security.notes,
        }
        artifact_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        context.security.artifact_path = str(artifact_path)
        return str(artifact_path)
