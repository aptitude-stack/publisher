"""Base stage type for the publisher pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod

from publisher.models import PublishContext


class PublisherStage(ABC):
    """Abstract base class for one publisher pipeline stage."""

    name: str

    @abstractmethod
    def run(self, context: PublishContext) -> None:
        """Mutate the shared context for this stage."""

    def _mark_pending(self, context: PublishContext, *messages: str) -> None:
        context.add_snapshot(
            stage_name=self.name,
            status="pending",
            messages=list(messages),
        )
