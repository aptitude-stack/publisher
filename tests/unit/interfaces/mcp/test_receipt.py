from __future__ import annotations

from publisher.artifacts.report import report_path

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from publisher.domain.models import PublishContext, SkillSource
from publisher.interfaces.mcp import receipt as receipt_module
from publisher.interfaces.mcp.receipt import (
    config_fingerprint,
    load_inspection_receipt,
    receipt_matches,
    write_inspection_receipt,
)


def _context(skill_root: Path) -> PublishContext:
    context = PublishContext(
        source=SkillSource(
            file_path=str(skill_root),
            slug_override="example-skill",
            intent_override="create_skill",
            trust_tier="untrusted",
            namespace="public",
            artifact_origin="internal",
        ),
        report_path=str(report_path(skill_root)),
    )
    context.inventory.skill_root = str(skill_root)
    context.identity.slug = "example-skill"
    context.identity.version = "1.0.0"
    context.identity.intent = "create_skill"
    context.metadata.name = "Example Skill"
    context.metadata.maturity_score = 0.75
    context.security.score = 0.95
    context.security.decision = "allow"
    context.security.scanned = True
    context.validation.passed = True
    context.performance_exam.score = 0.8
    context.performance_exam.test_case_count = 2
    context.performance_exam.models_tested = ["gpt-4.1-mini"]
    context.metadata.extra["upskill_evaluation"] = {
        "status": "scored",
        "score": 0.8,
        "validation_errors": [],
    }
    context.ranking.total_score = 0.9
    context.ranking.label = "excellent"
    context.ranking.publish_decision = "allow"
    context.delivery_payload.slug = "example-skill"
    context.delivery_payload.version = "1.0.0"
    context.delivery_payload.intent = "create_skill"
    context.delivery_payload.metadata = {
        "name": "Example Skill",
        "maturity_score": 0.75,
        "security_score": 0.95,
        "overall_score": 0.9,
    }
    context.delivery_payload.governance = {
        "trust_tier": "untrusted",
        "namespace": "public",
        "artifact_origin": "internal",
        "policy_pack_slug": None,
        "provenance": None,
    }
    return context


def test_receipt_is_canonical_atomic_and_credential_values_are_not_serialized(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 8, 23, 10, tzinfo=timezone.utc)
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")

    receipt = write_inspection_receipt(
        _context(skill_root), bundle_bytes=b"bundle", now=now
    )
    receipt_path = report_path(skill_root)
    raw = receipt_path.read_text(encoding="utf-8")

    assert receipt["schema_version"] == 1
    assert receipt["source_bundle_sha256"] == hashlib.sha256(b"bundle").hexdigest()
    assert receipt["created_at"] == "2026-08-23T10:00:00Z"
    assert receipt["expires_at"] == "2026-08-23T11:00:00Z"
    assert json.loads(raw)["inspection_receipt"] == receipt
    assert raw == json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")) + "\n"
    assert "super-secret" not in raw
    assert receipt["config_fingerprint"]["credentials"]["OPENAI_API_KEY"] is True


def test_signed_receipt_verifies_token_and_payload_integrity(tmp_path: Path) -> None:
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    token = "publish-secret"
    receipt = write_inspection_receipt(
        _context(skill_root), bundle_bytes=b"bundle", publish_token=token
    )
    receipt_path = report_path(skill_root)
    raw = receipt_path.read_text(encoding="utf-8")

    assert isinstance(receipt["mac"], str)
    assert len(receipt["mac"]) == 64
    assert token not in raw
    assert hashlib.sha256(token.encode()).hexdigest() not in raw
    assert load_inspection_receipt(receipt_path, publish_token=token) == receipt
    assert load_inspection_receipt(receipt_path, publish_token="rotated-token") is None

    receipt["evidence"]["security"]["score"] = 0.1
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")
    assert load_inspection_receipt(receipt_path, publish_token=token) is None


def test_publisher_version_prefers_installed_package_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def package_version(name: str) -> str:
        seen.append(name)
        return "9.9.9"

    monkeypatch.setattr(receipt_module.importlib.metadata, "version", package_version)

    assert receipt_module._publisher_version() == "9.9.9"
    assert seen == ["aptitude-publisher"]


def test_publisher_version_falls_back_to_source_checkout_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    receipt_file = source_root / "publisher" / "interfaces" / "mcp" / "receipt.py"
    receipt_file.parent.mkdir(parents=True)
    (source_root / "pyproject.toml").write_text(
        '[project]\nversion = "0.2.0"\n', encoding="utf-8"
    )

    def package_missing(_: str) -> str:
        raise receipt_module.importlib.metadata.PackageNotFoundError(
            "aptitude-publisher"
        )

    monkeypatch.setattr(receipt_module.importlib.metadata, "version", package_missing)
    monkeypatch.setattr(receipt_module, "__file__", str(receipt_file))

    assert receipt_module._publisher_version() == "0.2.0"


