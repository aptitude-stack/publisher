"""Phase 6: build the final payload for the client endpoint."""

from __future__ import annotations

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class DeliveryStage(PublisherStage):
    """Prepare the final delivery object that matches the registry contract."""

    name = "delivery"

    def run(self, context: PublishContext) -> None:
        self._build_registry_payload(context)
        context.add_snapshot(
            stage_name=self.name,
            status="completed",
            data={
                "slug": context.delivery_payload.slug,
                "version": context.delivery_payload.version,
                "intent": context.delivery_payload.intent,
                "namespace": context.delivery_payload.governance.get("namespace"),
                "artifact_origin": context.delivery_payload.governance.get("artifact_origin"),
            },
            messages=[
                "Delivery stage built the normalized registry metadata payload.",
                "Bundle bytes are created separately and uploaded as the multipart artifact.",
            ],
        )

    def _build_registry_payload(self, context: PublishContext) -> None:
        """Assemble the registry metadata payload from the pipeline context."""
        context.delivery_payload.slug = context.identity.slug
        context.delivery_payload.version = context.identity.version
        context.delivery_payload.intent = context.identity.intent
        context.delivery_payload.content = {
            "bundle_member_root": "skill-bundle/",
            "skill_markdown_path": "skill-bundle/SKILL.md",
            "included_files": {
                "markdown": context.inventory.companion_markdown_files,
                "scripts": context.inventory.script_files,
                "references": context.inventory.reference_files,
                "assets": context.inventory.asset_files,
                "other": context.inventory.other_files,
            },
        }
        context.delivery_payload.metadata = {
            "name": context.metadata.name,
            "description": context.metadata.description,
            "tags": context.metadata.tags,
            "inputs_schema": context.metadata.inputs_schema,
            "outputs_schema": context.metadata.outputs_schema,
            "token_estimate": context.metadata.token_estimate,
            "maturity_score": context.metadata.maturity_score,
            "security_score": context.security.score,
        }
        provenance = None
        if context.inventory.repo_url and context.inventory.commit_sha:
            provenance = {
                "repo_url": context.inventory.repo_url,
                "commit_sha": context.inventory.commit_sha,
                "tree_path": context.inventory.tree_path,
                "publisher_identity": context.source.publisher_identity,
            }
        context.delivery_payload.governance = {
            "trust_tier": context.source.trust_tier,
            "namespace": context.source.namespace,
            "artifact_origin": context.source.artifact_origin,
            "policy_pack_slug": context.source.policy_pack_slug,
            "provenance": provenance,
        }
        context.delivery_payload.relationships = {
            "depends_on": [],
            "extends": [],
            "conflicts_with": [],
            "overlaps_with": [],
        }
