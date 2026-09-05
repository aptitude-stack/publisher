"""Phase 6: evaluate measured skill performance evidence from Upskill."""

from __future__ import annotations

from pathlib import Path

from publisher.domain.models import PublishContext
from publisher.integrations.upskill_eval import run_upskill_evaluation
from publisher.stages.base import PublisherStage


class PerformanceExamStage(PublisherStage):
    """Build a performance-exam artifact from Hugging Face Upskill results."""

    name = "performance_exam"

    def run(self, context: PublishContext) -> None:
        self._reset_exam_state(context)
        self._run_upskill_exam(context)
        self._apply_maturity_score(context)
        context.add_snapshot(
            stage_name=self.name,
            status="completed" if context.performance_exam.score is not None else "failed",
            data={
                "score": context.performance_exam.score,
                "maturity_score": context.metadata.maturity_score,
                "passed": context.performance_exam.passed,
                "test_case_count": context.performance_exam.test_case_count,
                "models_tested": context.performance_exam.models_tested,
                "baseline_success_rate": context.performance_exam.baseline_success_rate,
                "skilled_success_rate": context.performance_exam.skilled_success_rate,
                "skill_lift": context.performance_exam.skill_lift,
                "token_delta": context.performance_exam.token_delta,
                "efficiency_label": context.performance_exam.efficiency_label,
            },
            messages=[
                "Performance exam consumed Upskill as the sole performance source.",
                "Metadata maturity_score was generated from validation quality and Upskill score.",
                "No local performance estimate is produced when Upskill is unavailable or unscored.",
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
            "Performance exam depends only on scored Upskill evidence.",
            "Set OPENAI_API_KEY and UPSKILL_TESTS_PATH to enable publishable external evaluation.",
        ]

    def _run_upskill_exam(self, context: PublishContext) -> None:
        """Run Upskill and copy measured metrics into the performance exam."""
        result = run_upskill_evaluation(
            skill_root=self._resolve_skill_root(context),
        )
        context.metadata.extra["upskill_evaluation"] = {
            "status": result.status,
            "score": result.score,
            "passed": result.passed,
            "test_case_count": result.test_case_count,
            "baseline_success_rate": result.baseline_success_rate,
            "skilled_success_rate": result.skilled_success_rate,
            "skill_lift": result.skill_lift,
            "baseline_avg_tokens": result.baseline_avg_tokens,
            "skilled_avg_tokens": result.skilled_avg_tokens,
            "baseline_total_tokens": result.baseline_total_tokens,
            "skilled_total_tokens": result.skilled_total_tokens,
            "token_delta": result.token_delta,
            "models_tested": result.models_tested,
            "validation_errors": result.validation_errors,
            "validation_warnings": result.validation_warnings,
            "recommendations": result.recommendations,
            "command": result.command,
            "artifact_dir": result.artifact_dir,
            "reason": result.reason,
        }
        if result.status not in {"scored", "inconclusive"}:
            reason = result.reason or "upskill did not produce a scored result"
            context.performance_exam.notes.append(
                f"Upskill evaluator {result.status}: performance metrics unavailable: {reason}."
            )
            return
        if result.status == "inconclusive":
            context.performance_exam.notes.append(
                f"Upskill evaluator inconclusive: performance was not scored: {result.reason}."
            )

        exam = context.performance_exam
        exam.score = result.score if result.status == "scored" else None
        exam.passed = bool(result.passed)
        exam.test_case_count = result.test_case_count or 0
        exam.models_tested = result.models_tested
        exam.baseline_success_rate = result.baseline_success_rate
        exam.skilled_success_rate = result.skilled_success_rate
        exam.skill_lift = result.skill_lift
        exam.baseline_avg_tokens = result.baseline_avg_tokens
        exam.skilled_avg_tokens = result.skilled_avg_tokens
        exam.token_delta = result.token_delta
        if result.token_delta is not None:
            exam.efficiency_label = "improved" if result.token_delta < 0 else "neutral"
        self._apply_upskill_token_estimate(context)
        exam.notes.append("Performance metrics came from Upskill.")

    def _apply_upskill_token_estimate(self, context: PublishContext) -> None:
        """Use Upskill's measured with-skill token average as metadata token estimate."""
        skilled_avg_tokens = context.performance_exam.skilled_avg_tokens
        if skilled_avg_tokens is None or skilled_avg_tokens <= 0:
            context.metadata.extra["token_estimate_source"] = "publisher_content_heuristic"
            context.performance_exam.notes.append(
                "Upskill did not provide a positive skilled_avg_tokens value; metadata token_estimate was left unchanged."
            )
            return

        previous_estimate = context.metadata.token_estimate
        context.metadata.token_estimate = skilled_avg_tokens
        context.metadata.extra["token_estimate_source"] = "upskill.skilled_avg_tokens"
        context.metadata.extra["token_estimate_previous"] = previous_estimate
        context.performance_exam.notes.append(
            "Metadata token_estimate was updated from Upskill skilled_avg_tokens."
        )

    def _apply_maturity_score(self, context: PublishContext) -> None:
        """Generate maturity from validation quality and Upskill evidence."""
        validation_score = self._validation_maturity_score(context)
        upskill_score = context.performance_exam.score if context.performance_exam.score is not None else 0.0
        maturity_score = round((validation_score * 0.50) + (upskill_score * 0.50), 2)
        context.metadata.maturity_score = maturity_score
        context.metadata.extra["maturity_score_source"] = {
            "type": "generated",
            "formula": "0.50 * validation_score + 0.50 * upskill_score",
            "validation_score": validation_score,
            "upskill_score": upskill_score,
            "upskill_status": context.metadata.extra.get("upskill_evaluation", {}).get("status"),
        }
        context.performance_exam.notes.append(
            f"Metadata maturity_score was generated as {maturity_score:.2f} from validation {validation_score:.2f} and Upskill {upskill_score:.2f}."
        )

    def _validation_maturity_score(self, context: PublishContext) -> float:
        """Score validation quality from contract errors and warnings."""
        if context.validation.errors:
            return 0.0
        warning_count = len(context.validation.warnings)
        if warning_count == 0:
            return 1.0
        return max(0.0, round(1.0 - min(warning_count, 5) * 0.1, 2))

    def _resolve_skill_root(self, context: PublishContext) -> Path:
        """Resolve the skill folder for Upskill."""
        source_path = Path(context.source.file_path)
        if source_path.is_dir():
            return source_path
        if source_path.name == "SKILL.md":
            return source_path.parent
        return source_path.parent
