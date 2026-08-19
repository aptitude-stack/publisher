"""Interactive wizard for publishing skills through the Aptitude registry."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any, Literal, Sequence, TypeVar

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from publisher.artifacts.bundle import build_bundle_bytes
from publisher.app.cli import (
    _check_existing_slug_block,
    _default_admin_token,
    _default_publish_token,
    _default_registry_url,
    _existing_skill_lines,
    _load_local_env_defaults,
    _missing_publish_token_message,
    _preflight_identity_from_skill_path,
    _publisher_cli_version,
    _report_detail_sections,
    _registry_result_lines,
    _relationship_alert_lines,
    _relationship_check_token,
    _run_admin_batch_upload,
    _scan_profile_environment,
)
from publisher.app.pipeline import PublisherPipeline
from publisher.domain.models import PublishContext
from publisher.registry.client import (
    check_relationship_references,
    publish_to_registry,
)

try:
    import termios
    import tty
except ModuleNotFoundError:  # pragma: no cover - platform fallback
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]


Action = Literal["inspect", "publish", "batch_upload", "help", "exit"]
FailureAction = Literal["upload_another", "main_menu"]
SkillSource = Literal["local", "path"]
Intent = Literal["create_skill", "publish_version"]
ScanProfile = Literal["fast", "slow"]
TrustTier = Literal["untrusted", "internal", "verified"]
ArtifactOrigin = Literal["internal", "imported", "verified", "restricted"]
T = TypeVar("T")

CONSOLE = Console()


@dataclass(frozen=True, slots=True)
class CliTheme:
    """Visual tokens shared across the publisher wizard."""

    text_primary: str = "bold white"
    text_body: str = "white"
    text_muted: str = "grey70"
    text_subtle: str = "grey50"
    text_detail: str = "grey82"
    border_primary: str = "grey27"
    border_secondary: str = "grey35"
    accent: str = "#8fa3ad"


THEME = CliTheme()
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
    "                \\/_/           \n"
)


class _BackToMainMenu(Exception):
    """Raised by submenu key bindings to return to the top-level menu."""


@dataclass(frozen=True, slots=True)
class MenuSkill:
    """One skill folder available to the wizard."""

    path: Path
    name: str
    version: str
    intent: str


@dataclass(frozen=True, slots=True)
class PublishPlan:
    """The user's selected publish workflow."""

    action: Action
    skill_path: Path
    slug: str | None
    intent: Intent
    trust_tier: TrustTier
    namespace: str
    artifact_origin: ArtifactOrigin
    policy_pack_slug: str | None
    publisher_identity: str | None
    scan_profile: ScanProfile


def run_menu() -> int:
    """Run the guided publish wizard."""

    _load_local_env_defaults()
    _render_header()

    try:
        while True:
            action = _select(
                "Choose a flow",
                _flow_options(),
                subtitle="Start with inspect, publish, or help.",
                descriptions=_flow_descriptions(),
            )
            if action == "help":
                _render_help()
                continue
            if action == "exit":
                CONSOLE.print("[grey70]Exited publisher wizard.[/grey70]")
                return 0
            if action == "batch_upload":
                try:
                    _run_batch_upload_wizard()
                except _BackToMainMenu:
                    continue
                continue

            try:
                plan = _build_publish_plan(action)
                _print_step_separator()
                _render_plan(plan)
                _print_step_separator()
                if not _confirm("Run this workflow?", default=True, allow_back=True):
                    CONSOLE.print("[grey70]Workflow cancelled.[/grey70]")
                    continue
                result = _execute_plan(plan)
                if result == 0:
                    continue

                while result != 0:
                    failure_action = _select_failure_action(action)
                    if failure_action == "main_menu":
                        break

                    plan = _build_publish_plan(action)
                    _print_step_separator()
                    _render_plan(plan)
                    _print_step_separator()
                    if not _confirm("Run this workflow?", default=True, allow_back=True):
                        CONSOLE.print("[grey70]Workflow cancelled.[/grey70]")
                        break
                    result = _execute_plan(plan)
                    if result == 0:
                        break
            except _BackToMainMenu:
                continue
    except KeyboardInterrupt:
        CONSOLE.print("[grey70]Exited publisher wizard.[/grey70]")
        return 0


