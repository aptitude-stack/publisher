"""Pipeline orchestration for the publisher skeleton."""

from __future__ import annotations

from pathlib import Path

from publisher.gates.discovery import DiscoveryGate
from publisher.gates.identity import IdentityGate
from publisher.gates.metadata import MetadataGate
from publisher.gates.security import SecurityGate
from publisher.gates.validation import ValidationGate
from publisher.models import PublishContext, SkillSource
from publisher.stages.compression import CompressionStage
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
            CompressionStage(),
        )
        self._gates = {
            "discovery": DiscoveryGate(),
            "identity": IdentityGate(),
            "metadata": MetadataGate(),
            "security": SecurityGate(),
            "validation": ValidationGate(),
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
        artifact_root = source_path if source_path.is_dir() else source_path.parent
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
            artifacts_dir=str(artifact_root / ".publisher_artifacts"),
        )

    def run(self, context: PublishContext) -> PublishContext:
        """Run the full publisher pipeline with the current placeholder stages."""
        for stage in self._stages:
            stage.run(context)
            gate = self._gates.get(stage.name)
            if gate and not gate.verify(context):
                break
        return context
