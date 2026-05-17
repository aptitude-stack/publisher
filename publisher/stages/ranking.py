"""Phase 3: internal ranking system."""

from __future__ import annotations

import json
from pathlib import Path

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class RankingStage(PublisherStage):
    """Compute a combined quality score for the skill."""

    name = "ranking"

    def run(self, context: PublishContext) -> None:
        self._populate_weights(context)
        self._score_security(context)
        self._score_performance_exam(context)
        self._score_token_efficiency(context)
        self._score_metadata_completeness(context)
        self._score_instruction_quality(context)
        self._score_anthropic_compliance(context)
        self._finalize_total_score(context)
        artifact_path = self._write_ranking_artifact(context)
        context.add_snapshot(
            stage_name=self.name,
            status="completed",
            data={
                "total_score": context.ranking.total_score,
                "criteria_scores": context.ranking.criteria_scores,
                "label": context.ranking.label,
                "publish_decision": context.ranking.publish_decision,
                "artifact_path": artifact_path,
            },
            messages=[
                "Ranking stage combined security, token efficiency, compliance, metadata, and instruction quality.",
                "Security can override the publish decision even when a total score exists.",
            ],
        )

    def _populate_weights(self, context: PublishContext) -> None:
        """Define the rubric weights for the overall score."""
        context.ranking.weights = {
            "security": 0.30,
            "performance_exam": 0.25,
            "token_efficiency": 0.15,
            "anthropic_compliance": 0.20,
            "metadata_completeness": 0.10,
            "instruction_quality": 0.00,
        }
        context.ranking.criteria_scores = {}
        context.ranking.explanation = []

    def _score_security(self, context: PublishContext) -> None:
        """Use the security stage score directly."""
        score = context.security.score if context.security.score is not None else 0.0
        context.ranking.criteria_scores["security"] = round(score, 2)
        context.ranking.explanation.append(
            f"Security score contributes {score:.2f} based on prompt-injection findings."
        )

    def _score_performance_exam(self, context: PublishContext) -> None:
        """Use measured performance evidence when available."""
        score = context.performance_exam.score if context.performance_exam.score is not None else 0.0
        context.ranking.criteria_scores["performance_exam"] = round(score, 2)
        context.ranking.explanation.append(
            "Performance exam score contributes "
            f"{score:.2f} based on skill lift {context.performance_exam.skill_lift} "
            f"and token delta {context.performance_exam.token_delta}."
        )

    def _score_token_efficiency(self, context: PublishContext) -> None:
        """Score lower token usage higher."""
        exam = context.performance_exam
        token_estimate = context.metadata.token_estimate
        if exam.baseline_avg_tokens and exam.skilled_avg_tokens:
            if exam.skilled_avg_tokens <= exam.baseline_avg_tokens * 0.70:
                score = 1.0
            elif exam.skilled_avg_tokens <= exam.baseline_avg_tokens * 0.85:
                score = 0.8
            elif exam.skilled_avg_tokens <= exam.baseline_avg_tokens:
                score = 0.65
            else:
                score = 0.3
            explanation = (
                f"Token efficiency score is {score:.2f} from performance exam tokens "
                f"{exam.baseline_avg_tokens}->{exam.skilled_avg_tokens}."
            )
        else:
            if token_estimate is None:
                score = 0.4
            elif token_estimate <= 200:
                score = 1.0
            elif token_estimate <= 500:
                score = 0.8
            elif token_estimate <= 1000:
                score = 0.6
            elif token_estimate <= 2000:
                score = 0.4
            else:
                score = 0.2
            explanation = f"Token efficiency score is {score:.2f} for token estimate {token_estimate}."
        context.ranking.criteria_scores["token_efficiency"] = score
        context.ranking.explanation.append(explanation)

    def _score_metadata_completeness(self, context: PublishContext) -> None:
        """Score based on how many required metadata fields are present."""
        checks = [
            bool(context.metadata.name),
            bool(context.metadata.description),
            bool(context.metadata.tags),
            context.metadata.inputs_schema is not None,
            context.metadata.outputs_schema is not None,
        ]
        score = round(sum(1 for item in checks if item) / len(checks), 2)
        context.ranking.criteria_scores["metadata_completeness"] = score
        context.ranking.explanation.append(
            f"Metadata completeness score is {score:.2f} based on required metadata coverage."
        )

    def _score_instruction_quality(self, context: PublishContext) -> None:
        """Score SKILL.md structure quality from the parsed body."""
        body = context.source.parsed_content.get("body", "")
        lowered = body.lower() if isinstance(body, str) else ""
        components = [
            "# instructions" in lowered,
            "example" in lowered,
            "troubleshooting" in lowered,
        ]
        score = round(sum(1 for item in components if item) / len(components), 2)
        context.ranking.criteria_scores["instruction_quality"] = score
        context.ranking.explanation.append(
            f"Instruction quality score is {score:.2f} based on instructions/examples/troubleshooting sections."
        )

    def _score_anthropic_compliance(self, context: PublishContext) -> None:
        """Score compliance from validation errors and warnings."""
        if context.validation.errors:
            score = 0.0
        elif context.validation.warnings:
            score = 0.8
        else:
            score = 1.0
        context.ranking.criteria_scores["anthropic_compliance"] = score
        context.ranking.explanation.append(
            f"Anthropic compliance score is {score:.2f} based on validation errors/warnings."
        )

    def _finalize_total_score(self, context: PublishContext) -> None:
        """Compute the weighted total score and final label/decision."""
        total = 0.0
        for criterion, weight in context.ranking.weights.items():
            total += weight * context.ranking.criteria_scores.get(criterion, 0.0)
        total = round(total, 2)
        context.ranking.total_score = total

        if total >= 0.85:
            label = "excellent"
        elif total >= 0.70:
            label = "good"
        elif total >= 0.55:
            label = "review"
        else:
            label = "poor"
        context.ranking.label = label

        if context.security.decision == "block":
            decision = "block"
        elif not context.validation.passed or context.security.decision == "review_required":
            decision = "review_required"
        else:
            decision = "allow"
        context.ranking.publish_decision = decision
        context.ranking.explanation.append(
            f"Final weighted score is {total:.2f}, labeled {label}, with publish decision {decision}."
        )

    def _write_ranking_artifact(self, context: PublishContext) -> str:
        """Persist the phase 3 ranking result as JSON."""
        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "03_ranking.json"
        artifact = {
            "total_score": context.ranking.total_score,
            "criteria_scores": context.ranking.criteria_scores,
            "weights": context.ranking.weights,
            "label": context.ranking.label,
            "publish_decision": context.ranking.publish_decision,
            "explanation": context.ranking.explanation,
        }
        artifact_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        context.ranking.artifact_path = str(artifact_path)
        return str(artifact_path)
