from __future__ import annotations

from publisher.artifacts.report import report_path

import json
from pathlib import Path
from typing import Any

from publisher.domain.models import PublishContext, SkillSource
from publisher.interfaces.mcp.models import (
    InspectSkillInput,
    PublishSkillInput,
    ResponseFormat,
)
from publisher.registry.client import RegistryPublishResult


def _skill(tmp_path: Path) -> Path:
    root = tmp_path / "example-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: example-skill\ndescription: Example\n---\n\n# Instructions\n\nUse this skill.\n",
        encoding="utf-8",
    )
    (root / "aptitude.yaml").write_text("version: 1.0.0\nintent: create_skill\ntags: [test]\ninputs_schema: {}\noutputs_schema: {}\n")
    return root


def _context(root: Path, *, decision: str = "allow") -> PublishContext:
    context = PublishContext(
        source=SkillSource(
            file_path=str(root),
            slug_override="example-skill",
            intent_override="create_skill",
        ),
        report_path=str(report_path(root)),
    )
    context.inventory.skill_root = str(root)
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
    context.ranking.publish_decision = decision
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


class _Pipeline:
    def __init__(self, context: PublishContext) -> None:
        self.context = context
        self.run_count = 0

    def create_context(self, **_: Any) -> PublishContext:
        return self.context

    def run(self, context: PublishContext) -> PublishContext:
        self.run_count += 1
        return context


def test_inspect_defaults_to_markdown_and_refreshes_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))

    assert output.startswith("# Aptitude Publisher")
    assert "Overall: 9.0/10" in output
    assert "Refreshed: `True`" in output
    assert (report_path(root)).is_file()


def test_fresh_receipt_reuses_allowed_evidence_and_uses_registry_scores(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    inspect_pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    adapter = server.PublisherMcpAdapter(pipeline_factory=lambda: inspect_pipeline)
    adapter.inspect_skill(InspectSkillInput(skill_path=root))

    publish_pipeline = _Pipeline(_context(root))
    relationship_calls: list[dict[str, object]] = []
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(
        server,
        "check_relationship_references",
        lambda **kwargs: relationship_calls.append(kwargs) or [],
    )
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: RegistryPublishResult(
            status_code=201,
            body={
                "message": "published",
                "scores": {
                    "maturity_score": 0.11,
                    "security_score": 0.12,
                    "overall_score": 0.13,
                },
                "metadata": {
                    "maturity_score": 0.88,
                    "security_score": 0.91,
                    "overall_score": 0.9,
                },
            },
            request_id="request-1",
        ),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: publish_pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert publish_pipeline.run_count == 0
    assert relationship_calls
    assert payload["receipt"]["evidence_reused"] is True
    assert payload["receipt"]["evidence_refreshed"] is False
    assert payload["evaluation"]["scores"] == {
        "maturity_score": 0.88,
        "security_score": 0.91,
        "overall_score": 0.9,
    }
    assert "publish-secret" not in output
    assert "trust" not in output.lower()


def test_authoritative_registry_metadata_allows_null_scores_and_ignores_aliases() -> None:
    from publisher.interfaces.mcp.server import _authoritative_scores

    assert _authoritative_scores(
        {
            "scores": {
                "maturity_score": 0.11,
                "security_score": 0.12,
                "overall_score": 0.13,
            },
            "metadata": {
                "maturity_score": None,
                "security_score": 0.91,
                "overall_score": None,
            },
        }
    ) == {
        "maturity_score": None,
        "security_score": 0.91,
        "overall_score": None,
    }


def test_tampered_well_typed_receipt_payload_refreshes_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    inspect_pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    server.PublisherMcpAdapter(
        pipeline_factory=lambda: inspect_pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))
    receipt_path = report_path(root)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))["inspection_receipt"]
    receipt["final_payload"]["slug"] = "other-skill"
    receipt["final_payload"]["governance"]["namespace"] = "private"
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")

    publish_pipeline = _Pipeline(_context(root))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: RegistryPublishResult(status_code=201, body={}, request_id=None),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: publish_pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert publish_pipeline.run_count == 1
    assert payload["receipt"]["evidence_reused"] is False
    assert payload["receipt"]["evidence_refreshed"] is True


def test_explicit_publish_version_mismatch_blocks_before_upload(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    inspect_pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    server.PublisherMcpAdapter(
        pipeline_factory=lambda: inspect_pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))

    publish_pipeline = _Pipeline(_context(root))
    uploads: list[bool] = []
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: uploads.append(True)
        or RegistryPublishResult(status_code=201, body={}, request_id=None),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: publish_pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            version="2.0.0",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert publish_pipeline.run_count == 1
    assert uploads == []
    assert payload["status"] == "error"
    assert "identity did not match" in payload["message"]


