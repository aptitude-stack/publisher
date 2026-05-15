"""Console CLI for publishing skills through the Aptitude registry."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from publisher.bundle import build_bundle_bytes
from publisher.pipeline import PublisherPipeline
from publisher.registry_client import publish_to_registry


def main(argv: list[str] | None = None) -> int:
    """Run the publisher CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "publish":
        return _run_publish(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aptitude-publisher",
        description="Evaluate Aptitude skills and publish them to the registry.",
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
        default=os.environ.get("APTITUDE_REGISTRY_URL", "http://127.0.0.1:8000"),
        help="registry base URL",
    )
    publish_parser.add_argument(
        "--token",
        default=os.environ.get("APTITUDE_PUBLISH_TOKEN"),
        help="registry publish token; defaults to APTITUDE_PUBLISH_TOKEN",
    )
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the full local flow and stop before the API upload",
    )
    return parser


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("skill_path", help="path to the skill folder")
    parser.add_argument("--slug", help="override the skill slug for registry publish")
    parser.add_argument("--version", help="override the semantic version for this publish")
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
    return 0 if context.ranking.publish_decision != "block" else 1


def _run_publish(args: argparse.Namespace) -> int:
    context = _run_pipeline(args)
    _print_pipeline_report(context)
    if context.ranking.publish_decision == "block":
        print("\nPublish blocked by security or validation gates.")
        return 1

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

    if not args.token:
        print("\nMissing publish token. Pass --token or set APTITUDE_PUBLISH_TOKEN.")
        return 1

    result = publish_to_registry(
        registry_url=args.registry_url,
        token=args.token,
        context=context,
        bundle_bytes=bundle_bytes,
    )
    print("\n" + _separator())
    print("Registry Result")
    print(_separator())
    print(f"status         {result.status_code}")
    if result.request_id:
        print(f"request id     {result.request_id}")
    print(json.dumps(result.body, indent=2, ensure_ascii=True))
    return 0 if 200 <= result.status_code < 300 else 1


def _run_pipeline(args: argparse.Namespace):
    pipeline = PublisherPipeline()
    context = pipeline.create_context(
        file_path=str(Path(args.skill_path).resolve()),
        slug_override=args.slug,
        version_override=args.version,
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
    print("Aptitude Publisher")
    print(_separator())
    print(f"skill path      {context.inventory.skill_root}")
    print(f"slug            {context.identity.slug}")
    print(f"version         {context.identity.version}")
    print(f"intent          {context.identity.intent}")
    print(f"trust tier      {context.source.trust_tier}")
    print(f"namespace       {context.source.namespace}")
    print(f"artifact origin {context.source.artifact_origin}")

    print("\n" + _separator())
    print("Evaluation Summary")
    print(_separator())
    print(f"validation      {'passed' if context.validation.passed else 'failed'}")
    print(f"security score  {context.security.score}")
    print(f"security gate   {context.security.decision}")
    print(f"performance     {context.performance_exam.score}")
    print(f"lift            {context.performance_exam.skill_lift}")
    print(f"token delta     {context.performance_exam.token_delta}")
    print(f"ranking         {context.ranking.label}")
    print(f"publish decision {context.ranking.publish_decision}")

    print("\n" + _separator())
    print("Stages")
    print(_separator())
    for snapshot in context.stage_history:
        print(f"{snapshot.stage_name:<18} {snapshot.status}")

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


def _separator() -> str:
    return "-" * 72


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
