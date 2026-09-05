"""Optional Hugging Face upskill evaluation adapter."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
    baseline_total_tokens: int | None = None
    skilled_total_tokens: int | None = None
    token_delta: int | None = None
    models_tested: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    artifact_dir: str | None = None
    reason: str | None = None


def run_upskill_evaluation(
    *, skill_root: Path, artifacts_dir: Path | None = None
) -> UpskillEvaluation:
    """Run upskill when configured and normalize output for publisher stages."""
    if not configured_bool("PUBLISHER_UPSKILL_ENABLED", default=True):
        return UpskillEvaluation(status="disabled", reason="PUBLISHER_UPSKILL_ENABLED is false")

    tests_path, tests_error = _validated_tests_path()
    if tests_error:
        return UpskillEvaluation(
            status="failed",
            reason=tests_error,
        )
    if configured_bool("UPSKILL_NO_BASELINE", default=False):
        return UpskillEvaluation(
            status="failed",
            reason="UPSKILL_NO_BASELINE must be false for publishable performance evidence",
        )

    source_skill_root = Path(skill_root).expanduser().resolve()
    provider = os.environ.get("UPSKILL_PROVIDER", _DEFAULT_PROVIDER)
    base_url = os.environ.get("UPSKILL_BASE_URL")
    api_key = _upskill_api_key(base_url=base_url)
    model = _split_models(os.environ.get("UPSKILL_MODELS"))
    model_name = model[0] if model else _DEFAULT_MODEL
    if provider == "openai" and not api_key:
        return UpskillEvaluation(
            status="failed",
            reason="set OPENAI_API_KEY for official OpenAI evaluation",
        )

    command: list[str] | None = None
    try:
        with _temporary_directory_outside(source_skill_root) as temporary_root:
            workspace = Path(temporary_root).resolve()
            if _is_path_within(workspace, source_skill_root):
                raise OSError("temporary evaluator workspace must be outside the skill source")
            isolated_skill_root = workspace / "source" / source_skill_root.name
            output_dir = workspace / "output"
            _copy_skill_tree(source_skill_root, isolated_skill_root)
            output_dir.mkdir()

            command = _build_command(
                skill_root=isolated_skill_root,
                artifact_dir=output_dir,
                tests_path=tests_path,
                provider=provider,
                models=model or [model_name],
                executable_start=source_skill_root,
            )
            if command is None:
                return UpskillEvaluation(
                    status="not_available",
                    reason="install upskill or set PUBLISHER_UPSKILL_COMMAND",
                )

            command_env = os.environ.copy()
            command_env["PUBLISHER_UPSKILL_TEST_GEN_MODEL"] = _upskill_model_reference(
                provider,
                model[0] if model else model_name,
            )
            if base_url and provider == "openai":
                command_env["OPENAI_API_BASE"] = base_url
            if api_key and provider == "openai":
                command_env["OPENAI_API_KEY"] = api_key
            if tests_path:
                command_env["UPSKILL_TESTS_PATH"] = str(tests_path)
            command_env.setdefault("PYTHONIOENCODING", "utf-8")
            command_env.setdefault("PYTHONUTF8", "1")
            timeout_seconds = int(
                os.environ.get("PUBLISHER_UPSKILL_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
            )
            try:
                completed = run_command(
                    command,
                    cwd=isolated_skill_root,
                    timeout_seconds=timeout_seconds,
                    env=command_env,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return UpskillEvaluation(
                    status="failed",
                    command=_sanitize_command(command, isolated_skill_root, output_dir),
                    reason=_sanitize_text(str(exc), workspace),
                )

            payloads = _load_payloads(output_dir)
            payloads.extend(_payloads_from_text(completed.stdout or ""))
            unusable_generated_suite = (
                tests_path is None and _looks_like_unusable_generated_suite(payloads)
            )
            parsed = _normalize_payloads(payloads)

            status = "scored" if completed.returncode == 0 else "failed"
            reason = (
                None
                if completed.returncode == 0
                else f"upskill exited with status {completed.returncode}"
            )
            validation_errors = parsed.get("validation_errors", [])
            if status == "scored" and _looks_like_empty_provider_result(parsed):
                status = "failed"
                reason = (
                    "upskill produced zero-token failing results; provider calls likely failed "
                    "or returned no usable responses"
                )
                parsed = {}
            elif status == "scored" and unusable_generated_suite:
                status = "inconclusive"
                reason = "upskill generated tests produced unusable comparative evidence"
                validation_errors = [
                    "generated exact-text verifiers passed no assertions despite non-empty "
                    "model outputs"
                ]
                parsed["score"] = None
                parsed["passed"] = None
                parsed["recommendations"] = []
            elif status == "scored":
                validation_errors = _missing_upskill_metrics_errors(parsed)
                if validation_errors:
                    status = "failed"
                    reason = "upskill did not produce complete scored performance evidence"
            if status == "scored" and isinstance(parsed.get("score"), (int, float)):
                parsed["score"] = round(min(1.0, parsed["score"] + 0.30), 2)

            command = _sanitize_command(command, isolated_skill_root, output_dir)
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
                baseline_total_tokens=parsed.get("baseline_total_tokens"),
                skilled_total_tokens=parsed.get("skilled_total_tokens"),
                token_delta=parsed.get("token_delta"),
                models_tested=parsed.get("models_tested", []),
                validation_errors=_sanitize_string_list(validation_errors, workspace),
                validation_warnings=_sanitize_string_list(
                    parsed.get("validation_warnings", []), workspace
                ),
                recommendations=_sanitize_string_list(parsed.get("recommendations", []), workspace),
                command=command,
                reason=_sanitize_text(reason, workspace) if reason else None,
            )
    except OSError as exc:
        return UpskillEvaluation(
            status="failed",
            command=_sanitize_command(command or [], None, None),
            reason=f"could not prepare isolated Upskill workspace: {exc}",
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


def _copy_skill_tree(source: Path, destination: Path) -> None:
    """Copy evaluator input without carrying publisher artifacts or directory symlinks."""
    source = source.resolve()
    destination = destination.resolve()
    if _is_path_within(destination, source):
        raise OSError("temporary evaluator copy must be outside the skill source")

    for current, directory_names, _file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative_parts = current_path.relative_to(source).parts
        if ".publisher_artifacts" in relative_parts:
            directory_names[:] = []
            continue
        for name in tuple(directory_names):
            path = current_path / name
            if name == ".publisher_artifacts":
                directory_names.remove(name)
            elif path.is_symlink() and path.is_dir():
                raise OSError(f"directory symlink is not supported in evaluator input: {path}")

    def ignored(path: str, names: list[str]) -> list[str]:
        return [name for name in names if name == ".publisher_artifacts"]

    shutil.copytree(source, destination, ignore=ignored)


def _temporary_directory_outside(source: Path):
    """Create an evaluator workspace in a directory that is outside the source."""
    source = source.resolve()
    candidates = (Path(tempfile.gettempdir()), source.parent)
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if candidate.is_dir() and not _is_path_within(candidate, source):
            return tempfile.TemporaryDirectory(
                prefix="aptitude-publisher-eval-",
                dir=str(candidate),
            )
    raise OSError("could not create an evaluator workspace outside the skill source")


def _is_path_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _sanitize_command(
    command: list[str],
    isolated_skill_root: Path | None,
    output_dir: Path | None,
) -> list[str]:
    """Keep command shape while removing temporary paths and configured secrets."""
    secrets = _configured_secrets()
    temporary_root = isolated_skill_root.parent if isolated_skill_root else None
    sanitized: list[str] = []
    redact_next = False
    for part in command:
        if redact_next:
            sanitized.append("[redacted]")
            redact_next = False
            continue
        value = str(part)
        if temporary_root:
            if isolated_skill_root and _path_prefix(value, isolated_skill_root):
                value = _replace_path(value, isolated_skill_root, "<temporary-skill>")
            if output_dir and _path_prefix(value, output_dir):
                value = _replace_path(value, output_dir, "<temporary-output>")
            value = _replace_path(value, temporary_root, "<temporary-workspace>")
        value = _redact_secrets(value, secrets)
        if _is_sensitive_option(value):
            if "=" in value:
                option, _separator, _secret = value.partition("=")
                value = f"{option}=[redacted]"
            else:
                redact_next = True
        sanitized.append(value)
    return sanitized


def _sanitize_text(value: str | None, temporary_root: Path) -> str | None:
    if value is None:
        return None
    return _redact_secrets(
        value.replace(str(temporary_root), "<temporary-workspace>"),
        _configured_secrets(),
    )


def _sanitize_string_list(values: object, temporary_root: Path) -> list[str]:
    if not isinstance(values, list):
        return []
    return [
        sanitized
        for item in values
        if (sanitized := _sanitize_text(str(item).strip(), temporary_root))
    ]


def _configured_secrets() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for name, value in os.environ.items()
                if value
                and len(value) >= 4
                and any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            },
            key=len,
            reverse=True,
        )
    )


def _redact_secrets(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        value = value.replace(secret, "[redacted]")
    return value


def _is_sensitive_option(value: str) -> bool:
    option = value.split("=", 1)[0].lower()
    return any(
        marker in option
        for marker in (
            "api-key",
            "api_key",
            "token",
            "password",
            "secret",
            "authorization",
            "credential",
        )
    )


def _path_prefix(value: str, path: Path) -> bool:
    return value == str(path) or value.startswith(str(path) + "/")


def _replace_path(value: str, path: Path, replacement: str) -> str:
    return replacement + value[len(str(path)) :]


def _validated_tests_path() -> tuple[Path | None, str | None]:
    value = os.environ.get("UPSKILL_TESTS_PATH")
    if not value or value == _EXAMPLE_TESTS_PATH:
        return None, None

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
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
    executable_start: Path | None = None,
) -> list[str] | None:
    values = {
        "skill_path": str(skill_root),
        "artifact_dir": str(artifact_dir),
        "runs_dir": str(artifact_dir / "runs"),
    }
    command_template = os.environ.get("PUBLISHER_UPSKILL_COMMAND")
    if command_template:
        return render_command(command_template, values)

    executable = resolve_executable("upskill", start=executable_start or skill_root)
    if not executable:
        return None

    command = [
        sys.executable,
        "-m",
        "publisher.integrations.upskill_cli",
        "eval",
        str(skill_root),
        "--runs-dir",
        str(artifact_dir),
    ]
    if tests_path:
        command.extend(["--tests", str(tests_path)])
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
    baseline_total_tokens = _coerce_int(
        baseline.get("stats", {}).get("total_tokens")
        if isinstance(baseline.get("stats"), dict)
        else None
    )
    skilled_total_tokens = _coerce_int(
        skilled.get("stats", {}).get("total_tokens")
        if isinstance(skilled.get("stats"), dict)
        else None
    )
    metrics: dict[str, Any] = {
        "test_case_count": _coerce_int(skilled.get("assertions_total")),
        "baseline_success_rate": baseline_success,
        "skilled_success_rate": skilled_success,
        "baseline_avg_tokens": baseline_tokens,
        "skilled_avg_tokens": skilled_tokens,
        "baseline_total_tokens": baseline_total_tokens,
        "skilled_total_tokens": skilled_total_tokens,
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