def test_fresh_blocked_receipt_blocks_without_rerunning_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    inspect_pipeline = _Pipeline(_context(root, decision="block"))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    server.PublisherMcpAdapter(
        pipeline_factory=lambda: inspect_pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))

    publish_pipeline = _Pipeline(_context(root, decision="allow"))
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: (_ for _ in ()).throw(AssertionError("blocked receipt must not upload")),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: publish_pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert publish_pipeline.run_count == 0
    assert payload["status"] == "blocked"
    assert payload["receipt"]["evidence_reused"] is True
    assert payload["receipt"]["evidence_refreshed"] is False


def test_unsigned_inspection_receipt_is_not_reused_for_publish(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    for name in ("APTITUDE_PUBLISH_TOKEN", "APTITUDE_INTEGRATION_PUBLISH_TOKEN", "PUBLISH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    inspect_pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    server.PublisherMcpAdapter(
        pipeline_factory=lambda: inspect_pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))

    publish_pipeline = _Pipeline(_context(root))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: RegistryPublishResult(status_code=201, body={}, request_id=None),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: publish_pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert publish_pipeline.run_count == 1
    assert payload["receipt"]["evidence_reused"] is False


def test_tampered_blocked_receipt_cannot_become_reusable_allow(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    inspect_pipeline = _Pipeline(_context(root, decision="block"))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    server.PublisherMcpAdapter(
        pipeline_factory=lambda: inspect_pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))
    receipt_path = report_path(root)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))["inspection_receipt"]
    receipt["status"] = "ready"
    receipt["evidence"]["ranking"]["publish_decision"] = "allow"
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")

    publish_pipeline = _Pipeline(_context(root, decision="block"))
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: (_ for _ in ()).throw(AssertionError("blocked evaluation must not upload")),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: publish_pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert publish_pipeline.run_count == 1
    assert payload["status"] == "blocked"
    assert payload["receipt"]["evidence_reused"] is False


def test_tampered_signed_metadata_receipt_refreshes_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    inspect_pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    server.PublisherMcpAdapter(
        pipeline_factory=lambda: inspect_pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))
    receipt_path = report_path(root)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))["inspection_receipt"]
    receipt["final_payload"]["metadata"]["name"] = "Tampered"
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")

    publish_pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: RegistryPublishResult(status_code=201, body={}, request_id=None),
    )
    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: publish_pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert publish_pipeline.run_count == 1
    assert payload["receipt"]["evidence_reused"] is False


def test_expired_receipt_runs_full_pipeline_and_refreshes_it(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    inspect_pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    server.PublisherMcpAdapter(
        pipeline_factory=lambda: inspect_pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))
    receipt_path = report_path(root)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))["inspection_receipt"]
    receipt["expires_at"] = "2000-01-01T00:00:00Z"
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")

    pipeline = _Pipeline(_context(root))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: RegistryPublishResult(status_code=201, body={}, request_id=None),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert pipeline.run_count == 1
    assert payload["receipt"]["evidence_reused"] is False
    assert payload["receipt"]["evidence_refreshed"] is True


def test_corrupt_receipt_runs_full_pipeline_and_refreshes_it(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    receipt_path = report_path(root)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("{not-json", encoding="utf-8")
    pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: RegistryPublishResult(status_code=201, body={}, request_id=None),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert pipeline.run_count == 1
    assert payload["receipt"]["evidence_reused"] is False
    assert payload["receipt"]["evidence_refreshed"] is True


def test_semantically_corrupt_receipt_is_a_cache_miss_and_refreshes_it(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    root = _skill(tmp_path)
    inspect_pipeline = _Pipeline(_context(root))
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    server.PublisherMcpAdapter(
        pipeline_factory=lambda: inspect_pipeline
    ).inspect_skill(InspectSkillInput(skill_path=root))
    receipt_path = report_path(root)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))["inspection_receipt"]
    receipt["final_payload"]["metadata"] = ["not", "a", "mapping"]
    receipt_path.write_text(json.dumps({"schema_version": 1, "inspection_receipt": receipt}), encoding="utf-8")

    pipeline = _Pipeline(_context(root))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-secret")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: RegistryPublishResult(status_code=201, body={}, request_id=None),
    )

    output = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(output)

    assert pipeline.run_count == 1
    assert payload["status"] == "published"
    assert payload["receipt"]["evidence_reused"] is False
    assert payload["receipt"]["evidence_refreshed"] is True
