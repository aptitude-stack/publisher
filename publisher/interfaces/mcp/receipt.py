"""Reusable local inspection receipts for Publisher MCP publish calls."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib.metadata
import json
import os
from pathlib import Path
import tomllib
from collections.abc import Callable
from typing import Any

from publisher.domain.models import PublishContext
from publisher.artifacts.report import safe as _safe, write_report


RECEIPT_SCHEMA_VERSION = 1
RECEIPT_TTL = timedelta(hours=1)
_CREDENTIAL_ENV_NAMES = (
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "UPSKILL_API_KEY",
    "PUBLISHER_LLM_VALIDATION_API_KEY",
)
_RECEIPT_STATUSES = frozenset({"ready", "blocked"})
_RECEIPT_DECISIONS = frozenset({"allow", "review_required", "block"})
_SCORE_KEYS = frozenset(
    {
        "maturity_score",
        "security_score",
        "overall_score",
        "performance_evidence_score",
    }
)


def write_inspection_receipt(
    context: PublishContext,
    *,
    bundle_bytes: bytes,
    publish_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write one canonical receipt with an atomic same-directory replacement."""

    created_at = _utc(now)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "created_at": _format_time(created_at),
        "expires_at": _format_time(created_at + RECEIPT_TTL),
        "publisher_version": _publisher_version(),
        "evaluator_versions": _evaluator_versions(),
        "source_bundle_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
        "identity": {
            "slug": context.identity.slug,
            "version": context.identity.version,
            "intent": context.identity.intent,
        },
        "governance": {
            "trust_tier": context.source.trust_tier,
            "namespace": context.source.namespace,
            "artifact_origin": context.source.artifact_origin,
            "policy_pack_slug": context.source.policy_pack_slug,
            "publisher_identity": context.source.publisher_identity,
        },
        "config_fingerprint": config_fingerprint(),
        "final_payload": _safe(
            {
                "slug": context.delivery_payload.slug,
                "version": context.delivery_payload.version,
                "intent": context.delivery_payload.intent,
                "content": context.delivery_payload.content,
                "metadata": context.delivery_payload.metadata,
                "governance": context.delivery_payload.governance,
                "relationships": context.delivery_payload.relationships,
            }
        ),
        "gates": _safe([asdict(item) for item in context.gate_history]),
        "scores": {
            "maturity_score": context.metadata.maturity_score,
            "security_score": context.security.score,
            "overall_score": context.ranking.total_score,
            "performance_evidence_score": context.performance_exam.score,
        },
        "evidence": _safe(
            {
                "security": asdict(context.security),
                "validation": asdict(context.validation),
                "performance": asdict(context.performance_exam),
                "upskill": _upskill_evidence(context),
                "ranking": {
                    "criteria_scores": context.ranking.criteria_scores,
                    "weights": context.ranking.weights,
                    "label": context.ranking.label,
                    "publish_decision": context.ranking.publish_decision,
                    "explanation": context.ranking.explanation,
                },
            }
        ),
        "warnings": _safe(_warnings(context)),
        "status": _receipt_status(context.ranking.publish_decision),
    }
    receipt = _safe(receipt)
    receipt["mac"] = _receipt_mac(receipt, publish_token)
    write_report(context, status="ready" if context.ranking.publish_decision in {"allow", "review_required"} else "blocked", inspection_receipt=receipt)
    return receipt


