"""Interactive wizard for publishing skills through the Aptitude registry."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from typing import Any, Literal, Sequence, TypeVar

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from publisher.artifacts.bundle import build_bundle_bytes
from publisher.app.cli import (
    _default_publish_token,
    _default_registry_url,
    _load_local_env_defaults,
)
from publisher.app.pipeline import PublisherPipeline
from publisher.domain.models import PublishContext
from publisher.registry.client import publish_to_registry

try:
    import termios
    import tty
except ModuleNotFoundError:  # pragma: no cover - platform fallback
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]


Action = Literal["inspect", "publish", "help", "exit"]
Intent = Literal["create_skill", "publish_version"]
ScanProfile = Literal["fast", "slow"]
TrustTier = Literal["untrusted", "internal", "verified"]
ArtifactOrigin = Literal["internal", "imported", "verified", "restricted"]
T = TypeVar("T")

CONSOLE = Console()
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
WORDMARK = (
    "\n"
    "   ______          __        Publisher\n"
    "  /\\  _  \\        /\\ \\__     \n"
    "  \\ \\ \\L\\ \\  _____\\ \\ ,_\\    \n"
    "   \\ \\  __ \\/\\ '__`\\ \\ \\/    \n"
    "    \\ \\ \\/\\ \\ \\ \\L\\ \\ \\ \\_  \n"
    "     \\ \\_\\ \\_\\ \\ ,__/\\ \\__\\ \n"
    "      \\/_/\\/_/\\ \\ \\/  \\/__/ \n"
    "               \\ \\_\\         \n"
    "                \\/_/         \n"
)


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
    version: str
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

    while True:
        action = _select(
            "What do you want to do?",
            [
                ("Full inspect", "inspect"),
                ("Publish to registry", "publish"),
                ("Help", "help"),
                ("Exit", "exit"),
            ],
            descriptions={
                "inspect": "Run the full pipeline and show the evaluation report.",
                "publish": "Run all gates, build the bundle, and upload to the registry.",
                "help": "Show what each publisher phase does.",
                "exit": "Leave the publisher wizard.",
            },
        )
        if action == "help":
            _render_help()
            continue
        if action == "exit":
            CONSOLE.print("[grey70]No publish workflow was started.[/grey70]")
            return 0

        plan = _build_publish_plan(action)
        _render_plan(plan)
        if not _confirm("Run this workflow?", default=True):
            CONSOLE.print("[grey70]Workflow cancelled.[/grey70]")
            continue
        return _execute_plan(plan)


def _render_header() -> None:
    registry_url = _default_registry_url()
    CONSOLE.print(Text(WORDMARK, style="bold white"))
    summary = Table.grid(expand=True, padding=(0, 2))
    summary.add_column(style="grey70")
    summary.add_column(style="white")
    summary.add_row("Registry", registry_url)
    summary.add_row("Mode", "guided publish workflow")
    CONSOLE.print(
        Panel(
            summary,
            title="Aptitude Publisher",
            border_style="grey35",
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )


def _render_help() -> None:
    rows = [
        ("Discovery", "Finds the skill root and reads the files that form the skill."),
        ("Identity", "Derives the registry slug, version, namespace, and publish intent."),
        ("Metadata", "Collects public skill facts and fills generated estimates."),
        ("Validation", "Checks the skill against the Anthropic SKILL.md contract."),
        ("Security", "Uses NVIDIA Garak as the authoritative security gate."),
        ("Performance", "Uses Upskill as the source for performance and token efficiency."),
        ("Ranking", "Combines gate outputs into the final publish decision."),
        ("Compression", "Builds the immutable bundle that the client later installs."),
        ("Delivery", "Uploads the approved bundle and metadata to the registry."),
    ]
    table = Table(
        show_header=True,
        header_style="grey70",
        border_style="grey35",
        box=box.ROUNDED,
        expand=True,
    )
    table.add_column("Phase", style="bold white", no_wrap=True)
    table.add_column("What it means", style="white")
    for phase, meaning in rows:
        table.add_row(phase, meaning)
    CONSOLE.print(Panel(table, title="Publisher Flow", border_style="grey35"))


def _build_publish_plan(action: Action) -> PublishPlan:
    skills = _discover_skills(Path.cwd())
    skill = _select_skill(skills)
    default_version = skill.version if skill is not None else "1.0.0"
    default_intent = _normalize_intent(skill.intent if skill is not None else None)
    skill_path = skill.path if skill is not None else _prompt_path("Skill folder")

    version = _prompt_semver("Version", default=default_version)
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
    )
    scan_profile = _select(
        "Inspection depth",
        [
            ("Fast scan", "fast"),
            ("Full scan", "slow"),
        ],
        default="fast",
        descriptions={
            "fast": "Use faster Garak/Upskill settings for local iteration.",
            "slow": "Use broader Garak/Upskill behavior for deeper review.",
        },
    )

    return PublishPlan(
        action=action,
        skill_path=skill_path.resolve(),
        slug=None,
        version=version,
        intent=intent,
        trust_tier="untrusted",
        namespace="public",
        artifact_origin="internal",
        policy_pack_slug=None,
        publisher_identity=None,
        scan_profile=scan_profile,
    )


def _discover_skills(root: Path) -> list[MenuSkill]:
    """Find skill folders under the project root."""

    skills: list[MenuSkill] = []
    skill_files = {*root.glob("*/SKILL.md"), *root.glob("skills/*/SKILL.md")}
    for skill_file in sorted(skill_files):
        if skill_file.parent.name.startswith("."):
            continue
        frontmatter = _read_frontmatter(skill_file)
        metadata = frontmatter.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        skills.append(
            MenuSkill(
                path=skill_file.parent,
                name=str(frontmatter.get("name") or skill_file.parent.name),
                version=str(metadata.get("version") or "1.0.0"),
                intent=str(metadata.get("intent") or "create_skill"),
            )
        )
    return skills


def _select_skill(skills: list[MenuSkill]) -> MenuSkill | None:
    options: list[tuple[str, MenuSkill | None]] = [
        (f"{skill.name} ({skill.version})", skill) for skill in skills
    ]
    options.append(("Enter a skill path manually", None))
    return _select(
        "Skill source",
        options,
        descriptions={
            skill: str(skill.path)
            for skill in skills
        }
        | {None: "Use this when the skill lives outside the publisher repo."},
    )


def _execute_plan(plan: PublishPlan) -> int:
    with _activity("Running publisher pipeline"):
        context = _run_pipeline(plan)
    _render_pipeline_report(context)

    if plan.action == "inspect":
        return 0 if context.ranking.publish_decision != "block" else 1

    if context.ranking.publish_decision == "block":
        reasons = _format_gate_failures(context)
        CONSOLE.print(
            Panel(
                "[red]Publish blocked.[/red]"
                + (f"\n\n{reasons}" if reasons else ""),
                title="Publish Decision",
                border_style="red",
            )
        )
        return 1

    try:
        with _activity("Building compressed bundle"):
            bundle_bytes = build_bundle_bytes(context)
    except RuntimeError as exc:
        CONSOLE.print(
            Panel(
                f"[red]{exc}[/red]",
                title="Bundle creation failed",
                border_style="red",
            )
        )
        return 1

    _render_bundle(context, bundle_bytes)
    if not _confirm("Upload this skill to the registry?", default=False):
        CONSOLE.print("[grey70]Upload cancelled.[/grey70]")
        return 0

    token = _default_publish_token()
    if not token:
        CONSOLE.print(
            Panel(
                "Set APTITUDE_PUBLISH_TOKEN or PUBLISH_TOKEN before publishing.",
                title="Missing publish token",
                border_style="red",
            )
        )
        return 1

    with _activity("Uploading bundle to registry"):
        result = publish_to_registry(
            registry_url=_default_registry_url(),
            token=token,
            context=context,
            bundle_bytes=bundle_bytes,
        )
    _render_registry_result(result)
    return 0 if 200 <= result.status_code < 300 else 1


def _run_pipeline(plan: PublishPlan) -> PublishContext:
    pipeline = PublisherPipeline()
    context = pipeline.create_context(
        file_path=str(plan.skill_path),
        slug_override=plan.slug,
        version_override=plan.version,
        intent_override=plan.intent,
        trust_tier=plan.trust_tier,
        namespace=plan.namespace,
        artifact_origin=plan.artifact_origin,
        policy_pack_slug=plan.policy_pack_slug,
        publisher_identity=plan.publisher_identity,
    )
    with _scan_profile_environment(plan.scan_profile):
        return pipeline.run(context)


def _render_plan(plan: PublishPlan) -> None:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style="grey70", no_wrap=True)
    table.add_column(style="white")
    table.add_row("Action", _action_label(plan.action))
    table.add_row("Skill", str(plan.skill_path))
    table.add_row("Version", plan.version)
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
    CONSOLE.print(Panel(table, title="Publish Plan", border_style="grey35"))


def _render_pipeline_report(context: PublishContext) -> None:
    summary = Table.grid(expand=True, padding=(0, 2))
    summary.add_column(style="grey70", no_wrap=True)
    summary.add_column(style="white")
    summary.add_row("Skill path", str(context.inventory.skill_root))
    summary.add_row("Slug", context.identity.slug)
    summary.add_row("Version", context.identity.version)
    summary.add_row("Intent", context.identity.intent)
    summary.add_row("Trust tier", context.source.trust_tier)
    summary.add_row("Namespace", context.source.namespace)
    summary.add_row("Artifact origin", context.source.artifact_origin)

    evaluation = Table(
        show_header=True,
        header_style="grey70",
        border_style="grey35",
        box=box.ROUNDED,
        expand=True,
    )
    evaluation.add_column("Signal", style="grey70")
    evaluation.add_column("Value", style="white")
    evaluation.add_row("Validation", "passed" if context.validation.passed else "failed")
    evaluation.add_row("Security score", str(context.security.score))
    evaluation.add_row("Security gate", context.security.decision)
    evaluation.add_row("Performance", str(context.performance_exam.score))
    evaluation.add_row("Lift", str(context.performance_exam.skill_lift))
    evaluation.add_row("Token delta", str(context.performance_exam.token_delta))
    evaluation.add_row("Ranking", context.ranking.label)
    evaluation.add_row("Publish decision", context.ranking.publish_decision)

    stages = Table(
        show_header=True,
        header_style="grey70",
        border_style="grey35",
        box=box.ROUNDED,
        expand=True,
    )
    stages.add_column("Stage", style="white")
    stages.add_column("Status", style="grey70")
    for snapshot in context.stage_history:
        stages.add_row(snapshot.stage_name, snapshot.status)

    gates = Table(
        show_header=True,
        header_style="grey70",
        border_style="grey35",
        box=box.ROUNDED,
        expand=True,
    )
    gates.add_column("Gate", style="white")
    gates.add_column("Status", style="grey70", no_wrap=True)
    gates.add_column("Why", style="white")
    for gate in context.gate_history:
        status = "passed" if gate.passed else "failed"
        explanation = gate.explanation or ""
        if gate.blocking_issues:
            explanation = "\n".join(
                [explanation, *[f"Blocking: {issue}" for issue in gate.blocking_issues]]
            ).strip()
        if gate.warnings:
            explanation = "\n".join(
                [explanation, *[f"Warning: {warning}" for warning in gate.warnings]]
            ).strip()
        gates.add_row(gate.gate_name, status, explanation)

    panels: list[Panel] = [
        Panel(summary, title="Skill Identity", border_style="grey35"),
        Panel(evaluation, title="Evaluation Summary", border_style="grey35"),
        Panel(stages, title="Stages", border_style="grey35"),
    ]
    if context.gate_history:
        panels.append(Panel(gates, title="Gate Results", border_style="grey35"))
    if context.validation.errors:
        panels.append(
            Panel(
                "\n".join(f"- {error}" for error in context.validation.errors),
                title="Validation Errors",
                border_style="red",
            )
        )
    if context.security.findings:
        findings = Table(
            show_header=True,
            header_style="grey70",
            border_style="grey35",
            box=box.ROUNDED,
            expand=True,
        )
        findings.add_column("Severity", style="white")
        findings.add_column("Check", style="grey70")
        findings.add_column("Field", style="grey70")
        findings.add_column("Evidence", style="white")
        for finding in context.security.findings:
            findings.add_row(
                str(finding.get("severity", "")),
                str(finding.get("check", "")),
                str(finding.get("field", "")),
                str(finding.get("evidence", "")),
            )
        panels.append(Panel(findings, title="Security Findings", border_style="red"))
    if context.performance_exam.score is not None or context.performance_exam.notes:
        upskill = Table(
            show_header=True,
            header_style="grey70",
            border_style="grey35",
            box=box.ROUNDED,
            expand=True,
        )
        upskill.add_column("Signal", style="grey70")
        upskill.add_column("Value", style="white")
        upskill.add_row("Status", "passed" if context.performance_exam.passed else "not passed")
        upskill.add_row("Score", str(context.performance_exam.score))
        upskill.add_row("Models", ", ".join(context.performance_exam.models_tested) or "none")
        upskill.add_row("Test cases", str(context.performance_exam.test_case_count))
        upskill.add_row("Baseline success", str(context.performance_exam.baseline_success_rate))
        upskill.add_row("Skilled success", str(context.performance_exam.skilled_success_rate))
        upskill.add_row("Skill lift", str(context.performance_exam.skill_lift))
        upskill.add_row("Baseline avg tokens", str(context.performance_exam.baseline_avg_tokens))
        upskill.add_row("Skilled avg tokens", str(context.performance_exam.skilled_avg_tokens))
        upskill.add_row("Token delta", str(context.performance_exam.token_delta))
        upskill.add_row("Efficiency", str(context.performance_exam.efficiency_label))
        upskill.add_row("Token estimate source", str(context.metadata.extra.get("token_estimate_source")))
        explanation = _upskill_explanation(context)
        if explanation:
            upskill.add_row("Explanation", explanation)
        if context.performance_exam.notes:
            upskill.add_row("Notes", "\n".join(context.performance_exam.notes))
        panels.append(Panel(upskill, title="Upskill Findings", border_style="yellow"))
    CONSOLE.print(Group(*panels))


def _upskill_explanation(context: PublishContext) -> str:
    exam = context.performance_exam
    if exam.score is None:
        return "Upskill did not return a scored performance result."
    if exam.skill_lift is not None and exam.skill_lift <= 0:
        return (
            "Upskill scored the run, but the model did not improve with the skill "
            "because baseline and skilled success were the same."
        )
    if exam.token_delta is not None and exam.token_delta > 0:
        return "Upskill scored the run, but the skill used more tokens than the baseline."
    if exam.passed:
        return "Upskill showed measurable performance evidence for the skill."
    return "Upskill scored the run, but its pass criteria were not met."


def _render_bundle(context: PublishContext, bundle_bytes: bytes) -> None:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style="grey70", no_wrap=True)
    table.add_column(style="white")
    table.add_row("Path root", str(context.inventory.skill_root))
    table.add_row("Bundle size", f"{len(bundle_bytes)} bytes")
    table.add_row("Registry slug", context.identity.slug)
    table.add_row("Registry version", context.identity.version)
    CONSOLE.print(Panel(table, title="Bundle", border_style="grey35"))


def _render_registry_result(result) -> None:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style="grey70", no_wrap=True)
    table.add_column(style="white")
    table.add_row("Status", str(result.status_code))
    if result.request_id:
        table.add_row("Request id", result.request_id)
    CONSOLE.print(Panel(table, title="Registry Result", border_style="grey35"))
    CONSOLE.print_json(data=result.body)


@contextmanager
def _scan_profile_environment(profile: ScanProfile):
    """Apply temporary Garak/Upskill settings for the selected inspection depth."""
    if profile == "fast":
        overrides = {
            "GARAK_PROBES": "promptinject.HijackHateHumans",
            "GARAK_GENERATIONS": "1",
            "GARAK_PARALLEL_ATTEMPTS": "4",
            "GARAK_CONFIDENCE_INTERVAL_METHOD": "none",
            "GARAK_SOFT_PROBE_PROMPT_CAP": "8",
            "PUBLISHER_GARAK_TIMEOUT_SECONDS": "90",
            "UPSKILL_USE_DEFAULT_TESTS": "true",
            "PUBLISHER_UPSKILL_TIMEOUT_SECONDS": "120",
        }
    else:
        overrides = {
            "GARAK_PROBES": "promptinject",
            "GARAK_GENERATIONS": "5",
            "GARAK_PARALLEL_ATTEMPTS": "1",
            "GARAK_CONFIDENCE_INTERVAL_METHOD": "bootstrap",
            "GARAK_SOFT_PROBE_PROMPT_CAP": "256",
            "PUBLISHER_GARAK_TIMEOUT_SECONDS": "600",
            "UPSKILL_USE_DEFAULT_TESTS": "false",
            "PUBLISHER_UPSKILL_TIMEOUT_SECONDS": "600",
        }

    previous = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _format_gate_failures(context: PublishContext) -> str:
    lines: list[str] = []
    for gate in context.gate_history:
        if gate.passed:
            continue
        lines.append(f"{gate.gate_name}: {gate.explanation or 'failed'}")
    return "\n".join(lines)


def _select(
    title: str,
    options: Sequence[tuple[str, T]],
    *,
    default: T | None = None,
    descriptions: dict[T, str] | None = None,
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
        return _prompt_select(title, options, default_index=default_index)

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style
    except ModuleNotFoundError:
        return _prompt_select(title, options, default_index=default_index)

    state = {"index": default_index}

    def render_menu() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = [("class:title", f"{title}\n")]
        fragments.append(("class:hint", "Use up/down and Enter.\n\n"))
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
        fragments.append(("class:hint", "\n[q] cancel\n\n"))
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

    application: Application[T] = Application(
        layout=Layout(HSplit([Window(control, always_hide_cursor=True)])),
        key_bindings=bindings,
        mouse_support=False,
        full_screen=False,
        style=Style.from_dict(
            {
                "title": "bold #ffffff",
                "item": "#b8b8b8",
                "active": "bold #ffffff",
                "marker-active": "bold #8fa3ad",
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
) -> T:
    CONSOLE.print(f"[bold]{title}[/bold]")
    for index, (label, _) in enumerate(options, start=1):
        CONSOLE.print(f"{index}. {label}")
    while True:
        raw = CONSOLE.input(f"Select [{default_index + 1}]: ").strip()
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


def _prompt_semver(label: str, *, default: str) -> str:
    while True:
        raw = CONSOLE.input(f"{label} [{default}]: ").strip()
        version = raw or default
        if SEMVER_PATTERN.fullmatch(version):
            return version
        CONSOLE.print("[yellow]Version must be semantic versioning in the form X.Y.Z.[/yellow]")


def _confirm(label: str, *, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = CONSOLE.input(f"{label} [{suffix}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


@contextmanager
def _activity(label: str):
    if not sys.stderr.isatty():
        yield
        return
    with Progress(
        SpinnerColumn(style="#8fa3ad"),
        TextColumn("[white]{task.description}"),
        BarColumn(
            bar_width=28,
            complete_style="#8fa3ad",
            finished_style="#8fa3ad",
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
        "inspect": "Full inspect",
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
