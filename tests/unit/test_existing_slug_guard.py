from __future__ import annotations

import json

from publisher.app.cli import (
    _existing_skill_lines,
    _should_block_existing_slug,
)
from publisher.registry.client import ExistingSkill, ExistingSkillVersion, get_existing_skill


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_get_existing_skill_returns_visible_versions(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(http_request, timeout):
        captured["url"] = http_request.full_url
        captured["auth"] = http_request.headers["Authorization"]
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "slug": "python-patterns",
                "versions": [
                    {
                        "version": "1.0.0",
                        "lifecycle_status": "published",
                        "review_state": "approved",
                        "promotion_channel": "prod",
                        "is_current_default": True,
                    }
                ],
            }
        )

    monkeypatch.setattr("publisher.registry.client.request.urlopen", fake_urlopen)

    existing = get_existing_skill(
        registry_url="https://api.example.test/",
        token="read-token",
        slug="python-patterns",
    )

    assert existing == ExistingSkill(
        slug="python-patterns",
        versions=(
            ExistingSkillVersion(
                version="1.0.0",
                lifecycle_status="published",
                review_state="approved",
                promotion_channel="prod",
                is_current_default=True,
            ),
        ),
    )
    assert captured == {
        "url": "https://api.example.test/skills/python-patterns",
        "auth": "Bearer read-token",
        "timeout": 10,
    }


def test_existing_slug_blocks_create_skill_but_not_publish_version() -> None:
    existing = ExistingSkill(
        slug="python-patterns",
        versions=(ExistingSkillVersion(version="1.0.0"),),
    )

    assert _should_block_existing_slug(intent="create_skill", existing_skill=existing) is True
    assert _should_block_existing_slug(intent="publish_version", existing_skill=existing) is False
    assert _should_block_existing_slug(intent="create_skill", existing_skill=None) is False


def test_existing_skill_lines_show_reuse_guidance() -> None:
    existing = ExistingSkill(
        slug="python-patterns",
        versions=(
            ExistingSkillVersion(
                version="1.0.0",
                lifecycle_status="published",
                review_state="approved",
                promotion_channel="prod",
                is_current_default=True,
            ),
        ),
    )

    assert _existing_skill_lines(existing) == [
        ("slug", "python-patterns"),
        ("version", "1.0.0 current default, published, approved, prod"),
        ("reuse", "Use publish_version for a new version, or depend on the existing skill."),
    ]
