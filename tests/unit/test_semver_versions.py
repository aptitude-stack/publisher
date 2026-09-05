from pathlib import Path

import pytest

from publisher.domain.models import PublishContext, SkillSource
from publisher.gates.identity import IdentityGate
from publisher.interfaces.mcp.receipt import write_inspection_receipt
from publisher.registry.client import build_publish_metadata
from publisher.relationships import normalize_relationships
from publisher.stages.delivery import DeliveryStage
from publisher.stages.discovery import DiscoveryStage
from publisher.stages.identity import IdentityStage


@pytest.mark.parametrize(
    "version,valid",
    [
        ("0.0.0", True),
        ("1.2.3", True),
        ("1.2.3-codex", True),
        ("1.2.3-gpt-6-astra", True),
        ("1.2.3-codex.0", True),
        ("1.2.3-01codex", True),
        ("1.2.3+001.build", True),
        ("1.2.3-codex.1+build.001", True),
        ("v1.2.3-codex", False),
        ("1.2", False),
        ("1.2.x-codex", False),
        ("01.2.3", False),
        ("1.02.3", False),
        ("1.2.03", False),
        ("1.2.3-01", False),
        ("1.2.3-codex.01", False),
        ("1.2.3-", False),
        ("1.2.3+", False),
        ("1.2.3-codex..1", False),
        ("1.2.3-codex_1", False),
        ("1.2.3+build..1", False),
        ("1.2.3+build+1", False),
        ("1.2.3-codex/extra", False),
        ("1.٢.3", False),
        ("1.2.3-codex\nextra", False),
    ],
)
def test_identity_and_relationships_validate_semver(version: str, valid: bool) -> None:
    context = PublishContext(source=SkillSource(file_path="unused"))
    context.identity.slug = "version-test"
    context.identity.version = version
    context.identity.intent = "publish_version"
    assert IdentityGate().verify(context) is valid

    for field, value in (
        ("version", version),
        ("version_constraint", f">={version},<2.0.0"),
    ):
        relationships = {"depends_on": [{"slug": "python-base", field: value}]}
        if valid:
            assert (
                normalize_relationships(relationships)["depends_on"]
                == relationships["depends_on"]
            )
        else:
            with pytest.raises(ValueError, match="semver|semantic version"):
                normalize_relationships(relationships)


@pytest.mark.parametrize(
    "version", ["1.2.3-codex", "1.2.3-gpt-6-astra", "1.2.3-codex.1+build.001"]
)
@pytest.mark.parametrize("override", [False, True])
def test_suffix_survives_manifest_override_delivery_and_receipt(
    tmp_path: Path, version: str, override: bool
) -> None:
    (tmp_path / "SKILL.md").write_text(
        '---\nname: version-test\ndescription: "Use when testing versions."\n---\nTest versions.\n'
    )
    manifest_version = "0.1.0" if override else version
    (tmp_path / "aptitude.yaml").write_text(
        f'version: "{manifest_version}"\nintent: publish_version\n'
    )
    context = PublishContext(
        source=SkillSource(
            file_path=str(tmp_path), version_override=version if override else None
        )
    )
    DiscoveryStage().run(context)
    IdentityStage().run(context)
    assert IdentityGate().verify(context)
    assert context.identity.version == version
    assert not (tmp_path / "agents" / "openai.yaml").exists()

    DeliveryStage().run(context)
    assert build_publish_metadata(context)["version"] == version
    receipt = write_inspection_receipt(context, bundle_bytes=b"test-bundle")
    assert receipt["identity"]["version"] == version
    assert receipt["final_payload"]["version"] == version
