"""Base gate type for publisher pipeline verification."""

from __future__ import annotations

from abc import ABC, abstractmethod

from publisher.models import PublishContext


class PublisherGate(ABC):
    """Abstract base class for gates that verify stage output."""

    name: str
    stage_name: str

    @abstractmethod
    def verify(self, context: PublishContext) -> bool:
        """Return True when the pipeline may continue past this gate."""
