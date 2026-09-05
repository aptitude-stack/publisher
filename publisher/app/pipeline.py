"""Pipeline orchestration for the publisher skeleton."""

from __future__ import annotations

from pathlib import Path

from publisher.gates.discovery import DiscoveryGate
from publisher.gates.identity import IdentityGate
from publisher.gates.metadata import MetadataGate
from publisher.gates.performance_exam import PerformanceExamGate
from publisher.gates.security import SecurityGate
from publisher.gates.validation import ValidationGate
from publisher.domain.models import PublishContext, SkillSource
from publisher.artifacts.report import report_path, write_report
from publisher.stages.delivery import DeliveryStage
from publisher.stages.discovery import DiscoveryStage
from publisher.stages.identity import IdentityStage
from publisher.stages.metadata import MetadataStage
from publisher.stages.performance_exam import PerformanceExamStage
from publisher.stages.ranking import RankingStage
from publisher.stages.security import SecurityStage
from publisher.stages.validation import ValidationStage


class PublisherPipeline:
    """Runs the publisher stages in the expected order."""

    _NON_TERMINAL_FAILED_GATES = {"security", "performance_exam"}

    def __init__(self) -> None:
        self._stages = (
            DiscoveryStage(),
            IdentityStage(),
            MetadataStage(),
            SecurityStage(),
            ValidationStage(),
            PerformanceExamStage(),
            RankingStage(),
            DeliveryStage(),
        )
        self._gates = {
            "discovery": DiscoveryGate(),
            "identity": IdentityGate(),
            "metadata": MetadataGate(),
            "security": SecurityGate(),
            "validation": ValidationGate(),
            "performance_exam": PerformanceExamGate(),
        }

    def create_context(
        self,
        *,
        file_path: str,
        raw_content: str | None = None,
        slug_override: str | None = None,
        version_override: str | None = None,
        intent_override: str | None = None,
        trust_tier: str = "untrusted",
        namespace: str = "public",
        artifact_origin: str = "internal",
        policy_pack_slug: str | None = None,
        publisher_identity: str | None = None,
    ) -> PublishContext:
        """Create the shared context for one publish flow."""
        source_path = Path(file_path)
        return PublishContext(
            source=SkillSource(
                file_path=file_path,
                raw_content=raw_content,
                file_name=source_path.name,
                slug_override=slug_override,
                version_override=version_override,
                intent_override=intent_override,
                trust_tier=trust_tier,
                namespace=namespace,
                artifact_origin=artifact_origin,
                policy_pack_slug=policy_pack_slug,
                publisher_identity=publisher_identity,
            ),
            report_path=str(report_path(source_path)),
        )

    def run(self, context: PublishContext) -> PublishContext:
        """Run the full publisher pipeline with the current placeholder stages."""
        write_report(context, status="running")
        try:
            for stage in self._stages:
                stage.run(context)
                write_report(context, status="running")
                gate = self._gates.get(stage.name)
                if gate:
                    passed = gate.verify(context)
                    write_report(context, status="running")
                    if not passed and stage.name not in self._NON_TERMINAL_FAILED_GATES:
                        break
            status = "ready" if context.ranking.publish_decision in {"allow", "review_required"} else "blocked"
            write_report(context, status=status)
        except BaseException as exc:
            write_report(context, status="failed", error=str(exc))
            raise
        return context
