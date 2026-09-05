from __future__ import annotations

from io import BytesIO
import tarfile

from publisher.app.pipeline import PublisherPipeline
from publisher.artifacts.bundle import build_bundle_bytes
from publisher.stages.discovery import DiscoveryStage


def _write_skill_with_stale_artifacts(tmp_path):
    skill_root = tmp_path / "artifact-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        """---
name: artifact-skill
description: "Use when testing publisher artifact exclusion."
---

# Instructions

Use this skill for artifact exclusion tests.
""",
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").with_name("aptitude.yaml").write_text("""version: 0.1.0
intent: create_skill
tags: [test]
inputs_schema: {"type":"object"}
outputs_schema: {"type":"object"}
""", encoding="utf-8")
    (skill_root / "notes.txt").write_text("include me", encoding="utf-8")
    artifacts_dir = skill_root / ".publisher_artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "00_inventory.json").write_text("exclude me", encoding="utf-8")
    return skill_root


def test_discovery_excludes_publisher_artifacts(tmp_path) -> None:
    skill_root = _write_skill_with_stale_artifacts(tmp_path)
    context = PublisherPipeline().create_context(file_path=str(skill_root))

    DiscoveryStage().run(context)

    assert context.inventory.other_files == ["aptitude.yaml", "notes.txt"]


def test_bundle_excludes_publisher_artifacts(tmp_path) -> None:
    skill_root = _write_skill_with_stale_artifacts(tmp_path)
    context = PublisherPipeline().create_context(file_path=str(skill_root))
    context.inventory.skill_root = str(skill_root)

    import zstandard as zstd

    tar_bytes = zstd.ZstdDecompressor().decompress(build_bundle_bytes(context))
    with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:") as archive:
        names = archive.getnames()

    assert "skill-bundle/SKILL.md" in names
    assert "skill-bundle/notes.txt" in names
    assert "skill-bundle/.publisher_artifacts/00_inventory.json" not in names