def _flow_options() -> list[tuple[str, Action]]:
    options: list[tuple[str, Action]] = [
        ("Publish to registry", "publish"),
        ("Inspect", "inspect"),
    ]
    if _default_admin_token():
        options.append(("Admin batch upload", "batch_upload"))
    options.extend(
        [
            ("Help", "help"),
            ("Exit", "exit"),
        ]
    )
    return options


def _flow_descriptions() -> dict[Action, str]:
    descriptions: dict[Action, str] = {
        "inspect": "Run the full pipeline and show the evaluation report.",
        "publish": "Run all gates, build the bundle, and upload to the registry.",
        "batch_upload": "Admin token detected; upload every skill in a directory.",
        "help": "Show what each publisher phase does.",
        "exit": "Leave the publisher wizard.",
    }
    return descriptions


def _render_header() -> None:
    CONSOLE.print(Text(WORDMARK, style=THEME.text_primary))
    CONSOLE.print(
        f"Aptitude Publisher {_publisher_cli_version()} - "
        "Review-first CLI for validating and publishing skills."
    )
    CONSOLE.print("─" * CONSOLE.width, style=THEME.border_secondary)
    CONSOLE.print()


def _render_step_separator(width: int, stream: object = sys.stdout) -> str:
    """Render the shared separator used between wizard steps."""

    glyph = "─" if _stream_supports_text(stream, "─") else "-"
    return glyph * max(1, width)


def _print_step_separator() -> None:
    """Print one blank-line-separated divider between wizard steps."""

    CONSOLE.file.write(f"\n{_render_step_separator(CONSOLE.size.width, sys.stdout)}\n\n")
    flush = getattr(CONSOLE.file, "flush", None)
    if callable(flush):
        flush()


def _panel_box_for_stream(stream: object) -> box.Box:
    """Return the best available Rich box style for the current stream."""

    if _stream_supports_text(stream, "╭╮╰╯│─"):
        return box.ROUNDED
    return box.ASCII


def _table_box_for_stream(stream: object) -> box.Box:
    """Return a light table box that does not compete with panel frames."""

    if _stream_supports_text(stream, "─"):
        return box.SIMPLE_HEAD
    return box.ASCII2


def _frame(
    renderable: Any,
    *,
    title: str,
    border_style: str | None = None,
    subtitle: str | None = None,
) -> Panel:
    """Render one resolver-style publisher frame."""

    return Panel(
        renderable,
        title=Text(title, style=THEME.text_primary),
        subtitle=subtitle,
        border_style=border_style or THEME.border_secondary,
        box=_panel_box_for_stream(sys.stdout),
        padding=(1, 1),
    )


def _render_help() -> None:
    rows = [
        ("Discovery", "Finds the skill root and reads the files that form the skill."),
        ("Identity", "Derives the registry slug, version, namespace, and publish intent."),
        ("Metadata", "Collects public skill facts and fills generated estimates."),
        ("Validation", "Checks the skill against the Anthropic SKILL.md contract."),
        ("Security", "Uses LLM Guard as the authoritative skill security gate."),
        ("Performance", "Uses Upskill as the source for performance and token efficiency."),
        ("Ranking", "Combines gate outputs into the final publish decision."),
        ("Compression", "Builds the immutable bundle that the client later installs."),
        ("Delivery", "Uploads the approved bundle and metadata to the registry."),
    ]
    table = Table(
        show_header=True,
        header_style=THEME.text_muted,
        border_style=THEME.border_primary,
        box=_table_box_for_stream(sys.stdout),
        expand=True,
    )
    table.add_column("Phase", style=THEME.text_primary, no_wrap=True)
    table.add_column("What it means", style=THEME.text_body)
    for phase, meaning in rows:
        table.add_row(phase, meaning)
    CONSOLE.print(_frame(table, title="Publisher Flow"))


