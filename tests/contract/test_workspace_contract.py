"""Local checkout contract check; no server, upload, or runtime cross-imports.

Run with the three sibling repositories and their existing .venv directories.
Each consumer validates the producer's actual output in its own interpreter.
"""

import json
from pathlib import Path
import subprocess

import pytest

from publisher.app.pipeline import PublisherPipeline
from publisher.artifacts.bundle import build_bundle_bytes
from publisher.registry.client import build_publish_metadata
from publisher.stages.delivery import DeliveryStage
from publisher.stages.discovery import DiscoveryStage
from publisher.stages.identity import IdentityStage
from publisher.stages.metadata import MetadataStage
from publisher.relationships import normalize_relationships


def test_publish_metadata_and_bundle_are_consumable(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    for repo in ("registry", "resolver"):
        if not (workspace / repo / ".venv/bin/python").exists():
            pytest.skip("Workspace contract check requires sibling Registry and Resolver environments")
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Use when testing.\n---\n# Instructions\nTest contracts.\n")
    (skill / "aptitude.yaml").write_text('version: "1.2.3-codex+build.1"\nintent: create_skill\n')
    context = PublisherPipeline().create_context(file_path=str(skill))
    for stage in (DiscoveryStage(), IdentityStage(), MetadataStage(), DeliveryStage()):
        stage.run(context)
    (tmp_path / "request.json").write_text(json.dumps(build_publish_metadata(context)))
    (tmp_path / "skill.tar.zst").write_bytes(build_bundle_bytes(context))

    registry_check = '''
import json,sys
from pathlib import Path
from app.interface.dto.skills_publish import SkillVersionCreateRequest
from app.interface.dto.skills_fetch import SkillVersionMetadataResponse
from app.interface.validation import validate_skill_bundle
p=Path(sys.argv[1])
request=SkillVersionCreateRequest.model_validate_json((p/'request.json').read_text())
metadata=request.metadata.model_dump(mode='json')
assert not {'inputs_schema','outputs_schema'} & metadata.keys()
assert metadata['tags']==[]
assert metadata['assessment']['schema_version']==1
assert metadata['assessment']['maturity']['upskill_score'] is None
assert metadata['assessment']['security']['scanned'] is False
bundle=(p/'skill.tar.zst').read_bytes()
validate_skill_bundle(bundle,filename='skill.tar.zst',media_type='application/zstd')
from hashlib import sha256
checksum={'algorithm':'sha256','digest':sha256(bundle).hexdigest()}
response=SkillVersionMetadataResponse(slug='demo',version=request.version,install_count=0,
    version_checksum=checksum,content={'checksum':checksum,'size_bytes':len(bundle),'media_type':'application/zstd'},
    metadata=metadata,lifecycle_status='published',trust_tier='untrusted',namespace='public',
    artifact_origin='internal',review_state='approved',promotion_channel='prod',published_at='2026-09-06T00:00:00Z')
(p/'response.json').write_text(response.model_dump_json())
assert json.loads((p/'response.json').read_text())['metadata']['assessment']==json.loads((p/'request.json').read_text())['metadata']['assessment']
'''
    resolver_check = '''
import sys
from pathlib import Path
from aptitude_resolver.registry.transport_models import MetadataResponse
from aptitude_resolver.registry.mappers import map_metadata_response
from aptitude_resolver.lockfile import LockedSkill
from aptitude_resolver.execution.archive import extract_tar_zstd_artifact
p=Path(sys.argv[1])
response=MetadataResponse.model_validate_json((p/'response.json').read_text())
metadata=map_metadata_response(response)
assert metadata.coordinate.version=='1.2.3-codex+build.1'
assert not hasattr(metadata,'inputs_schema') and not hasattr(metadata,'outputs_schema')
node=LockedSkill(node_id='demo@'+response.version,slug='demo',version=response.version,
    artifact_ref='local',name=metadata.name,description=metadata.description,tags=metadata.tags,
    headers=metadata.headers,rendered_summary=metadata.rendered_summary,lifecycle_status=metadata.lifecycle_status,
    trust_tier=metadata.trust_tier,published_at=metadata.published_at,
    content_checksum_algorithm=metadata.content_checksum_algorithm,content_checksum_digest=metadata.content_checksum_digest,
    content_size_bytes=metadata.content_size_bytes)
paths=extract_tar_zstd_artifact(node=node,artifact=(p/'skill.tar.zst').read_bytes(),target_dir=p/'extracted')
assert 'skill-bundle/SKILL.md' in paths
assert (p/'extracted/skill-bundle/SKILL.md').read_bytes()==(p/'demo/SKILL.md').read_bytes()
'''
    for repo, code in (("registry", registry_check), ("resolver", resolver_check)):
        result = subprocess.run(
            [str(workspace / repo / ".venv/bin/python"), "-c", code, str(tmp_path)],
            cwd=workspace / repo, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"{repo} rejected Publisher output:\n{result.stderr}"


def test_dependency_contract_examples_agree(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    cases = [
        ({"slug": "dep", "version": "1.2.3-codex+build.1"}, True),
        ({"slug": "dep", "version": "1.2.3-01"}, False),
        ({"slug": "dep", "version_constraint": "=1.2.3"}, True),
        ({"slug": "dep", "version_constraint": "!=1.2.3,>=1.0.0,<2.0.0"}, True),
        ({"slug": "dep", "version_constraint": ">=1.2.3-codex,<2.0.0"}, True),
        ({"slug": "dep", "version_constraint": "1.2.3"}, False),
        ({"slug": "dep", "version_constraint": ">=1.0.0\n"}, False),
        ({"slug": "dep", "version_constraint": ">=\n1.0.0"}, False),
        ({"slug": "dep", "version_constraint": ">=1.0.0,\r<2.0.0"}, False),
        ({"slug": "dep", "version_constraint": ">=\t1.0.0, <2.0.0"}, True),
        ({"slug": "dep", "version_constraint": ",".join([">=1.0.0"] * 30)}, False),
        ({"slug": "dep", "version": "1.0.0", "markers": ["os:linux", "os:linux"]}, True),
        ({"slug": "dep", "version": "1.0.0", "markers": ["_private"]}, False),
        ({"slug": "dep", "version": "1.0.0", "markers": ["a\n"]}, False),
        ({"slug": "dep"}, False),
        ({"slug": "dep", "version": "1.0.0", "version_constraint": ">=1.0.0"}, False),
    ]
    for selector, allowed in cases:
        try:
            normalize_relationships({"depends_on": [selector]})
            accepted = True
        except ValueError:
            accepted = False
        assert accepted == allowed, selector
    examples = tmp_path / "selectors.json"
    examples.write_text(json.dumps(cases))
    for repo, model_import in (
        ("registry", "from app.interface.dto.skills_publish import DependencySelectorRequest as Model"),
        ("resolver", "from aptitude_resolver.registry.transport_models import DependencySelector as Model"),
    ):
        interpreter = workspace / repo / ".venv/bin/python"
        if not interpreter.exists():
            pytest.skip("Workspace contract check requires sibling environments")
        code = model_import + '''
import json,sys
from pathlib import Path
for selector,allowed in json.loads(Path(sys.argv[1]).read_text()):
    try:
        result=Model.model_validate(selector)
        accepted=True
    except ValueError:
        accepted=False
    assert accepted==allowed, selector
    if accepted and 'markers' in selector:
        assert result.markers==selector['markers']
'''
        result = subprocess.run([str(interpreter), "-c", code, str(examples)],
                                cwd=workspace / repo, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"{repo}: {result.stderr}"
