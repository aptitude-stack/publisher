"""Validated input and output models for publisher MCP tools."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


Intent = Literal["create_skill", "publish_version"]
TrustTier = Literal["untrusted", "internal", "verified"]
ArtifactOrigin = Literal["internal", "imported", "verified", "restricted"]


class _StrictInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class InspectSkillInput(_StrictInput):
    """Input for evaluating one local skill."""

    skill_path: Path
    slug: str | None = None
    version: str | None = None
    intent: Intent | None = None
    trust_tier: TrustTier = "untrusted"
    namespace: str = Field(default="public", min_length=1)
    artifact_origin: ArtifactOrigin = "internal"
    policy_pack_slug: str | None = None
    publisher_identity: str | None = None

    @field_validator("skill_path")
    @classmethod
    def validate_skill_path(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        skill_file = path / "SKILL.md" if path.is_dir() else path
        if (
            not path.exists()
            or not skill_file.is_file()
            or skill_file.name != "SKILL.md"
        ):
            raise ValueError("skill_path must be a skill directory or SKILL.md file")
        return path


class PublishSkillInput(InspectSkillInput):
    """Input for evaluating and publishing one local skill."""

    slug: str = Field(min_length=1)
    intent: Intent
    registry_url: str | None = None
    confirm_upload: Literal[True]

    @field_validator("registry_url")
    @classmethod
    def validate_registry_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("registry_url must use http or https")
        return value.rstrip("/")


class EvaluationSummary(BaseModel):
    """Concise pipeline result returned to MCP callers."""

    skill_path: str
    slug: str | None
    version: str | None
    intent: str | None
    publish_decision: str | None
    validation_passed: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    security_score: float | None
    security_decision: str | None
    performance_score: float | None
    ranking_score: float | None
    ranking_label: str | None
    artifacts_dir: str | None


class RegistrySummary(BaseModel):
    """Registry response details safe to return to an MCP client."""

    status_code: int
    request_id: str | None = None
    body: dict[str, object] = Field(default_factory=dict)
    bundle_size_bytes: int


class PublisherToolResult(BaseModel):
    """Shared structured result for publisher MCP tools."""

    ok: bool
    status: Literal["ready", "blocked", "published", "error"]
    message: str
    evaluation: EvaluationSummary | None = None
    registry: RegistrySummary | None = None
    warnings: list[str] = Field(default_factory=list)
