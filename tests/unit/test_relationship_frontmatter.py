from __future__ import annotations

import textwrap

import pytest

from publisher.app.pipeline import PublisherPipeline
from publisher.app.cli import _relationship_alert_lines
from publisher.manifest import load_manifest
from publisher.registry.client import (
    RelationshipCheckIssue,
    build_publish_metadata,
    check_relationship_references,
)
from publisher.relationships import normalize_relationships
from publisher.stages.delivery import DeliveryStage
from publisher.stages.discovery import DiscoveryStage
from publisher.stages.validation import ValidationStage


class _FakeResponse:
    def __init__(self, payload: bytes = b"{}") -> None:
        self.status = 200
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _skill_markdown(frontmatter_extra: str = "") -> str:
    return f"""---
name: relationship-skill
description: "Generates relationship examples; use when the user asks to publish a related skill."
{frontmatter_extra}---

# Instructions

Publish the skill with relationship metadata.

# Example

Input: a skill folder.
Output: registry metadata.

# Troubleshooting

If relationship data is invalid, fix aptitude.yaml.
"""


def _context_for_skill(tmp_path, manifest_extra: str = ""):
    skill_root = tmp_path / "relationship-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        _skill_markdown(),
        encoding="utf-8",
    )
    manifest = textwrap.dedent(manifest_extra).strip()
    (skill_root / "aptitude.yaml").write_text(
        """version: 1.0.0
intent: create_skill
tags: [relationships, registry]
inputs_schema: {type: object}
outputs_schema: {type: object}
"""
        + (f"{manifest}\n" if manifest else ""),
        encoding="utf-8",
    )
    return PublisherPipeline().create_context(file_path=str(skill_root))


def _write_related_skill(tmp_path, name: str) -> None:
    skill_root = tmp_path / name
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        f"""---
name: {name}
description: "Helps validate relationship targets; use when another skill references it."
---

# Instructions

Use this skill as a local relationship target.
""",
        encoding="utf-8",
    )


def test_load_manifest_accepts_nested_relationship_yaml(tmp_path) -> None:
    skill_root = tmp_path / "relationship-skill"
    skill_root.mkdir()
    (skill_root / "aptitude.yaml").write_text(
        """relationships:
  depends_on:
    - slug: python-base
      version_constraint: ">=1.0.0,<2.0.0"
      optional: false
      markers: ["linux"]
  extends:
    - slug: python-base
      version: 1.0.0
  conflicts_with: []
  overlaps_with:
    - slug: python-format
      version: 2.0.0
""",
        encoding="utf-8",
    )
    manifest = load_manifest(skill_root)

    relationships = manifest["relationships"]
    assert relationships["depends_on"][0]["slug"] == "python-base"
    assert (
        relationships["depends_on"][0]["version_constraint"]
        == ">=1.0.0,<2.0.0"
    )
    assert relationships["depends_on"][0]["optional"] is False
    assert relationships["depends_on"][0]["markers"] == ["linux"]
    assert relationships["extends"][0]["version"] == "1.0.0"
    assert relationships["overlaps_with"][0]["version"] == "2.0.0"


def test_normalize_relationships_defaults_to_empty_registry_families() -> None:
    assert normalize_relationships({}) == {
        "depends_on": [],
        "extends": [],
        "conflicts_with": [],
        "overlaps_with": [],
    }


def test_normalize_relationships_rejects_dotted_slug() -> None:
    with pytest.raises(ValueError, match="not a valid registry slug"):
        normalize_relationships(
            {"depends_on": [{"slug": "python.base", "version": "1.0.0"}]}
        )


def test_delivery_payload_includes_authored_manifest_relationships(tmp_path) -> None:
    context = _context_for_skill(
        tmp_path,
        """relationships:
  depends_on:
    - slug: python-base
      version_constraint: ">=1.0.0,<2.0.0"
      optional: true
      markers: ["ci", "linux"]
  extends:
    - slug: python-base
      version: 1.0.0
  conflicts_with:
    - slug: python-legacy
      version: 0.9.0
  overlaps_with:
    - slug: python-format
      version: 2.0.0
""",
    )
    DiscoveryStage().run(context)
    context.identity.slug = "relationship-skill"
    context.identity.version = "1.0.0"
    context.identity.intent = "create_skill"
    context.metadata.name = "relationship-skill"
    context.metadata.description = "Relationship skill"
    context.metadata.tags = ["relationships", "registry"]

    DeliveryStage().run(context)

    metadata = build_publish_metadata(context)
    assert metadata["relationships"] == {
        "depends_on": [
            {
                "slug": "python-base",
                "version_constraint": ">=1.0.0,<2.0.0",
                "optional": True,
                "markers": ["ci", "linux"],
            }
        ],
        "extends": [{"slug": "python-base", "version": "1.0.0"}],
        "conflicts_with": [{"slug": "python-legacy", "version": "0.9.0"}],
        "overlaps_with": [{"slug": "python-format", "version": "2.0.0"}],
    }


def test_delivery_payload_defaults_omitted_manifest_relationships(tmp_path) -> None:
    context = _context_for_skill(
        tmp_path,
        """  relationships:
    depends_on:
      - slug: python-base
        version_constraint: ">=1.0.0,<2.0.0"
        optional: true
""",
    )
    DiscoveryStage().run(context)
    context.identity.slug = "relationship-skill"
    context.identity.version = "1.0.0"
    context.identity.intent = "create_skill"
    context.metadata.name = "relationship-skill"
    context.metadata.description = "Relationship skill"
    context.metadata.tags = ["relationships", "registry"]

    DeliveryStage().run(context)

    metadata = build_publish_metadata(context)
    assert metadata["relationships"]["depends_on"] == [
        {
            "slug": "python-base",
            "version_constraint": ">=1.0.0,<2.0.0",
            "optional": True,
        }
    ]


