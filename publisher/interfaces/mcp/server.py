"""FastMCP adapter for the existing publisher workflow."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import os
from pathlib import Path
import warnings
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from publisher.artifacts.bundle import build_bundle_bytes
from publisher.artifacts.report import report_path
from publisher.app.pipeline import PublisherPipeline
from publisher.domain.models import (
    GateResult,
    PublishContext,
    SkillSource,
)
from publisher.interfaces.mcp.formatting import format_response
from publisher.interfaces.mcp.models import (
    EvaluationSummary,
    InspectSkillInput,
    PublisherToolResult,
    PublishSkillInput,
    ResponseFormat,
    RegistrySummary,
)
from publisher.interfaces.mcp.receipt import (
    config_fingerprint,
    load_inspection_receipt,
    receipt_matches,
    write_inspection_receipt,
)
from publisher.registry.client import (
    RegistryLookupUnavailable,
    check_relationship_references,
    get_existing_skill,
    publish_to_registry,
)


_DEFAULT_REGISTRY_URL = "https://api.aptitude-registry.dev"
_PUBLISH_TOKEN_ENV_NAMES = (
    "APTITUDE_PUBLISH_TOKEN",
    "APTITUDE_INTEGRATION_PUBLISH_TOKEN",
    "PUBLISH_TOKEN",
)
_READ_TOKEN_ENV_NAMES = (
    "APTITUDE_READ_TOKEN",
    "APTITUDE_REGISTRY_READ_TOKEN",
    "REGISTRY_READ_TOKEN",
)

TOOL_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "aptitude_publisher_inspect_skill": ToolAnnotations(
        title="Inspect Aptitude Skill for Publishing",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    "aptitude_publisher_publish_skill": ToolAnnotations(
        title="Publish Aptitude Skill",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
}


PipelineFactory = Callable[[], PublisherPipeline]


class PublisherMcpAdapter:
    """Translate validated MCP calls into the publisher's existing components."""

    def __init__(
        self, *, pipeline_factory: PipelineFactory = PublisherPipeline
    ) -> None:
        self._pipeline_factory = pipeline_factory

    def inspect_skill(self, params: InspectSkillInput) -> str:
        """Run the full local evaluation pipeline and return its publish summary."""

        try:
            context = self._run_pipeline(params)
            bundle = build_bundle_bytes(context)
            receipt = write_inspection_receipt(
                context,
                bundle_bytes=bundle,
                publish_token=_first_env_value(_PUBLISH_TOKEN_ENV_NAMES),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return _render_error(f"Inspection failed: {exc}", params.response_format)

        evaluation = _evaluation_summary(context)
        ready = _publish_ready(context)
        return _render(
            PublisherToolResult(
                ok=ready,
                status="ready" if ready else "blocked",
                message=(
                    "Skill evaluation is ready for an explicit publish call."
                    if ready
                    else "Skill evaluation blocked publishing."
                ),
                evaluation=evaluation,
                warnings=evaluation.warnings,
                evidence_refreshed=True,
                receipt_created_at=receipt["created_at"],
                receipt_expires_at=receipt["expires_at"],
            ),
            params.response_format,
        )

    def publish_skill(self, params: PublishSkillInput) -> str:
        """Reuse a fresh local inspection or inspect before explicit upload."""

        publish_token = _first_env_value(_PUBLISH_TOKEN_ENV_NAMES)
        if publish_token is None:
            return _render_error(
                "Missing publish token. Set APTITUDE_PUBLISH_TOKEN, "
                "APTITUDE_INTEGRATION_PUBLISH_TOKEN, or PUBLISH_TOKEN.",
                params.response_format,
            )

        registry_url = params.registry_url or _default_registry_url()
        lookup_token = _first_env_value(_READ_TOKEN_ENV_NAMES) or publish_token
        if params.intent == "create_skill":
            try:
                existing = get_existing_skill(
                    registry_url=registry_url,
                    token=lookup_token,
                    slug=params.slug,
                )
            except RegistryLookupUnavailable as exc:
                return _render_error(
                    f"Could not verify whether slug {params.slug!r} exists: {exc}",
                    params.response_format,
                )
            if existing is not None:
                return _render(
                    PublisherToolResult(
                        ok=False,
                        status="blocked",
                        message=f"Skill slug {params.slug!r} already exists in the registry.",
                    ),
                    params.response_format,
                )

        pipeline = self._pipeline_factory()
        receipt_path = _receipt_path(params.skill_path)
        receipt = load_inspection_receipt(
            receipt_path, publish_token=publish_token
        )
        candidate_context: PublishContext | None = None
        if receipt is not None:
            try:
                candidate_context = _context_from_receipt(receipt, params)
            except (AttributeError, KeyError, TypeError, ValueError):
                receipt = None
        if candidate_context is None:
            candidate_context = self._create_context(pipeline, params)
        try:
            bundle = build_bundle_bytes(candidate_context)
        except (OSError, RuntimeError, ValueError) as exc:
            return _render_error(f"Evaluation failed: {exc}", params.response_format)

        source_bundle_sha256 = sha256(bundle).hexdigest()
        expected_identity = _receipt_identity(receipt, params)
        expected_governance = _governance_inputs(params)
        reused = bool(
            receipt is not None
            and receipt_matches(
                receipt,
                identity=expected_identity,
                governance=expected_governance,
                source_bundle_sha256=source_bundle_sha256,
                config=config_fingerprint(),
            )
        )
        if reused:
            context = candidate_context
        else:
            context = self._create_context(pipeline, params)
            try:
                context = pipeline.run(context)
                receipt = write_inspection_receipt(
                    context,
                    bundle_bytes=bundle,
                    publish_token=publish_token,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                return _render_error(f"Evaluation failed: {exc}", params.response_format)

        if (
            receipt is None
            or receipt.get("source_bundle_sha256") != source_bundle_sha256
        ):
            return _render_error(
                "Inspection receipt source bundle digest did not match the delivery "
                "bundle.",
                params.response_format,
            )

        evaluation = _evaluation_summary(context)
        try:
            relationship_warnings = [
                issue.message
                for issue in check_relationship_references(
                    registry_url=registry_url,
                    token=lookup_token,
                    relationships=context.delivery_payload.relationships,
                )
            ]
        except (OSError, RuntimeError, ValueError) as exc:
            return _render_error(
                f"Relationship verification failed: {exc}", params.response_format
            )
        receipt_kwargs = _receipt_result_kwargs(receipt, reused=reused)
        if not _publish_ready(context):
            return _render(
                PublisherToolResult(
                    ok=False,
                    status="blocked",
                    message="Skill evaluation blocked registry upload.",
                    evaluation=evaluation,
                    warnings=[*evaluation.warnings, *relationship_warnings],
                    **receipt_kwargs,
                ),
                params.response_format,
            )
        if (
            context.identity.slug != params.slug
            or (
                params.version is not None
                and context.identity.version != params.version
            )
            or context.identity.intent != params.intent
        ):
            return _render_error(
                "Evaluated identity did not match the explicit publish slug and intent.",
                params.response_format,
            )

        try:
            registry_result = publish_to_registry(
                registry_url=registry_url,
                token=publish_token,
                context=context,
                bundle_bytes=bundle,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return _render_error(f"Registry upload failed: {exc}", params.response_format)

        authoritative = _authoritative_scores(registry_result.body)
        evaluation = evaluation.model_copy(update=authoritative)
        registry = RegistrySummary(
            status_code=registry_result.status_code,
            request_id=registry_result.request_id,
            body=registry_result.body,
            bundle_size_bytes=len(bundle),
        )
        published = 200 <= registry_result.status_code < 300
        return _render(
            PublisherToolResult(
                ok=published,
                status="published" if published else "error",
                message=(
                    "Skill published successfully."
                    if published
                    else f"Registry returned HTTP {registry_result.status_code}."
                ),
                evaluation=evaluation,
                registry=registry,
                warnings=[*evaluation.warnings, *relationship_warnings],
                **receipt_kwargs,
            ),
            params.response_format,
        )

    def _create_context(
        self, pipeline: PublisherPipeline, params: InspectSkillInput
    ) -> PublishContext:
        context = pipeline.create_context(
            file_path=str(params.skill_path),
            slug_override=params.slug,
            version_override=params.version,
            intent_override=params.intent,
            trust_tier=params.trust_tier,
            namespace=params.namespace,
            artifact_origin=params.artifact_origin,
            policy_pack_slug=params.policy_pack_slug,
            publisher_identity=params.publisher_identity,
        )
        root = (
            params.skill_path
            if params.skill_path.is_dir()
            else params.skill_path.parent
        )
        context.inventory.skill_root = str(root)
        return context

    def _run_pipeline(self, params: InspectSkillInput) -> PublishContext:
        pipeline = self._pipeline_factory()
        return pipeline.run(self._create_context(pipeline, params))


def create_server(adapter: PublisherMcpAdapter | None = None) -> FastMCP:
    """Create the local Aptitude Publisher MCP server."""

    active_adapter = adapter or PublisherMcpAdapter()
    with warnings.catch_warnings():
        # FastMCP 1.27-1.29 leaves its generic lifespan annotation unresolved.
        warnings.filterwarnings(
            "ignore",
            message=r"Field 'lifespan' has an incomplete definition:.*",
        )
        mcp = FastMCP(
            "aptitude_publisher_mcp",
            instructions=(
                "Inspect a local skill before publishing it. Inspection writes a report to "
                "the local Publisher cache; publishing additionally changes registry state "
                "and requires explicit slug, intent, and confirmation."
            ),
        )

    @mcp.tool(
        name="aptitude_publisher_inspect_skill",
        annotations=TOOL_ANNOTATIONS["aptitude_publisher_inspect_skill"],
    )
    def aptitude_publisher_inspect_skill(
        params: InspectSkillInput,
    ) -> str:
        """Evaluate a local skill and write review artifacts without uploading it."""

        return active_adapter.inspect_skill(params)

    @mcp.tool(
        name="aptitude_publisher_publish_skill",
        annotations=TOOL_ANNOTATIONS["aptitude_publisher_publish_skill"],
    )
    def aptitude_publisher_publish_skill(
        params: PublishSkillInput,
    ) -> str:
        """Reuse fresh inspection evidence and upload using environment credentials."""

        return active_adapter.publish_skill(params)

    @mcp.resource("aptitude-publisher://manifest")
    def aptitude_publisher_manifest() -> str:
        """Return the publisher MCP capability and safety manifest."""

        return json.dumps(
            {
                "server": "aptitude_publisher_mcp",
                "transport": "stdio",
                "tools": [
                    "aptitude_publisher_inspect_skill",
                    "aptitude_publisher_publish_skill",
                ],
                "inspect_writes": "local Publisher cache report",
                "publish_requires": ["slug", "intent", "confirm_upload=true"],
            },
            indent=2,
            sort_keys=True,
        )

    @mcp.prompt("aptitude_publisher_review_and_publish")
    def aptitude_publisher_review_and_publish(skill_path: str) -> str:
        """Guide an agent through review before an explicit registry upload."""

        return (
            f"Inspect `{skill_path}` with `aptitude_publisher_inspect_skill`. Review "
            "validation, security, performance, overall scores, warnings, slug, version, "
            "and intent. Ask the user to confirm the registry mutation, then call "
            "`aptitude_publisher_publish_skill` with the explicit slug, intent, and "
            "`confirm_upload=true`."
        )

    return mcp


def _evaluation_summary(context: PublishContext) -> EvaluationSummary:
    failed_gates = [gate for gate in context.gate_history if not gate.passed]
    blocking_issues = [
        issue
        for gate in failed_gates
        for issue in (
            gate.blocking_issues or [gate.explanation or f"{gate.gate_name} failed"]
        )
    ]
    warnings = [
        *context.validation.warnings,
        *(warning for gate in context.gate_history for warning in gate.warnings),
    ]
    return EvaluationSummary(
        skill_path=context.inventory.skill_root or context.source.file_path,
        slug=context.identity.slug,
        version=context.identity.version,
        intent=context.identity.intent,
        publish_decision=context.ranking.publish_decision,
        validation_passed=context.validation.passed,
        blocking_issues=blocking_issues,
        warnings=warnings,
        security_score=context.security.score,
        security_decision=context.security.decision,
        performance_evidence_score=context.performance_exam.score,
        maturity_score=context.metadata.maturity_score,
        overall_score=context.ranking.total_score,
        overall_label=context.ranking.label,
        report_path=context.report_path,
    )


def _publish_ready(context: PublishContext) -> bool:
    payload = context.delivery_payload
    return bool(
        context.ranking.publish_decision != "block"
        and payload.slug
        and payload.version
        and payload.intent
        and payload.metadata.get("name")
        and payload.governance
    )


def _first_env_value(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _default_registry_url() -> str:
    configured = os.environ.get("APTITUDE_REGISTRY_URL") or os.environ.get(
        "APTITUDE_SERVER_BASE_URL"
    )
    if configured:
        return configured
    app_port = os.environ.get("APP_PORT")
    return f"http://127.0.0.1:{app_port}" if app_port else _DEFAULT_REGISTRY_URL


def _render(result: PublisherToolResult, response_format: ResponseFormat) -> str:
    return format_response(result, response_format)


def _render_error(message: str, response_format: ResponseFormat) -> str:
    return _render(_error_result(message), response_format)


def _receipt_path(skill_path: Path) -> Path:
    return report_path(skill_path)


def _receipt_identity(
    receipt: dict[str, Any] | None, params: InspectSkillInput
) -> dict[str, Any]:
    receipt_identity = receipt.get("identity") if isinstance(receipt, dict) else None
    receipt_version = (
        receipt_identity.get("version")
        if isinstance(receipt_identity, dict)
        else None
    )
    return {
        "slug": params.slug,
        "version": params.version or receipt_version,
        "intent": params.intent,
    }


def _governance_inputs(params: InspectSkillInput) -> dict[str, Any]:
    return {
        "trust_tier": params.trust_tier,
        "namespace": params.namespace,
        "artifact_origin": params.artifact_origin,
        "policy_pack_slug": params.policy_pack_slug,
        "publisher_identity": params.publisher_identity,
    }


def _receipt_result_kwargs(
    receipt: dict[str, object] | None, *, reused: bool
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        return {"evidence_reused": reused, "evidence_refreshed": not reused}
    return {
        "evidence_reused": reused,
        "evidence_refreshed": not reused,
        "receipt_created_at": receipt.get("created_at"),
        "receipt_expires_at": receipt.get("expires_at"),
    }


def _context_from_receipt(
    receipt: dict[str, Any] | None, params: InspectSkillInput
) -> PublishContext:
    if receipt is None:
        raise ValueError("receipt is required")
    governance = receipt.get("governance", {})
    governance = governance if isinstance(governance, dict) else {}
    root = params.skill_path if params.skill_path.is_dir() else params.skill_path.parent
    context = PublishContext(
        source=SkillSource(
            file_path=str(root),
            slug_override=params.slug,
            version_override=params.version,
            intent_override=params.intent,
            trust_tier=str(governance.get("trust_tier", params.trust_tier)),
            namespace=str(governance.get("namespace", params.namespace)),
            artifact_origin=str(
                governance.get("artifact_origin", params.artifact_origin)
            ),
            policy_pack_slug=governance.get("policy_pack_slug"),
            publisher_identity=governance.get("publisher_identity"),
        ),
        report_path=str(report_path(root)),
    )
    context.inventory.skill_root = str(root)
    identity = receipt.get("identity", {})
    if isinstance(identity, dict):
        context.identity.slug = identity.get("slug")
        context.identity.version = identity.get("version")
        context.identity.intent = identity.get("intent")

    evidence = receipt.get("evidence", {})
    evidence = evidence if isinstance(evidence, dict) else {}
    _update_object(context.security, evidence.get("security"))
    _update_object(context.validation, evidence.get("validation"))
    _update_object(context.performance_exam, evidence.get("performance"))
    upskill = evidence.get("upskill")
    if isinstance(upskill, dict):
        context.metadata.extra["upskill_evaluation"] = upskill
    ranking = evidence.get("ranking")
    if isinstance(ranking, dict):
        _update_object(context.ranking, ranking)
    scores = receipt.get("scores", {})
    if isinstance(scores, dict):
        context.metadata.maturity_score = scores.get("maturity_score")
        context.metadata.security_score = scores.get("security_score")
        context.performance_exam.score = scores.get("performance_evidence_score")
        context.ranking.total_score = scores.get("overall_score")

    payload = receipt.get("final_payload", {})
    if isinstance(payload, dict):
        context.delivery_payload.slug = payload.get("slug")
        context.delivery_payload.version = payload.get("version")
        context.delivery_payload.intent = payload.get("intent")
        for key in ("content", "metadata", "governance", "relationships"):
            value = payload.get(key)
            if isinstance(value, dict):
                setattr(context.delivery_payload, key, value)

    gates = receipt.get("gates", [])
    if isinstance(gates, list):
        context.gate_history = [
            GateResult(
                gate_name=str(item.get("gate_name", "receipt")),
                passed=bool(item.get("passed", False)),
                explanation=item.get("explanation"),
                blocking_issues=list(item.get("blocking_issues", [])),
                warnings=list(item.get("warnings", [])),
                data=dict(item.get("data", {})),
            )
            for item in gates
            if isinstance(item, dict)
        ]
    return context


def _update_object(target: object, values: Any) -> None:
    if not isinstance(values, dict):
        return
    for key, value in values.items():
        if isinstance(key, str) and hasattr(target, key):
            setattr(target, key, value)


def _authoritative_scores(body: dict[str, Any]) -> dict[str, float | None]:
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    found: dict[str, float | None] = {}
    for key in ("maturity_score", "security_score", "overall_score"):
        if key not in metadata:
            continue
        score = metadata.get(key)
        if score is None:
            found[key] = None
            continue
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            found[key] = max(0.0, min(1.0, float(score)))
    return found


def _error_result(message: str) -> PublisherToolResult:
    return PublisherToolResult(ok=False, status="error", message=message)
