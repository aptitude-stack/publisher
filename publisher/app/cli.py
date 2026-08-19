"""Console CLI for publishing skills through the Aptitude registry."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from publisher.artifacts.bundle import build_bundle_bytes
from publisher.app.pipeline import PublisherPipeline
from publisher.frontmatter import parse_skill_markdown
from publisher.registry.client import (
    ExistingSkill,
    RegistryLookupUnavailable,
    RegistryPublishResult,
    RelationshipCheckIssue,
    check_relationship_references,
    get_existing_skill,
    publish_to_registry,
)


_DEFAULT_REGISTRY_URL = "https://api.aptitude-registry.dev"
_PACKAGE_NAME = "aptitude-publisher"
_DEFAULT_PROG = "aptitude-publisher"
_TEXT_BODY = "white"
_TEXT_MUTED = "grey70"
_BORDER_PRIMARY = "grey27"
_ACCENT = "#8fa3ad"
_PUBLISH_TOKEN_ENV_NAMES = (
    "APTITUDE_PUBLISH_TOKEN",
    "APTITUDE_INTEGRATION_PUBLISH_TOKEN",
    "PUBLISH_TOKEN",
)
_ADMIN_TOKEN_ENV_NAMES = (
    "APTITUDE_ADMIN_TOKEN",
    "APTITUDE_REGISTRY_ADMIN_TOKEN",
    "REGISTRY_ADMIN_TOKEN",
)
_READ_TOKEN_ENV_NAMES = (
    "APTITUDE_READ_TOKEN",
    "APTITUDE_REGISTRY_READ_TOKEN",
    "REGISTRY_READ_TOKEN",
)
ScanProfile = Literal["fast", "slow"]


def main(argv: list[str] | None = None) -> int:
    """Run the publisher CLI."""
    _load_local_env_defaults()
    if argv is None:
        argv = sys.argv[1:]
        prog = Path(sys.argv[0]).name or _DEFAULT_PROG
    else:
        prog = _DEFAULT_PROG

    parser = _build_parser(prog=prog)
    args = parser.parse_args(argv)
    if args.command is None:
        from publisher.app.menu import run_menu

        return run_menu()

    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "publish":
        return _run_publish(args)
    if args.command == "admin-batch-upload":
        return _run_admin_batch_upload(args)
    if args.command == "mcp":
        from publisher.interfaces.mcp.main import main as run_mcp_server

        run_mcp_server()
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _build_parser(prog: str = _DEFAULT_PROG) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Evaluate Aptitude skills and publish them to the registry.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_publisher_cli_version()}",
    )
    subparsers = parser.add_subparsers(dest="command")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="run the publisher pipeline and show evaluation results",
    )
    _add_shared_arguments(inspect_parser)

    publish_parser = subparsers.add_parser(
        "publish",
        help="run the publisher pipeline, build a bundle, and upload to the registry",
    )
    _add_shared_arguments(publish_parser)
    publish_parser.add_argument(
        "--registry-url",
        default=_default_registry_url(),
        help=(
            "registry base URL; defaults to APTITUDE_REGISTRY_URL, "
            "APTITUDE_SERVER_BASE_URL, local APP_PORT, or the public registry"
        ),
    )
    publish_parser.add_argument(
        "--token",
        default=_default_publish_token(),
        help=(
            "registry publish token; defaults to APTITUDE_PUBLISH_TOKEN, "
            "APTITUDE_INTEGRATION_PUBLISH_TOKEN, or PUBLISH_TOKEN"
        ),
    )
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the full local flow and stop before the API upload",
    )

    batch_parser = subparsers.add_parser(
        "admin-batch-upload",
        help="publish multiple skill folders concurrently with an admin token",
    )
    batch_parser.add_argument("skill_paths", nargs="+", help="paths to skill folders")
    _add_batch_shared_arguments(batch_parser)
    batch_parser.add_argument(
        "--registry-url",
        default=_default_registry_url(),
        help=(
            "registry base URL; defaults to APTITUDE_REGISTRY_URL, "
            "APTITUDE_SERVER_BASE_URL, local APP_PORT, or the public registry"
        ),
    )
    batch_parser.add_argument(
        "--admin-token",
        "--token",
        dest="admin_token",
        default=_default_admin_token(),
        help=(
            "registry admin token; defaults to APTITUDE_ADMIN_TOKEN, "
            "APTITUDE_REGISTRY_ADMIN_TOKEN, or REGISTRY_ADMIN_TOKEN"
        ),
    )
    batch_parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="number of skill uploads to run concurrently; default: 4",
    )
    batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run every local flow and stop before API uploads",
    )
    batch_parser.add_argument(
        "--scan-profile",
        choices=("fast", "full"),
        default="fast",
        help="local scan profile for every skill; default: fast",
    )

    subparsers.add_parser(
        "mcp",
        help="run the local stdio MCP server",
    )

    return parser


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("skill_path", help="path to the skill folder")
    parser.add_argument("--slug", help="override the skill slug for registry publish")
    parser.add_argument("--version", help="override the semantic version for this publish")
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="show detailed structure, risk, and quality summaries (default)",
    )
    _add_publish_metadata_arguments(
        parser,
        default_trust_tier="untrusted",
        default_artifact_origin="internal",
    )


def _add_batch_shared_arguments(parser: argparse.ArgumentParser) -> None:
    _add_publish_metadata_arguments(
        parser,
        default_trust_tier="verified",
        default_artifact_origin="verified",
    )


def _add_publish_metadata_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_trust_tier: str,
    default_artifact_origin: str,
) -> None:
    parser.add_argument(
        "--intent",
        choices=("create_skill", "publish_version"),
        help="override publish intent",
    )
    parser.add_argument(
        "--trust-tier",
        default=default_trust_tier,
        choices=("untrusted", "internal", "verified"),
        help="governance trust tier",
    )
    parser.add_argument("--namespace", default="public", help="target registry namespace")
    parser.add_argument(
        "--artifact-origin",
        default=default_artifact_origin,
        choices=("internal", "imported", "verified", "restricted"),
        help="governance artifact origin",
    )
    parser.add_argument("--policy-pack-slug", help="optional governance policy-pack slug")
    parser.add_argument("--publisher-identity", help="optional provenance publisher identity")


def _run_inspect(args: argparse.Namespace) -> int:
    context = _run_pipeline(args)
    _print_pipeline_report(context, verbose=args.verbose)
    return 0 if _publish_payload_ready(context) and context.ranking.publish_decision != "block" else 1


def _run_publish(args: argparse.Namespace) -> int:
    if not args.dry_run and not _has_env_value(args.token):
        print("\n" + _missing_publish_token_message())
        return 1

    if not args.dry_run and _print_existing_slug_preflight_block_if_needed(
        registry_url=args.registry_url,
        token=_relationship_check_token(args.token),
        skill_path=args.skill_path,
        slug_override=args.slug,
        intent_override=args.intent,
    ):
        return 1

    context = _run_pipeline(args)
    _print_pipeline_report(context, verbose=args.verbose)
    if not _publish_payload_ready(context) or context.ranking.publish_decision == "block":
        print("\nPublish blocked before registry upload.")
        _print_gate_failures(context)
        return 1

    if not args.dry_run and _print_existing_slug_block_if_needed(
        registry_url=args.registry_url,
        token=_relationship_check_token(args.token),
        context=context,
    ):
        return 1

    _print_relationship_alerts(
        registry_url=args.registry_url,
        token=_relationship_check_token(args.token),
        relationships=context.delivery_payload.relationships,
    )

    try:
        bundle_bytes = build_bundle_bytes(context)
    except RuntimeError as exc:
        print(f"\nBundle creation failed: {exc}")
        return 1

    print("\n" + _separator())
    print("Bundle")
    print(_separator())
    print(f"path root      {context.inventory.skill_root}")
    print(f"bundle size    {len(bundle_bytes)} bytes")
    print(f"registry slug  {context.identity.slug}")
    print(f"registry ver   {context.identity.version}")

    if args.dry_run:
        print("\nDry run enabled; upload skipped.")
        return 0

    result = publish_to_registry(
        registry_url=args.registry_url,
        token=args.token,
        context=context,
        bundle_bytes=bundle_bytes,
    )
    print("\n" + _separator())
    print("Registry Result")
    print(_separator())
    for label, value in _registry_result_lines(result):
        print(f"{label:<14} {value}")
    return 0 if 200 <= result.status_code < 300 else 1


@dataclass(frozen=True, slots=True)
class BatchUploadResult:
    """One skill result for admin batch upload summary output."""

    index: int
    path: str
    slug: str | None = None
    version: str | None = None
    status: str = "failed"
    http_status: int | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PreflightIdentity:
    """Minimal skill identity read before running expensive evaluator stages."""

    slug: str | None
    intent: str | None


@dataclass(frozen=True, slots=True)
class ExistingSlugBlock:
    """Reason a create-skill upload cannot continue before full inspection."""

    slug: str
    message: str
    existing_skill: ExistingSkill | None = None


def _run_admin_batch_upload(args: argparse.Namespace) -> int:
    if not args.dry_run and not _has_env_value(args.admin_token):
        print("\n" + _missing_admin_token_message())
        return 1

    concurrency = max(1, min(args.concurrency, len(args.skill_paths)))

    results: list[BatchUploadResult | None] = [None] * len(args.skill_paths)
    scan_profile = _normalize_scan_profile(args.scan_profile)
    with _batch_progress(total=len(args.skill_paths)) as progress:
        with _scan_profile_environment(scan_profile):
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {
                    executor.submit(_upload_one_batch_skill, index, path, args): index
                    for index, path in enumerate(args.skill_paths, start=1)
                }
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # pragma: no cover - defensive boundary
                        result = BatchUploadResult(
                            index=index,
                            path=args.skill_paths[index - 1],
                            status="failed",
                            message=str(exc),
                        )
                    results[index - 1] = result
                    progress.advance(status=result.status)

    completed = [result for result in results if result is not None]
    _print_batch_summary(
        completed,
        requested_count=len(args.skill_paths),
        concurrency=concurrency,
        dry_run=args.dry_run,
        scan_profile=scan_profile,
        trust_tier=args.trust_tier,
        artifact_origin=args.artifact_origin,
    )
    if not completed:
        return 1
    return 0 if all(_batch_result_succeeded(result) for result in completed) else 1


def _upload_one_batch_skill(
    index: int,
    skill_path: str,
    args: argparse.Namespace,
) -> BatchUploadResult:
    if not args.dry_run:
        identity = _preflight_identity_from_skill_path(
            skill_path=skill_path,
            slug_override=None,
            intent_override=args.intent,
        )
        block = _check_existing_slug_block(
            registry_url=args.registry_url,
            token=args.admin_token,
            slug=identity.slug,
            intent=identity.intent,
        )
        if block is not None:
            return BatchUploadResult(
                index=index,
                path=skill_path,
                slug=block.slug,
                status="blocked",
                message=block.message,
            )

    try:
        context = _run_pipeline(args, skill_path=skill_path)
    except Exception as exc:
        return BatchUploadResult(
            index=index,
            path=skill_path,
            status="pipeline_failed",
            message=str(exc),
        )

    slug = context.identity.slug
    version = context.identity.version
    if (
        not _publish_payload_ready(context)
        or context.ranking.publish_decision == "block"
    ):
        return BatchUploadResult(
            index=index,
            path=skill_path,
            slug=slug,
            version=version,
            status="blocked",
            message=_batch_block_message(context),
        )

    if _batch_existing_slug_blocked(args=args, context=context):
        return BatchUploadResult(
            index=index,
            path=skill_path,
            slug=slug,
            version=version,
            status="blocked",
            message="slug already exists",
        )

    try:
        bundle_bytes = build_bundle_bytes(context)
    except RuntimeError as exc:
        return BatchUploadResult(
            index=index,
            path=skill_path,
            slug=slug,
            version=version,
            status="bundle_failed",
            message=str(exc),
        )

    if args.dry_run:
        return BatchUploadResult(
            index=index,
            path=skill_path,
            slug=slug,
            version=version,
            status="ready",
            message=f"bundle {len(bundle_bytes)} bytes",
        )

    result = publish_to_registry(
        registry_url=args.registry_url,
        token=args.admin_token,
        context=context,
        bundle_bytes=bundle_bytes,
    )
    if 200 <= result.status_code < 300:
        return BatchUploadResult(
            index=index,
            path=skill_path,
            slug=slug,
            version=version,
            status="uploaded",
            http_status=result.status_code,
            message=_batch_success_message(result),
        )
    return BatchUploadResult(
        index=index,
        path=skill_path,
        slug=slug,
        version=version,
        status="upload_failed",
        http_status=result.status_code,
        message=_batch_failure_message(result),
    )


def _batch_existing_slug_blocked(*, args: argparse.Namespace, context) -> bool:
    if context.identity.intent != "create_skill" or not context.identity.slug:
        return False
    if not args.admin_token:
        return False
    try:
        existing = get_existing_skill(
            registry_url=args.registry_url,
            token=args.admin_token,
            slug=context.identity.slug,
        )
    except RegistryLookupUnavailable:
        return True
    return _should_block_existing_slug(
        intent=context.identity.intent,
        existing_skill=existing,
    )


def _batch_block_message(context) -> str:
    failed_gates = [gate for gate in context.gate_history if not gate.passed]
    if failed_gates:
        return "; ".join(
            f"{gate.gate_name}: {gate.explanation or 'failed'}" for gate in failed_gates
        )
    return f"publish decision {context.ranking.publish_decision or 'not ready'}"


def _batch_success_message(result: RegistryPublishResult) -> str:
    if result.request_id:
        return f"request {result.request_id}"
    return "accepted"


def _batch_failure_message(result: RegistryPublishResult) -> str:
    body = result.body if isinstance(result.body, dict) else {}
    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message is not None:
            return str(message)
    message = body.get("message")
    if message is not None:
        return str(message)
    return "registry upload failed"


def _print_batch_summary(
    results: list[BatchUploadResult],
    *,
    requested_count: int,
    concurrency: int,
    dry_run: bool,
    scan_profile: ScanProfile,
    trust_tier: str,
    artifact_origin: str,
) -> None:
    console = Console()
    metadata = Table.grid(padding=(0, 2))
    metadata.add_column(style=_TEXT_MUTED, no_wrap=True)
    metadata.add_column(style=_TEXT_BODY)
    metadata.add_row("Skills", str(requested_count))
    metadata.add_row("Concurrency", str(concurrency))
    metadata.add_row("Mode", "dry-run" if dry_run else "upload")
    metadata.add_row("Scan profile", _scan_profile_label(scan_profile))
    metadata.add_row("Trust tier", trust_tier)
    metadata.add_row("Origin", artifact_origin)

    results_table = Table(
        box=box.SIMPLE_HEAD,
        header_style=_TEXT_MUTED,
        border_style=_BORDER_PRIMARY,
        expand=True,
        show_lines=False,
        pad_edge=False,
    )
    results_table.add_column("#", style=_TEXT_MUTED, justify="right", no_wrap=True)
    results_table.add_column("Status", no_wrap=True)
    results_table.add_column("HTTP", style=_TEXT_MUTED, justify="right", no_wrap=True)
    results_table.add_column("Slug", style=_TEXT_BODY, overflow="fold")
    results_table.add_column("Version", style=_TEXT_MUTED, no_wrap=True)
    results_table.add_column("Message", style=_TEXT_BODY, overflow="fold")
    for result in results:
        http_status = "" if result.http_status is None else str(result.http_status)
        slug = result.slug or "-"
        version = result.version or "-"
        message = result.message or result.path
        results_table.add_row(
            str(result.index),
            result.status,
            http_status,
            slug,
            version,
            message,
            style=_batch_status_style(result.status),
        )

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    counts_table = Table.grid(padding=(0, 2))
    counts_table.add_column(style=_TEXT_MUTED, no_wrap=True)
    counts_table.add_column(style=_TEXT_BODY, justify="right")
    for status, count in sorted(counts.items()):
        counts_table.add_row(status, str(count), style=_batch_status_style(status))

    console.print(
        Panel(
            Group(metadata, "", results_table, "", counts_table),
            title="Admin Batch Upload Summary",
            border_style=_BORDER_PRIMARY,
            box=box.ROUNDED,
            expand=True,
        )
    )


def _batch_result_succeeded(result: BatchUploadResult) -> bool:
    return result.status in {"uploaded", "ready"}


def _batch_status_style(status: str) -> str:
    if status in {"uploaded", "ready"}:
        return "green"
    failed_statuses = {
        "blocked",
        "pipeline_failed",
        "bundle_failed",
        "upload_failed",
        "failed",
    }
    if status in failed_statuses:
        return "red"
    return _TEXT_BODY


@contextmanager
def _scan_profile_environment(profile: ScanProfile, *, upskill_verbose: bool = False):
    """Apply temporary LLM Guard/Upskill settings for the selected inspection depth."""
    if profile == "fast":
        overrides = {
            "PUBLISHER_LLM_GUARD_PROMPT_INJECTION_THRESHOLD": "0.90",
            "UPSKILL_USE_DEFAULT_TESTS": "true",
            "PUBLISHER_UPSKILL_TIMEOUT_SECONDS": "120",
        }
    else:
        overrides = {
            "PUBLISHER_LLM_GUARD_PROMPT_INJECTION_THRESHOLD": "0.85",
            "UPSKILL_USE_DEFAULT_TESTS": "false",
            "PUBLISHER_UPSKILL_TIMEOUT_SECONDS": "600",
        }

    if upskill_verbose:
        overrides["PUBLISHER_UPSKILL_VERBOSE"] = "true"

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


def _normalize_scan_profile(value: str) -> ScanProfile:
    if value == "full":
        return "slow"
    if value in {"fast", "slow"}:
        return value
    raise ValueError(f"Unsupported scan profile: {value}")


def _scan_profile_label(profile: ScanProfile) -> str:
    if profile == "fast":
        return "fast"
    return "full"


@contextmanager
def _batch_progress(*, total: int):
    console = Console(stderr=True)
    with Progress(
        SpinnerColumn(style=_ACCENT),
        TextColumn("[white]{task.description}"),
        BarColumn(
            bar_width=28,
            complete_style=_ACCENT,
            finished_style=_ACCENT,
        ),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Running batch upload", total=total)

        class BatchProgress:
            completed = 0

            def advance(self, *, status: str) -> None:
                self.completed += 1
                progress.update(
                    task,
                    description=f"Processed {self.completed}/{total}: {status}",
                    advance=1,
                )

        yield BatchProgress()


def _run_pipeline(args: argparse.Namespace, *, skill_path: str | None = None):
    pipeline = PublisherPipeline()
    resolved_skill_path = skill_path or args.skill_path
    context = pipeline.create_context(
        file_path=str(Path(resolved_skill_path).resolve()),
        slug_override=getattr(args, "slug", None),
        version_override=getattr(args, "version", None),
        intent_override=args.intent,
        trust_tier=args.trust_tier,
        namespace=args.namespace,
        artifact_origin=args.artifact_origin,
        policy_pack_slug=args.policy_pack_slug,
        publisher_identity=args.publisher_identity,
    )
    return pipeline.run(context)


def _print_pipeline_report(context, *, verbose: bool = False) -> None:
    rows = _report_phase_rows(context)
    if not verbose:
        print("Phase      Grade       Details")
        print(_separator())
        for phase, grade, reason in rows:
            print(f"{phase:<10} {grade:<11} {reason}")
        return

    for title, detail_rows in _report_detail_sections(context):
        _print_report_block(title, detail_rows)


def _print_report_block(title: str, rows: list[tuple[str, str]]) -> None:
    print("\n" + _separator())
    print(title)
    print(_separator())
    for label, value in rows:
        print(f"{label:<16} {value}")


def _evaluation_status(context, key: str) -> str:
    value = context.metadata.extra.get(key)
    return str(value.get("status", "not run")) if isinstance(value, dict) else "not run"


def _report_detail_sections(context) -> list[tuple[str, list[tuple[str, str]]]]:
    phase_rows = {phase: (grade, reason) for phase, grade, reason in _report_phase_rows(context)}
    structure_grade, structure_reason = phase_rows["Structure"]
    risk_grade, risk_reason = phase_rows["Risk"]
    quality_grade, quality_reason = phase_rows["Quality"]

    structure_gates = _phase_gates(
        context,
        {"discovery_gate", "identity_gate", "metadata_gate", "validation_gate"},
    )
    structure_issues = list(
        dict.fromkeys(
            [
                *context.validation.errors,
                *(issue for gate in structure_gates for issue in gate.blocking_issues),
            ]
        )
    )
    structure_warnings = list(
        dict.fromkeys(
            [
                *context.validation.warnings,
                *(warning for gate in structure_gates for warning in gate.warnings),
            ]
        )
    )
    structure_rows = [("Status", structure_grade)]
    if context.validation.checks_run:
        structure_rows.append(
            (
                "Validation coverage",
                f"{len(context.validation.checks_run)} checks: skill folder, SKILL.md, "
                "frontmatter, instructions, relationships, LLM contract",
            )
        )
    structure_rows.extend(
        (f"Issue {index}", issue)
        for index, issue in enumerate(structure_issues, start=1)
    )
    structure_rows.extend(
        (f"Warning {index}", warning)
        for index, warning in enumerate(structure_warnings, start=1)
    )
    if structure_grade == "failed" and not structure_issues:
        structure_rows.append(("Issue", structure_reason))

    risk_rows = [
        ("Decision", context.security.decision or risk_grade),
        ("Safety score", _format_score(context.security.score)),
        ("LLM Guard status", _evaluation_status(context, "llm_guard_security")),
        ("Findings", str(len(context.security.findings))),
    ]
    for index, finding in enumerate(context.security.findings, start=1):
        severity = str(finding.get("severity", "unknown"))
        check = str(finding.get("check", "security check"))
        risk_rows.append((f"Finding {index}", f"{severity} · {check}"))
        for label, key in (("Reason", "reason"), ("Location", "field"), ("Evidence", "evidence")):
            value = finding.get(key)
            if value:
                risk_rows.append((f"{label} {index}", str(value)))
    if risk_reason != "No blocking risk found.":
        risk_rows.append(("Summary", risk_reason))

    quality_status = context.metadata.extra.get("upskill_evaluation")
    quality_evidence = quality_status if isinstance(quality_status, dict) else {}
    quality_inconclusive = quality_evidence.get("status") == "inconclusive"
    baseline_success = quality_evidence.get("baseline_success_rate")
    skilled_success = quality_evidence.get("skilled_success_rate")
    lift = quality_evidence.get("skill_lift")
    token_delta = quality_evidence.get("token_delta")
    if lift is None:
        lift = context.performance_exam.skill_lift
    if token_delta is None:
        token_delta = context.performance_exam.token_delta
    quality_rows = [
        ("Upskill status", _evaluation_status(context, "upskill_evaluation")),
        ("Performance score", _format_score(context.performance_exam.score)),
    ]
    if quality_inconclusive:
        baseline_total_tokens = quality_evidence.get("baseline_total_tokens")
        skilled_total_tokens = quality_evidence.get("skilled_total_tokens")
        if isinstance(baseline_total_tokens, int) and isinstance(skilled_total_tokens, int):
            quality_rows.append(
                (
                    "Observed tokens",
                    f"baseline {baseline_total_tokens:,} · with skill {skilled_total_tokens:,}",
                )
            )
    else:
        quality_rows.extend(
            [
                ("Lift", _format_lift(lift)),
                ("Token delta", str(token_delta)),
            ]
        )
        if baseline_success is not None:
            quality_rows.append(("Baseline success", _format_percentage(baseline_success)))
        if skilled_success is not None:
            quality_rows.append(("Skilled success", _format_percentage(skilled_success)))
    if isinstance(quality_status, dict):
        reason = quality_status.get("reason")
        if reason:
            quality_rows.append(("Reason", str(reason)))
        if not quality_inconclusive:
            quality_rows.extend(
                (f"Detail {index}", str(error))
                for index, error in enumerate(quality_status.get("validation_errors", []), start=1)
            )
        verdict = None
        suggestions = []
        for recommendation in quality_status.get("recommendations", []):
            recommendation = str(recommendation)
            if recommendation.strip().casefold() in {"keep skill", "skill may not be beneficial"}:
                verdict = verdict or recommendation
            else:
                suggestions.append(recommendation)
        if verdict:
            quality_rows.append(("Summary", verdict))
        quality_rows.extend(
            (f"Suggestion {index}", suggestion)
            for index, suggestion in enumerate(suggestions, start=1)
        )
    if not quality_inconclusive and quality_grade in {"failed", "review_required"} and not any(
        label == "Summary" for label, _ in quality_rows
    ):
        quality_rows.append(("Summary", quality_reason))

    final_score_rows = [
        ("Security score", _format_score(context.security.score)),
        ("Maturity score", _format_score(context.metadata.maturity_score)),
        ("Publish decision", str(context.ranking.publish_decision)),
    ]

    return [
        ("Structure Validation", structure_rows),
        ("Risk Validation", risk_rows),
        ("Performance Evaluation", quality_rows),
        ("Final Scores", final_score_rows),
    ]


def _format_score(value: float | None) -> str:
    """Render normalized 0–1 scores as a user-facing score out of ten."""
    if value is None:
        return "not scored"
    return f"{value * 10:.1f} / 10.0"


def _format_percentage(value: float) -> str:
    """Render an Upskill success rate as a whole percentage."""
    return f"{value:.0%}"


def _format_lift(value: float | None) -> str:
    """Render Upskill lift as percentage points rather than a raw fraction."""
    if value is None:
        return "None"
    return f"{value * 100:+.0f}pp"


def _report_phase_rows(context) -> list[tuple[str, str, str]]:
    """Return the user-facing evaluation phases without pipeline internals."""
    structure_gates = _phase_gates(
        context,
        {"discovery_gate", "identity_gate", "metadata_gate", "validation_gate"},
    )
    risk_gates = _phase_gates(context, {"security_gate"})
    quality_gates = _phase_gates(context, {"performance_exam_gate"})

    structure_issue = _first_gate_issue(structure_gates) or _first_item(context.validation.errors)
    structure_warning = _first_gate_warning(structure_gates) or _first_item(context.validation.warnings)
    validation_ran = context.validation.passed or any(
        gate.gate_name == "validation_gate" for gate in structure_gates
    )
    structure_grade = "failed" if structure_issue else ("passed" if validation_ran else "not evaluated")
    structure_reason = structure_issue or structure_warning or (
        "Structure checks passed." if validation_ran else "No structure checks were run."
    )

    risk_issue = _first_gate_issue(risk_gates) or _first_security_finding(context)
    risk_grade = context.security.decision or ("failed" if risk_issue else "not scored")
    risk_reason = risk_issue or "No blocking risk found."

    quality_status = context.metadata.extra.get("upskill_evaluation", {})
    quality_inconclusive = (
        isinstance(quality_status, dict) and quality_status.get("status") == "inconclusive"
    )
    quality_failed = (
        isinstance(quality_status, dict) and quality_status.get("status") == "failed"
    ) or bool(_first_gate_issue(quality_gates))
    quality_grade = (
        "failed"
        if quality_failed
        else "review_required"
        if quality_inconclusive
        else (context.ranking.label or "not evaluated")
    )
    quality_reason = (
        str(quality_status.get("reason"))
        if isinstance(quality_status, dict) and quality_status.get("reason")
        else None
    )
    quality_reason = quality_reason or (
        _first_item(quality_status.get("validation_errors", []))
        if isinstance(quality_status, dict)
        else None
    )
    quality_reason = quality_reason or _first_gate_issue(quality_gates)
    quality_reason = quality_reason or (
        "Quality evaluation completed."
        if context.performance_exam.score is not None
        else "No scored quality evaluation was returned."
    )

    return [
        ("Structure", structure_grade, structure_reason),
        (
            "Risk",
            risk_grade,
            f"Safety score {_format_score(context.security.score)}. {risk_reason}",
        ),
        (
            "Quality",
            quality_grade,
            f"Performance score {_format_score(context.performance_exam.score)}. {quality_reason}",
        ),
    ]


def _phase_gates(context, names: set[str]):
    return [gate for gate in context.gate_history if gate.gate_name in names]


def _first_gate_issue(gates) -> str | None:
    for gate in gates:
        if not gate.passed:
            return _first_item(gate.blocking_issues) or gate.explanation or "Validation failed."
    return None


def _first_gate_warning(gates) -> str | None:
    for gate in gates:
        warning = _first_item(gate.warnings)
        if warning:
            return warning
    return None


def _first_security_finding(context) -> str | None:
    finding = context.security.findings[0] if context.security.findings else None
    if not finding:
        return None
    severity = finding.get("severity", "unknown")
    check = finding.get("check", "security check")
    return f"{severity}: {check}"


def _first_item(values) -> str | None:
    return str(values[0]) if values else None


def _print_gate_failures(context) -> None:
    failed_gates = [gate for gate in context.gate_history if not gate.passed]
    if not failed_gates:
        return
    print("Gate failure reasons:")
    for gate in failed_gates:
        print(f"- {gate.gate_name}: {gate.explanation or 'failed'}")


def _registry_result_lines(result) -> list[tuple[str, str]]:
    """Build concise registry response lines without dumping response JSON."""
    lines = [("status", str(result.status_code))]
    if result.request_id:
        lines.append(("request id", result.request_id))

    body = result.body if isinstance(result.body, dict) else {}
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if code is not None:
            lines.append(("error code", str(code)))
        message = error.get("message")
        if message is not None:
            lines.append(("message", str(message)))
        lines.extend(_registry_error_detail_lines(error))
        return lines

    message = body.get("message")
    if message is not None:
        lines.append(("message", str(message)))
    return lines


def _print_existing_slug_block_if_needed(
    *,
    registry_url: str,
    token: str | None,
    context,
) -> bool:
    intent = context.identity.intent
    if intent != "create_skill":
        return False

    slug = context.identity.slug
    if not slug:
        return False

    return _print_existing_slug_block(
        registry_url=registry_url,
        token=token,
        slug=slug,
        intent=intent,
    )


def _print_existing_slug_preflight_block_if_needed(
    *,
    registry_url: str,
    token: str | None,
    skill_path: str,
    slug_override: str | None,
    intent_override: str | None,
) -> bool:
    identity = _preflight_identity_from_skill_path(
        skill_path=skill_path,
        slug_override=slug_override,
        intent_override=intent_override,
    )
    if not identity.slug:
        return False
    return _print_existing_slug_block(
        registry_url=registry_url,
        token=token,
        slug=identity.slug,
        intent=identity.intent,
    )


def _print_existing_slug_block(
    *,
    registry_url: str,
    token: str | None,
    slug: str,
    intent: str | None,
) -> bool:
    if intent != "create_skill":
        return False

    print("\n" + _separator())
    print("Existing Slug Check")
    print(_separator())
    block = _check_existing_slug_block(
        registry_url=registry_url,
        token=token,
        slug=slug,
        intent=intent,
    )
    if block is None:
        print(f"slug           {slug}")
        print("status         available")
        return False

    print(block.message)
    if block.existing_skill is None:
        return True

    for label, value in _existing_skill_lines(block.existing_skill):
        print(f"{label:<14} {value}")
    return True


def _check_existing_slug_block(
    *,
    registry_url: str,
    token: str | None,
    slug: str | None,
    intent: str | None,
) -> ExistingSlugBlock | None:
    if intent != "create_skill" or not slug:
        return None
    if not token:
        return ExistingSlugBlock(
            slug=slug,
            message=(
                "Publish blocked: cannot verify slug uniqueness without a read or "
                "publish token.\nSet APTITUDE_READ_TOKEN, REGISTRY_READ_TOKEN, "
                "or APTITUDE_PUBLISH_TOKEN."
            ),
        )

    try:
        existing = get_existing_skill(
            registry_url=registry_url,
            token=token,
            slug=slug,
        )
    except RegistryLookupUnavailable as exc:
        return ExistingSlugBlock(
            slug=slug,
            message=(
                f"Publish blocked: cannot verify whether slug {slug!r} exists.\n"
                f"reason         {exc}"
            ),
        )

    if not _should_block_existing_slug(intent=intent, existing_skill=existing):
        return None

    return ExistingSlugBlock(
        slug=slug,
        message="Publish blocked: this slug already exists in the registry.",
        existing_skill=existing,
    )


def _preflight_identity_from_skill_path(
    *,
    skill_path: str,
    slug_override: str | None,
    intent_override: str | None,
) -> PreflightIdentity:
    slug = _string_or_none(slug_override)
    intent = _string_or_none(intent_override)
    if slug and intent:
        return PreflightIdentity(slug=slug, intent=intent)

    frontmatter = _read_skill_frontmatter_for_preflight(skill_path)
    metadata = frontmatter.get("metadata", {}) if isinstance(frontmatter, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}

    return PreflightIdentity(
        slug=slug or _string_or_none(frontmatter.get("name")),
        intent=intent or _string_or_none(metadata.get("intent")),
    )


def _read_skill_frontmatter_for_preflight(skill_path: str) -> dict[str, Any]:
    skill_file = _resolve_skill_file(Path(skill_path))
    try:
        frontmatter, _body = parse_skill_markdown(skill_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return frontmatter


def _resolve_skill_file(path: Path) -> Path:
    if path.is_dir():
        return path / "SKILL.md"
    if path.name == "SKILL.md":
        return path
    return path.parent / "SKILL.md"


def _string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _should_block_existing_slug(
    *,
    intent: str | None,
    existing_skill: ExistingSkill | None,
) -> bool:
    return intent == "create_skill" and existing_skill is not None


def _existing_skill_lines(existing_skill: ExistingSkill) -> list[tuple[str, str]]:
    lines = [("slug", existing_skill.slug)]
    if not existing_skill.versions:
        lines.append(("versions", "none visible"))
    for version in existing_skill.versions:
        details = _existing_skill_version_details(version)
        value = version.version if not details else f"{version.version} {details}"
        lines.append(("version", value))
    lines.append(
        (
            "reuse",
            "Use publish_version for a new version, or depend on the existing skill.",
        )
    )
    return lines


def _existing_skill_version_details(version) -> str:
    details: list[str] = []
    if version.is_current_default:
        details.append("current default")
    for value in (
        version.lifecycle_status,
        version.review_state,
        version.promotion_channel,
    ):
        if value:
            details.append(value)
    return ", ".join(details)


def _registry_error_detail_lines(error: dict[object, object]) -> list[tuple[str, str]]:
    details = error.get("details")
    if not isinstance(details, dict):
        return []
    errors = details.get("errors")
    if not isinstance(errors, list):
        return []

    lines: list[tuple[str, str]] = []
    for index, item in enumerate(errors, start=1):
        if not isinstance(item, dict):
            continue
        loc = item.get("loc")
        location = ".".join(str(part) for part in loc) if isinstance(loc, list) else ""
        message = str(item.get("msg") or item.get("message") or item)
        value = f"{location}: {message}" if location else message
        lines.append((f"error {index}", value))
    return lines


def _publish_payload_ready(context) -> bool:
    """Return true only after delivery built the registry contract payload."""
    payload = context.delivery_payload
    return bool(
        payload.slug
        and payload.version
        and payload.intent
        and payload.metadata.get("name")
        and payload.governance
    )


def _print_relationship_alerts(
    *,
    registry_url: str,
    token: str | None,
    relationships: dict[str, object],
) -> None:
    if not _has_relationships(relationships):
        return

    print("\n" + _separator())
    print("Relationship Alerts")
    print(_separator())
    if not token:
        print(
            "- skipped relationship existence check: set APTITUDE_READ_TOKEN, "
            "REGISTRY_READ_TOKEN, or a publish token with read scope."
        )
        return

    issues = check_relationship_references(
        registry_url=registry_url,
        token=token,
        relationships=relationships,
    )
    if not issues:
        print("- all referenced relationship targets were found.")
        return

    for line in _relationship_alert_lines(issues):
        print(line)


def _relationship_alert_lines(issues: list[RelationshipCheckIssue]) -> list[str]:
    lines: list[str] = []
    for issue in issues:
        coordinate = issue.slug if issue.version is None else f"{issue.slug}@{issue.version}"
        lines.append(f"- {issue.kind} {issue.family} target {coordinate}: {issue.message}")
    return lines


def _has_relationships(relationships: dict[str, object]) -> bool:
    for value in relationships.values():
        if isinstance(value, list) and value:
            return True
    return False


def _separator() -> str:
    return "-" * 72


def _publisher_cli_version() -> str:
    try:
        return importlib_metadata.version(_PACKAGE_NAME)
    except importlib_metadata.PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if not pyproject_path.is_file():
            return "unknown"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        project = data.get("project", {})
        version = project.get("version") if isinstance(project, dict) else None
        return str(version or "unknown")


def _load_local_env_defaults() -> None:
    """Load local env files without overriding non-empty shell values."""
    for env_file in _candidate_env_files():
        _load_env_file(env_file)


def _candidate_env_files() -> tuple[Path, ...]:
    package_root = Path(__file__).resolve().parents[1]
    workspace_root = package_root.parent
    return (
        Path.cwd() / ".env",
        package_root / ".env",
        workspace_root / "aptitude-server" / ".env",
        workspace_root / "aptitude-server" / "_env",
    )


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = _parse_env_line(line)
        if key and not os.environ.get(key):
            os.environ[key] = value


def _parse_env_line(line: str) -> tuple[str | None, str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None, ""

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not key or key.startswith("export "):
        key = key.removeprefix("export ").strip()
    if not key:
        return None, ""

    value = _strip_inline_comment(raw_value.strip()).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _strip_inline_comment(value: str) -> str:
    in_single_quote = False
    in_double_quote = False
    for index, char in enumerate(value):
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif char == "#" and not in_single_quote and not in_double_quote:
            if index == 0 or value[index - 1].isspace():
                return value[:index]
    return value


def _default_registry_url() -> str:
    configured = os.environ.get("APTITUDE_REGISTRY_URL") or os.environ.get(
        "APTITUDE_SERVER_BASE_URL"
    )
    if configured:
        return configured

    app_port = os.environ.get("APP_PORT")
    if app_port:
        return f"http://127.0.0.1:{app_port}"

    return _DEFAULT_REGISTRY_URL


def _default_publish_token() -> str | None:
    return _first_env_value(_PUBLISH_TOKEN_ENV_NAMES)


def _default_admin_token() -> str | None:
    return _first_env_value(_ADMIN_TOKEN_ENV_NAMES)


def _relationship_check_token(publish_token: str | None) -> str | None:
    return _first_env_value(_READ_TOKEN_ENV_NAMES) or (
        publish_token if _has_env_value(publish_token) else None
    )


def _first_env_value(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if _has_env_value(value):
            return value
    return None


def _has_env_value(value: str | None) -> bool:
    return bool(value and value.strip())


def _missing_publish_token_message() -> str:
    return (
        "Missing publish token. Pass --token or set "
        f"{_format_env_names(_PUBLISH_TOKEN_ENV_NAMES)}."
    )


def _missing_admin_token_message() -> str:
    return (
        "Missing admin token. Pass --admin-token or set "
        f"{_format_env_names(_ADMIN_TOKEN_ENV_NAMES)}."
    )


def _format_env_names(names: tuple[str, ...]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f", or {names[-1]}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
