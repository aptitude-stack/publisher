"""Loading and validation for Aptitude's source metadata sidecar."""

from __future__ import annotations

from collections.abc import Mapping
import math
from pathlib import Path
from typing import Any

import yaml

from publisher.relationships import normalize_relationships


MANIFEST_FILENAME = "aptitude.yaml"
MANIFEST_FIELDS = frozenset(
    {
        "version",
        "intent",
        "tags",
        "inputs_schema",
        "outputs_schema",
        "relationships",
        "token_estimate",
        "maturity_score",
        "security_score",
    }
)
LEGACY_APTITUDE_FIELDS = frozenset(MANIFEST_FIELDS)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "manifest keys must be strings",
                key_node.start_mark,
            )
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_manifest(skill_root: Path) -> dict[str, Any]:
    """Load and validate the required ``aptitude.yaml`` sidecar."""
    manifest_path = skill_root / MANIFEST_FILENAME
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(
            f"Missing required {MANIFEST_FILENAME} beside SKILL.md: {manifest_path}"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Unable to read {MANIFEST_FILENAME}: {exc}") from exc

    try:
        parsed = yaml.load(manifest_text, Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, TypeError) as exc:
        if "recursive" in str(exc).lower():
            raise ValueError(
                f"{MANIFEST_FILENAME} must not contain recursive aliases."
            ) from exc
        raise ValueError(f"{MANIFEST_FILENAME} must be valid YAML: {exc}") from exc

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{MANIFEST_FILENAME} must contain a YAML mapping.")

    unknown_fields = sorted(set(parsed) - MANIFEST_FIELDS)
    if unknown_fields:
        fields = ", ".join(unknown_fields)
        raise ValueError(f"Unknown field(s) in {MANIFEST_FILENAME}: {fields}")

    _validate_manifest_types(parsed)
    try:
        parsed["relationships"] = normalize_relationships(parsed.get("relationships"))
    except ValueError as exc:
        raise ValueError(f"{MANIFEST_FILENAME} relationships are invalid: {exc}") from exc
    return parsed


def legacy_aptitude_fields(frontmatter: object) -> list[str]:
    """Return Aptitude fields that still appear in SKILL.md frontmatter."""
    if not isinstance(frontmatter, Mapping):
        return []

    fields = [key for key in frontmatter if key in LEGACY_APTITUDE_FIELDS]
    metadata = frontmatter.get("metadata")
    if isinstance(metadata, Mapping):
        fields.extend(
            f"metadata.{key}" for key in metadata if key in LEGACY_APTITUDE_FIELDS
        )
    return fields


def _validate_manifest_types(manifest: Mapping[str, Any]) -> None:
    string_fields = ("version", "intent")
    for field in string_fields:
        value = manifest.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{MANIFEST_FILENAME} field {field!r} must be a non-empty string.")

    tags = manifest.get("tags")
    if tags is not None:
        if not isinstance(tags, list) or not all(
            isinstance(tag, str) for tag in tags
        ):
            raise ValueError(f"{MANIFEST_FILENAME} field 'tags' must be a list of strings.")

    for field in ("inputs_schema", "outputs_schema"):
        value = manifest.get(field)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"{MANIFEST_FILENAME} field {field!r} must be a mapping.")
        if value is not None:
            _validate_json_value(value, field, active_ids=set())

    relationships = manifest.get("relationships")
    if relationships is not None and not isinstance(relationships, Mapping):
        raise ValueError(
            f"{MANIFEST_FILENAME} field 'relationships' must be a mapping."
        )

    token_estimate = manifest.get("token_estimate")
    if token_estimate is not None and (
        isinstance(token_estimate, bool)
        or not isinstance(token_estimate, int)
        or token_estimate < 0
    ):
        raise ValueError(
            f"{MANIFEST_FILENAME} field 'token_estimate' must be a non-negative integer."
        )

    for field in ("maturity_score", "security_score"):
        value = manifest.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError(
                f"{MANIFEST_FILENAME} field {field!r} must be a number."
            )
        if value is not None and isinstance(value, float) and not math.isfinite(value):
            raise ValueError(
                f"{MANIFEST_FILENAME} field {field!r} must be a finite number between 0 and 1."
            )
        if value is not None and not 0 <= value <= 1:
            raise ValueError(
                f"{MANIFEST_FILENAME} field {field!r} must be a finite number between 0 and 1."
            )


def _validate_json_value(value: object, field: str, *, active_ids: set[int]) -> None:
    """Reject YAML values that cannot be represented safely in JSON reports."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{MANIFEST_FILENAME} field {field!r} must contain finite numbers.")
        return

    value_id = id(value)
    if value_id in active_ids:
        raise ValueError(f"{MANIFEST_FILENAME} field {field!r} must not contain recursive aliases.")
    active_ids.add(value_id)
    try:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise ValueError(
                        f"{MANIFEST_FILENAME} field {field!r} must use string object keys."
                    )
                _validate_json_value(nested, f"{field}.{key}", active_ids=active_ids)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                _validate_json_value(nested, f"{field}[{index}]", active_ids=active_ids)
        else:
            raise ValueError(
                f"{MANIFEST_FILENAME} field {field!r} must contain JSON-compatible values."
            )
    finally:
        active_ids.remove(value_id)
