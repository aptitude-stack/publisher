"""Shared models for the publisher pipeline skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SkillSource:
    """Raw skill input that enters the publisher pipeline."""

    file_path: str
    raw_content: str | None = None
    file_name: str | None = None
    parsed_content: dict[str, Any] = field(default_factory=dict)
    slug_override: str | None = None
    version_override: str | None = None
    intent_override: str | None = None
    trust_tier: str = "untrusted"
    namespace: str = "public"
    artifact_origin: str = "internal"
    policy_pack_slug: str | None = None
    publisher_identity: str | None = None


@dataclass(slots=True)
class SkillInventory:
    """Discovered file inventory for one skill folder."""

    skill_root: str | None = None
    skill_markdown_path: str | None = None
    scripts_dir: str | None = None
    references_dir: str | None = None
    assets_dir: str | None = None
    repo_root: str | None = None
    repo_url: str | None = None
    commit_sha: str | None = None
    tree_path: str | None = None
    companion_markdown_files: list[str] = field(default_factory=list)
    script_files: list[str] = field(default_factory=list)
    reference_files: list[str] = field(default_factory=list)
    asset_files: list[str] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)
    artifact_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IdentityInfo:
    """Phase 1 output: slug, version, and intent."""

    slug: str | None = None
    version: str | None = None
    intent: str | None = None
    artifact_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MetadataInfo:
    """Phase 2 output: metadata prepared for publish."""

    name: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    inputs_schema: dict[str, Any] | None = None
    outputs_schema: dict[str, Any] | None = None
    token_estimate: int | None = None
    word_count: int | None = None
    maturity_score: float | None = None
    security_score: float | None = None
    artifact_path: str | None = None
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RankingInfo:
    """Phase 3 output: internal scoring and ranking signals."""

    total_score: float | None = None
    criteria_scores: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    label: str | None = None
    publish_decision: str | None = None
    artifact_path: str | None = None
    explanation: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SecurityInfo:
    """Phase 4 output: security scan results."""

    score: float | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    scan_targets: list[str] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    severity_counts: dict[str, int] = field(default_factory=dict)
    decision: str | None = None
    artifact_path: str | None = None
    notes: list[str] = field(default_factory=list)
    scanned: bool = False


@dataclass(slots=True)
class ValidationInfo:
    """Phase 5 output: verification and validation results."""

    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    artifact_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PerformanceExamInfo:
    """Phase 6 output: measured skill-performance evidence."""

    score: float | None = None
    passed: bool = False
    test_case_count: int = 0
    models_tested: list[str] = field(default_factory=list)
    baseline_success_rate: float | None = None
    skilled_success_rate: float | None = None
    skill_lift: float | None = None
    baseline_avg_tokens: int | None = None
    skilled_avg_tokens: int | None = None
    token_delta: int | None = None
    efficiency_label: str | None = None
    artifact_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DeliveryPayload:
    """Phase 6 output: final payload for the client endpoint."""

    slug: str | None = None
    version: str | None = None
    intent: str | None = None
    content: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    governance: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompressionInfo:
    """Phase 7 output: compressed delivery package details."""

    algorithm: str | None = None
    compressed_artifact_path: str | None = None
    manifest_artifact_path: str | None = None
    available: bool = False
    uncompressed_size: int | None = None
    compressed_size: int | None = None
    compression_ratio: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StageSnapshot:
    """Stores per-stage results for traceability."""

    stage_name: str
    status: str = "pending"
    data: dict[str, Any] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GateResult:
    """Stores the result of a gate that verifies stage readiness."""

    gate_name: str
    passed: bool
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PublishContext:
    """Mutable pipeline state shared across publisher stages."""

    source: SkillSource
    artifacts_dir: str | None = None
    inventory: SkillInventory = field(default_factory=SkillInventory)
    identity: IdentityInfo = field(default_factory=IdentityInfo)
    metadata: MetadataInfo = field(default_factory=MetadataInfo)
    ranking: RankingInfo = field(default_factory=RankingInfo)
    security: SecurityInfo = field(default_factory=SecurityInfo)
    validation: ValidationInfo = field(default_factory=ValidationInfo)
    performance_exam: PerformanceExamInfo = field(default_factory=PerformanceExamInfo)
    delivery_payload: DeliveryPayload = field(default_factory=DeliveryPayload)
    compression: CompressionInfo = field(default_factory=CompressionInfo)
    stage_history: list[StageSnapshot] = field(default_factory=list)
    gate_history: list[GateResult] = field(default_factory=list)

    def add_snapshot(
        self,
        *,
        stage_name: str,
        status: str,
        data: dict[str, Any] | None = None,
        messages: list[str] | None = None,
    ) -> None:
        """Record one stage result in a simple trace log."""
        self.stage_history.append(
            StageSnapshot(
                stage_name=stage_name,
                status=status,
                data=data or {},
                messages=messages or [],
            )
        )

    def add_gate_result(
        self,
        *,
        gate_name: str,
        passed: bool,
        blocking_issues: list[str] | None = None,
        warnings: list[str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record the outcome of one gate evaluation."""
        self.gate_history.append(
            GateResult(
                gate_name=gate_name,
                passed=passed,
                blocking_issues=blocking_issues or [],
                warnings=warnings or [],
                data=data or {},
            )
        )
