from __future__ import annotations

import hashlib
from io import BytesIO
import tarfile

import pytest

from publisher.app.pipeline import PublisherPipeline
from publisher.artifacts.bundle import build_bundle_bytes
from publisher.manifest import load_manifest
from publisher.stages.delivery import DeliveryStage
from publisher.stages.discovery import DiscoveryStage
from publisher.stages.identity import IdentityStage
from publisher.stages.metadata import MetadataStage
from publisher.stages.validation import ValidationStage


def _write_skill(skill_root, *, manifest: str, frontmatter: str = ""):
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        f"""---
name: manifest-skill
description: "Publishes sidecar metadata; use when testing the publisher manifest."
{frontmatter}---

# Instructions

Use this skill for manifest tests.

# Example

Input: a skill.
Output: a published skill.

# Troubleshooting

Fix invalid manifest values before publishing.
""",
        encoding="utf-8",
    )
    (skill_root / "aptitude.yaml").write_text(manifest, encoding="utf-8")


def test_discovery_identity_metadata_and_delivery_use_aptitude_manifest(tmp_path) -> None:
    skill_root = tmp_path / "manifest-skill"
    _write_skill(
        skill_root,
        manifest="""version: 1.2.3
intent: create_skill
tags: [python, registry]
inputs_schema: {type: object}
outputs_schema: {type: object}
relationships:
  depends_on:
    - slug: python-base
      version_constraint: ">=1.0.0,<2.0.0"
""",
    )
    context = PublisherPipeline().create_context(file_path=str(skill_root))

    DiscoveryStage().run(context)
    IdentityStage().run(context)
    MetadataStage().run(context)
    DeliveryStage().run(context)

    assert context.source.parsed_content["manifest"]["intent"] == "create_skill"
    assert context.identity.version == "1.2.3"
    assert context.metadata.tags == ["python", "registry"]
    assert context.metadata.inputs_schema == {"type": "object"}
    assert context.delivery_payload.relationships["depends_on"] == [
        {"slug": "python-base", "version_constraint": ">=1.0.0,<2.0.0"}
    ]


def test_identity_cli_values_override_manifest_values(tmp_path) -> None:
    skill_root = tmp_path / "manifest-skill"
    _write_skill(
        skill_root,
        manifest="version: 1.2.3\nintent: create_skill\ntags: [test]\n",
    )
    context = PublisherPipeline().create_context(
        file_path=str(skill_root),
        version_override="9.9.9",
        intent_override="publish_version",
    )

    DiscoveryStage().run(context)
    IdentityStage().run(context)

    assert context.identity.version == "9.9.9"
    assert context.identity.intent == "publish_version"


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("version: 1.0.0\nversion: 2.0.0\n", "duplicate key"),
        ("version: 1.0.0\nunknown: true\n", "Unknown field"),
        ("version: 1.0.0\ntags: nope\n", "tags.*list of strings"),
        ("version: 1.0.0\ntoken_estimate: -1\n", "non-negative integer"),
        ("version: 1.0.0\nmaturity_score: .nan\n", "finite number"),
        (
            "version: 1.0.0\ninputs_schema: {date: 2026-01-01}\n",
            "JSON-compatible",
        ),
        (
            "version: 1.0.0\ninputs_schema: {values: !!set {x: null}}\n",
            "JSON-compatible",
        ),
        (
            "version: 1.0.0\ninputs_schema: &schema {self: *schema}\n",
            "recursive aliases",
        ),
        (
            "version: 1.0.0\nrelationships:\n  depends_on:\n    - slug: base\n",
            "exactly one of version or version_constraint",
        ),
    ],
)
def test_load_manifest_rejects_invalid_source_metadata(tmp_path, manifest, message) -> None:
    skill_root = tmp_path / "manifest-skill"
    skill_root.mkdir()
    (skill_root / "aptitude.yaml").write_text(manifest, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_manifest(skill_root)


def test_validation_requires_sidecar_and_rejects_legacy_aptitude_fields(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PUBLISHER_LLM_VALIDATION_ENABLED", "false")
    skill_root = tmp_path / "manifest-skill"
    _write_skill(
        skill_root,
        manifest="version: 1.0.0\nintent: create_skill\ntags: [test]\n",
        frontmatter="metadata:\n  version: 0.9.0\n  author: test\n",
    )
    context = PublisherPipeline().create_context(file_path=str(skill_root))

    ValidationStage().run(context)

    assert not context.validation.passed
    assert any("metadata.version" in error for error in context.validation.errors)
    assert not any("metadata.author" in error for error in context.validation.errors)

    (skill_root / "aptitude.yaml").unlink()
    context = PublisherPipeline().create_context(file_path=str(skill_root))
    ValidationStage().run(context)
    assert any("Missing required aptitude.yaml" in error for error in context.validation.errors)


def test_bundle_contains_sidecar_and_openai_config_and_changes_digest(tmp_path) -> None:
    skill_root = tmp_path / "manifest-skill"
    _write_skill(
        skill_root,
        manifest="version: 1.0.0\nintent: create_skill\ntags: [test]\n",
    )
    openai_dir = skill_root / "agents"
    openai_dir.mkdir()
    (openai_dir / "openai.yaml").write_text("display_name: Manifest\n", encoding="utf-8")
    context = PublisherPipeline().create_context(file_path=str(skill_root))
    context.inventory.skill_root = str(skill_root)

    first_bundle = build_bundle_bytes(context)
    first_digest = hashlib.sha256(first_bundle).hexdigest()
    import zstandard as zstd

    tar_bytes = zstd.ZstdDecompressor().decompress(first_bundle)
    with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:") as archive:
        assert "skill-bundle/aptitude.yaml" in archive.getnames()
        assert "skill-bundle/agents/openai.yaml" in archive.getnames()
    (skill_root / "aptitude.yaml").write_text(
        "version: 1.0.1\nintent: create_skill\ntags: [test]\n",
        encoding="utf-8",
    )
    second_digest = hashlib.sha256(build_bundle_bytes(context)).hexdigest()

    assert first_digest != second_digest


def test_invalid_utf8_manifest_names_the_sidecar(tmp_path):
    (tmp_path / "aptitude.yaml").write_bytes(b"\xff")
    with pytest.raises(ValueError, match="Unable to read aptitude.yaml"):
        load_manifest(tmp_path)
