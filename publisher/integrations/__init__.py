"""External service integrations used by the publisher."""

from publisher.integrations.github_api import fetch_repository_signals

__all__ = ["fetch_repository_signals"]