def _build_publish_plan(action: Action) -> PublishPlan:
    skills = _discover_skills(Path.cwd())
    _print_step_separator()
    skill = _select_skill(skills)
    if skill is None:
        skill_path = _prompt_path("Skill folder")
        skill = _read_menu_skill(skill_path / "SKILL.md")
    else:
        skill_path = skill.path

    default_intent = _normalize_intent(skill.intent if skill is not None else None)

    if action == "publish":
        _print_step_separator()
        intent = _select(
            "Publish intent",
            [
                ("Create new skill", "create_skill"),
                ("Publish new version", "publish_version"),
            ],
            default=default_intent,
            descriptions={
                "create_skill": "Use this when the registry does not have the skill yet.",
                "publish_version": "Use this when the slug already exists and this is a new version.",
            },
            allow_back=True,
        )
        _print_step_separator()
    else:
        intent = default_intent
    scan_profile = _select(
        "Inspection depth",
        [
            ("Fast scan", "fast"),
            ("Full scan", "slow"),
        ],
        default="fast",
        descriptions={
            "fast": "Use quicker checks for local iteration.",
            "slow": "Use broader checks for deeper review.",
        },
        allow_back=True,
    )

    return PublishPlan(
        action=action,
        skill_path=skill_path.resolve(),
        slug=None,
        intent=intent,
        trust_tier="untrusted",
        namespace="public",
        artifact_origin="internal",
        policy_pack_slug=None,
        publisher_identity=None,
        scan_profile=scan_profile,
    )


def _discover_skills(root: Path) -> list[MenuSkill]:
    """Find skill folders under the project root and nested catalog folders."""

    skills: list[MenuSkill] = []
    skill_files = {
        *root.glob("*/SKILL.md"),
        *root.glob("skills/**/SKILL.md"),
    }
    for skill_file in sorted(skill_files):
        if any(part.startswith(".") for part in skill_file.parts):
            continue
        skill = _read_menu_skill(skill_file)
        if skill is not None:
            skills.append(skill)
    return skills


def _read_menu_skill(skill_file: Path) -> MenuSkill | None:
    if not skill_file.is_file():
        return None

    frontmatter = _read_frontmatter(skill_file)
    metadata = frontmatter.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    return MenuSkill(
        path=skill_file.parent,
        name=str(frontmatter.get("name") or skill_file.parent.name),
        version=str(metadata.get("version") or "1.0.0"),
        intent=str(metadata.get("intent") or "create_skill"),
    )


def _select_skill(skills: list[MenuSkill]) -> MenuSkill | None:
    if not skills:
        return None

    source = _select(
        "Skill source",
        [
            ("Choose from local skills", "local"),
            ("Upload from path", "path"),
        ],
        descriptions={
            "local": "Select a skill discovered in this publisher workspace.",
            "path": "Enter a skill folder outside the local list.",
        },
        allow_back=True,
    )
    if source == "path":
        return None

    options: list[tuple[str, MenuSkill | None]] = [
        (f"{skill.name} ({skill.version})", skill) for skill in skills
    ]
    _print_step_separator()
    return _select(
        "Local skill",
        options,
        descriptions={
            skill: str(skill.path)
            for skill in skills
        },
        allow_back=True,
    )


def _select_failure_action(action: Action) -> FailureAction:
    retry_label = "Inspect another skill" if action == "inspect" else "Upload another skill"
    retry_description = (
        "Start a new inspection workflow with a different skill."
        if action == "inspect"
        else "Start a new publish workflow with a different skill."
    )
    CONSOLE.print()
    return _select(
        "Workflow failed",
        [
            (retry_label, "upload_another"),
            ("Back to main menu", "main_menu"),
        ],
        subtitle="Choose what to do next.",
        descriptions={
            "upload_another": retry_description,
            "main_menu": "Return to the first menu.",
        },
        allow_back=True,
    )


