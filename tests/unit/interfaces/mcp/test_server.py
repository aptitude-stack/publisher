from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
import warnings

from publisher.domain.models import PublishContext, SkillSource
from publisher.interfaces.mcp.models import InspectSkillInput, PublishSkillInput
from publisher.registry.client import (
    ExistingSkill,
    ExistingSkillVersion,
    RegistryLookupUnavailable,
    RegistryPublishResult,
    RelationshipCheckIssue,
)


def _context(skill_root: Path, *, decision: str = "allow") -> PublishContext:
    context = PublishContext(
        source=SkillSource(
            file_path=str(skill_root),
            slug_override="example-skill",
            intent_override="create_skill",
        ),
        artifacts_dir=str(skill_root / ".publisher_artifacts"),
    )
    context.inventory.skill_root = str(skill_root)
    context.identity.slug = "example-skill"
    context.identity.version = "1.0.0"
    context.identity.intent = "create_skill"
    context.metadata.name = "Example Skill"
    context.security.score = 0.95
    context.security.decision = "pass"
    context.validation.passed = True
    context.performance_exam.score = 0.8
    context.ranking.total_score = 0.9
    context.ranking.label = "recommended"
    context.ranking.publish_decision = decision
    context.delivery_payload.slug = "example-skill"
    context.delivery_payload.version = "1.0.0"
    context.delivery_payload.intent = "create_skill"
    context.delivery_payload.metadata = {"name": "Example Skill"}
    context.delivery_payload.governance = {"trust_tier": "untrusted"}
    return context


class FakePipeline:
    def __init__(self, result: PublishContext) -> None:
        self.result = result
        self.created: dict[str, Any] | None = None
        self.run_count = 0

    def create_context(self, **kwargs: Any) -> PublishContext:
        self.created = kwargs
        return self.result

    def run(self, context: PublishContext) -> PublishContext:
        self.run_count += 1
        return context


def _skill(tmp_path: Path) -> Path:
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\nname: example-skill\nmetadata:\n  version: 1.0.0\n---\n",
        encoding="utf-8",
    )
    return skill_root


def test_inspect_skill_returns_structured_evaluation(tmp_path: Path) -> None:
    from publisher.interfaces.mcp.server import PublisherMcpAdapter

    skill_root = _skill(tmp_path)
    pipeline = FakePipeline(_context(skill_root))
    adapter = PublisherMcpAdapter(pipeline_factory=lambda: pipeline)

    result = adapter.inspect_skill(InspectSkillInput(skill_path=skill_root))

    assert result.ok is True
    assert result.status == "ready"
    assert result.evaluation is not None
    assert result.evaluation.slug == "example-skill"
    assert result.evaluation.validation_passed is True
    assert pipeline.run_count == 1


def test_publish_skill_requires_environment_token_before_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp.server import PublisherMcpAdapter

    skill_root = _skill(tmp_path)
    pipeline = FakePipeline(_context(skill_root))
    for name in (
        "APTITUDE_PUBLISH_TOKEN",
        "APTITUDE_INTEGRATION_PUBLISH_TOKEN",
        "PUBLISH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    result = PublisherMcpAdapter(pipeline_factory=lambda: pipeline).publish_skill(
        PublishSkillInput(
            skill_path=skill_root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
        )
    )

    assert result.status == "error"
    assert "APTITUDE_PUBLISH_TOKEN" in result.message
    assert pipeline.run_count == 0


def test_publish_skill_blocks_duplicate_create_before_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    skill_root = _skill(tmp_path)
    pipeline = FakePipeline(_context(skill_root))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "fake-token")
    monkeypatch.setattr(
        server,
        "get_existing_skill",
        lambda **_: ExistingSkill(
            slug="example-skill",
            versions=(ExistingSkillVersion(version="1.0.0"),),
        ),
    )

    result = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=skill_root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
        )
    )

    assert result.status == "blocked"
    assert "already exists" in result.message
    assert pipeline.run_count == 0


def test_publish_skill_reports_unavailable_duplicate_check_before_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    skill_root = _skill(tmp_path)
    pipeline = FakePipeline(_context(skill_root))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "fake-token")
    monkeypatch.setattr(
        server,
        "get_existing_skill",
        lambda **_: (_ for _ in ()).throw(RegistryLookupUnavailable("offline")),
    )

    result = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=skill_root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
        )
    )

    assert result.status == "error"
    assert "Could not verify" in result.message
    assert pipeline.run_count == 0


