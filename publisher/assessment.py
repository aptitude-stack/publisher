"""Bounded public summaries of existing evaluation results, without raw evidence."""

from datetime import datetime, timezone
import re
from typing import Any

from publisher.artifacts.report import safe
from publisher.domain.models import PublishContext


# Quoted paths may contain spaces; unquoted paths end at whitespace/punctuation.
_ABSOLUTE_PATH = re.compile(
    r'''["'`](?:[A-Za-z]:[\\/]|/|\\\\)[^"'`\n]*["'`]'''
    + r'''|(?<![\w/\\])(?:[A-Za-z]:[\\/]|/|\\\\)[^\s<>"'`,;)]*'''
)


def _public_text(value: str) -> str:
    return _ABSOLUTE_PATH.sub("[path redacted]", safe(value))[:1000]


def _text_list(values: list[str]) -> tuple[list[str], int]:
    unique = list(dict.fromkeys(_public_text(value) for value in values))
    return unique[:100], max(0, len(unique) - 100)


def build_assessment(
    context: PublishContext, *, now: datetime | None = None
) -> dict[str, Any]:
    """Project only public fields; never copy evaluator dictionaries wholesale."""
    exam = context.performance_exam
    upskill = context.metadata.extra.get("upskill_evaluation", {})
    maturity_source = context.metadata.extra.get("maturity_score_source", {})
    warnings, warnings_omitted = _text_list([
        *context.validation.warnings, *upskill.get("validation_warnings", []),
    ])
    models, models_omitted = _text_list(exam.models_tested)
    checks, checks_omitted = _text_list(context.security.checks_run)
    findings = [
        {
            "check": _public_text(item["check"]),
            "severity": item["severity"],
            "explanation": _public_text(item["reason"]),
        }
        for item in context.security.findings[:100]
    ]
    return {
        "schema_version": 1,
        "assessed_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "maturity": {
            "validation_passed": context.validation.passed,
            "validation_score": maturity_source.get("validation_score", 0.0),
            "upskill_score": exam.score,
            "upskill_status": _public_text(upskill.get("status", "unavailable")),
            "test_case_count": exam.test_case_count,
            "models_tested": models,
            "models_omitted": models_omitted,
            "baseline_success_rate": exam.baseline_success_rate,
            "skilled_success_rate": exam.skilled_success_rate,
            "warnings": warnings,
            "warnings_omitted": warnings_omitted,
        },
        "security": {
            "scanned": context.security.scanned,
            "decision": context.security.decision,
            "checks_run": checks,
            "checks_omitted": checks_omitted,
            "findings": findings,
            "findings_omitted": max(0, len(context.security.findings) - 100),
        },
    }
