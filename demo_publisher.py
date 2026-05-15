"""Simple demo runner for the publisher pipeline."""

from __future__ import annotations

from pathlib import Path

from publisher.pipeline import PublisherPipeline


ROOT = Path(__file__).resolve().parent


def run_demo(skill_dir: Path) -> None:
    pipeline = PublisherPipeline()
    context = pipeline.create_context(file_path=str(skill_dir))
    context = pipeline.run(context)

    print(f"\n=== Demo: {skill_dir.name} ===")
    print("Stages:")
    for snapshot in context.stage_history:
        print(f"  - {snapshot.stage_name}: {snapshot.status}")

    print("Gates:")
    for gate in context.gate_history:
        result = "passed" if gate.passed else "failed"
        print(f"  - {gate.gate_name}: {result}")
        for issue in gate.blocking_issues:
            print(f"      blocking: {issue}")
        for warning in gate.warnings:
            print(f"      warning: {warning}")

    print("Summary:")
    print(f"  - security decision: {context.security.decision}")
    print(f"  - security score: {context.security.score}")
    print(f"  - validation passed: {context.validation.passed}")
    print(f"  - performance exam score: {context.performance_exam.score}")
    print(f"  - performance exam lift: {context.performance_exam.skill_lift}")
    print(f"  - performance exam token delta: {context.performance_exam.token_delta}")
    print(f"  - ranking label: {context.ranking.label}")
    print(f"  - publish decision: {context.ranking.publish_decision}")
    print(f"  - compression available: {context.compression.available}")
    print(f"  - compression artifact: {context.compression.compressed_artifact_path}")

    if context.compression.notes:
        print("  - compression notes:")
        for note in context.compression.notes:
            print(f"      {note}")

    if context.security.findings:
        print("  - security findings:")
        for finding in context.security.findings:
            print(
                "      "
                f"{finding['severity']} | {finding['check']} | "
                f"{finding['field']} | {finding['evidence']}"
            )


def main() -> None:
    run_demo(ROOT / "project-planning-skill")
    run_demo(ROOT / "low-evaluation-skill")
    run_demo(ROOT / "blocked-security-skill")


if __name__ == "__main__":
    main()
