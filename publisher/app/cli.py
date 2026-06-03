"""Console CLI for publishing skills through the Aptitude registry."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
import os
import sys
import tomllib
from pathlib import Path

from publisher.artifacts.bundle import build_bundle_bytes
from publisher.app.pipeline import PublisherPipeline
from publisher.registry.client import (
    ExistingSkill,
    RegistryLookupUnavailable,
    RelationshipCheckIssue,
    check_relationship_references,
    get_existing_skill,
    publish_to_registry,
)


_DEFAULT_REGISTRY_URL = "http://127.0.0.1:8000"
_PACKAGE_NAME = "aptitude-publisher"
_DEFAULT_PROG = "aptitude-publisher"


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
            "APTITUDE_SERVER_BASE_URL, or local APP_PORT"
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
            "APTITUDE_SERVER_BASE_URL, or local APP_PORT"
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

    return parser


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("skill_path", help="path to the skill folder")
    parser.add_argument("--slug", help="override the skill slug for registry publish")
    parser.add_argument("--version", help="override the semantic version for this publish")
    _add_publish_metadata_arguments(parser)


def _add_batch_shared_arguments(parser: argparse.ArgumentParser) -> None:
    _add_publish_metadata_arguments(parser)


def _add_publish_metadata_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--intent",
        choices=("create_skill", "publish_version"),
        help="override publish intent",
    )
    parser.add_argument(
        "--trust-tier",
        default="untrusted",
        choices=("untrusted", "internal", "verified"),
        help="governance trust tier",
    )
    parser.add_argument("--namespace", default="public", help="target registry namespace")
    parser.add_argument(
        "--artifact-origin",
        default="internal",
        choices=("internal", "imported", "verified", "restricted"),
        help="governance artifact origin",
    )
    parser.add_argument("--policy-pack-slug", help="optional governance policy-pack slug")
    parser.add_argument("--publisher-identity", help="optional provenance publisher identity")


def _run_inspect(args: argparse.Namespace) -> int:
    context = _run_pipeline(args)
    _print_pipeline_report(context)
    return 0 if _publish_payload_ready(context) and context.ranking.publish_decision != "block" else 1


def _run_publish(args: argparse.Namespace) -> int:
    context = _run_pipeline(args)
    _print_pipeline_report(context)
    if not _publish_payload_ready(context) or context.ranking.publish_decision == "block":
        print("\nPublish blocked before registry upload.")
        _print_gate_failures(context)
        return 1

    if not args.dry_run and not args.token:
        print(
            "\nMissing publish token. Pass --token or set APTITUDE_PUBLISH_TOKEN "
            "or PUBLISH_TOKEN."
        )
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


def _run_admin_batch_upload(args: argparse.Namespace) -> int:
    if not args.dry_run and not args.admin_token:
        print(
            "\nMissing admin token. Pass --admin-token or set APTITUDE_ADMIN_TOKEN, "
            "APTITUDE_REGISTRY_ADMIN_TOKEN, or REGISTRY_ADMIN_TOKEN."
        )
        return 1

    concurrency = max(1, min(args.concurrency, len(args.skill_paths)))

    results: list[BatchUploadResult | None] = [None] * len(args.skill_paths)
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

    completed = [result for result in results if result is not None]
    _print_batch_summary(
        completed,
        requested_count=len(args.skill_paths),
        concurrency=concurrency,
        dry_run=args.dry_run,
    )
    if not completed:
        return 1
    return 0 if all(_batch_result_succeeded(result) for result in completed) else 1


def _upload_one_batch_skill(
    index: int,
    skill_path: str,
    args: argparse.Namespace,
) -> BatchUploadResult:
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
) -> None:
    print(_separator())
    print("Admin Batch Upload Summary")
    print(_separator())
    print(f"skills       {requested_count}")
    print(f"concurrency  {concurrency}")
    print(f"mode         {'dry-run' if dry_run else 'upload'}")
    print()
    header = f"{'#':<4} {'status':<15} {'http':<5} {'slug':<24} {'version':<12} message"
    print(header)
    print("-" * len(header))
    for result in results:
        http_status = "" if result.http_status is None else str(result.http_status)
        slug = result.slug or "-"
        version = result.version or "-"
        message = result.message or result.path
        print(
            f"{result.index:<4} {result.status:<15} {http_status:<5} "
            f"{slug:<24} {version:<12} {message}"
        )

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print("\nCounts")
    for status, count in sorted(counts.items()):
        print(f"{status:<15} {count}")


def _batch_result_succeeded(result: BatchUploadResult) -> bool:
    return result.status in {"uploaded", "ready"}


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


def _print_pipeline_report(context) -> None:
    print(_separator())
    print(f"Aptitude Publisher {_publisher_cli_version()}")
    print(_separator())
    print(f"cli version     {_publisher_cli_version()}")
    print(f"skill path      {context.inventory.skill_root}")
    print(f"slug            {context.identity.slug}")
    print(f"skill version   {context.identity.version}")
    print(f"intent          {context.identity.intent}")
    print(f"trust tier      {context.source.trust_tier}")
    print(f"namespace       {context.source.namespace}")
    print(f"artifact origin {context.source.artifact_origin}")

    print("\n" + _separator())
    print("Evaluation Summary")
    print(_separator())
    llm_guard_status = context.metadata.extra.get("llm_guard_security", {})
    upskill_status = context.metadata.extra.get("upskill_evaluation", {})
    print(f"validation      {'passed' if context.validation.passed else 'failed'}")
    print(f"llm guard status {llm_guard_status.get('status') if isinstance(llm_guard_status, dict) else None}")
    print(f"security score  {context.security.score}")
    print(f"security gate   {context.security.decision}")
    print(f"upskill status  {upskill_status.get('status') if isinstance(upskill_status, dict) else None}")
    print(f"performance     {context.performance_exam.score}")
    print(f"maturity score  {context.metadata.maturity_score}")
    print(f"lift            {context.performance_exam.skill_lift}")
    print(f"token delta     {context.performance_exam.token_delta}")
    print(f"ranking         {context.ranking.label}")
    print(f"publish decision {context.ranking.publish_decision}")

    print("\n" + _separator())
    print("Stages")
    print(_separator())
    for snapshot in context.stage_history:
        print(f"{snapshot.stage_name:<18} {snapshot.status}")

    if context.gate_history:
        print("\n" + _separator())
        print("Gate Results")
        print(_separator())
        for gate in context.gate_history:
            status = "passed" if gate.passed else "failed"
            print(f"{gate.gate_name:<18} {status}")
            if gate.explanation:
                print(f"  why           {gate.explanation}")
            for issue in gate.blocking_issues:
                print(f"  blocking      {issue}")
            for warning in gate.warnings:
                print(f"  warning       {warning}")

    if context.security.findings:
        print("\n" + _separator())
        print("Security Findings")
        print(_separator())
        for finding in context.security.findings:
            print(
                f"{finding['severity']:<8} {finding['check']:<40} "
                f"{finding['field']:<24} {finding['evidence']}"
            )

    if context.validation.errors:
        print("\n" + _separator())
        print("Validation Errors")
        print(_separator())
        for error in context.validation.errors:
            print(f"- {error}")


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

    print("\n" + _separator())
    print("Existing Slug Check")
    print(_separator())
    if not token:
        print(
            "Publish blocked: cannot verify slug uniqueness without a read or publish token."
        )
        print(
            "Set APTITUDE_READ_TOKEN, REGISTRY_READ_TOKEN, or APTITUDE_PUBLISH_TOKEN."
        )
        return True

    try:
        existing = get_existing_skill(
            registry_url=registry_url,
            token=token,
            slug=slug,
        )
    except RegistryLookupUnavailable as exc:
        print(f"Publish blocked: cannot verify whether slug {slug!r} exists.")
        print(f"reason         {exc}")
        return True

    if not _should_block_existing_slug(intent=intent, existing_skill=existing):
        print(f"slug           {slug}")
        print("status         available")
        return False

    print("Publish blocked: this slug already exists in the registry.")
    for label, value in _existing_skill_lines(existing):
        print(f"{label:<14} {value}")
    return True


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
    """Load local env files without overriding shell-provided values."""
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
        if key and key not in os.environ:
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
    return (
        os.environ.get("APTITUDE_PUBLISH_TOKEN")
        or os.environ.get("APTITUDE_INTEGRATION_PUBLISH_TOKEN")
        or os.environ.get("PUBLISH_TOKEN")
    )


def _default_admin_token() -> str | None:
    return (
        os.environ.get("APTITUDE_ADMIN_TOKEN")
        or os.environ.get("APTITUDE_REGISTRY_ADMIN_TOKEN")
        or os.environ.get("REGISTRY_ADMIN_TOKEN")
    )


def _relationship_check_token(publish_token: str | None) -> str | None:
    return (
        os.environ.get("APTITUDE_READ_TOKEN")
        or os.environ.get("APTITUDE_REGISTRY_READ_TOKEN")
        or os.environ.get("REGISTRY_READ_TOKEN")
        or publish_token
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
