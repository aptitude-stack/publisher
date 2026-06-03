from __future__ import annotations

from pathlib import Path

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
