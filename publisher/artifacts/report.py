"""One replaceable, private evaluation report per canonical skill directory."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from publisher.domain.models import PublishContext

_SENSITIVE_KEY_MARKERS = (
    "api_key", "access_token", "publish_token", "read_token", "secret", "password",
)


def report_path(skill_path: str | Path) -> Path:
    source = Path(skill_path).resolve()
    root = source if source.is_dir() else source.parent
    configured = Path(os.environ.get("XDG_CACHE_HOME", ""))
    cache = configured if configured.is_absolute() else Path.home() / ".cache"
    cache = (cache / "aptitude" / "publisher").resolve()
    if cache.is_relative_to(root):
        raise ValueError("Publisher cache must be outside the skill directory; change XDG_CACHE_HOME.")
    key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return cache / f"{key}.json"


def safe(value: Any) -> Any:
    """Redact credential fields and configured credential values before persistence."""
    if is_dataclass(value):
        return safe(asdict(value))
    if isinstance(value, dict):
        return {
            str(key): safe(item)
            for key, item in value.items()
            if (isinstance(item, bool) or not any(
                marker in str(key).lower() for marker in _SENSITIVE_KEY_MARKERS
            )) and str(key) not in {"stdout", "stderr", "command", "raw_output"}
        }
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if len(secret) >= 4 and any(marker in key.lower() for marker in _SENSITIVE_KEY_MARKERS):
                value = value.replace(secret, "[redacted]")
    return value


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        raise OSError(f"Cannot write Publisher cache report at {path}: {exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def write_report(
    context: PublishContext,
    *,
    status: str,
    error: str | None = None,
    inspection_receipt: dict[str, Any] | None = None,
) -> None:
    path = report_path(context.source.file_path)
    context.report_path = str(path)
    root = Path(context.inventory.skill_root or context.source.file_path).resolve()
    if not root.is_dir():
        root = root.parent
    payload = safe({
        "schema_version": 1,
        "skill_root": str(root),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "stages": context.stage_history,
        "gates": context.gate_history,
        "evidence": {
            "inventory": context.inventory,
            "identity": context.identity,
            "metadata": context.metadata,
            "security": context.security,
            "validation": context.validation,
            "performance": context.performance_exam,
            "ranking": context.ranking,
        },
        "warnings": [*context.validation.warnings, *(warning for gate in context.gate_history for warning in gate.warnings)],
        "error": error,
    })
    # Already sanitized before signing; changing the receipt here invalidates its MAC.
    payload["inspection_receipt"] = inspection_receipt
    atomic_write_json(path, payload)
