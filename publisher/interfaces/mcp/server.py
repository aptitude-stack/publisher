"""FastMCP adapter for the existing publisher workflow."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
import warnings

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from publisher.artifacts.bundle import build_bundle_bytes
from publisher.app.pipeline import PublisherPipeline
from publisher.domain.models import PublishContext
from publisher.interfaces.mcp.models import (
    EvaluationSummary,
    InspectSkillInput,
    PublisherToolResult,
    PublishSkillInput,
    RegistrySummary,
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

    def inspect_skill(self, params: InspectSkillInput) -> PublisherToolResult:
        """Run the full local evaluation pipeline and return its publish summary."""

        try:
            context = self._run_pipeline(params)
        except (OSError, RuntimeError, ValueError) as exc:
            return _error_result(f"Inspection failed: {exc}")

        evaluation = _evaluation_summary(context)
        ready = _publish_ready(context)
        return PublisherToolResult(
            ok=ready,
            status="ready" if ready else "blocked",
            message=(
                "Skill evaluation is ready for an explicit publish call."
                if ready
                else "Skill evaluation blocked publishing."
            ),
            evaluation=evaluation,
            warnings=evaluation.warnings,
        )

    def publish_skill(self, params: PublishSkillInput) -> PublisherToolResult:
        """Evaluate and publish a skill after explicit MCP write confirmation."""

        publish_token = _first_env_value(_PUBLISH_TOKEN_ENV_NAMES)
        if publish_token is None:
            return _error_result(
                "Missing publish token. Set APTITUDE_PUBLISH_TOKEN, "
                "APTITUDE_INTEGRATION_PUBLISH_TOKEN, or PUBLISH_TOKEN."
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
                return _error_result(
                    f"Could not verify whether slug {params.slug!r} exists: {exc}"
                )
            if existing is not None:
                return PublisherToolResult(
                    ok=False,
                    status="blocked",
                    message=f"Skill slug {params.slug!r} already exists in the registry.",
                )

        try:
            context = self._run_pipeline(params)
        except (OSError, RuntimeError, ValueError) as exc:
            return _error_result(f"Evaluation failed: {exc}")

        evaluation = _evaluation_summary(context)
        if not _publish_ready(context):
            return PublisherToolResult(
                ok=False,
                status="blocked",
                message="Skill evaluation blocked registry upload.",
                evaluation=evaluation,
                warnings=evaluation.warnings,
            )
        if (
            context.identity.slug != params.slug
            or context.identity.intent != params.intent
        ):
            return _error_result(
                "Evaluated identity did not match the explicit publish slug and intent."
            )

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
            return _error_result(f"Relationship verification failed: {exc}")
        try:
            bundle = build_bundle_bytes(context)
            registry_result = publish_to_registry(
                registry_url=registry_url,
                token=publish_token,
                context=context,
                bundle_bytes=bundle,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return _error_result(f"Registry upload failed: {exc}")

        registry = RegistrySummary(
            status_code=registry_result.status_code,
            request_id=registry_result.request_id,
            body=registry_result.body,
            bundle_size_bytes=len(bundle),
        )
        published = 200 <= registry_result.status_code < 300
        return PublisherToolResult(
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
        )

    def _run_pipeline(self, params: InspectSkillInput) -> PublishContext:
        pipeline = self._pipeline_factory()
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
        return pipeline.run(context)


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
                "Inspect a local skill before publishing it. Inspection writes local "
                ".publisher_artifacts; publishing additionally changes registry state "
                "and requires explicit slug, intent, and confirmation."
            ),
        )

    @mcp.tool(
        name="aptitude_publisher_inspect_skill",
        annotations=TOOL_ANNOTATIONS["aptitude_publisher_inspect_skill"],
    )
    def aptitude_publisher_inspect_skill(
        params: InspectSkillInput,
    ) -> PublisherToolResult:
        """Evaluate a local skill and write review artifacts without uploading it."""

        return active_adapter.inspect_skill(params)

    @mcp.tool(
        name="aptitude_publisher_publish_skill",
        annotations=TOOL_ANNOTATIONS["aptitude_publisher_publish_skill"],
    )
    def aptitude_publisher_publish_skill(
        params: PublishSkillInput,
    ) -> PublisherToolResult:
        """Freshly evaluate and upload a skill using environment credentials."""

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
                "inspect_writes": ".publisher_artifacts",
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
            "validation, security, performance, ranking, warnings, slug, version, "
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
        performance_score=context.performance_exam.score,
        ranking_score=context.ranking.total_score,
        ranking_label=context.ranking.label,
        artifacts_dir=context.artifacts_dir,
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


def _error_result(message: str) -> PublisherToolResult:
    return PublisherToolResult(ok=False, status="error", message=message)
