from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from publisher.interfaces.mcp.models import (
    InspectSkillInput,
    PublishSkillInput,
    ResponseFormat,
)


def test_inspect_input_resolves_existing_skill_directory(tmp_path: Path) -> None:
    skill_root = tmp_path / "example-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\nname: example-skill\n---\n", encoding="utf-8"
    )

    params = InspectSkillInput(skill_path=skill_root)

    assert params.skill_path == skill_root.resolve()


def test_inspect_input_rejects_missing_skill_file(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="skill_path must be"):
        InspectSkillInput(skill_path=tmp_path)


def test_inspect_input_forbids_unknown_fields(tmp_path: Path) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: example-skill\n---\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        InspectSkillInput(skill_path=skill_file, token="secret")


def test_publish_input_requires_explicit_true_confirmation(tmp_path: Path) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: example-skill\n---\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="Input should be True"):
        PublishSkillInput(
            skill_path=skill_file,
            slug="example-skill",
            intent="create_skill",
            confirm_upload=False,
        )


def test_publish_input_rejects_non_http_registry_url(tmp_path: Path) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: example-skill\n---\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="registry_url must use http or https"):
        PublishSkillInput(
            skill_path=skill_file,
            slug="example-skill",
            intent="create_skill",
            registry_url="file:///tmp/registry",
            confirm_upload=True,
        )


def test_public_mcp_inputs_default_to_markdown_and_accept_machine_formats(
    tmp_path: Path,
) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: example-skill\n---\n", encoding="utf-8")

    inspect = InspectSkillInput(skill_path=skill_file)
    publish = PublishSkillInput(
        skill_path=skill_file,
        slug="example-skill",
        intent="create_skill",
        confirm_upload=True,
        response_format=ResponseFormat.JSON,
    )

    assert inspect.response_format is ResponseFormat.MARKDOWN
    assert publish.response_format is ResponseFormat.JSON
    assert InspectSkillInput(
        skill_path=skill_file, response_format=ResponseFormat.TOON
    ).response_format is ResponseFormat.TOON
