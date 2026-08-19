"""LLM Guard adapter for scanning skill-package text."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from publisher.integrations.external_tools import configured_bool


_DEFAULT_ENABLED = True
_DEFAULT_MAX_TEXT_CHARS = 120_000


@dataclass(frozen=True, slots=True)
class LlmGuardSecurityResult:
    """Normalized LLM Guard security scan result."""

    status: str
    score: float | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    artifact_dir: str | None = None
    reason: str | None = None


def run_llm_guard_security_scan(
    *,
    skill_root: Path,
    artifacts_dir: Path,
    field_values: dict[str, str],
) -> LlmGuardSecurityResult:
    """Run LLM Guard input scanners over skill-package text."""
    if not configured_bool("PUBLISHER_LLM_GUARD_ENABLED", default=_DEFAULT_ENABLED):
        return LlmGuardSecurityResult(
            status="disabled",
            score=1.0,
            reason="PUBLISHER_LLM_GUARD_ENABLED is false",
        )

    llm_guard_dir = artifacts_dir / "llm_guard"
    llm_guard_dir.mkdir(parents=True, exist_ok=True)

    try:
        with _suppress_llm_guard_output():
            scan_prompt, scanners = _load_llm_guard()
    except ImportError as exc:
        return LlmGuardSecurityResult(
            status="not_available",
            reason=f"Install llm-guard to enable skill security scanning: {exc}",
            artifact_dir=str(llm_guard_dir),
        )
    except Exception as exc:  # pragma: no cover - defensive around optional scanner deps
        return LlmGuardSecurityResult(
            status="failed",
            reason=f"LLM Guard scanner initialization failed: {exc}",
            artifact_dir=str(llm_guard_dir),
        )

    findings: list[dict[str, Any]] = []
    scanner_scores: dict[str, dict[str, float]] = {}
    scanner_validity: dict[str, dict[str, bool]] = {}
    expected_scanners = set(_scanner_names(scanners))
    scanned_fields = 0

    for field_name, raw_text in field_values.items():
        text = _bounded_text(raw_text)
        if not text.strip():
            continue
        scanned_fields += 1
        try:
            with _suppress_llm_guard_output():
                _sanitized, results_valid, results_score = scan_prompt(scanners, text)
        except Exception as exc:  # pragma: no cover - scanner/runtime version drift
            return _failed_result(
                llm_guard_dir=llm_guard_dir,
                reason=f"LLM Guard failed while scanning {field_name}: {exc}",
            )

        returned_scanners = {str(name) for name in results_valid}
        scored_scanners = {str(name) for name in results_score}
        if returned_scanners != expected_scanners or scored_scanners != expected_scanners:
            return _failed_result(
                llm_guard_dir=llm_guard_dir,
                reason=f"LLM Guard returned missing scanner results for {field_name}",
            )

        for scanner_name, is_valid in results_valid.items():
            normalized_name = str(scanner_name)
            score = _coerce_score(results_score.get(scanner_name))
            scanner_scores.setdefault(normalized_name, {})[field_name] = score
            scanner_validity.setdefault(normalized_name, {})[field_name] = bool(is_valid)
            if is_valid:
                continue
            findings.append(
                _finding(
                    check=f"llm_guard:{normalized_name}",
                    severity=_severity_for_scanner(normalized_name, score),
                    field_name=field_name,
                    reason=f"LLM Guard {normalized_name} scanner marked this skill text as unsafe.",
                    evidence=_safe_evidence(normalized_name, text),
                    score=score,
                )
            )

    if not scanned_fields:
        return _failed_result(
            llm_guard_dir=llm_guard_dir,
            reason="LLM Guard did not receive any non-empty scan targets",
        )

    score = _score_from_findings(findings)
    artifact = {
        "skill_root": str(skill_root),
        "status": "scored",
        "score": score,
        "checks_run": _scanner_names(scanners),
        "findings": findings,
        "scanner_scores": scanner_scores,
        "scanner_validity": scanner_validity,
    }
    (llm_guard_dir / "llm_guard.report.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    return LlmGuardSecurityResult(
        status="scored",
        score=score,
        findings=findings,
        checks_run=_scanner_names(scanners),
        artifact_dir=str(llm_guard_dir),
    )


def _failed_result(*, llm_guard_dir: Path, reason: str) -> LlmGuardSecurityResult:
    """Persist and return an unscored evaluator failure."""
    artifact = {"status": "failed", "score": None, "reason": reason}
    (llm_guard_dir / "llm_guard.report.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return LlmGuardSecurityResult(
        status="failed",
        artifact_dir=str(llm_guard_dir),
        reason=reason,
    )


def _load_llm_guard():
    from llm_guard import scan_prompt
    from llm_guard.input_scanners import InvisibleText, PromptInjection, Secrets

    scanners = [
        PromptInjection(threshold=_float_env("PUBLISHER_LLM_GUARD_PROMPT_INJECTION_THRESHOLD", 0.85)),
        Secrets(),
        InvisibleText(),
    ]
    return scan_prompt, scanners


@contextmanager
def _suppress_llm_guard_output():
    """Keep third-party scanner logs out of the interactive publisher output."""
    noisy_loggers = [
        "llm_guard",
        "llm_guard.input_scanners",
        "llm_guard.input_scanners.prompt_injection",
        "llm_guard.input_scanners.prompt_injection.prompt_injection",
        "transformers",
        "huggingface_hub",
        "optimum",
        "sentence_transformers",
    ]
    previous_levels = {
        name: logging.getLogger(name).level
        for name in noisy_loggers
    }
    root_previous_level = logging.getLogger().level

    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.ERROR)
    logging.getLogger().setLevel(max(root_previous_level, logging.WARNING))

    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            yield
    finally:
        for logger_name, level in previous_levels.items():
            logging.getLogger(logger_name).setLevel(level)
        logging.getLogger().setLevel(root_previous_level)


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _bounded_text(text: str) -> str:
    max_chars = int(os.environ.get("PUBLISHER_LLM_GUARD_MAX_TEXT_CHARS", _DEFAULT_MAX_TEXT_CHARS))
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _scanner_names(scanners: list[object]) -> list[str]:
    return [scanner.__class__.__name__ for scanner in scanners]


def _coerce_score(value: object) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def _severity_for_scanner(scanner_name: str, score: float) -> str:
    lowered = scanner_name.lower()
    if "secret" in lowered:
        return "critical"
    if "prompt" in lowered and score >= 0.9:
        return "critical"
    if "prompt" in lowered:
        return "high"
    if "invisible" in lowered:
        return "medium"
    if score >= 0.9:
        return "critical"
    if score >= 0.7:
        return "high"
    return "medium"


def _safe_evidence(scanner_name: str, text: str) -> str:
    if "secret" in scanner_name.lower():
        return "redacted_secret_candidate"
    snippet = " ".join(text.strip().split())
    return snippet[:240]


def _finding(
    *,
    check: str,
    severity: str,
    field_name: str,
    reason: str,
    evidence: str,
    score: float | None,
) -> dict[str, Any]:
    return {
        "check": check,
        "severity": severity,
        "status": "matched",
        "field": field_name,
        "reason": reason,
        "evidence": evidence,
        "score": score,
    }


def _score_from_findings(findings: list[dict[str, Any]]) -> float:
    penalties = {
        "low": 0.05,
        "medium": 0.15,
        "high": 0.3,
        "critical": 0.5,
    }
    score = 1.0
    for finding in findings:
        score -= penalties.get(str(finding.get("severity")), 0.0)
    return max(0.0, round(score, 2))
