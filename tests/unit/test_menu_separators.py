from __future__ import annotations

from contextlib import contextmanager
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from publisher.app import menu
from publisher.domain.models import PublishContext, SkillSource
from publisher.registry.client import ExistingSkill, ExistingSkillVersion


class _AsciiStream:
    encoding = "ascii"


def test_render_step_separator_uses_stream_safe_glyphs() -> None:
    assert menu._render_step_separator(3) == "───"
    assert menu._render_step_separator(3, _AsciiStream()) == "---"
    assert menu._render_step_separator(0, _AsciiStream()) == "-"


def test_interactive_pipeline_report_shows_only_three_evaluation_phases(monkeypatch) -> None:
    """The wizard must not reintroduce the detailed report by default."""
    output = StringIO()
    monkeypatch.setattr(menu, "CONSOLE", Console(file=output, width=120))
    context = PublishContext(source=SkillSource(file_path="skills/example"))
    context.validation.passed = True
    context.security.score = 1.0
    context.security.decision = "allow"
    context.ranking.label = "review"

    menu._render_pipeline_report(context)

    rendered = output.getvalue()
    assert "Structure" in rendered
    assert "Risk" in rendered
    assert "Quality" in rendered
    assert "Stages" not in rendered
    assert "Gate Results" not in rendered
    assert "Skill Identity" not in rendered


def test_wizard_pipeline_enables_verbose_upskill(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    @contextmanager
    def fake_scan_environment(profile, *, upskill_verbose):
        captured["profile"] = profile
        captured["upskill_verbose"] = upskill_verbose
        yield

    class FakePipeline:
        def create_context(self, **kwargs):
            return object()

        def run(self, context):
            return context

    monkeypatch.setattr(menu, "_scan_profile_environment", fake_scan_environment)
    monkeypatch.setattr(menu, "PublisherPipeline", FakePipeline)
    plan = menu.PublishPlan(
        action="inspect",
        skill_path=tmp_path,
        slug=None,
        intent="create_skill",
        trust_tier="untrusted",
        namespace="public",
        artifact_origin="internal",
        policy_pack_slug=None,
        publisher_identity=None,
        scan_profile="fast",
    )

    menu._run_pipeline(plan)

    assert captured == {"profile": "fast", "upskill_verbose": True}


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


def test_publish_plan_requires_token_before_pipeline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("APTITUDE_PUBLISH_TOKEN", raising=False)
    monkeypatch.delenv("APTITUDE_INTEGRATION_PUBLISH_TOKEN", raising=False)
    monkeypatch.delenv("PUBLISH_TOKEN", raising=False)
    monkeypatch.setattr(
        menu,
        "_run_pipeline",
        lambda *args, **kwargs: pytest.fail("publish should fail before the pipeline"),
    )

    plan = menu.PublishPlan(
        action="publish",
        skill_path=tmp_path,
        slug=None,
        intent="create_skill",
        trust_tier="untrusted",
        namespace="public",
        artifact_origin="internal",
        policy_pack_slug=None,
        publisher_identity=None,
        scan_profile="fast",
    )

    assert menu._execute_plan(plan) == 1


def test_publish_plan_blocks_existing_create_slug_before_pipeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "python-patterns"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: python-patterns
description: "Use when testing publisher preflight behavior."
metadata:
  version: 0.1.0
  intent: create_skill
---

# python-patterns

Use this skill for publisher unit tests.
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "publish-token")
    monkeypatch.setattr(
        "publisher.app.cli.get_existing_skill",
        lambda **kwargs: ExistingSkill(
            slug="python-patterns",
            versions=(ExistingSkillVersion(version="1.0.0"),),
        ),
    )
    monkeypatch.setattr(
        menu,
        "_run_pipeline",
        lambda *args, **kwargs: pytest.fail("publish should fail before the pipeline"),
    )

    plan = menu.PublishPlan(
        action="publish",
        skill_path=skill_dir,
        slug=None,
        intent="create_skill",
        trust_tier="untrusted",
        namespace="public",
        artifact_origin="internal",
        policy_pack_slug=None,
        publisher_identity=None,
        scan_profile="fast",
    )

    assert menu._execute_plan(plan) == 1