def test_receipt_load_rejects_expired_corrupt_and_mismatched_inputs(tmp_path: Path) -> None:
    now = datetime(2026, 8, 23, 10, tzinfo=timezone.utc)
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    context = _context(skill_root)
    receipt = write_inspection_receipt(context, bundle_bytes=b"bundle", now=now)
    receipt_path = report_path(skill_root)

    assert load_inspection_receipt(receipt_path, now=now + timedelta(minutes=59)) == receipt
    assert load_inspection_receipt(receipt_path, now=now + timedelta(hours=1)) is None

    receipt_path.write_text("{not-json", encoding="utf-8")
    assert load_inspection_receipt(receipt_path, now=now) is None

    assert receipt_matches(
        receipt,
        identity={"slug": "other-skill", "version": "1.0.0", "intent": "create_skill"},
        governance={"namespace": "public", "artifact_origin": "internal"},
        source_bundle_sha256=receipt["source_bundle_sha256"],
    ) is False


def test_receipt_load_rejects_semantic_corruption_and_extended_ttl(tmp_path: Path) -> None:
    now = datetime(2026, 8, 23, 10, tzinfo=timezone.utc)
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    context = _context(skill_root)
    receipt = write_inspection_receipt(context, bundle_bytes=b"bundle", now=now)
    receipt_path = report_path(skill_root)

    receipt["scores"]["overall_score"] = {"not": "a score"}
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")
    assert load_inspection_receipt(receipt_path, now=now) is None

    receipt = write_inspection_receipt(context, bundle_bytes=b"bundle", now=now)
    receipt["expires_at"] = "2026-08-23T12:00:00Z"
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")
    assert load_inspection_receipt(receipt_path, now=now + timedelta(minutes=59)) is None


def test_receipt_load_rejects_invalid_decisions_and_status_mismatch(tmp_path: Path) -> None:
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    context = _context(skill_root)
    receipt_path = report_path(skill_root)

    receipt = write_inspection_receipt(context, bundle_bytes=b"bundle")
    receipt["evidence"]["ranking"]["publish_decision"] = "warn"
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")
    assert load_inspection_receipt(receipt_path) is None

    receipt = write_inspection_receipt(context, bundle_bytes=b"bundle")
    receipt["status"] = "blocked"
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")
    assert load_inspection_receipt(receipt_path) is None


def test_config_fingerprint_tracks_evaluator_switches_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("PUBLISHER_UPSKILL_ENABLED", raising=False)

    baseline = config_fingerprint()
    monkeypatch.setenv("GROQ_API_KEY", "groq-secret")
    with_groq = config_fingerprint()

    assert with_groq["credentials"]["GROQ_API_KEY"] is True
    assert "groq-secret" not in json.dumps(with_groq, sort_keys=True)
    assert {"enabled", "base_url", "timeout_seconds", "command_sha256", "use_default_tests"} <= set(
        with_groq["upskill"]
    )
    assert {"base_url", "timeout_seconds"} <= set(with_groq["validation"])

    monkeypatch.setenv("PUBLISHER_UPSKILL_ENABLED", "false")
    disabled = config_fingerprint()
    assert disabled != baseline
    assert disabled["upskill"]["enabled"] is False


def test_receipt_matches_rejects_well_typed_payload_cross_binding(tmp_path: Path) -> None:
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    context = _context(skill_root)
    receipt = write_inspection_receipt(context, bundle_bytes=b"bundle")
    receipt["final_payload"]["slug"] = "other-skill"
    receipt["final_payload"]["governance"]["namespace"] = "private"

    assert receipt_matches(
        receipt,
        identity=receipt["identity"],
        governance=receipt["governance"],
        source_bundle_sha256=receipt["source_bundle_sha256"],
        config=receipt["config_fingerprint"],
    ) is False


def test_receipt_write_preserves_existing_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    context = _context(skill_root)
    write_inspection_receipt(context, bundle_bytes=b"original")
    receipt_path = report_path(skill_root)
    original = receipt_path.read_bytes()

    def fail_replace(*_: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(receipt_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_inspection_receipt(context, bundle_bytes=b"replacement")

    assert receipt_path.read_bytes() == original
    assert not list(receipt_path.parent.glob(f".{receipt_path.name}.*"))


def test_invalid_utf8_report_is_a_cache_miss(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"\xff")
    assert load_inspection_receipt(path) is None
