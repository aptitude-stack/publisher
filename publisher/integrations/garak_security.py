"""Optional NVIDIA garak security scan adapter."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from publisher.integrations.external_tools import (
    configured_bool,
    render_command,
    resolve_executable,
    run_command,
)


_DEFAULT_TIMEOUT_SECONDS = 180


@dataclass(frozen=True, slots=True)
class GarakSecurityResult:
    """Normalized result from an optional garak scan."""

    status: str
    score: float | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    artifact_dir: str | None = None
    reason: str | None = None


def run_garak_security_scan(*, skill_root: Path, artifacts_dir: Path) -> GarakSecurityResult:
    """Run garak when configured and normalize its output for the publisher."""
    if not configured_bool("PUBLISHER_GARAK_ENABLED", default=True):
        return GarakSecurityResult(status="disabled", reason="PUBLISHER_GARAK_ENABLED is false")

    garak_dir = artifacts_dir / "garak"
    garak_dir.mkdir(parents=True, exist_ok=True)

    command = _build_command(skill_root=skill_root, artifact_dir=garak_dir)
    if command is None:
        return GarakSecurityResult(
            status="not_configured",
            artifact_dir=str(garak_dir),
            reason=(
                "install garak and set GARAK_TARGET_TYPE/GARAK_TARGET_NAME, "
                "or set PUBLISHER_GARAK_COMMAND"
            ),
        )

    try:
        completed = run_command(
            command,
            cwd=skill_root,
            timeout_seconds=int(os.environ.get("PUBLISHER_GARAK_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # type: ignore[name-defined]
        return GarakSecurityResult(
            status="failed",
            command=command,
            artifact_dir=str(garak_dir),
            reason=str(exc),
        )

    (garak_dir / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (garak_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    findings = _load_findings(garak_dir)
    if not findings:
        findings = _findings_from_output(completed.stdout + "\n" + completed.stderr)

    score = _score_from_findings(findings)
    status = "scored" if completed.returncode == 0 else "failed"
    reason = None if completed.returncode == 0 else f"garak exited with status {completed.returncode}"
    return GarakSecurityResult(
        status=status,
        score=score,
        findings=findings,
        checks_run=sorted({str(item.get("check", "garak")) for item in findings}) or ["garak"],
        command=command,
        artifact_dir=str(garak_dir),
        reason=reason,
    )


def _build_command(*, skill_root: Path, artifact_dir: Path) -> list[str] | None:
    values = {
        "skill_path": str(skill_root),
        "skill_file": str(skill_root / "SKILL.md"),
        "artifact_dir": str(artifact_dir),
    }
    command_template = os.environ.get("PUBLISHER_GARAK_COMMAND")
    if command_template:
        return render_command(command_template, values)

    executable = resolve_executable("garak", start=skill_root)
    target_type = os.environ.get("GARAK_TARGET_TYPE")
    target_name = os.environ.get("GARAK_TARGET_NAME")
    if not executable or not target_type or not target_name:
        return None

    probes = os.environ.get("GARAK_PROBES", "promptinject")
    command = [
        executable,
        "--target_type",
        target_type,
        "--target_name",
        target_name,
        "--probes",
        probes,
        "--report_prefix",
        str(artifact_dir / "garak"),
    ]
    detector = os.environ.get("GARAK_DETECTORS")
    if detector:
        command.extend(["--detectors", detector])
    return command


def _load_findings(artifact_dir: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for candidate in sorted(artifact_dir.glob("**/*")):
        if candidate.suffix.lower() not in {".json", ".jsonl"}:
            continue
        findings.extend(_findings_from_json_file(candidate))
    return findings


def _findings_from_json_file(path: Path) -> list[dict[str, Any]]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    findings: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        for line in content.splitlines():
            try:
                findings.extend(_findings_from_payload(json.loads(line)))
            except json.JSONDecodeError:
                continue
        return findings
    try:
        return _findings_from_payload(json.loads(content))
    except json.JSONDecodeError:
        return []


def _findings_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if _looks_like_hit(payload):
            return [_normalize_payload_finding(payload)]
        findings: list[dict[str, Any]] = []
        for value in payload.values():
            findings.extend(_findings_from_payload(value))
        return findings
    if isinstance(payload, list):
        findings: list[dict[str, Any]] = []
        for item in payload:
            findings.extend(_findings_from_payload(item))
        return findings
    return []


def _looks_like_hit(payload: dict[str, Any]) -> bool:
    if payload.get("status") in {"matched", "fail", "failed"}:
        return True
    if payload.get("passed") is False:
        return True
    if payload.get("detector_result") is True:
        return True
    if payload.get("score") is not None and payload.get("probe") is not None:
        return True
    return False


def _normalize_payload_finding(payload: dict[str, Any]) -> dict[str, Any]:
    severity = str(payload.get("severity") or payload.get("level") or "medium").lower()
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "high" if payload.get("passed") is False else "medium"
    check = str(payload.get("check") or payload.get("probe") or payload.get("detector") or "garak")
    return {
        "check": f"garak:{check}",
        "severity": severity,
        "status": "matched",
        "field": "garak.report",
        "reason": str(payload.get("reason") or payload.get("description") or "garak reported a vulnerability hit"),
        "evidence": str(payload.get("evidence") or payload.get("prompt") or payload.get("output") or check),
    }


def _findings_from_output(output: str) -> list[dict[str, Any]]:
    lowered = output.lower()
    if "fail" not in lowered and "vulnerab" not in lowered:
        return []
    return [
        {
            "check": "garak:output_summary",
            "severity": "high",
            "status": "matched",
            "field": "garak.output",
            "reason": "garak output indicated at least one failed/vulnerable probe",
            "evidence": "garak textual output",
        }
    ]


def _score_from_findings(findings: list[dict[str, Any]]) -> float:
    penalties = {
        "low": 0.05,
        "medium": 0.15,
        "high": 0.3,
        "critical": 0.5,
    }
    score = 1.0
    for finding in findings:
        score -= penalties.get(str(finding.get("severity", "medium")).lower(), 0.15)
    return max(0.0, round(score, 2))
