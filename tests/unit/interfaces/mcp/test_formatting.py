from __future__ import annotations

import json

from publisher.interfaces.mcp.formatting import format_response
from publisher.interfaces.mcp.models import (
    EvaluationSummary,
    PublisherToolResult,
    RegistrySummary,
    ResponseFormat,
)


def _result() -> PublisherToolResult:
    return PublisherToolResult(
        ok=True,
        status="published",
        message="Skill published successfully.",
        evaluation=EvaluationSummary(
            skill_path="/tmp/example-skill",
            slug="example-skill",
            version="1.0.0",
            intent="create_skill",
            publish_decision="allow",
            validation_passed=True,
            security_score=0.95,
            security_decision="allow",
            performance_evidence_score=0.8,
            maturity_score=0.75,
            overall_score=0.9,
            overall_label="excellent",
            artifacts_dir="/tmp/example-skill/.publisher_artifacts",
        ),
        evidence_reused=True,
        receipt_created_at="2026-08-23T10:00:00Z",
        receipt_expires_at="2026-08-23T11:00:00Z",
    )


def test_markdown_uses_ten_point_scores_and_canonical_names() -> None:
    output = format_response(_result(), ResponseFormat.MARKDOWN)

    assert "Security: 9.5/10" in output
    assert "Maturity: 7.5/10" in output
    assert "Overall: 9.0/10" in output
    assert "Performance evidence (non-persisted): 8.0/10" in output
    assert "ranking" not in output.lower()
    assert "trust" not in output.lower()


def test_json_uses_normalized_scores_and_explicit_scale_without_trust() -> None:
    output = format_response(_result(), ResponseFormat.JSON)
    payload = json.loads(output)

    assert payload["score_scale"] == {
        "normalized_min": 0.0,
        "normalized_max": 1.0,
        "display_min": 0.0,
        "display_max": 10.0,
    }
    assert payload["scores"] == {
        "maturity_score": 0.75,
        "security_score": 0.95,
        "overall_score": 0.9,
    }
    assert payload["performance_evidence"] == {
        "score": 0.8,
        "persisted": False,
    }
    assert "trust" not in output.lower()
    assert "ranking" not in output.lower()


def test_toon_is_machine_readable_and_keeps_normalized_scores() -> None:
    output = format_response(_result(), ResponseFormat.TOON)

    assert "score_scale" in output
    assert "overall" in output
    assert "0.9" in output
    assert "trust" not in output.lower()


def test_machine_formats_remove_nested_registry_trust_context() -> None:
    result = _result()
    result.registry = RegistrySummary(
        status_code=201,
        body={
            "provenance": {
                "trust_context": {
                    "policy_profile": "internal-only",
                    "trust_tier": "verified",
                }
            }
        },
        bundle_size_bytes=10,
    )

    outputs = [
        format_response(result, ResponseFormat.JSON),
        format_response(result, ResponseFormat.TOON),
        format_response(result, ResponseFormat.MARKDOWN),
    ]
    for output in outputs:
        assert "trust_context" not in output
        assert "policy_profile" not in output
