from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from publisher.app.pipeline import PublisherPipeline
from publisher.artifacts.report import report_path, write_report
from publisher.domain.models import PublishContext, SkillSource


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def test_cache_keys_resolve_aliases_and_distinguish_same_names(tmp_path):
    one = tmp_path / "one" / "skill"
    two = tmp_path / "two" / "skill"
    one.mkdir(parents=True)
    two.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(one, target_is_directory=True)
    assert report_path(one) == report_path(alias) == report_path(one / "SKILL.md")
    assert report_path(one) != report_path(two)


def test_relative_xdg_uses_home_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "relative")
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "skill"
    source.mkdir()
    assert report_path(source).parent == tmp_path / ".cache/aptitude/publisher"


def test_atomic_report_is_private_redacted_and_replaces_receipt(tmp_path, monkeypatch):
    root = tmp_path / "skill"
    root.mkdir()
    context = PublishContext(source=SkillSource(file_path=str(root)))
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    context.security.notes = ["failed with secret-value"]
    context.metadata.extra = {"api_key": "secret-value", "stdout": "raw transcript"}
    write_report(context, status="ready", inspection_receipt={"example": True})
    write_report(context, status="running")
    report = Path(context.report_path)
    raw = report.read_text()
    assert json.loads(raw)["inspection_receipt"] is None
    assert "secret-value" not in raw and "raw transcript" not in raw
    assert report.stat().st_mode & 0o777 == 0o600
    assert list(root.iterdir()) == []
    assert list(report.parent.iterdir()) == [report]