def _run_batch_upload_wizard() -> int:
    admin_token = _default_admin_token()
    if not admin_token:
        CONSOLE.print(
            _frame(
                "Set APTITUDE_ADMIN_TOKEN, APTITUDE_REGISTRY_ADMIN_TOKEN, "
                "or REGISTRY_ADMIN_TOKEN to enable admin batch upload.",
                title="Admin Batch Upload",
                border_style="yellow",
            )
        )
        return 1

    _print_step_separator()
    skills_directory = _prompt_directory("Skills directory")
    skills = _discover_skills(skills_directory)
    if not skills:
        CONSOLE.print(
            _frame(
                f"No skill folders with SKILL.md were found under {skills_directory}.",
                title="Admin Batch Upload",
                border_style="yellow",
            )
        )
        return 1

    skill_paths = [str(skill.path.resolve()) for skill in skills]

    args = argparse.Namespace(
        skill_paths=skill_paths,
        intent=None,
        trust_tier="verified",
        namespace="public",
        artifact_origin="verified",
        policy_pack_slug=None,
        publisher_identity=None,
        registry_url=_default_registry_url(),
        admin_token=admin_token,
        concurrency=4,
        dry_run=False,
        scan_profile="fast",
    )
    return _run_admin_batch_upload(args)


def _render_batch_upload_plan(*, skills_directory: Path, skill_paths: Sequence[str]) -> None:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style=THEME.text_muted, no_wrap=True)
    table.add_column(style=THEME.text_body)
    table.add_row("Action", "Admin batch upload")
    table.add_row("Directory", str(skills_directory.resolve()))
    table.add_row("Skills", str(len(skill_paths)))
    table.add_row("Concurrency", "4")
    table.add_row("Registry", _default_registry_url())
    CONSOLE.print(_frame(table, title="Admin Batch Upload Plan"))
    CONSOLE.print()


def _execute_plan(plan: PublishPlan) -> int:
    publish_token = _default_publish_token()
    if plan.action == "publish" and not publish_token:
        CONSOLE.print(
            _frame(
                _missing_publish_token_message(),
                title="Missing publish token",
                border_style="red",
            )
        )
        return 1

    if plan.action == "publish" and _render_existing_slug_preflight_block_if_needed(
        plan=plan,
        token=_relationship_check_token(publish_token),
    ):
        return 1

    with _activity("Running publisher pipeline"):
        context = _run_pipeline(plan)
    _render_pipeline_report(context)

    if plan.action == "inspect":
        return 0 if _publish_payload_ready(context) and context.ranking.publish_decision != "block" else 1

    if not _publish_payload_ready(context) or context.ranking.publish_decision == "block":
        reasons = _format_gate_failures(context)
        CONSOLE.print(
            _frame(
                "[red]Publish blocked before registry upload.[/red]"
                + (f"\n\n{reasons}" if reasons else ""),
                title="Publish Decision",
                border_style="red",
            )
        )
        return 1

    lookup_token = _relationship_check_token(publish_token)
    if _render_existing_slug_block_if_needed(context=context, token=lookup_token):
        return 1

    try:
        with _activity("Building compressed bundle"):
            bundle_bytes = build_bundle_bytes(context)
    except RuntimeError as exc:
        CONSOLE.print(
            _frame(
                f"[red]{exc}[/red]",
                title="Bundle creation failed",
                border_style="red",
            )
        )
        return 1

    _render_bundle(context, bundle_bytes)
    _render_relationship_alerts(context)
    if not _confirm("Upload this skill to the registry?", default=False, allow_back=True):
        CONSOLE.print("[grey70]Upload cancelled.[/grey70]")
        return 0

    with _activity("Uploading bundle to registry"):
        result = publish_to_registry(
            registry_url=_default_registry_url(),
            token=publish_token,
            context=context,
            bundle_bytes=bundle_bytes,
        )
    _render_registry_result(result)
    if 200 <= result.status_code < 300:
        _print_step_separator()
    return 0 if 200 <= result.status_code < 300 else 1


def _run_pipeline(plan: PublishPlan) -> PublishContext:
    pipeline = PublisherPipeline()
    context = pipeline.create_context(
        file_path=str(plan.skill_path),
        slug_override=plan.slug,
        version_override=None,
        intent_override=plan.intent,
        trust_tier=plan.trust_tier,
        namespace=plan.namespace,
        artifact_origin=plan.artifact_origin,
        policy_pack_slug=plan.policy_pack_slug,
        publisher_identity=plan.publisher_identity,
    )
    with _scan_profile_environment(plan.scan_profile, upskill_verbose=True):
        return pipeline.run(context)


