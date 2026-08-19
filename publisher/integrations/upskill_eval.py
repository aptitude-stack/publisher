"""Optional Hugging Face upskill evaluation adapter."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
_DEFAULT_MODEL = "gpt-4.1-mini"
_DEFAULT_PROVIDER = "openai"
_EXAMPLE_TESTS_PATH = "/absolute/path/to/upskill-tests.json"


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
    recommendations: list[str] = field(default_factory=list)
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

    tests_path, tests_error = _validated_tests_path()
    if tests_error:
        return UpskillEvaluation(
            status="failed",
            artifact_dir=str(upskill_dir),
            reason=tests_error,
        )
    if configured_bool("UPSKILL_NO_BASELINE", default=False):
        return UpskillEvaluation(
            status="failed",
            artifact_dir=str(upskill_dir),
            reason="UPSKILL_NO_BASELINE must be false for publishable performance evidence",
        )

    provider = os.environ.get("UPSKILL_PROVIDER", _DEFAULT_PROVIDER)
    base_url = os.environ.get("UPSKILL_BASE_URL")
    api_key = _upskill_api_key(base_url=base_url)
    model = _split_models(os.environ.get("UPSKILL_MODELS"))
    model_name = model[0] if model else _DEFAULT_MODEL
    if provider == "openai" and not api_key:
        return UpskillEvaluation(
            status="failed",
            artifact_dir=str(upskill_dir),
            reason="set OPENAI_API_KEY for official OpenAI evaluation",
        )

    command = _build_command(
        skill_root=skill_root,
        artifact_dir=upskill_dir,
        tests_path=tests_path,
        provider=provider,
        models=model or [model_name],
    )
    if command is None:
        return UpskillEvaluation(
            status="not_available",
            artifact_dir=str(upskill_dir),
            reason="install upskill or set PUBLISHER_UPSKILL_COMMAND",
        )

    try:
        command_env = os.environ.copy()
        command_env["UPSKILL_CONFIG"] = str(
            _write_upskill_config(
                upskill_dir=upskill_dir,
                provider=provider,
                model=model[0] if model else model_name,
            )
        )
        if base_url and provider == "openai":
            command_env["OPENAI_API_BASE"] = base_url
        if api_key and provider == "openai":
            command_env["OPENAI_API_KEY"] = api_key
        completed = run_command(
            command,
            cwd=skill_root,
            timeout_seconds=int(os.environ.get("PUBLISHER_UPSKILL_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)),
            env=command_env,
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
    duplicate_generated_verifiers = tests_path is None and _has_duplicate_exact_text_verifiers(
        payloads
    )
    unusable_generated_suite = tests_path is None and _looks_like_unusable_generated_suite(payloads)
    parsed = _normalize_payloads(payloads)

    status = "scored" if completed.returncode == 0 else "failed"
    reason = None if completed.returncode == 0 else f"upskill exited with status {completed.returncode}"
    validation_errors = parsed.get("validation_errors", [])
    if status == "scored" and _looks_like_empty_provider_result(parsed):
        status = "failed"
        reason = (
            "upskill produced zero-token failing results; provider calls likely failed "
            "or returned no usable responses"
        )
        parsed = {}
    elif status == "scored" and (duplicate_generated_verifiers or unusable_generated_suite):
        status = "inconclusive"
        if duplicate_generated_verifiers:
            reason = "upskill generated duplicate exact-text verifiers"
            validation_errors = ["generated exact-text verifiers duplicate expected checks"]
        else:
            reason = "upskill generated tests produced unusable comparative evidence"
            validation_errors = [
                "generated exact-text verifiers passed no assertions despite non-empty model outputs"
            ]
        parsed["score"] = None
        parsed["passed"] = None
        parsed["recommendations"] = []
    elif status == "scored":
        validation_errors = _missing_upskill_metrics_errors(parsed)
        if validation_errors:
            status = "failed"
            reason = "upskill did not produce complete scored performance evidence"
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
        validation_errors=validation_errors,
        validation_warnings=parsed.get("validation_warnings", []),
        recommendations=parsed.get("recommendations", []),
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


def _looks_like_unusable_generated_suite(payloads: list[Any]) -> bool:
    """Reject generated suites that cannot award either evaluated run any credit."""
    for payload in payloads:
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            continue
        results = {
            result.get("run_type"): result
            for result in payload["results"]
            if isinstance(result, dict)
        }
        baseline = results.get("baseline")
        skilled = results.get("with_skill")
        if not isinstance(baseline, dict) or not isinstance(skilled, dict):
            continue

        def produced_output_but_passed_nothing(result: dict[str, Any]) -> bool:
            stats = result.get("stats")
            return (
                _coerce_int(result.get("assertions_passed")) == 0
                and (_coerce_int(result.get("assertions_total")) or 0) > 0
                and isinstance(stats, dict)
                and (_coerce_int(stats.get("output_tokens")) or 0) > 0
            )

        if produced_output_but_passed_nothing(baseline) and produced_output_but_passed_nothing(
            skilled
        ):
            return True
    return False


def _has_duplicate_exact_text_verifiers(payloads: list[Any]) -> bool:
    """Detect generated test cases that enforce the same exact text twice."""
    for payload in payloads:
        if not isinstance(payload, dict) or not isinstance(payload.get("test_case"), dict):
            continue
        test_case = payload["test_case"]
        expected = test_case.get("expected")
        verifiers = test_case.get("verifiers")
        if not isinstance(expected, dict) or not isinstance(verifiers, list):
            continue
        expected_values = expected.get("contains")
        if not isinstance(expected_values, list):
            continue
        normalized_expected = [str(value).strip().casefold() for value in expected_values]
        for verifier in verifiers:
            if not isinstance(verifier, dict) or verifier.get("type") != "contains":
                continue
            values = verifier.get("values")
            if not isinstance(values, list):
                continue
            if [str(value).strip().casefold() for value in values] == normalized_expected:
                return True
    return False


def _reset_artifact_dir(path: Path) -> None:
    """Clear stale Upskill files so each result belongs to the current run."""
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _validated_tests_path() -> tuple[Path | None, str | None]:
    value = os.environ.get("UPSKILL_TESTS_PATH")
    if not value or value == _EXAMPLE_TESTS_PATH:
        return None, None

    path = Path(value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"UPSKILL_TESTS_PATH is not readable JSON: {exc}"

    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list) or not cases:
        return None, "UPSKILL_TESTS_PATH must contain a non-empty cases list"
    return path, None


def _upskill_api_key(*, base_url: str | None) -> str | None:
    if base_url:
        return os.environ.get("UPSKILL_API_KEY") or os.environ.get("OPENAI_API_KEY")
    return os.environ.get("OPENAI_API_KEY")


def _build_command(
    *,
    skill_root: Path,
    artifact_dir: Path,
    tests_path: Path | None,
    provider: str,
    models: list[str],
) -> list[str] | None:
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
        "--runs-dir",
        str(artifact_dir),
    ]
    if tests_path:
        command.extend(["--tests", str(tests_path)])
    else:
        command.extend(["--test-gen-model", _upskill_model_reference(provider, models[0])])
    for model in models:
        command.extend(["--model", _upskill_model_reference(provider, model)])
    if configured_bool("PUBLISHER_UPSKILL_VERBOSE", default=False):
        command.append("--verbose")
    if configured_bool("UPSKILL_NO_BASELINE", default=False):
        command.append("--no-baseline")
    return command


def _upskill_model_reference(provider: str, model: str) -> str:
    """Use upstream Upskill's provider-qualified model format."""
    return model if model.startswith(f"{provider}.") else f"{provider}.{model}"


