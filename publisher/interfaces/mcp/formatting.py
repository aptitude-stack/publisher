"""Response formatting for the Publisher MCP tools."""

from __future__ import annotations

import json
from typing import Any

import toons
from pydantic import BaseModel

from publisher.interfaces.mcp.models import (
    PublisherToolResult,
    ResponseFormat,
)


_SCORE_SCALE = {
    "normalized_min": 0.0,
    "normalized_max": 1.0,
    "display_min": 0.0,
    "display_max": 10.0,
}
_PRIVATE_RESPONSE_KEYS = {
    "trust",
    "trust_tier",
    "trusttier",
    "ranking",
    "ranking_score",
    "ranking_label",
    "total_score",
    "trust_context",
}


def format_response(value: Any, response_format: ResponseFormat) -> str:
    """Render one Publisher MCP result for a caller-selected format."""

    if not isinstance(response_format, ResponseFormat):
        response_format = ResponseFormat(response_format)
    data = _public_data(value)
    if response_format is ResponseFormat.JSON:
        return json.dumps(data, indent=2, sort_keys=True)
    if response_format is ResponseFormat.TOON:
        return toons.dumps(data)
    return _format_markdown(data)


def _public_data(value: Any) -> Any:
    if isinstance(value, PublisherToolResult):
        return _publisher_result_data(value)
    if isinstance(value, BaseModel):
        return _scrub(value.model_dump(mode="json", exclude_none=True))
    return _scrub(value)


def _publisher_result_data(result: PublisherToolResult) -> dict[str, Any]:
    data = result.model_dump(mode="json", exclude_none=True)
    evaluation = data.pop("evaluation", None)
    data.pop("registry", None)
    data["score_scale"] = dict(_SCORE_SCALE)
    if evaluation is not None:
        data["evaluation"] = _evaluation_data(evaluation)
        data["scores"] = dict(data["evaluation"]["scores"])
        data["performance_evidence"] = dict(data["evaluation"]["performance_evidence"])
    receipt = {
        "evidence_reused": result.evidence_reused,
        "evidence_refreshed": result.evidence_refreshed,
        "created_at": result.receipt_created_at,
        "expires_at": result.receipt_expires_at,
    }
    data["receipt"] = {key: value for key, value in receipt.items() if value is not None}
    if result.registry is not None:
        data["registry"] = {
            "status_code": result.registry.status_code,
            "request_id": result.registry.request_id,
            "bundle_size_bytes": result.registry.bundle_size_bytes,
            "body": _scrub(result.registry.body),
        }
    return _scrub(data)


def _evaluation_data(evaluation: dict[str, Any]) -> dict[str, Any]:
    score_keys = {
        "maturity_score": evaluation.pop("maturity_score", None),
        "security_score": evaluation.pop("security_score", None),
        "overall_score": evaluation.pop("overall_score", None),
    }
    performance_score = evaluation.pop("performance_evidence_score", None)
    evaluation.pop("overall_label", None)
    evaluation["scores"] = {
        key: _normalized_score(value)
        for key, value in score_keys.items()
        if value is not None
    }
    evaluation["performance_evidence"] = {
        "score": _normalized_score(performance_score),
        "persisted": False,
    }
    return _scrub(evaluation)


def _normalized_score(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(max(0.0, min(1.0, float(value))), 4)


def _format_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Aptitude Publisher",
        "",
        f"Status: `{data.get('status', 'error')}`",
        f"Message: {data.get('message', '')}",
    ]
    evaluation = data.get("evaluation")
    if isinstance(evaluation, dict):
        slug = evaluation.get("slug")
        version = evaluation.get("version")
        if slug:
            coordinate = f"{slug}@{version}" if version else str(slug)
            lines.extend(["", f"Skill: `{coordinate}`"])
        scores = evaluation.get("scores", {})
        performance = evaluation.get("performance_evidence", {})
        lines.extend(["", "## Scores"])
        for label, key in (
            ("Maturity", "maturity_score"),
            ("Security", "security_score"),
            ("Overall", "overall_score"),
        ):
            if key in scores:
                lines.append(f"- {label}: {_display_score(scores[key])}/10")
        if performance.get("score") is not None:
            lines.append(
                "- Performance evidence (non-persisted): "
                f"{_display_score(performance['score'])}/10"
            )
        if evaluation.get("blocking_issues"):
            lines.extend(["", "## Blocking issues"])
            lines.extend(f"- {item}" for item in evaluation["blocking_issues"])
        if evaluation.get("warnings"):
            lines.extend(["", "## Warnings"])
            lines.extend(f"- {item}" for item in evaluation["warnings"])
    receipt = data.get("receipt")
    if isinstance(receipt, dict) and receipt:
        lines.extend(
            [
                "",
                "## Evidence",
                f"- Reused: `{receipt.get('evidence_reused', False)}`",
                f"- Refreshed: `{receipt.get('evidence_refreshed', False)}`",
            ]
        )
        if receipt.get("created_at"):
            lines.append(f"- Created: `{receipt['created_at']}`")
        if receipt.get("expires_at"):
            lines.append(f"- Expires: `{receipt['expires_at']}`")
    registry = data.get("registry")
    if isinstance(registry, dict):
        lines.extend(
            [
                "",
                "## Registry",
                f"- HTTP status: `{registry.get('status_code')}`",
                f"- Bundle: `{registry.get('bundle_size_bytes')} bytes`",
            ]
        )
    return "\n".join(lines)


def _display_score(value: Any) -> str:
    normalized = _normalized_score(value)
    return "n/a" if normalized is None else f"{normalized * 10:.1f}"


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if str(key).lower() not in _PRIVATE_RESPONSE_KEYS
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value