def _render_plan(plan: PublishPlan) -> None:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style=THEME.text_muted, no_wrap=True)
    table.add_column(style=THEME.text_body)
    table.add_row("Action", _action_label(plan.action))
    table.add_row("CLI version", _publisher_cli_version())
    table.add_row("Skill", plan.skill_path.name)
    table.add_row("Skill version", "resolved during inspection")
    if plan.action == "publish":
        table.add_row("Intent", plan.intent)
    table.add_row("Inspection depth", _scan_profile_label(plan.scan_profile))
    table.add_row("Trust", plan.trust_tier)
    table.add_row("Namespace", plan.namespace)
    table.add_row("Origin", plan.artifact_origin)
    if plan.slug:
        table.add_row("Slug override", plan.slug)
    if plan.policy_pack_slug:
        table.add_row("Policy pack", plan.policy_pack_slug)
    if plan.publisher_identity:
        table.add_row("Publisher", plan.publisher_identity)
    CONSOLE.print(_frame(table, title="Publish Plan"))
    CONSOLE.print()


def _render_pipeline_report(context: PublishContext) -> None:
    sections = _report_detail_sections(context)
    for index, (title, rows) in enumerate(sections):
        if index:
            _print_step_separator()
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(style=THEME.text_muted, no_wrap=True)
        table.add_column(style=THEME.text_body)
        for label, value in rows:
            table.add_row(label, value)
        CONSOLE.print(_frame(table, title=title))


def _render_bundle(context: PublishContext, bundle_bytes: bytes) -> None:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style=THEME.text_muted, no_wrap=True)
    table.add_column(style=THEME.text_body)
    table.add_row("Path root", str(context.inventory.skill_root))
    table.add_row("Bundle size", f"{len(bundle_bytes)} bytes")
    table.add_row("Registry slug", context.identity.slug)
    table.add_row("Registry version", context.identity.version)
    CONSOLE.print(_frame(table, title="Bundle"))


def _render_registry_result(result) -> None:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style=THEME.text_muted, no_wrap=True)
    table.add_column(style=THEME.text_body)
    for label, value in _registry_result_lines(result):
        table.add_row(label.title(), value)
    border_style = "green" if 200 <= result.status_code < 300 else "red"
    CONSOLE.print(_frame(table, title="Registry Result", border_style=border_style))


def _render_existing_slug_block_if_needed(
    *,
    context: PublishContext,
    token: str | None,
) -> bool:
    return _render_existing_slug_block(
        slug=context.identity.slug,
        intent=context.identity.intent,
        token=token,
    )


def _render_existing_slug_preflight_block_if_needed(
    *,
    plan: PublishPlan,
    token: str | None,
) -> bool:
    identity = _preflight_identity_from_skill_path(
        skill_path=str(plan.skill_path),
        slug_override=plan.slug,
        intent_override=plan.intent,
    )
    return _render_existing_slug_block(
        slug=identity.slug,
        intent=identity.intent,
        token=token,
    )


def _render_existing_slug_block(
    *,
    slug: str | None,
    intent: str | None,
    token: str | None,
) -> bool:
    block = _check_existing_slug_block(
        registry_url=_default_registry_url(),
        token=token,
        slug=slug,
        intent=intent,
    )
    if block is None:
        return False

    if block.existing_skill is None:
        CONSOLE.print(
            _frame(
                block.message,
                title="Existing Slug Check",
                border_style="red",
            )
        )
        return True

    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style=THEME.text_muted, no_wrap=True)
    table.add_column(style=THEME.text_body)
    for label, value in _existing_skill_lines(block.existing_skill):
        table.add_row(label.title(), value)
    CONSOLE.print(
        _frame(
            table,
            title="Existing Skill Found",
            subtitle="Create new skill is blocked; reuse this skill or publish a new version.",
            border_style="red",
        )
    )
    return True