def _write_upskill_config(*, upskill_dir: Path, provider: str, model: str) -> Path:
    """Pin Upskill's FastAgent defaults to the selected publisher model."""
    model_reference = _upskill_model_reference(provider, model)
    fastagent_config_path = upskill_dir / "fastagent.config.yaml"
    fastagent_config_path.write_text(
        f"default_model: {model_reference}\n",
        encoding="utf-8",
    )
    config_path = upskill_dir / "upskill.config.yaml"
    config_path.write_text(
        "\n".join(
            (
                f"skill_generation_model: {model_reference}",
                f"test_gen_model: {model_reference}",
                f"eval_model: {model_reference}",
                f"fastagent_config: {fastagent_config_path}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return config_path


def _missing_upskill_metrics_errors(parsed: dict[str, Any]) -> list[str]:
    """Reject incomplete upstream run summaries before they reach publish gates."""
    errors: list[str] = []
    if not isinstance(parsed.get("test_case_count"), int) or parsed["test_case_count"] <= 0:
        errors.append("upskill did not record evaluated test cases")
    for label in ("baseline", "skilled"):
        tokens = parsed.get(f"{label}_avg_tokens")
        if not isinstance(tokens, int) or tokens <= 0:
            errors.append(f"{label} evaluation did not report token usage")
    return errors


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

    recommendations = re.findall(r"Recommendation:\s*(.+)", value)
    if recommendations:
        metrics["recommendations"] = [
            re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", item).strip()
            for item in recommendations
        ]

    return metrics


def _normalize_payloads(payloads: list[Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "validation_errors": [],
        "validation_warnings": [],
        "recommendations": [],
        "models_tested": [],
    }
    for payload in payloads:
        _merge_metrics(metrics, _metrics_from_payload(payload))
    return metrics


def _merge_metrics(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if value is None:
            continue
        if key in {"validation_errors", "validation_warnings", "recommendations", "models_tested"}:
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

    upstream_metrics = _metrics_from_upstream_run_summary(payload)
    if upstream_metrics:
        return upstream_metrics

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
        "recommendations": _coerce_string_list(
            _first_present(payload, "recommendations", "suggestions", "improvements")
        ),
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


def _metrics_from_upstream_run_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the batch summary written by the upstream Upskill CLI."""
    results = payload.get("results")
    if not isinstance(results, list):
        return {}

    by_type = {
        str(result.get("run_type")): result
        for result in results
        if isinstance(result, dict) and result.get("run_type") in {"baseline", "with_skill"}
    }
    baseline = by_type.get("baseline")
    skilled = by_type.get("with_skill")
    if baseline is None or skilled is None:
        return {}

    def success_rate(result: dict[str, Any]) -> float | None:
        total = _coerce_int(result.get("assertions_total"))
        passed = _coerce_int(result.get("assertions_passed"))
        if total is None or passed is None or total <= 0:
            return None
        return passed / total

    def avg_tokens(result: dict[str, Any]) -> int | None:
        total = _coerce_int(
            result.get("stats", {}).get("total_tokens") if isinstance(result.get("stats"), dict) else None
        )
        test_count = _coerce_int(result.get("assertions_total"))
        if total is None or test_count is None or test_count <= 0:
            return None
        return round(total / test_count)

    baseline_success = success_rate(baseline)
    skilled_success = success_rate(skilled)
    baseline_tokens = avg_tokens(baseline)
    skilled_tokens = avg_tokens(skilled)
    metrics: dict[str, Any] = {
        "test_case_count": _coerce_int(skilled.get("assertions_total")),
        "baseline_success_rate": baseline_success,
        "skilled_success_rate": skilled_success,
        "baseline_avg_tokens": baseline_tokens,
        "skilled_avg_tokens": skilled_tokens,
        "models_tested": _coerce_string_list(payload.get("model")),
        "validation_errors": [
            str(result["error_message"])
            for result in (baseline, skilled)
            if result.get("error_message")
        ],
        "validation_warnings": [],
        "recommendations": [],
    }
    if baseline_success is not None and skilled_success is not None:
        metrics["skill_lift"] = round(skilled_success - baseline_success, 4)
    if baseline_tokens is not None and skilled_tokens is not None:
        metrics["token_delta"] = skilled_tokens - baseline_tokens
    metrics["passed"] = _coerce_bool(skilled.get("passed"))
    metrics["score"] = _score_from_metrics(metrics)
    return metrics


def _score_from_metrics(metrics: dict[str, Any]) -> float | None:
    """Apply the publisher's existing performance rubric to upstream evidence."""
    skilled_success = metrics.get("skilled_success_rate")
    skill_lift = metrics.get("skill_lift")
    token_delta = metrics.get("token_delta")
    baseline_tokens = metrics.get("baseline_avg_tokens")
    if not isinstance(skilled_success, float) or not isinstance(skill_lift, float):
        return None
    token_score = 0.0
    if isinstance(token_delta, int) and token_delta < 0:
        token_score = min(1.0, abs(token_delta) / max(baseline_tokens or 1, 1))
    return round((skilled_success * 0.70) + (max(0.0, skill_lift) * 0.20) + (token_score * 0.10), 2)


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
