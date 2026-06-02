"""LLM-backed validation for Anthropic-style SKILL.md contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request


_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
_DEFAULT_MODEL = "llama-3.1-8b-instant"
_DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class LlmValidationResult:
    """Normalized result from the LLM validation pass."""

    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model: str | None = None
    reason: str | None = None


def run_llm_skill_validation(*, skill_root: Path, skill_file: Path) -> LlmValidationResult:
    """Validate SKILL.md semantically using an OpenAI-compatible chat API."""
    if not _enabled():
        return LlmValidationResult(status="disabled", reason="PUBLISHER_LLM_VALIDATION_ENABLED is false")

    api_key = _api_key()
    if not api_key:
        return LlmValidationResult(
            status="not_configured",
            reason="set PUBLISHER_LLM_VALIDATION_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY",
        )

    if not skill_file.exists():
        return LlmValidationResult(status="skipped", reason="SKILL.md does not exist")

    payload = _build_request_payload(
        skill_root=skill_root,
        skill_file=skill_file,
        model=_model_name(),
    )
    try:
        response_payload = _post_chat_completion(
            payload=payload,
            api_key=api_key,
            base_url=_base_url(),
        )
    except (OSError, error.URLError, TimeoutError) as exc:
        return LlmValidationResult(status="failed", model=_model_name(), reason=str(exc))

    try:
        content = response_payload["choices"][0]["message"]["content"]
        parsed = _parse_json_object(str(content))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return LlmValidationResult(
            status="failed",
            model=_model_name(),
            reason=f"LLM validation response was not parseable JSON: {exc}",
        )

    return LlmValidationResult(
        status="scored",
        errors=_string_list(parsed.get("errors")),
        warnings=_string_list(parsed.get("warnings")),
        notes=_string_list(parsed.get("notes")),
        model=_model_name(),
    )


def _enabled() -> bool:
    value = os.environ.get("PUBLISHER_LLM_VALIDATION_ENABLED")
    if value is None:
        return bool(_api_key())
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _api_key() -> str | None:
    return (
        os.environ.get("PUBLISHER_LLM_VALIDATION_API_KEY")
        or os.environ.get("GROQ_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


def _base_url() -> str:
    return (
        os.environ.get("PUBLISHER_LLM_VALIDATION_BASE_URL")
        or os.environ.get("UPSKILL_BASE_URL")
        or _DEFAULT_BASE_URL
    ).rstrip("/")


def _model_name() -> str:
    return (
        os.environ.get("PUBLISHER_LLM_VALIDATION_MODEL")
        or os.environ.get("GARAK_TARGET_NAME")
        or _DEFAULT_MODEL
    )


def _build_request_payload(*, skill_root: Path, skill_file: Path, model: str) -> dict[str, Any]:
    content = skill_file.read_text(encoding="utf-8")
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You validate Anthropic-style SKILL.md files for a publisher pipeline. "
                    "Return only compact JSON with keys errors, warnings, and notes. "
                    "Errors are blocking contract violations. Warnings are non-blocking quality issues."
                ),
            },
            {
                "role": "user",
                "content": _validation_prompt(skill_root=skill_root, content=content),
            },
        ],
    }


def _validation_prompt(*, skill_root: Path, content: str) -> str:
    return (
        "Validate this SKILL.md against the contract below.\n\n"
        "Blocking errors:\n"
        "- File must use YAML frontmatter delimited by --- at the top.\n"
        "- Frontmatter must include name and description.\n"
        "- Name must be kebab-case, match the folder name, and avoid reserved words claude/anthropic.\n"
        "- Description must explain what the skill does and when to use it.\n"
        "- Description and frontmatter string fields must not include XML angle brackets.\n"
        "- Body after frontmatter must contain usable instructions.\n\n"
        "Non-blocking warnings:\n"
        "- Missing Instructions heading.\n"
        "- Missing examples.\n"
        "- Missing troubleshooting guidance.\n"
        "- Ambiguous or weak use-when trigger guidance.\n\n"
        f"Folder name: {skill_root.name}\n\n"
        "Return JSON only, for example:\n"
        "{\"errors\":[],\"warnings\":[\"...\"],\"notes\":[\"...\"]}\n\n"
        "SKILL.md:\n"
        "```markdown\n"
        f"{content[:24000]}\n"
        "```"
    )


def _post_chat_completion(*, payload: dict[str, Any], api_key: str, base_url: str) -> dict[str, Any]:
    url = f"{base_url}/chat/completions"
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    timeout = int(os.environ.get("PUBLISHER_LLM_VALIDATION_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS))
    with request.urlopen(http_request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_json_object(value: str) -> dict[str, Any]:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON response must be an object")
    return parsed


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