def test_concurrent_writers_leave_one_complete_report(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    def write(index):
        context = PublishContext(source=SkillSource(file_path=str(root)))
        write_report(context, status="failed", error=str(index))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    path = report_path(root)
    assert json.loads(path.read_text())["error"] in {str(i) for i in range(12)}
    assert list(path.parent.iterdir()) == [path]


def test_pipeline_records_exception_and_preserves_source(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("original")
    class BrokenStage:
        name = "broken"
        def run(self, context):
            raise ValueError("evaluator failed")
    pipeline = PublisherPipeline()
    pipeline._stages = (BrokenStage(),)
    context = pipeline.create_context(file_path=str(root))
    with pytest.raises(ValueError, match="evaluator failed"):
        pipeline.run(context)
    payload = json.loads(Path(context.report_path).read_text())
    assert payload["status"] == "failed"
    assert payload["error"] == "evaluator failed"
    assert payload["inspection_receipt"] is None
    assert (root / "SKILL.md").read_text() == "original"
    assert len(list(root.iterdir())) == 1


def test_inspect_and_publish_preserve_read_only_source_and_reuse_report(tmp_path, monkeypatch):
    from publisher.integrations.llm_guard_security import LlmGuardSecurityResult
    from publisher.integrations.upskill_eval import UpskillEvaluation
    from publisher.interfaces.mcp.models import InspectSkillInput, PublishSkillInput
    from publisher.interfaces.mcp.server import PublisherMcpAdapter
    from publisher.registry.client import RegistryPublishResult
    import shutil

    root = tmp_path / "secure-good"
    fixture = Path(__file__).parents[1] / "fixtures/skills/secure-good"
    root.mkdir()
    for name in ("SKILL.md", "aptitude.yaml"):
        shutil.copyfile(fixture / name, root / name)
    original = {p.name: p.read_bytes() for p in root.iterdir()}
    calls = []
    def evaluate(**kwargs):
        calls.append(kwargs)
        return UpskillEvaluation(
            status="scored", score=0.9, passed=True, test_case_count=2,
            models_tested=["test-model"], baseline_success_rate=0.5,
            skilled_success_rate=1.0, skill_lift=0.5,
            baseline_avg_tokens=100, skilled_avg_tokens=80, token_delta=-20,
        )
    monkeypatch.setattr("publisher.stages.security.run_llm_guard_security_scan", lambda **_: LlmGuardSecurityResult(status="scored", score=1.0))
    monkeypatch.setattr("publisher.stages.performance_exam.run_upskill_evaluation", evaluate)
    monkeypatch.setenv("PUBLISHER_LLM_VALIDATION_ENABLED", "false")
    monkeypatch.setenv("APTITUDE_PUBLISH_TOKEN", "test-publish-token")
    monkeypatch.setattr("publisher.interfaces.mcp.server.get_existing_skill", lambda **_: None)
    monkeypatch.setattr("publisher.interfaces.mcp.server.check_relationship_references", lambda **_: [])
    monkeypatch.setattr("publisher.interfaces.mcp.server.publish_to_registry", lambda **_: RegistryPublishResult(status_code=201, body={}, request_id=None))
    for path in root.iterdir():
        path.chmod(0o444)
    root.chmod(0o555)
    try:
        adapter = PublisherMcpAdapter()
        inspected = json.loads(adapter.inspect_skill(InspectSkillInput(skill_path=root, response_format="json")))
        assert inspected["ok"], inspected
        published = json.loads(adapter.publish_skill(PublishSkillInput(
            skill_path=root, slug="secure-good", version="0.0.1", intent="create_skill",
            confirm_upload=True, response_format="json",
        )))
        assert published["status"] == "published", published
        assert published["evidence_reused"] is True
        assert len(calls) == 1
        assert {p.name: p.read_bytes() for p in root.iterdir()} == original
        path = report_path(root)
        assert list(path.parent.iterdir()) == [path]
        assert json.loads(path.read_text())["inspection_receipt"] is not None
        manifest = root / "aptitude.yaml"
        manifest.chmod(0o644)
        manifest.write_text(manifest.read_text() + "\n# Metadata changed\n")
        manifest.chmod(0o444)
        refreshed = json.loads(adapter.publish_skill(PublishSkillInput(
            skill_path=root, slug="secure-good", version="0.0.1", intent="create_skill",
            confirm_upload=True, response_format="json",
        )))
        assert refreshed["status"] == "published", refreshed
        assert refreshed["evidence_reused"] is False
        assert len(calls) == 2
        assert list(path.parent.iterdir()) == [path]
    finally:
        root.chmod(0o755)
        for path in root.iterdir():
            path.chmod(0o644)


def test_cache_write_error_does_not_fall_back_to_source(tmp_path, monkeypatch):
    root = tmp_path / "skill"
    root.mkdir()
    context = PublishContext(source=SkillSource(file_path=str(root)))
    def reject_replace(*args):
        raise PermissionError("cache is read-only")
    monkeypatch.setattr("publisher.artifacts.report.os.replace", reject_replace)
    with pytest.raises(OSError, match="Cannot write Publisher cache report"):
        write_report(context, status="running")
    assert list(root.iterdir()) == []
    assert list(report_path(root).parent.iterdir()) == []


def test_cache_inside_source_is_rejected_before_writing(tmp_path, monkeypatch):
    root = tmp_path / "skill"
    root.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "cache"))
    with pytest.raises(ValueError, match="outside the skill directory"):
        report_path(root)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("legacy", [False, True])
def test_invalid_source_blocks_before_external_evaluation(tmp_path, monkeypatch, legacy):
    root = tmp_path / "skill"
    root.mkdir()
    extra = "metadata:\n  version: 1.0.0\n" if legacy else ""
    (root / "SKILL.md").write_text("---\nname: skill\ndescription: Use when testing.\n" + extra + "---\nInstructions")
    (root / "aptitude.yaml").write_text("tags: [test]\n" if legacy else "unknown: value\n")
    def unexpected(**kwargs):
        pytest.fail("invalid source reached an external evaluator")
    monkeypatch.setattr("publisher.stages.security.run_llm_guard_security_scan", unexpected)
    monkeypatch.setattr("publisher.stages.performance_exam.run_upskill_evaluation", unexpected)
    pipeline = PublisherPipeline()
    context = pipeline.run(pipeline.create_context(file_path=str(root)))
    payload = json.loads(Path(context.report_path).read_text())
    assert payload["status"] == "blocked"
    assert any("aptitude.yaml" in issue for gate in payload["gates"] for issue in gate["blocking_issues"])
