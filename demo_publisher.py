"""Interactive CLI demo for publishing skills through the Aptitude registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any, TypeVar, cast

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from publisher.bundle import build_bundle_bytes
from publisher.cli import (
    _default_publish_token,
    _default_registry_url,
    _load_local_env_defaults,
)
from publisher.models import PublishContext
from publisher.pipeline import PublisherPipeline
from publisher.registry_client import RegistryPublishResult, publish_to_registry


ROOT = Path(__file__).resolve().parent
CONSOLE = Console()
TEXT_PRIMARY = "bold white"
TEXT_BODY = "white"
TEXT_MUTED = "grey70"
TEXT_SUBTLE = "grey50"
TEXT_DETAIL = "grey82"
BORDER_PRIMARY = "grey27"
BORDER_SECONDARY = "grey35"
ACCENT = "#8fa3ad"
PLAN_SUMMARY_LABEL_WIDTH = len("Validation")
T = TypeVar("T")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
WORDMARK = (
    "\n"
    "   ______          __          \n"
    "  /\\  _  \\        /\\ \\__       \n"
    "  \\ \\ \\L\\ \\  _____\\ \\ ,_\\      \n"
    "   \\ \\  __ \\/\\ '__`\\ \\ \\/      \n"
    "    \\ \\ \\/\\ \\ \\ \\L\\ \\ \\ \\_  __ \n"
    "     \\ \\_\\ \\_\\ \\ ,__/\\ \\__\\/\\_\\\n"
    "      \\/_/\\/_/\\ \\ \\/  \\/__/\\/_/\n"
    "               \\ \\_\\           \n"
    "                \\/_/           \n\n"
)


@dataclass(frozen=True, slots=True)
class DemoSkill:
    """One local skill folder available to the demo."""

    path: Path
    name: str
    description: str
    version: str
    intent: str


def main() -> None:
    """Run a client-style publish demo from the terminal."""
    try:
        _run_demo()
    except KeyboardInterrupt:
        CONSOLE.print("\n[yellow]Publish cancelled by user.[/yellow]")


def _run_demo() -> None:
    _load_local_env_defaults()
    skills = _discover_skills()
    if not skills:
        CONSOLE.print("[red]No local skill folders with SKILL.md were found.[/red]")
        return

    _render_header()
    skill = _prompt_for_skill(skills)
    version = _prompt_for_version(skill)

    with CONSOLE.status("[bold white]Running publisher checks", spinner="dots"):
        context = _run_pipeline(skill=skill, version=version)

    _render_evaluation(context)
    problems = _collect_problems(context)
    if _is_hard_blocked(context):
        _render_blocked(context, problems)
        return

    if problems:
        _render_review_prompt(context, problems)
        if not _confirm("Upload this skill anyway?", default=False):
            CONSOLE.print("[yellow]Upload cancelled by user.[/yellow]")
            return
    elif not _confirm("Upload selected skill?", default=True):
        CONSOLE.print("[yellow]Upload cancelled by user.[/yellow]")
        return

    token = _default_publish_token()
    if not token:
        CONSOLE.print(
            "[red]Missing publish token. Set APTITUDE_PUBLISH_TOKEN or PUBLISH_TOKEN.[/red]"
        )
        return

    try:
        bundle_bytes = build_bundle_bytes(context)
    except RuntimeError as exc:
        CONSOLE.print(f"[red]Bundle creation failed: {exc}[/red]")
        return

    with CONSOLE.status("[bold white]Publishing to registry", spinner="dots"):
        result = publish_to_registry(
            registry_url=_default_registry_url(),
            token=token,
            context=context,
            bundle_bytes=bundle_bytes,
        )

    _render_registry_result(result, context)


def _render_header() -> None:
    CONSOLE.print(Text(WORDMARK, style=TEXT_PRIMARY))
    CONSOLE.print(
        Text.assemble(
            ("Aptitude", TEXT_PRIMARY),
            (" - Review-first CLI for publishing existing skills.", TEXT_MUTED),
        )
    )
    _write_separator()
    CONSOLE.print(Text("guided publish flow", style=TEXT_MUTED))
    CONSOLE.print(Text(f"registry: {_default_registry_url()}", style=TEXT_SUBTLE))
    CONSOLE.print()


def _prompt_for_skill(skills: list[DemoSkill]) -> DemoSkill:
    return _select_one(
        "Select an existing skill to publish",
        [(f"{skill.name}@{skill.version}", skill) for skill in skills],
    )


def _prompt_for_version(skill: DemoSkill) -> str:
    while True:
        version = Prompt.ask("Version to publish", default=skill.version).strip()
        if SEMVER_PATTERN.fullmatch(version):
            return version
        CONSOLE.print(
            "[yellow]Version must be semantic versioning in the form X.Y.Z, "
            "for example 0.1.0.[/yellow]"
        )


def _run_pipeline(*, skill: DemoSkill, version: str) -> PublishContext:
    pipeline = PublisherPipeline()
    context = pipeline.create_context(
        file_path=str(skill.path),
        version_override=version,
        intent_override=skill.intent,
    )
    return pipeline.run(context)


def _render_evaluation(context: PublishContext) -> None:
    summary = Group(
        Text(
            _format_summary_row(
                ("Selected", f"{context.identity.slug}@{context.identity.version}"),
            ),
            style=TEXT_PRIMARY,
        ),
        Text(
            _format_summary_row(
                ("Validation", "passed" if context.validation.passed else "failed"),
            ),
            style=TEXT_DETAIL,
        ),
        Text(
            _format_summary_row(("Security", str(context.security.decision))),
            style=TEXT_DETAIL,
        ),
        Text(
            _format_summary_row(("Ranking", str(context.ranking.label))),
            style=TEXT_MUTED,
        ),
        Text(
            _format_summary_row(("Decision", str(context.ranking.publish_decision))),
            style=TEXT_MUTED,
        ),
        Text(""),
        Text("Publisher Checks", style=TEXT_PRIMARY),
        *[
            Text(
                f"{index}. {snapshot.stage_name} -> {snapshot.status}",
                style=TEXT_MUTED,
            )
            for index, snapshot in enumerate(context.stage_history, start=1)
        ],
    )

    CONSOLE.print(
        Panel(
            summary,
            title="Review Plan",
            border_style=BORDER_SECONDARY,
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )


def _render_review_prompt(context: PublishContext, problems: list[str]) -> None:
    lines: list[Text] = [
        Text(f"{context.identity.slug} was held for review.", style=TEXT_PRIMARY),
        Text(
            "The publisher did not auto-approve this skill because the checks below "
            "found quality or safety concerns. You can still choose to upload it.",
            style=TEXT_MUTED,
        ),
        Text(""),
    ]
    for index, problem in enumerate(problems, start=1):
        lines.append(Text(f"{index}. {problem}", style=TEXT_MUTED))
    CONSOLE.print(
        Panel(
            Group(*lines),
            title=f"Upload Needs Approval: {context.identity.slug}",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )


def _render_blocked(context: PublishContext, problems: list[str]) -> None:
    body = Text()
    body.append(f"{context.identity.slug} was blocked before upload.\n\n", style="bold red")
    for problem in problems:
        body.append(f"- {problem}\n", style=TEXT_BODY)
    CONSOLE.print(
        Panel(
            body,
            title="Publish Blocked",
            border_style="red",
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )


def _render_registry_result(
    result: RegistryPublishResult,
    context: PublishContext,
) -> None:
    success = 200 <= result.status_code < 300
    summary = Group(
        Text(
            _format_summary_row(
                ("Status", result.status_code),
                ("Request", result.request_id or "(none)"),
                ("Skill", context.identity.slug),
                ("Version", context.identity.version),
            ),
            style=TEXT_PRIMARY,
        )
    )

    title = "Publish Complete" if success else "Publish Rejected"
    CONSOLE.print(
        Panel(
            summary,
            title=title,
            border_style=BORDER_SECONDARY if success else "red",
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )
    if not success:
        CONSOLE.print_json(data=result.body)


def _discover_skills() -> list[DemoSkill]:
    skills: list[DemoSkill] = []
    for skill_file in sorted(ROOT.glob("*/SKILL.md")):
        if skill_file.parent.name.startswith("."):
            continue
        frontmatter = _read_frontmatter(skill_file)
        metadata = frontmatter.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        skills.append(
            DemoSkill(
                path=skill_file.parent,
                name=str(frontmatter.get("name") or skill_file.parent.name),
                description=str(frontmatter.get("description") or ""),
                version=str(metadata.get("version") or "0.1.0"),
                intent=str(metadata.get("intent") or "create_skill"),
            )
        )
    return skills


def _collect_problems(context: PublishContext) -> list[str]:
    problems: list[str] = []
    problems.extend(context.validation.errors)
    problems.extend(context.validation.warnings)
    for gate in context.gate_history:
        problems.extend(gate.blocking_issues)
        problems.extend(gate.warnings)
    for finding in context.security.findings:
        severity = finding.get("severity", "unknown")
        check = finding.get("check", "security")
        evidence = finding.get("evidence", "")
        problems.append(f"{severity}: {check} ({evidence})")
    if context.ranking.publish_decision == "review_required":
        problems.append("Ranking decision is review_required.")
    return _dedupe(problems)


def _is_hard_blocked(context: PublishContext) -> bool:
    if context.security.decision == "block":
        return True
    if context.ranking.publish_decision == "block":
        return True
    return not (
        context.delivery_payload.intent
        and context.delivery_payload.version
        and context.delivery_payload.metadata
    )


def _read_frontmatter(skill_file: Path) -> dict[str, Any]:
    content = skill_file.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return {}
    closing_index = content.find("\n---\n", 4)
    if closing_index == -1:
        return {}
    return _parse_simple_yaml(content[4:closing_index])


def _parse_simple_yaml(frontmatter_text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_nested_key: str | None = None
    for raw_line in frontmatter_text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith("  ") and current_nested_key:
            stripped = raw_line.strip()
            if ":" not in stripped:
                continue
            nested_key, nested_value = stripped.split(":", 1)
            nested_map = result.setdefault(current_nested_key, {})
            if isinstance(nested_map, dict):
                nested_map[nested_key.strip()] = _parse_scalar(nested_value.strip())
            continue

        current_nested_key = None
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            result[key] = {}
            current_nested_key = key
            continue
        result[key] = _parse_scalar(value)
    return result


def _parse_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",")]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _select_one(
    title: str,
    options: list[tuple[str, T]],
    *,
    help_text: str | None = None,
    descriptions: dict[T, str] | None = None,
) -> T:
    if not options:
        raise ValueError("Expected at least one option.")
    if not sys.stdin.isatty() and not sys.stdout.isatty():
        return _select_one_fallback(title, options, help_text=help_text)

    try:
        return _select_one_prompt_toolkit(
            title,
            options,
            help_text=help_text,
            descriptions=descriptions,
        )
    except KeyboardInterrupt:
        raise
    except (EOFError, ModuleNotFoundError, Exception):
        return _select_one_fallback(title, options, help_text=help_text)


def _select_one_prompt_toolkit(
    title: str,
    options: list[tuple[str, T]],
    *,
    help_text: str | None = None,
    descriptions: dict[T, str] | None = None,
) -> T:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    state = {"index": 0}

    def render_menu() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = [("class:title", f"{title}\n")]
        if help_text:
            fragments.append(("class:hint", f"{help_text}\n"))
        fragments.append(("class:hint", "\n"))
        active_value = options[state["index"]][1]
        active_description = descriptions.get(active_value) if descriptions else None
        for index, (label, _) in enumerate(options):
            is_active = index == state["index"]
            marker = "●" if is_active else "○"
            marker_style = "class:marker-active" if is_active else "class:item"
            label_style = "class:active" if is_active else "class:item"
            fragments.append((marker_style, f"{marker} "))
            fragments.append((label_style, label))
            if is_active and active_description:
                fragments.append(("class:detail", f" - {active_description}"))
            fragments.append(("", "\n"))
        fragments.append(
            (
                "class:hint",
                "\n[↑↓/j/k] move  [enter] confirm  [q] cancel\n\n",
            )
        )
        return fragments

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("k")
    @bindings.add("c-p")
    def _move_up(event) -> None:
        state["index"] = (state["index"] - 1) % len(options)
        event.app.invalidate()

    @bindings.add("down")
    @bindings.add("j")
    @bindings.add("c-n")
    def _move_down(event) -> None:
        state["index"] = (state["index"] + 1) % len(options)
        event.app.invalidate()

    @bindings.add("enter")
    def _accept(event) -> None:
        event.app.exit(result=options[state["index"]][1])

    @bindings.add("q")
    @bindings.add("c-c")
    def _abort(event) -> None:
        event.app.exit(exception=KeyboardInterrupt())

    application: Application[T] = Application(
        layout=Layout(
            HSplit([Window(FormattedTextControl(render_menu), always_hide_cursor=True)])
        ),
        key_bindings=bindings,
        mouse_support=False,
        full_screen=False,
        style=Style.from_dict(
            {
                "title": "bold #ffffff",
                "item": "#b8b8b8",
                "active": "bold #ffffff",
                "marker-active": f"bold {ACCENT}",
                "hint": "#7a7a7a",
                "detail": "#d8d8d8",
            }
        ),
    )
    return cast(T, application.run())


def _select_one_fallback(
    title: str,
    options: list[tuple[str, T]],
    *,
    help_text: str | None = None,
) -> T:
    print(title)
    if help_text:
        print(help_text)
    for index, (label, _) in enumerate(options, start=1):
        print(f"  {index}. {label}")
    while True:
        raw_choice = input("Select option by number: ").strip()
        try:
            index = int(raw_choice)
        except ValueError:
            print("Enter a valid number.")
            continue
        if 1 <= index <= len(options):
            return options[index - 1][1]
        print("Selection out of range.")


def _confirm(label: str, *, default: bool) -> bool:
    options = [("Yes", True), ("No", False)] if default else [("No", False), ("Yes", True)]
    return _select_one(label, options)


def _format_summary_row(*items: tuple[str, object]) -> str:
    return "\n".join(
        f"{label:<{PLAN_SUMMARY_LABEL_WIDTH}} : {value}" for label, value in items
    )


def _write_separator() -> None:
    width = CONSOLE.size.width
    CONSOLE.file.write("─" * max(24, width) + "\n\n")
    flush = getattr(CONSOLE.file, "flush", None)
    if callable(flush):
        flush()


if __name__ == "__main__":
    main()