@pytest.mark.parametrize(
    ("relationships", "message"),
    [
        (
            {
                "depends_on": [
                    {
                        "slug": "python-base",
                        "version": "1.0.0",
                        "version_constraint": ">=1.0.0,<2.0.0",
                    }
                ]
            },
            "exactly one of version or version_constraint",
        ),
        (
            {"depends_on": [{"slug": "python-base"}]},
            "exactly one of version or version_constraint",
        ),
        (
            {"suggests": [{"slug": "python-base", "version": "1.0.0"}]},
            "Unknown relationship family",
        ),
        (
            {
                "extends": [
                    {
                        "slug": "python-base",
                        "version": "1.0.0",
                        "version_constraint": ">=1.0.0,<2.0.0",
                    }
                ]
            },
            "Unknown field",
        ),
    ],
)
def test_normalize_relationships_rejects_registry_invalid_shapes(
    relationships,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_relationships(relationships)


def test_validation_blocks_invalid_manifest_relationships(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PUBLISHER_LLM_VALIDATION_ENABLED", "false")
    context = _context_for_skill(
        tmp_path,
        """relationships:
  depends_on:
    - slug: python-base
      version: 1.0.0
      version_constraint: ">=1.0.0,<2.0.0"
""",
    )

    DiscoveryStage().run(context)
    ValidationStage().run(context)

    assert not context.validation.passed
    assert any(
        "exactly one of version or version_constraint" in error
        for error in context.validation.errors
    )


def test_validation_warns_when_relationship_targets_missing_from_repo(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PUBLISHER_LLM_VALIDATION_ENABLED", "false")
    context = _context_for_skill(
        tmp_path,
        """relationships:
  depends_on:
    - slug: missing-skill
      version_constraint: ">=1.0.0"
""",
    )

    DiscoveryStage().run(context)
    ValidationStage().run(context)

    assert context.validation.passed
    assert any(
        "Relationship target missing-skill is not present in the local skill repository"
        in warning
        for warning in context.validation.warnings
    )
    assert context.validation.errors == []


def test_validation_warns_when_manifest_relationship_targets_missing_from_repo(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PUBLISHER_LLM_VALIDATION_ENABLED", "false")
    context = _context_for_skill(
        tmp_path,
        """  relationships:
    depends_on:
      - slug: missing-skill
        version_constraint: ">=1.0.0"
""",
    )

    DiscoveryStage().run(context)
    ValidationStage().run(context)

    assert context.validation.passed
    assert any(
        "Relationship target missing-skill is not present in the local skill repository"
        in warning
        for warning in context.validation.warnings
    )
    assert context.validation.errors == []


def test_validation_accepts_relationship_targets_present_in_repo(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PUBLISHER_LLM_VALIDATION_ENABLED", "false")
    _write_related_skill(tmp_path, "present-skill")
    context = _context_for_skill(
        tmp_path,
        """relationships:
  depends_on:
    - slug: present-skill
      version_constraint: ">=1.0.0"
""",
    )

    DiscoveryStage().run(context)
    ValidationStage().run(context)

    assert context.validation.passed


def test_check_relationship_references_reports_missing_skills(monkeypatch) -> None:
    def fake_urlopen(http_request, timeout):
        url = http_request.full_url
        if url.endswith("/skills/python-base"):
            return _FakeResponse(b'{"slug":"python-base","versions":[{"version":"1.0.0"}]}')
        if url.endswith("/skills/python-missing/9.9.9"):
            from urllib.error import HTTPError

            raise HTTPError(url, 404, "not found", hdrs=None, fp=None)
        if url.endswith("/skills/python-ghost"):
            from urllib.error import HTTPError

            raise HTTPError(url, 404, "not found", hdrs=None, fp=None)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("publisher.registry.client.request.urlopen", fake_urlopen)

    issues = check_relationship_references(
        registry_url="https://registry.example.test",
        token="reader.token",
        relationships={
            "depends_on": [
                {"slug": "python-base", "version_constraint": ">=1.0.0,<2.0.0"},
                {"slug": "python-ghost", "version_constraint": ">=1.0.0,<2.0.0"},
            ],
            "extends": [{"slug": "python-missing", "version": "9.9.9"}],
            "conflicts_with": [],
            "overlaps_with": [],
        },
    )

    assert issues == [
        RelationshipCheckIssue(
            kind="missing",
            family="depends_on",
            slug="python-ghost",
            version=None,
            message="No visible versions found for relationship target python-ghost.",
        ),
        RelationshipCheckIssue(
            kind="missing",
            family="extends",
            slug="python-missing",
            version="9.9.9",
            message="Relationship target python-missing@9.9.9 was not found.",
        ),
    ]


def test_relationship_alert_lines_describe_missing_targets() -> None:
    lines = _relationship_alert_lines(
        [
            RelationshipCheckIssue(
                kind="missing",
                family="extends",
                slug="python-missing",
                version="9.9.9",
                message="Relationship target python-missing@9.9.9 was not found.",
            )
        ]
    )

    assert lines == [
        "- missing extends target python-missing@9.9.9: "
        "Relationship target python-missing@9.9.9 was not found."
    ]