def _render_relationship_alerts(context: PublishContext) -> None:
    relationships = context.delivery_payload.relationships
    if not _has_relationships(relationships):
        return

    token = _relationship_check_token(_default_publish_token())
    if not token:
        CONSOLE.print(
            _frame(
                "Skipped relationship existence check. Set APTITUDE_READ_TOKEN, "
                "REGISTRY_READ_TOKEN, or a publish token with read scope.",
                title="Relationship Alerts",
                border_style="yellow",
            )
        )
        return

    issues = check_relationship_references(
        registry_url=_default_registry_url(),
        token=token,
        relationships=relationships,
    )
    if not issues:
        CONSOLE.print(
            _frame(
                "All referenced relationship targets were found.",
                title="Relationship Alerts",
            )
        )
        return

    CONSOLE.print(
        _frame(
            "\n".join(_relationship_alert_lines(issues)),
            title="Relationship Alerts",
            border_style="yellow",
        )
    )


def _has_relationships(relationships: dict[str, object]) -> bool:
    for value in relationships.values():
        if isinstance(value, list) and value:
            return True
    return False


def _format_gate_failures(context: PublishContext) -> str:
    lines: list[str] = []
    for gate in context.gate_history:
        if gate.passed:
            continue
        lines.append(f"{gate.gate_name}: {gate.explanation or 'failed'}")
    return "\n".join(lines)


def _publish_payload_ready(context: PublishContext) -> bool:
    """Return true only after delivery built the registry contract payload."""
    payload = context.delivery_payload
    return bool(
        payload.slug
        and payload.version
        and payload.intent
        and payload.metadata.get("name")
        and payload.governance
    )


def _select(
    title: str,
    options: Sequence[tuple[str, T]],
    *,
    default: T | None = None,
    descriptions: dict[T, str] | None = None,
    subtitle: str | None = None,
    allow_back: bool = False,
) -> T:
    if not options:
        raise ValueError("options cannot be empty")

    default_index = 0
    if default is not None:
        for index, (_, value) in enumerate(options):
            if value == default:
                default_index = index
                break

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _prompt_select(
            title,
            options,
            default_index=default_index,
            subtitle=subtitle,
            allow_back=allow_back,
        )

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style
    except ModuleNotFoundError:
        return _prompt_select(
            title,
            options,
            default_index=default_index,
            subtitle=subtitle,
            allow_back=allow_back,
        )

    state = {"index": default_index}

    def render_menu() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = [("class:title", f"{title}\n")]
        if subtitle:
            fragments.append(("class:subtitle", f"{subtitle}\n"))
        fragments.append(("", "\n"))
        active_description = None
        if descriptions is not None:
            active_description = descriptions.get(options[state["index"]][1])
        for option_index, (label, _) in enumerate(options):
            is_active = option_index == state["index"]
            marker = "●" if is_active else "○"
            marker_style = "class:marker-active" if is_active else "class:item"
            label_style = "class:active" if is_active else "class:item"
            fragments.append((marker_style, f"{marker} "))
            fragments.append((label_style, label))
            if is_active and active_description:
                fragments.append(("class:detail", f" - {active_description}"))
            fragments.append(("", "\n"))
        hint = "\n[↑↓] move  [enter] confirm"
        if allow_back:
            hint += "  [b] back to main"
        hint += "  [q] cancel\n\n"
        fragments.append(("class:hint", hint))
        return fragments

    control = FormattedTextControl(render_menu, focusable=True)
    bindings = KeyBindings()

    @bindings.add("up")
    def _move_up(event) -> None:
        state["index"] = (state["index"] - 1) % len(options)
        event.app.invalidate()

    @bindings.add("down")
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

    if allow_back:
        @bindings.add("b")
        def _back_to_main(event) -> None:
            event.app.exit(exception=_BackToMainMenu())

    application: Application[T] = Application(
        layout=Layout(HSplit([Window(control, always_hide_cursor=True)])),
        key_bindings=bindings,
        mouse_support=False,
        full_screen=False,
        style=Style.from_dict(
            {
                "title": "bold #ffffff",
                "subtitle": "#d8d8d8",
                "item": "#b8b8b8",
                "active": "bold #ffffff",
                "marker-active": f"bold {THEME.accent}",
                "hint": "#7a7a7a",
                "detail": "#d8d8d8",
            }
        ),
    )
    return application.run()


