from __future__ import annotations

from publisher.domain.models import PublishContext, SkillSource
from publisher.registry.client import build_publish_metadata
from publisher.stages.delivery import DeliveryStage


def test_delivery_persists_overall_score_as_the_canonical_registry_score() -> None:
    context = PublishContext(source=SkillSource(file_path="/tmp/example-skill"))
    context.identity.slug = "example-skill"
    context.identity.version = "1.0.0"
    context.identity.intent = "create_skill"
    context.metadata.name = "Example Skill"
    context.metadata.maturity_score = 0.75
    context.security.score = 0.95
    context.ranking.total_score = 0.9

    DeliveryStage().run(context)

    assert context.delivery_payload.metadata["maturity_score"] == 0.75
    assert context.delivery_payload.metadata["security_score"] == 0.95
    assert context.delivery_payload.metadata["overall_score"] == 0.9
    assert build_publish_metadata(context)["metadata"]["overall_score"] == 0.9