def load_inspection_receipt(
    path: Path,
    *,
    publish_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return a structurally valid, unexpired receipt or ``None``."""

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or report.get("schema_version") != 1:
            return None
        payload = report.get("inspection_receipt")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != RECEIPT_SCHEMA_VERSION
    ):
        return None
    created_at = _parse_time(payload.get("created_at"))
    expires_at = _parse_time(payload.get("expires_at"))
    current = _utc(now)
    if (
        created_at is None
        or expires_at is None
        or expires_at != created_at + RECEIPT_TTL
        or expires_at <= current
        or created_at > current
    ):
        return None
    required = {
        "source_bundle_sha256",
        "publisher_version",
        "evaluator_versions",
        "identity",
        "governance",
        "config_fingerprint",
        "final_payload",
        "gates",
        "scores",
        "evidence",
        "warnings",
        "status",
        "mac",
    }
    if not required.issubset(payload):
        return None
    if publish_token is not None and not _verify_receipt_mac(payload, publish_token):
        return None
    return payload if _valid_receipt_payload(payload) else None


def receipt_matches(
    receipt: dict[str, Any],
    *,
    identity: dict[str, Any],
    governance: dict[str, Any],
    source_bundle_sha256: str,
    config: dict[str, Any] | None = None,
) -> bool:
    """Check the caller's identity, governance, source, and evaluator config."""

    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        return False
    if not isinstance(receipt.get("mac"), str):
        return False
    if receipt.get("publisher_version") != _publisher_version():
        return False
    if receipt.get("evaluator_versions") != _evaluator_versions():
        return False
    if receipt.get("source_bundle_sha256") != source_bundle_sha256:
        return False
    if receipt.get("identity") != identity:
        return False
    receipt_governance = receipt.get("governance")
    if not isinstance(receipt_governance, dict):
        return False
    if any(receipt_governance.get(key) != value for key, value in governance.items()):
        return False
    if not _payload_matches_identity_governance(receipt):
        return False
    if config is not None and receipt.get("config_fingerprint") != config:
        return False
    return True


def _payload_matches_identity_governance(receipt: dict[str, Any]) -> bool:
    identity = receipt.get("identity")
    final_payload = receipt.get("final_payload")
    governance = receipt.get("governance")
    if not isinstance(identity, dict) or not isinstance(final_payload, dict):
        return False
    if not isinstance(governance, dict):
        return False
    if any(final_payload.get(key) != identity.get(key) for key in ("slug", "version", "intent")):
        return False

    payload_governance = final_payload.get("governance")
    if not isinstance(payload_governance, dict):
        return False
    for key in ("trust_tier", "namespace", "artifact_origin", "policy_pack_slug"):
        if key not in payload_governance or payload_governance.get(key) != governance.get(key):
            return False
    if "publisher_identity" in payload_governance:
        if payload_governance["publisher_identity"] != governance.get("publisher_identity"):
            return False
    provenance = payload_governance.get("provenance")
    if provenance is not None:
        if not isinstance(provenance, dict):
            return False
        if (
            "publisher_identity" in provenance
            and provenance["publisher_identity"] != governance.get("publisher_identity")
        ):
            return False
    return True


def config_fingerprint() -> dict[str, Any]:
    """Return non-secret evaluator configuration and credential presence only."""

    tests_path = os.environ.get("UPSKILL_TESTS_PATH")
    return {
        "credentials": {
            name: bool(os.environ.get(name, "").strip())
            for name in _CREDENTIAL_ENV_NAMES
        },
        "tests_file_sha256": _file_sha256(Path(tests_path)) if tests_path else None,
        "models": os.environ.get("UPSKILL_MODELS") or "gpt-4.1-mini",
        "provider": os.environ.get("UPSKILL_PROVIDER") or "openai",
        "no_baseline": os.environ.get("UPSKILL_NO_BASELINE") or "false",
        "upskill": {
            "enabled": _configured_bool("PUBLISHER_UPSKILL_ENABLED", default=True),
            "base_url": _hashed_setting(os.environ.get("UPSKILL_BASE_URL")),
            "timeout_seconds": os.environ.get("PUBLISHER_UPSKILL_TIMEOUT_SECONDS")
            or "600",
            "command_sha256": _hashed_setting(
                os.environ.get("PUBLISHER_UPSKILL_COMMAND")
            ),
            "use_default_tests": _configured_bool(
                "UPSKILL_USE_DEFAULT_TESTS", default=False
            ),
        },
        "security": {
            "enabled": os.environ.get("PUBLISHER_LLM_GUARD_ENABLED") or "true",
            "prompt_injection_threshold": os.environ.get(
                "PUBLISHER_LLM_GUARD_PROMPT_INJECTION_THRESHOLD"
            )
            or "0.85",
        },
        "validation": {
            "enabled": os.environ.get("PUBLISHER_LLM_VALIDATION_ENABLED") or "auto",
            "model": os.environ.get("PUBLISHER_LLM_VALIDATION_MODEL")
            or os.environ.get("GARAK_TARGET_NAME")
            or "llama-3.1-8b-instant",
            "base_url": _hashed_setting(
                os.environ.get("PUBLISHER_LLM_VALIDATION_BASE_URL")
                or os.environ.get("UPSKILL_BASE_URL")
                or "https://api.groq.com/openai/v1"
            ),
            "timeout_seconds": os.environ.get(
                "PUBLISHER_LLM_VALIDATION_TIMEOUT_SECONDS"
            )
            or "120",
        },
        "package_versions": _evaluator_versions(),
    }


def _configured_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _hashed_setting(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _upskill_evidence(context: PublishContext) -> dict[str, Any]:
    value = context.metadata.extra.get("upskill_evaluation", {})
    if not isinstance(value, dict):
        return {}
    keys = (
        "status",
        "score",
        "passed",
        "test_case_count",
        "baseline_success_rate",
        "skilled_success_rate",
        "skill_lift",
        "baseline_avg_tokens",
        "skilled_avg_tokens",
        "token_delta",
        "models_tested",
        "validation_errors",
        "validation_warnings",
        "recommendations",
        "reason",
    )
    return {key: value[key] for key in keys if key in value}


def _warnings(context: PublishContext) -> list[str]:
    return [
        *context.validation.warnings,
        *(warning for gate in context.gate_history for warning in gate.warnings),
    ]


def _publisher_version() -> str:
    try:
        return importlib.metadata.version("aptitude-publisher")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project", {})
        version = project.get("version") if isinstance(project, dict) else None
        return str(version or "unknown")
    except (OSError, tomllib.TOMLDecodeError):
        return "unknown"


def _evaluator_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ("upskill", "llm-guard"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unknown"
    return versions


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _valid_receipt_payload(payload: dict[str, Any]) -> bool:
    """Validate values used by receipt hydration before allowing cache reuse."""

    digest = payload.get("source_bundle_sha256")
    if not (
        isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    ):
        return False
    if not _is_non_empty_string(payload.get("publisher_version")):
        return False
    evaluator_versions = payload.get("evaluator_versions")
    if not _is_string_mapping(evaluator_versions):
        return False
    if payload.get("status") not in _RECEIPT_STATUSES:
        return False
    if not _valid_mac(payload.get("mac")):
        return False
    if not _valid_identity(payload.get("identity")):
        return False
    if not _valid_governance(payload.get("governance")):
        return False
    if not _valid_config_fingerprint(payload.get("config_fingerprint")):
        return False
    if not _valid_final_payload(payload.get("final_payload")):
        return False
    if not _payload_matches_identity_governance(payload):
        return False
    if not _valid_gates(payload.get("gates")):
        return False
    if not _valid_scores(payload.get("scores")):
        return False
    if not _valid_evidence(payload.get("evidence")):
        return False
    evidence = payload["evidence"]
    decision = evidence["ranking"]["publish_decision"]
    expected_status = "blocked" if decision == "block" else "ready"
    return payload["status"] == expected_status and _is_string_list(
        payload.get("warnings")
    )


def _valid_identity(value: Any) -> bool:
    return _valid_fields(
        value,
        {
            "slug": _is_optional_string,
            "version": _is_optional_string,
            "intent": _is_optional_string,
        },
    )


def _valid_governance(value: Any) -> bool:
    return _valid_fields(
        value,
        {
            "trust_tier": _is_optional_string,
            "namespace": _is_optional_string,
            "artifact_origin": _is_optional_string,
            "policy_pack_slug": _is_optional_string,
            "publisher_identity": _is_optional_string,
        },
    )


def _valid_config_fingerprint(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    credentials = value.get("credentials")
    if not isinstance(credentials, dict) or any(
        not isinstance(item, bool) for item in credentials.values()
    ):
        return False
    if not _is_optional_string(value.get("tests_file_sha256")):
        return False
    if not all(
        _is_non_empty_string(value.get(key))
        for key in ("models", "provider", "no_baseline")
    ):
        return False
    if not _valid_fields(
        value.get("security"),
        {
            "enabled": _is_non_empty_string,
            "prompt_injection_threshold": _is_non_empty_string,
        },
    ):
        return False
    if not _valid_fields(
        value.get("upskill"),
        {
            "enabled": _is_bool,
            "base_url": _is_optional_string,
            "timeout_seconds": _is_non_empty_string,
            "command_sha256": _is_optional_string,
            "use_default_tests": _is_bool,
        },
    ):
        return False
    if not _valid_fields(
        value.get("validation"),
        {
            "enabled": _is_non_empty_string,
            "model": _is_non_empty_string,
            "base_url": _is_non_empty_string,
            "timeout_seconds": _is_non_empty_string,
        },
    ):
        return False
    return _is_string_mapping(value.get("package_versions"))


def _valid_final_payload(value: Any) -> bool:
    return _valid_fields(
        value,
        {
            "slug": _is_optional_string,
            "version": _is_optional_string,
            "intent": _is_optional_string,
            "content": _is_mapping,
            "metadata": _is_mapping,
            "governance": _is_mapping,
            "relationships": _is_mapping,
        },
    )


def _valid_gates(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return all(
        _valid_fields(
            item,
            {
                "gate_name": _is_non_empty_string,
                "passed": _is_bool,
                "explanation": _is_optional_string,
                "blocking_issues": _is_string_list,
                "warnings": _is_string_list,
                "data": _is_mapping,
            },
        )
        for item in value
    )


def _valid_scores(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _SCORE_KEYS
        and all(_is_normalized_score(item) for item in value.values())
    )


def _valid_evidence(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not _valid_security(value.get("security")):
        return False
    if not _valid_validation(value.get("validation")):
        return False
    if not _valid_performance(value.get("performance")):
        return False
    if not _valid_upskill(value.get("upskill")):
        return False
    return _valid_ranking(value.get("ranking"))


def _valid_security(value: Any) -> bool:
    return _valid_fields(
        value,
        {
            "score": _is_normalized_score,
            "findings": _is_dict_list,
            "scan_targets": _is_string_list,
            "checks_run": _is_string_list,
            "severity_counts": _is_int_mapping,
            "decision": _is_optional_string,
            "artifact_path": _is_optional_string,
            "notes": _is_string_list,
            "scanned": _is_bool,
        },
    )


def _valid_validation(value: Any) -> bool:
    return _valid_fields(
        value,
        {
            "passed": _is_bool,
            "errors": _is_string_list,
            "warnings": _is_string_list,
            "checks_run": _is_string_list,
            "artifact_path": _is_optional_string,
            "notes": _is_string_list,
        },
    )


def _valid_performance(value: Any) -> bool:
    return _valid_fields(
        value,
        {
            "score": _is_normalized_score,
            "passed": _is_bool,
            "test_case_count": lambda item: isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0,
            "models_tested": _is_string_list,
            "baseline_success_rate": _is_normalized_score,
            "skilled_success_rate": _is_normalized_score,
            "skill_lift": _is_number_or_none,
            "baseline_avg_tokens": _is_optional_int,
            "skilled_avg_tokens": _is_optional_int,
            "token_delta": _is_optional_int,
            "efficiency_label": _is_optional_string,
            "artifact_path": _is_optional_string,
            "notes": _is_string_list,
        },
    )


def _valid_upskill(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    status = value.get("status")
    errors = value.get("validation_errors", [])
    return _is_optional_string(status) and _is_string_list(errors)


def _valid_ranking(value: Any) -> bool:
    if not _valid_fields(
        value,
        {
            "criteria_scores": _is_number_mapping,
            "weights": _is_number_mapping,
            "label": _is_optional_string,
            "publish_decision": _is_non_empty_string,
            "explanation": _is_string_list,
        },
    ):
        return False
    return value["publish_decision"] in _RECEIPT_DECISIONS


def _valid_fields(
    value: Any, checks: dict[str, Callable[[Any], bool]]
) -> bool:
    return isinstance(value, dict) and all(
        key in value and check(value[key]) for key, check in checks.items()
    )


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_optional_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_dict_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _is_int_mapping(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(item, int) and not isinstance(item, bool) for item in value.values()
    )


def _is_number_mapping(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(item, (int, float)) and not isinstance(item, bool)
        for item in value.values()
    )


def _is_string_mapping(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    )


def _is_normalized_score(value: Any) -> bool:
    return value is None or (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0.0 <= value <= 1.0
    )


def _is_number_or_none(value: Any) -> bool:
    return value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def _is_optional_int(value: Any) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _receipt_status(decision: Any) -> str:
    return "blocked" if decision == "block" else "ready"


def _receipt_mac(receipt: dict[str, Any], publish_token: str | None) -> str | None:
    if publish_token is None:
        return None
    payload = {
        key: value for key, value in receipt.items() if key != "mac"
    }
    message = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hmac.new(
        publish_token.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def _verify_receipt_mac(receipt: dict[str, Any], publish_token: str) -> bool:
    mac = receipt.get("mac")
    expected = _receipt_mac(receipt, publish_token)
    return isinstance(mac, str) and expected is not None and hmac.compare_digest(
        mac, expected
    )


def _valid_mac(value: Any) -> bool:
    return value is None or (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