def _prompt_select(
    title: str,
    options: Sequence[tuple[str, T]],
    *,
    default_index: int,
    subtitle: str | None = None,
    allow_back: bool = False,
) -> T:
    CONSOLE.print(f"[bold]{title}[/bold]")
    if subtitle:
        CONSOLE.print(subtitle)
        CONSOLE.print()
    if allow_back:
        CONSOLE.print("0. Back to main menu")
    for index, (label, _) in enumerate(options, start=1):
        CONSOLE.print(f"{index}. {label}")
    while True:
        raw = CONSOLE.input(f"Select [{default_index + 1}]: ").strip()
        if allow_back and raw.lower() in {"0", "b", "back"}:
            raise _BackToMainMenu()
        if not raw:
            return options[default_index][1]
        try:
            selected = int(raw)
        except ValueError:
            CONSOLE.print("[yellow]Enter a number.[/yellow]")
            continue
        if 1 <= selected <= len(options):
            return options[selected - 1][1]
        CONSOLE.print("[yellow]Selection out of range.[/yellow]")


def _prompt_text(label: str, *, default: str) -> str:
    raw = CONSOLE.input(f"{label} [{default}]: ").strip()
    return raw or default


def _optional_text(label: str, *, default: str) -> str | None:
    raw = CONSOLE.input(f"{label} [{default or 'none'}]: ").strip()
    return raw or None


def _prompt_path(label: str) -> Path:
    while True:
        raw = CONSOLE.input(f"{label}: ").strip()
        if not raw:
            CONSOLE.print("[yellow]Enter a path.[/yellow]")
            continue
        path = Path(raw).expanduser()
        if path.exists():
            return path
        CONSOLE.print("[yellow]Path does not exist.[/yellow]")


def _prompt_directory(label: str) -> Path:
    while True:
        path = _prompt_path(label)
        if path.is_dir():
            return path
        CONSOLE.print("[yellow]Path is not a directory.[/yellow]")


def _confirm(label: str, *, default: bool, allow_back: bool = False) -> bool:
    return _select(
        label,
        [
            ("Yes", True),
            ("No", False),
        ],
        default=default,
        allow_back=allow_back,
    )


@contextmanager
def _activity(label: str):
    if not sys.stderr.isatty():
        yield
        return
    with Progress(
        SpinnerColumn(style=THEME.accent),
        TextColumn("[white]{task.description}"),
        BarColumn(
            bar_width=28,
            complete_style=THEME.accent,
            finished_style=THEME.accent,
        ),
        transient=True,
        console=Console(stderr=True),
    ) as progress:
        task = progress.add_task(label, total=100)
        progress.advance(task, 20)
        yield
        progress.advance(task, 80)


def _can_use_key_menu() -> bool:
    return (
        termios is not None
        and tty is not None
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and _stream_supports_text(sys.stdout, "●○╭╮╰╯")
    )


def _read_key() -> str:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        first = sys.stdin.read(1)
        if first == "\x1b":
            second = sys.stdin.read(1)
            third = sys.stdin.read(1)
            return first + second + third
        return first
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _clear_previous_menu(option_count: int, has_descriptions: bool) -> None:
    line_count = 2 + option_count + (1 if has_descriptions else 0)
    CONSOLE.print(f"\x1b[{line_count}F\x1b[0J", end="")


def _stream_supports_text(stream: object, text: str) -> bool:
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return True
    try:
        text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _normalize_intent(value: str | None) -> Intent:
    if value == "publish_version":
        return "publish_version"
    return "create_skill"


def _action_label(action: Action) -> str:
    labels = {
        "inspect": "Inspect",
        "publish": "Publish to registry",
        "help": "Help",
        "exit": "Exit",
    }
    return labels[action]


def _scan_profile_label(profile: ScanProfile) -> str:
    if profile == "fast":
        return "Fast scan"
    return "Full scan"


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
