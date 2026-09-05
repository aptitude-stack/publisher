"""Publisher rejects invalid wire inputs before external evaluation."""

from pathlib import Path

import pytest

from publisher.domain.models import PublishContext, SkillSource
from publisher.gates.metadata import MetadataGate
from publisher.manifest import load_manifest
from publisher.relationships import normalize_relationships
from publisher.stages.delivery import DeliveryStage
from publisher.stages.ranking import RankingStage
from publisher.artifacts.bundle import build_bundle_bytes
from publisher.gates.identity import IdentityGate


def test_optional_tags_and_removed_schemas_do_not_block_or_reduce_score() -> None:
    context = PublishContext(source=SkillSource(file_path="unused"))
    context.metadata.name = "demo"
    context.metadata.description = "Use when testing."
    assert MetadataGate().verify(context)
    RankingStage().run(context)
    assert context.ranking.criteria_scores["metadata_completeness"] == 1.0
    DeliveryStage().run(context)
    assert "inputs_schema" not in context.delivery_payload.metadata
    assert "outputs_schema" not in context.delivery_payload.metadata


@pytest.mark.parametrize("field", ["inputs_schema", "outputs_schema"])
def test_manifest_rejects_removed_fields(tmp_path: Path, field: str) -> None:
    (tmp_path / "aptitude.yaml").write_text(f"version: 1.0.0\n{field}: {{}}\n")
    with pytest.raises(ValueError, match="removed"):
        load_manifest(tmp_path)


@pytest.mark.parametrize("constraint", ["=1.2.3", "==1.2.3", ">=1.2.3-codex,<2.0.0"])
def test_registry_comparator_syntax(constraint: str) -> None:
    selector = {"slug": "demo", "version_constraint": constraint}
    assert normalize_relationships({"depends_on": [selector]})["depends_on"] == [selector]


@pytest.mark.parametrize("constraint", ["1.2.3", ",".join([">=1.0.0"] * 30)])
def test_rejects_bare_or_overlong_constraint(constraint: str) -> None:
    with pytest.raises(ValueError):
        normalize_relationships({"depends_on": [{"slug": "demo", "version_constraint": constraint}]})


def test_markers_preserve_authored_order_and_duplicates() -> None:
    selector = {"slug": "demo", "version": "1.0.0", "markers": ["os:linux", "os:linux", "x"]}
    assert normalize_relationships({"depends_on": [selector]})["depends_on"] == [selector]


@pytest.mark.parametrize("marker", ["_private", " os:linux", "", ".hidden"])
def test_rejects_invalid_markers(marker: str) -> None:
    with pytest.raises(ValueError):
        normalize_relationships({"depends_on": [{"slug": "demo", "version": "1.0.0", "markers": [marker]}]})


@pytest.mark.parametrize("field,limit", [("namespace", 128), ("policy_pack_slug", 128), ("publisher_identity", 200)])
def test_governance_lengths_checked_in_shared_gate(field: str, limit: int) -> None:
    context = PublishContext(source=SkillSource(file_path="unused"))
    context.identity.slug = "demo"
    context.identity.version = "1.0.0"
    context.identity.intent = "create_skill"
    setattr(context.source, field, "x" * limit)
    assert IdentityGate().verify(context)
    setattr(context.source, field, "x" * (limit + 1))
    assert not IdentityGate().verify(context)


def test_bundle_file_count_boundary(tmp_path: Path) -> None:
    context = PublishContext(source=SkillSource(file_path=str(tmp_path)))
    for i in range(200):
        (tmp_path / f"{i}.txt").write_text("x")
    assert build_bundle_bytes(context)
    (tmp_path / "overflow.txt").write_text("x")
    with pytest.raises(ValueError, match="200 files"):
        build_bundle_bytes(context)


def test_bundle_path_limit_counts_utf8_bytes(tmp_path: Path) -> None:
    context = PublishContext(source=SkillSource(file_path=str(tmp_path)))
    path = tmp_path / ("é" * 113 + "a")
    path.write_text("x")  # 13-byte archive prefix + 227-byte filename.
    assert build_bundle_bytes(context)
    path.rename(tmp_path / ("é" * 114))
    with pytest.raises(ValueError, match="240 bytes"):
        build_bundle_bytes(context)


def test_bundle_rejects_backslash_filename(tmp_path: Path) -> None:
    (tmp_path / "unsafe\\file").write_text("x")
    with pytest.raises(ValueError, match="unsafe separator"):
        build_bundle_bytes(PublishContext(source=SkillSource(file_path=str(tmp_path))))


def test_compressed_bundle_size_boundary(tmp_path: Path, monkeypatch) -> None:
    import publisher.artifacts.bundle as bundle_module

    context = PublishContext(source=SkillSource(file_path=str(tmp_path)))
    limit = bundle_module.MAX_BUNDLE_SIZE_BYTES
    monkeypatch.setattr(bundle_module, "_compress_entries", lambda _: b"x" * limit)
    assert len(build_bundle_bytes(context)) == limit
    monkeypatch.setattr(bundle_module, "_compress_entries", lambda _: b"x" * (limit + 1))
    with pytest.raises(ValueError, match="Compressed bundle exceeds"):
        build_bundle_bytes(context)


def test_bundle_preflight_stops_pipeline_before_evaluators(tmp_path: Path) -> None:
    from publisher.app.pipeline import PublisherPipeline
    from publisher.stages.discovery import DiscoveryStage

    (tmp_path / "SKILL.md").write_text("---\nname: demo\ndescription: Use when testing.\n---\nInstructions")
    (tmp_path / "aptitude.yaml").write_text("version: 1.0.0\nintent: create_skill\n")
    for i in range(199):
        (tmp_path / f"{i}.txt").write_text("x")

    class UnexpectedEvaluator:
        name = "security"

        def run(self, context):
            pytest.fail("External evaluation must not run for an invalid bundle")

    pipeline = PublisherPipeline()
    pipeline._stages = (DiscoveryStage(), UnexpectedEvaluator())
    context = pipeline.run(pipeline.create_context(file_path=str(tmp_path)))
    assert context.gate_history[-1].passed is False
    assert "200 files" in context.gate_history[-1].explanation
