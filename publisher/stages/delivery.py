"""Phase 6: build the final payload for the client endpoint."""

from __future__ import annotations

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class DeliveryStage(PublisherStage):
    """Prepare the final delivery object without sending it yet."""

    name = "delivery"

    def run(self, context: PublishContext) -> None:
        self._build_payload_placeholder(context)
        context.add_snapshot(
            stage_name=self.name,
            status="placeholder",
            data={
                "slug": context.delivery_payload.slug,
                "version": context.delivery_payload.version,
                "intent": context.delivery_payload.intent,
            },
            messages=[
                "TODO: finalize the exact output contract for the client endpoint.",
                "TODO: send the payload only after validation is implemented.",
            ],
        )

    def _build_payload_placeholder(self, context: PublishContext) -> None:
        """Assemble a draft payload shape that mirrors the server contract."""
        context.delivery_payload.slug = context.identity.slug
        context.delivery_payload.version = context.identity.version
        context.delivery_payload.intent = context.identity.intent
        context.delivery_payload.content = {
            "raw_markdown": context.source.parsed_content.get("body", ""),
            "rendered_summary": None,
        }
        context.delivery_payload.metadata = {
            "name": context.metadata.name,
            "description": context.metadata.description,
            "tags": context.metadata.tags,
            "headers": context.metadata.headers,
            "inputs_schema": context.metadata.inputs_schema,
            "outputs_schema": context.metadata.outputs_schema,
            "token_estimate": context.metadata.token_estimate,
            "maturity_score": context.metadata.maturity_score,
            "security_score": context.security.score,
        }
        context.delivery_payload.governance = {
            "trust_tier": "untrusted",
            "provenance": None,
        }
        context.delivery_payload.relationships = {
            "depends_on": [],
            "extends": [],
            "conflicts_with": [],
            "overlaps_with": [],
        }