def test_publish_skill_does_not_upload_blocked_evaluation(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    skill_root = _skill(tmp_path)
    pipeline = FakePipeline(_context(skill_root, decision="block"))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "fake-token")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: (_ for _ in ()).throw(AssertionError("upload must not run")),
    )

    result = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=skill_root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
        )
    )

    assert result.status == "blocked"
    assert result.evaluation is not None
    assert result.evaluation.publish_decision == "block"


def test_publish_skill_uploads_fresh_bundle_and_returns_warnings(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    skill_root = _skill(tmp_path)
    context = _context(skill_root)
    context.delivery_payload.relationships = {"depends_on": [{"slug": "missing-skill"}]}
    pipeline = FakePipeline(context)
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "fake-token")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "build_bundle_bytes", lambda _: b"bundle")
    monkeypatch.setattr(
        server,
        "check_relationship_references",
        lambda **_: [
            RelationshipCheckIssue(
                kind="missing",
                family="depends_on",
                slug="missing-skill",
                version=None,
                message="No visible versions found for relationship target missing-skill.",
            )
        ],
    )
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: RegistryPublishResult(
            status_code=201,
            body={"message": "published"},
            request_id="request-123",
        ),
    )

    result = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=skill_root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
        )
    )

    assert result.status == "published"
    assert result.registry is not None
    assert result.registry.status_code == 201
    assert result.registry.request_id == "request-123"
    assert result.registry.bundle_size_bytes == 6
    assert result.warnings == [
        "No visible versions found for relationship target missing-skill."
    ]
    assert pipeline.run_count == 1


def test_publish_skill_reports_relationship_verification_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    skill_root = _skill(tmp_path)
    pipeline = FakePipeline(_context(skill_root))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "fake-token")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(
        server,
        "check_relationship_references",
        lambda **_: (_ for _ in ()).throw(ValueError("invalid registry response")),
    )
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: (_ for _ in ()).throw(AssertionError("upload must not run")),
    )

    result = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=skill_root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
        )
    )

    assert result.status == "error"
    assert (
        result.message == "Relationship verification failed: invalid registry response"
    )


def test_publish_skill_reports_bundle_failure_without_upload(
    tmp_path: Path, monkeypatch
) -> None:
    from publisher.interfaces.mcp import server

    skill_root = _skill(tmp_path)
    pipeline = FakePipeline(_context(skill_root))
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "fake-token")
    monkeypatch.setattr(server, "get_existing_skill", lambda **_: None)
    monkeypatch.setattr(server, "check_relationship_references", lambda **_: [])
    monkeypatch.setattr(
        server,
        "build_bundle_bytes",
        lambda _: (_ for _ in ()).throw(RuntimeError("zstd unavailable")),
    )
    monkeypatch.setattr(
        server,
        "publish_to_registry",
        lambda **_: (_ for _ in ()).throw(AssertionError("upload must not run")),
    )

    result = server.PublisherMcpAdapter(
        pipeline_factory=lambda: pipeline
    ).publish_skill(
        PublishSkillInput(
            skill_path=skill_root,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=True,
        )
    )

    assert result.status == "error"
    assert result.message == "Registry upload failed: zstd unavailable"


def test_tool_annotations_and_registration_match_side_effects() -> None:
    from publisher.interfaces.mcp.server import TOOL_ANNOTATIONS, create_server

    assert TOOL_ANNOTATIONS["aptitude_publisher_inspect_skill"].readOnlyHint is False
    assert TOOL_ANNOTATIONS["aptitude_publisher_inspect_skill"].destructiveHint is False
    assert TOOL_ANNOTATIONS["aptitude_publisher_publish_skill"].destructiveHint is True

    async def list_names() -> tuple[list[str], list[str], list[str]]:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mcp = create_server()
        assert caught == []
        return (
            [tool.name for tool in await mcp.list_tools()],
            [str(resource.uri) for resource in await mcp.list_resources()],
            [prompt.name for prompt in await mcp.list_prompts()],
        )

    tools, resources, prompts = asyncio.run(list_names())

    assert tools == [
        "aptitude_publisher_inspect_skill",
        "aptitude_publisher_publish_skill",
    ]
    assert resources == ["aptitude-publisher://manifest"]
    assert prompts == ["aptitude_publisher_review_and_publish"]
