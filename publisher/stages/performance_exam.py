"""Phase 6: evaluate measured skill performance evidence."""

from __future__ import annotations

import json
from pathlib import Path

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class PerformanceExamStage(PublisherStage):
    """Build a deterministic performance-exam artifact for ranking."""

    name = "performance_exam"

    def run(self, context: PublishContext) -> None:
        self._reset_exam_state(context)
        self._derive_exam_metrics(context)
        artifact_path = self._write_exam_artifact(context)
        context.add_snapshot(
            stage_name=self.name,
            status="completed",
            data={
                "score": context.performance_exam.score,
                "passed": context.performance_exam.passed,
                "test_case_count": context.performance_exam.test_case_count,
                "models_tested": context.performance_exam.models_tested,
                "baseline_success_rate": context.performance_exam.baseline_success_rate,
                "skilled_success_rate": context.performance_exam.skilled_success_rate,
                "skill_lift": context.performance_exam.skill_lift,
                "token_delta": context.performance_exam.token_delta,
                "efficiency_label": context.performance_exam.efficiency_label,
                "artifact_path": artifact_path,
            },
            messages=[
                "Performance exam estimated whether the skill improves task success and token usage.",
                "This placeholder exam gives ranking a dedicated measured-performance field to consume.",
            ],
        )

    def _reset_exam_state(self, context: PublishContext) -> None:
        exam = context.performance_exam
        exam.score = None
        exam.passed = False
        exam.test_case_count = 0
        exam.models_tested = []
        exam.baseline_success_rate = None
        exam.skilled_success_rate = None
        exam.skill_lift = None
        exam.baseline_avg_tokens = None
        exam.skilled_avg_tokens = None
        exam.token_delta = None
        exam.efficiency_label = None
        exam.notes = [
            "Performance exam is a deterministic placeholder until live benchmark execution is wired in.",
            "The field is intended to hold upskill-style evidence such as success lift and token deltas.",
        ]

    def _derive_exam_metrics(self, context: PublishContext) -> None:
        exam = context.performance_exam
        token_estimate = context.metadata.token_estimate or 900
        findings_count = len(context.security.findings)
        validation_penalty = 0.20 if context.validation.errors else 0.0
        warning_penalty = min(0.10, len(context.validation.warnings) * 0.05)
        security_penalty = min(0.30, findings_count * 0.08)
        quality_signal = self._quality_signal(context)
        structural_signal = max(0.20, round(1.0 - warning_penalty - validation_penalty, 2))

        baseline = max(0.35, round(0.58 - validation_penalty - warning_penalty - security_penalty, 2))
        lift = max(
            0.0,
            round(
                (0.18 + (context.security.score or 0.0) * 0.10)
                * quality_signal
                * structural_signal,
                2,
            ),
        )
        skilled = min(1.0, round(baseline + lift, 2))

        token_savings = max(40, min(320, int(token_estimate * 0.18)))
        skilled_tokens = max(80, token_estimate - token_savings)
        token_delta = skilled_tokens - token_estimate

        exam.test_case_count = 3
        exam.models_tested = ["publisher-sonnet-baseline"]
        exam.baseline_success_rate = baseline
        exam.skilled_success_rate = skilled
        exam.skill_lift = round(skilled - baseline, 2)
        exam.baseline_avg_tokens = token_estimate
        exam.skilled_avg_tokens = skilled_tokens
        exam.token_delta = token_delta
        exam.efficiency_label = "improved" if token_delta < 0 else "neutral"

        normalized_lift = min(1.0, exam.skill_lift / 0.30) if exam.skill_lift is not None else 0.0
        normalized_efficiency = min(1.0, abs(token_delta) / max(token_estimate, 1)) if token_delta < 0 else 0.0
        exam.score = round(
            (normalized_lift * 0.55)
            + (normalized_efficiency * 0.10)
            + (quality_signal * 0.25)
            + (structural_signal * 0.10),
            2,
        )
        exam.passed = skilled >= baseline and (context.security.decision != "block")

    def _quality_signal(self, context: PublishContext) -> float:
        """Blend declared maturity and security metadata into one quality signal."""
        maturity = context.metadata.maturity_score if context.metadata.maturity_score is not None else 0.5
        declared_security = (
            context.metadata.security_score
            if context.metadata.security_score is not None
            else (context.security.score if context.security.score is not None else 0.5)
        )
        return round((maturity + declared_security) / 2, 2)

    def _write_exam_artifact(self, context: PublishContext) -> str:
        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "05_performance_exam.json"
        exam = context.performance_exam
        artifact = {
            "score": exam.score,
            "passed": exam.passed,
            "test_case_count": exam.test_case_count,
            "models_tested": exam.models_tested,
            "baseline_success_rate": exam.baseline_success_rate,
            "skilled_success_rate": exam.skilled_success_rate,
            "skill_lift": exam.skill_lift,
            "baseline_avg_tokens": exam.baseline_avg_tokens,
            "skilled_avg_tokens": exam.skilled_avg_tokens,
            "token_delta": exam.token_delta,
            "efficiency_label": exam.efficiency_label,
            "notes": exam.notes,
        }
        artifact_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        exam.artifact_path = str(artifact_path)
        return str(artifact_path)
