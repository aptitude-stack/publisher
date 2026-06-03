"""Optional Hugging Face upskill evaluation adapter."""

from __future__ import annotations

import json
import os
import re
import subprocess
import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from publisher.integrations.external_tools import (
    configured_bool,
    render_command,
    resolve_executable,
    run_command,
)


_DEFAULT_TIMEOUT_SECONDS = 600


@dataclass(frozen=True, slots=True)
class UpskillEvaluation:
    """Normalized validation/performance result from upskill."""

    status: str
    score: float | None = None
    passed: bool | None = None
    test_case_count: int | None = None
    baseline_success_rate: float | None = None
    skilled_success_rate: float | None = None
    skill_lift: float | None = None
    baseline_avg_tokens: int | None = None
    skilled_avg_tokens: int | None = None
    token_delta: int | None = None
    models_tested: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    artifact_dir: str | None = None
    reason: str | None = None


def run_upskill_evaluation(*, skill_root: Path, artifacts_dir: Path) -> UpskillEvaluation:
    """Run upskill when configured and normalize output for publisher stages."""
    if not configured_bool("PUBLISHER_UPSKILL_ENABLED", default=True):
        return UpskillEvaluation(status="disabled", reason="PUBLISHER_UPSKILL_ENABLED is false")

    upskill_dir = artifacts_dir / "upskill"
    _reset_artifact_dir(upskill_dir)
    upskill_dir.mkdir(parents=True, exist_ok=True)

    direct_result = _run_direct_openai_compatible_eval(skill_root=skill_root, artifact_dir=upskill_dir)
    if direct_result is not None:
        return direct_result

    command = _build_command(skill_root=skill_root, artifact_dir=upskill_dir)
    if command is None:
        return UpskillEvaluation(
            status="not_available",
            artifact_dir=str(upskill_dir),
            reason="install upskill or set PUBLISHER_UPSKILL_COMMAND",
        )

    try:
        completed = run_command(
            command,
            cwd=skill_root,
            timeout_seconds=int(os.environ.get("PUBLISHER_UPSKILL_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return UpskillEvaluation(
            status="failed",
            command=command,
            artifact_dir=str(upskill_dir),
            reason=str(exc),
        )

    (upskill_dir / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (upskill_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    payloads = _load_payloads(upskill_dir)
    payloads.extend(_payloads_from_text(completed.stdout))
    parsed = _normalize_payloads(payloads)

    status = "scored" if completed.returncode == 0 else "failed"
    reason = None if completed.returncode == 0 else f"upskill exited with status {completed.returncode}"
    if status == "scored" and _looks_like_empty_provider_result(parsed):
        status = "failed"
        reason = (
            "upskill produced zero-token failing results; provider calls likely failed "
            "or returned no usable responses"
        )
        parsed = {}
    return UpskillEvaluation(
        status=status,
        score=parsed.get("score"),
        passed=parsed.get("passed"),
        test_case_count=parsed.get("test_case_count"),
        baseline_success_rate=parsed.get("baseline_success_rate"),
        skilled_success_rate=parsed.get("skilled_success_rate"),
        skill_lift=parsed.get("skill_lift"),
        baseline_avg_tokens=parsed.get("baseline_avg_tokens"),
        skilled_avg_tokens=parsed.get("skilled_avg_tokens"),
        token_delta=parsed.get("token_delta"),
        models_tested=parsed.get("models_tested", []),
        validation_errors=parsed.get("validation_errors", []),
        validation_warnings=parsed.get("validation_warnings", []),
        command=command,
        artifact_dir=str(upskill_dir),
        reason=reason,
    )


def _looks_like_empty_provider_result(parsed: dict[str, Any]) -> bool:
    """Detect CLI-success output that represents failed provider calls."""
    test_count = parsed.get("test_case_count")
    if not isinstance(test_count, int) or test_count <= 0:
        return False

    baseline_tokens = parsed.get("baseline_avg_tokens")
    skilled_tokens = parsed.get("skilled_avg_tokens")
    baseline_success = parsed.get("baseline_success_rate")
    skilled_success = parsed.get("skilled_success_rate")
    return (
        baseline_tokens == 0
        and skilled_tokens == 0
        and baseline_success == 0.0
        and skilled_success == 0.0
    )


def _reset_artifact_dir(path: Path) -> None:
    """Clear stale Upskill files so each result belongs to the current run."""
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _run_direct_openai_compatible_eval(*, skill_root: Path, artifact_dir: Path) -> UpskillEvaluation | None:
    """Run Upskill through its Python API when a custom OpenAI-compatible key is needed."""
    base_url = os.environ.get("UPSKILL_BASE_URL")
    api_key = (
        os.environ.get("UPSKILL_API_KEY")
        or os.environ.get("GROQ_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    tests_path = os.environ.get("UPSKILL_TESTS_PATH")
    if not base_url or not api_key:
        return None
    if not tests_path and not configured_bool("UPSKILL_USE_DEFAULT_TESTS", default=True):
        return None

    try:
        from upskill.models import Skill, TestCase
    except ImportError as exc:
        return UpskillEvaluation(status="not_available", reason=str(exc))

    try:
        skill = _load_upskill_skill(skill_root, Skill)
        test_cases = (
            _load_upskill_test_cases(Path(tests_path), TestCase)
            if tests_path
            else _default_upskill_test_cases(skill_root=skill_root, test_case_type=TestCase)
        )
    except (OSError, ValueError, TypeError) as exc:
        return UpskillEvaluation(status="failed", reason=str(exc))

    model = _split_models(os.environ.get("UPSKILL_MODELS"))
    model_name = model[0] if model else None
    provider = os.environ.get("UPSKILL_PROVIDER", "openai")
    no_baseline = configured_bool("UPSKILL_NO_BASELINE", default=False)

    try:
        result = asyncio.run(
            _evaluate_direct_skill(
                skill=skill,
                test_cases=test_cases,
                model=model_name,
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                run_baseline=not no_baseline,
            )
        )
    except Exception as exc:  # noqa: BLE001 - external evaluator errors are normalized.
        return UpskillEvaluation(status="failed", reason=str(exc))

    baseline_avg_tokens = _average_tokens(result.baseline_total_tokens, len(test_cases))
    skilled_avg_tokens = _average_tokens(result.with_skill_total_tokens, len(test_cases))
    token_delta = (
        skilled_avg_tokens - baseline_avg_tokens
        if baseline_avg_tokens is not None and skilled_avg_tokens is not None
        else None
    )
    score = _score_direct_result(
        skilled_success_rate=result.with_skill_success_rate,
        skill_lift=result.skill_lift,
        token_delta=token_delta,
        baseline_avg_tokens=baseline_avg_tokens,
    )
    if _direct_result_looks_empty_provider_failure(
        test_case_count=len(test_cases),
        baseline_avg_tokens=baseline_avg_tokens,
        skilled_avg_tokens=skilled_avg_tokens,
        baseline_success_rate=result.baseline_success_rate,
        skilled_success_rate=result.with_skill_success_rate,
    ):
        return UpskillEvaluation(
            status="failed",
            artifact_dir=str(artifact_dir),
            reason=(
                "upskill produced zero-token failing results; provider calls likely failed "
                "or returned no usable responses"
            ),
        )

    return UpskillEvaluation(
        status="scored",
        score=score,
        passed=result.is_beneficial,
        test_case_count=len(test_cases),
        baseline_success_rate=result.baseline_success_rate,
        skilled_success_rate=result.with_skill_success_rate,
        skill_lift=result.skill_lift,
        baseline_avg_tokens=baseline_avg_tokens,
        skilled_avg_tokens=skilled_avg_tokens,
        token_delta=token_delta,
        models_tested=[result.model],
        command=["upskill-python-api", str(skill_root)],
        artifact_dir=str(artifact_dir),
    )


def _direct_result_looks_empty_provider_failure(
    *,
    test_case_count: int,
    baseline_avg_tokens: int | None,
    skilled_avg_tokens: int | None,
    baseline_success_rate: float,
    skilled_success_rate: float,
) -> bool:
    if test_case_count <= 0:
        return False
    return (
        baseline_avg_tokens == 0
        and skilled_avg_tokens == 0
        and baseline_success_rate == 0.0
        and skilled_success_rate == 0.0
    )


async def _evaluate_direct_skill(
    *,
    skill: Any,
    test_cases: list[Any],
    model: str | None,
    provider: str,
    base_url: str | None,
    api_key: str | None,
    run_baseline: bool,
) -> Any:
    """Run the direct evaluator while explicitly closing async provider clients."""
    from upskill.config import Config
    from upskill.models import EvalResults

    config = Config.load()
    model_name = model or config.effective_eval_model
    results = EvalResults(skill_name=skill.name, model=model_name)

    for test_case in test_cases:
        results.with_skill_results.append(
            await _run_direct_test(
                test_case=test_case,
                skill=skill,
                model=model_name,
                provider=provider,
                base_url=base_url,
                api_key=api_key,
            )
        )
    _populate_direct_metrics(results, test_cases, prefix="with_skill")

    if run_baseline:
        for test_case in test_cases:
            results.baseline_results.append(
                await _run_direct_test(
                    test_case=test_case,
                    skill=None,
                    model=model_name,
                    provider=provider,
                    base_url=base_url,
                    api_key=api_key,
                )
            )
        _populate_direct_metrics(results, test_cases, prefix="baseline")

    return results


async def _run_direct_test(
    *,
    test_case: Any,
    skill: Any | None,
    model: str,
    provider: str,
    base_url: str | None,
    api_key: str | None,
) -> Any:
    from upskill.models import TestResult

    system = "You are a helpful AI assistant."
    if skill:
        system += f"\n\n## Skill: {skill.name}\n\n{skill.body}"

    user_content = test_case.input
    if test_case.context and "files" in test_case.context:
        for filename, content in test_case.context["files"].items():
            user_content += f"\n\n```{filename}\n{content}\n```"

    try:
        if provider == "openai":
            output, tokens_used = await _run_openai_chat(
                model=model,
                system=system,
                user_content=user_content,
                base_url=base_url,
                api_key=api_key,
            )
        else:
            output, tokens_used = await _run_anthropic_chat(
                model=model,
                system=system,
                user_content=user_content,
                base_url=base_url,
                api_key=api_key,
            )
        return TestResult(
            test_case=test_case,
            success=_check_expected(output, test_case.expected),
            output=output,
            tokens_used=tokens_used,
            turns=1,
        )
    except Exception as exc:  # noqa: BLE001 - provider errors become test failures.
        return TestResult(test_case=test_case, success=False, error=str(exc))


async def _run_openai_chat(
    *,
    model: str,
    system: str,
    user_content: str,
    base_url: str | None,
    api_key: str | None,
) -> tuple[str, int]:
    from openai import AsyncOpenAI

    if base_url and not api_key:
        api_key = "sk-no-key-required"

    async with AsyncOpenAI(base_url=base_url, api_key=api_key) as client:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )
    output = response.choices[0].message.content or ""
    tokens_used = (
        response.usage.prompt_tokens + response.usage.completion_tokens
        if response.usage
        else 0
    )
    return output, tokens_used


async def _run_anthropic_chat(
    *,
    model: str,
    system: str,
    user_content: str,
    base_url: str | None,
    api_key: str | None,
) -> tuple[str, int]:
    from anthropic import AsyncAnthropic

    kwargs = {}
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key

    async with AsyncAnthropic(**kwargs) as client:
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
    output = _extract_anthropic_text(response.content)
    tokens_used = response.usage.input_tokens + response.usage.output_tokens
    return output, tokens_used


def _extract_anthropic_text(content: list[Any]) -> str:
    texts: list[str] = []
    for block in content:
        if hasattr(block, "text"):
            texts.append(block.text)
        elif hasattr(block, "thinking"):
            texts.append(block.thinking)
        elif isinstance(block, dict):
            if block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            elif block.get("type") == "thinking":
                texts.append(str(block.get("thinking", "")))
    return "\n".join(texts)


def _check_expected(output: str, expected: dict[str, Any] | None) -> bool:
    if not expected:
        return True
    contains = expected.get("contains")
    if contains is not None:
        return str(contains).lower() in output.lower()
    return True


def _populate_direct_metrics(results: Any, test_cases: list[Any], *, prefix: str) -> None:
    result_items = getattr(results, f"{prefix}_results")
    successes = sum(1 for result in result_items if result.success)
    setattr(
        results,
        f"{prefix}_success_rate",
        successes / len(test_cases) if test_cases else 0,
    )
    setattr(
        results,
        f"{prefix}_total_tokens",
        sum(result.tokens_used for result in result_items),
    )
    setattr(
        results,
        f"{prefix}_avg_turns",
        sum(result.turns for result in result_items) / len(test_cases) if test_cases else 0,
    )


def _load_upskill_skill(skill_root: Path, skill_type: Any) -> Any:
    """Load either publisher-style frontmatter or current Upskill markdown."""
    skill_file = skill_root / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        closing_index = content.find("\n---\n", 4)
        if closing_index == -1:
            raise ValueError("SKILL.md frontmatter must end with a closing --- delimiter.")
        frontmatter = _parse_simple_yaml(content[4:closing_index])
        body = content[closing_index + 5 :]
        name = str(frontmatter.get("name") or skill_root.name)
        description = str(frontmatter.get("description") or name)
        return skill_type(name=name, description=description, body=body)

    lines = content.splitlines()
    name = lines[0].lstrip("# ").strip() if lines else skill_root.name
    description = lines[2].strip() if len(lines) > 2 else name
    body = "\n".join(lines[4:]) if len(lines) > 4 else content
    return skill_type(name=name, description=description, body=body)


def _parse_simple_yaml(frontmatter_text: str) -> dict[str, Any]:
    """Parse the shallow YAML frontmatter fields needed by Upskill."""
    result: dict[str, Any] = {}
    for raw_line in frontmatter_text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith(" ") or ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        result[key.strip()] = value.strip().strip("'\"")
    return result


def _load_upskill_test_cases(tests_path: Path, test_case_type: Any) -> list[Any]:
    """Load Upskill test cases from JSON."""
    payload = json.loads(tests_path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("UPSKILL_TESTS_PATH must point to a JSON list or an object with cases.")
    return [test_case_type(**case) for case in cases]


def _default_upskill_test_cases(*, skill_root: Path, test_case_type: Any) -> list[Any]:
    """Build one small deterministic test case when no external tests file is configured."""
    skill_file = skill_root / "SKILL.md"
    name = skill_root.name
    description = name
    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError:
        content = ""
    if content.startswith("---\n"):
        closing_index = content.find("\n---\n", 4)
        if closing_index != -1:
            frontmatter = _parse_simple_yaml(content[4:closing_index])
            name = str(frontmatter.get("name") or name)
            description = str(frontmatter.get("description") or description)

    return [
        test_case_type(
            input=(
                f"Use the {name} skill for this task. "
                f"Task description: {description}"
            ),
            expected={"mentions_skill_purpose": True},
        )
    ]


def _average_tokens(total_tokens: int, test_count: int) -> int | None:
    """Return integer average tokens per test case."""
    if test_count <= 0:
        return None
    return round(total_tokens / test_count)


def _score_direct_result(
    *,
    skilled_success_rate: float,
    skill_lift: float,
    token_delta: int | None,
    baseline_avg_tokens: int | None,
) -> float:
    """Score direct Upskill results with the same rubric used for parsed CLI tables."""
    token_score = 0.0
    if token_delta is not None and token_delta < 0:
        token_score = min(1.0, abs(token_delta) / max(baseline_avg_tokens or 1, 1))
    return round(
        (skilled_success_rate * 0.70)
        + (max(0.0, skill_lift) * 0.20)
        + (token_score * 0.10),
        2,
    )


def _build_command(*, skill_root: Path, artifact_dir: Path) -> list[str] | None:
    values = {
        "skill_path": str(skill_root),
        "artifact_dir": str(artifact_dir),
        "runs_dir": str(artifact_dir / "runs"),
    }
    command_template = os.environ.get("PUBLISHER_UPSKILL_COMMAND")
    if command_template:
        return render_command(command_template, values)

    executable = resolve_executable("upskill", start=skill_root)
    if not executable:
        return None

    command = [
        executable,
        "eval",
        str(skill_root),
    ]
    tests_path = os.environ.get("UPSKILL_TESTS_PATH")
    if tests_path:
        command.extend(["--tests", tests_path])
    provider = os.environ.get("UPSKILL_PROVIDER")
    if provider:
        command.extend(["--provider", provider])
    base_url = os.environ.get("UPSKILL_BASE_URL")
    if base_url:
        command.extend(["--base-url", base_url])
    for model in _split_models(os.environ.get("UPSKILL_MODELS")):
        command.extend(["-m", model])
    if configured_bool("UPSKILL_NO_BASELINE", default=False):
        command.append("--no-baseline")
    return command


def _split_models(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_payloads(artifact_dir: Path) -> list[Any]:
    payloads: list[Any] = []
    for candidate in sorted(artifact_dir.glob("**/*")):
        if candidate.suffix.lower() not in {".json", ".jsonl"}:
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        if candidate.suffix.lower() == ".jsonl":
            for line in content.splitlines():
                payloads.extend(_payloads_from_text(line))
        else:
            payloads.extend(_payloads_from_text(content))
    return payloads


def _payloads_from_text(value: str) -> list[Any]:
    value = value.strip()
    if not value:
        return []
    try:
        return [json.loads(value)]
    except json.JSONDecodeError:
        metrics = _metrics_from_table_output(value)
        return [metrics] if metrics else []


def _metrics_from_table_output(value: str) -> dict[str, Any]:
    """Parse the text table printed by current upskill CLI releases."""
    metrics: dict[str, Any] = {}

    success_match = re.search(
        r"│\s*success\s*│\s*(\d+)/(\d+)\s+\((\d+)%\)\s*│\s*(\d+)/(\d+)\s+\((\d+)%\)",
        value,
    )
    if success_match:
        baseline_passed, baseline_total, baseline_percent, skilled_passed, skilled_total, skilled_percent = (
            success_match.groups()
        )
        total = max(int(baseline_total), int(skilled_total))
        metrics["test_case_count"] = total
        metrics["baseline_success_rate"] = int(baseline_percent) / 100
        metrics["skilled_success_rate"] = int(skilled_percent) / 100
        metrics["passed"] = int(skilled_passed) == int(skilled_total) and int(skilled_total) > 0
        metrics["skill_lift"] = round(
            metrics["skilled_success_rate"] - metrics["baseline_success_rate"],
            4,
        )

    token_match = re.search(r"│\s*tokens\s*│\s*(\d+)\s*│\s*(\d+)", value)
    if token_match:
        baseline_tokens, skilled_tokens = (int(item) for item in token_match.groups())
        metrics["baseline_avg_tokens"] = baseline_tokens
        metrics["skilled_avg_tokens"] = skilled_tokens
        metrics["token_delta"] = skilled_tokens - baseline_tokens

    if "skilled_success_rate" in metrics:
        lift = metrics.get("skill_lift") or 0.0
        token_delta = metrics.get("token_delta")
        token_score = 0.0
        if isinstance(token_delta, int) and token_delta < 0:
            baseline_tokens = metrics.get("baseline_avg_tokens") or 1
            token_score = min(1.0, abs(token_delta) / max(baseline_tokens, 1))
        metrics["score"] = round(
            (metrics["skilled_success_rate"] * 0.70)
            + (max(0.0, lift) * 0.20)
            + (token_score * 0.10),
            2,
        )

    return metrics


def _normalize_payloads(payloads: list[Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "validation_errors": [],
        "validation_warnings": [],
        "models_tested": [],
    }
    for payload in payloads:
        _merge_metrics(metrics, _metrics_from_payload(payload))
    return metrics


def _merge_metrics(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if value is None:
            continue
        if key in {"validation_errors", "validation_warnings", "models_tested"}:
            current = target.setdefault(key, [])
            for item in value:
                if item not in current:
                    current.append(item)
            continue
        target[key] = value


def _metrics_from_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        metrics: dict[str, Any] = {}
        for item in payload:
            _merge_metrics(metrics, _metrics_from_payload(item))
        return metrics
    if not isinstance(payload, dict):
        return {}

    metrics = {
        "score": _coerce_float(
            _first_present(
                payload,
                "score",
                "overall_score",
                "success_rate",
                "pass_rate",
            )
        ),
        "passed": _coerce_bool(payload.get("passed"))
        if payload.get("passed") is not None
        else _coerce_bool(payload.get("success")),
        "test_case_count": _coerce_int(
            _first_present(payload, "test_case_count", "tests", "num_tests")
        ),
        "baseline_success_rate": _coerce_float(
            _first_present(payload, "baseline_success_rate", "baseline_pass_rate")
        ),
        "skilled_success_rate": _coerce_float(
            _first_present(
                payload,
                "skilled_success_rate",
                "with_skill_success_rate",
                "skill_success_rate",
            )
        ),
        "baseline_avg_tokens": _coerce_int(
            _first_present(payload, "baseline_avg_tokens", "baseline_tokens")
        ),
        "skilled_avg_tokens": _coerce_int(
            _first_present(
                payload,
                "skilled_avg_tokens",
                "with_skill_avg_tokens",
                "skill_tokens",
            )
        ),
        "models_tested": _coerce_string_list(payload.get("models") or payload.get("models_tested")),
        "validation_errors": _coerce_string_list(payload.get("errors")),
        "validation_warnings": _coerce_string_list(payload.get("warnings")),
    }
    if metrics["baseline_success_rate"] is not None and metrics["skilled_success_rate"] is not None:
        metrics["skill_lift"] = round(
            metrics["skilled_success_rate"] - metrics["baseline_success_rate"], 4
        )
    else:
        metrics["skill_lift"] = _coerce_float(_first_present(payload, "skill_lift", "lift"))

    if metrics["baseline_avg_tokens"] is not None and metrics["skilled_avg_tokens"] is not None:
        metrics["token_delta"] = metrics["skilled_avg_tokens"] - metrics["baseline_avg_tokens"]
    else:
        metrics["token_delta"] = _coerce_int(payload.get("token_delta"))

    nested_metrics: dict[str, Any] = {}
    for key in ("summary", "result", "results", "metrics", "benchmark"):
        _merge_metrics(nested_metrics, _metrics_from_payload(payload.get(key)))
    _merge_metrics(nested_metrics, metrics)
    return nested_metrics


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    """Return the first non-None value without discarding valid zeroes."""
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and "/" in value:
        left, right = value.split("/", 1)
        try:
            denominator = float(right.strip())
            if denominator == 0:
                return None
            return round(float(left.strip()) / denominator, 4)
        except ValueError:
            return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric > 1.0 and numeric <= 100.0:
        numeric = numeric / 100.0
    return max(0.0, min(1.0, numeric))


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []
