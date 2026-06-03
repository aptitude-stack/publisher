from __future__ import annotations

from pathlib import Path

import pytest

from publisher.app import menu


class _AsciiStream:
    encoding = "ascii"


def test_render_step_separator_uses_stream_safe_glyphs() -> None:
    assert menu._render_step_separator(3) == "───"
    assert menu._render_step_separator(3, _AsciiStream()) == "---"
    assert menu._render_step_separator(0, _AsciiStream()) == "-"


def test_build_publish_plan_prints_separators_between_decisions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skill = menu.MenuSkill(
        path=tmp_path,
        name="example",
        version="0.1.0",
        intent="create_skill",
    )
    events: list[str] = []

    def fake_separator() -> None:
        events.append("separator")

    def fake_select(title, options, **kwargs):
        events.append(f"select:{title}")
        if title == "Skill source":
            return "local"
        return options[0][1]

    monkeypatch.setattr(menu, "_discover_skills", lambda root: [skill])
    monkeypatch.setattr(menu, "_print_step_separator", fake_separator)
    monkeypatch.setattr(menu, "_select", fake_select)

    plan = menu._build_publish_plan("publish")

    assert plan.skill_path == tmp_path.resolve()
    assert events == [
        "separator",
        "select:Skill source",
        "separator",
        "select:Local skill",
        "separator",
        "select:Publish intent",
        "separator",
        "select:Inspection depth",
    ]


def test_flow_options_hide_batch_upload_without_admin_token(monkeypatch) -> None:
    monkeypatch.delenv("APTITUDE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("APTITUDE_REGISTRY_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("REGISTRY_ADMIN_TOKEN", raising=False)

    values = [value for _, value in menu._flow_options()]

    assert "batch_upload" not in values


def test_flow_options_show_batch_upload_with_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("APTITUDE_ADMIN_TOKEN", "admin-token")

    values = [value for _, value in menu._flow_options()]

    assert "batch_upload" in values


def test_batch_upload_wizard_expands_directory_into_skill_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Example\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_admin_batch_upload(args):
        captured["skill_paths"] = args.skill_paths
        captured["admin_token"] = args.admin_token
        captured["dry_run"] = args.dry_run
        captured["scan_profile"] = args.scan_profile
        captured["trust_tier"] = args.trust_tier
        captured["artifact_origin"] = args.artifact_origin
        return 0

    monkeypatch.setenv("APTITUDE_ADMIN_TOKEN", "admin-token")
    monkeypatch.setattr(menu, "_prompt_directory", lambda label: tmp_path)
    monkeypatch.setattr(menu, "_print_step_separator", lambda: None)
    monkeypatch.setattr(
        menu,
        "_render_batch_upload_plan",
        lambda **kwargs: pytest.fail("batch wizard should not render a plan"),
    )
    monkeypatch.setattr(
        menu,
        "_confirm",
        lambda *args, **kwargs: pytest.fail("batch wizard should not confirm"),
    )
    monkeypatch.setattr(menu, "_run_admin_batch_upload", fake_run_admin_batch_upload)

    assert menu._run_batch_upload_wizard() == 0
    assert captured == {
        "skill_paths": [str(skill_dir.resolve())],
        "admin_token": "admin-token",
        "dry_run": False,
        "scan_profile": "fast",
        "trust_tier": "verified",
        "artifact_origin": "verified",
    }
