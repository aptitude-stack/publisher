"""Base gate type for publisher pipeline verification."""

from __future__ import annotations

from abc import ABC, abstractmethod

from publisher.domain.models import PublishContext


class PublisherGate(ABC):
    """Abstract base class for gates that verify stage output."""

    name: str
    stage_name: str

    @abstractmethod
    def verify(self, context: PublishContext) -> bool:
        """Return True when the pipeline may continue past this gate."""


def explain_gate_result(
    *,
    passed: bool,
    passed_message: str,
    blocking_issues: list[str],
    warnings: list[str],
) -> str:
    """Return a concise human-readable explanation for one gate result."""
    if blocking_issues:
        return "Failed because " + "; ".join(blocking_issues)
    if warnings:
        return passed_message + " Warnings: " + "; ".join(warnings)
    if passed:
        return passed_message
    return "Failed without a recorded blocking issue."
