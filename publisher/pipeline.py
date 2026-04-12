"""Pipeline orchestration for the publisher skeleton."""

from __future__ import annotations

from pathlib import Path

from publisher.models import PublishContext, SkillSource
from publisher.stages.delivery import DeliveryStage
from publisher.stages.discovery import DiscoveryStage
from publisher.stages.identity import IdentityStage
from publisher.stages.metadata import MetadataStage
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
            RankingStage(),
            DeliveryStage(),
        )

    def create_context(
        self,
        *,
        file_path: str,
        raw_content: str | None = None,
    ) -> PublishContext:
        """Create the shared context for one publish flow."""
        source_path = Path(file_path)
        artifact_root = source_path if source_path.is_dir() else source_path.parent
        return PublishContext(
            source=SkillSource(
                file_path=file_path,
                raw_content=raw_content,
                file_name=source_path.name,
            ),
            artifacts_dir=str(artifact_root / ".publisher_artifacts"),
        )

    def run(self, context: PublishContext) -> PublishContext:
        """Run the full publisher pipeline with the current placeholder stages."""
        for stage in self._stages:
            stage.run(context)
        return context
