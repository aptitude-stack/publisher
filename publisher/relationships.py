"""Relationship normalization for registry publish metadata."""

from __future__ import annotations

import re
from typing import Any, Mapping


RELATIONSHIP_FAMILIES = (
    "depends_on",
    "extends",
    "conflicts_with",
    "overlaps_with",
)
_EXACT_FAMILIES = frozenset({"extends", "conflicts_with", "overlaps_with"})
_DEPENDENCY_FIELDS = frozenset(
    {"slug", "version", "version_constraint", "optional", "markers"}
)
_EXACT_FIELDS = frozenset({"slug", "version"})
_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,127})$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_VERSION_CONSTRAINT_PATTERN = re.compile(
    r"^\s*(?:==|!=|<=|>=|<|>)?\s*"
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\s*,\s*(?:==|!=|<=|>=|<|>)?\s*"
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)*\s*$"
)
_MARKER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def normalize_relationships(value: object) -> dict[str, list[dict[str, Any]]]:
    """Return a registry-shaped relationship payload from manifest data."""
    if value is None:
        relationships: Mapping[str, object] = {}
    elif isinstance(value, Mapping):
        relationships = value
    else:
        raise ValueError("relationships must be a YAML mapping.")

    normalized: dict[str, list[dict[str, Any]]] = {
        family: [] for family in RELATIONSHIP_FAMILIES
    }
    for family in relationships:
        if family not in normalized:
            raise ValueError(f"Unknown relationship family: {family}")

    for family in RELATIONSHIP_FAMILIES:
        normalized[family] = _normalize_family(family, relationships.get(family, []))
    return normalized


def relationship_manifest_value(manifest: object) -> object:
    """Return authored relationships from the Aptitude metadata manifest."""
    if not isinstance(manifest, Mapping):
        return None
    return manifest.get("relationships")


def _normalize_family(family: str, value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"relationships.{family} must be a list.")

    normalized = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"relationships.{family}[{index}] must be a mapping.")
        if family == "depends_on":
            normalized.append(_normalize_dependency(item, family=family, index=index))
        elif family in _EXACT_FAMILIES:
            normalized.append(_normalize_exact(item, family=family, index=index))
    return normalized


def _normalize_dependency(
    item: Mapping[str, object],
    *,
    family: str,
    index: int,
) -> dict[str, Any]:
    _reject_unknown_fields(item, allowed=_DEPENDENCY_FIELDS, family=family, index=index)
    selector: dict[str, Any] = {
        "slug": _required_slug(item, family=family, index=index),
    }

    version = _optional_semver(item.get("version"), family=family, index=index)
    version_constraint = _optional_version_constraint(
        item.get("version_constraint"),
        family=family,
        index=index,
    )
    if (version is None) == (version_constraint is None):
        raise ValueError(
            f"relationships.{family}[{index}] must include exactly one of version "
            "or version_constraint."
        )
    if version is not None:
        selector["version"] = version
    if version_constraint is not None:
        selector["version_constraint"] = version_constraint

    if "optional" in item:
        optional = item["optional"]
        if optional is not None and not isinstance(optional, bool):
            raise ValueError(
                f"relationships.{family}[{index}].optional must be a boolean."
            )
        selector["optional"] = optional

    markers = _normalize_markers(item.get("markers"), family=family, index=index)
    if markers:
        selector["markers"] = markers
    return selector


def _normalize_exact(
    item: Mapping[str, object],
    *,
    family: str,
    index: int,
) -> dict[str, Any]:
    _reject_unknown_fields(item, allowed=_EXACT_FIELDS, family=family, index=index)
    return {
        "slug": _required_slug(item, family=family, index=index),
        "version": _required_semver(item.get("version"), family=family, index=index),
    }


def _reject_unknown_fields(
    item: Mapping[str, object],
    *,
    allowed: frozenset[str],
    family: str,
    index: int,
) -> None:
    for field in item:
        if field not in allowed:
            raise ValueError(
                f"Unknown field in relationships.{family}[{index}]: {field}"
            )


def _required_slug(item: Mapping[str, object], *, family: str, index: int) -> str:
    slug = item.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError(
            f"relationships.{family}[{index}].slug must be a non-empty string."
        )
    stripped = slug.strip()
    if _SLUG_PATTERN.fullmatch(stripped) is None:
        raise ValueError(
            f"relationships.{family}[{index}].slug is not a valid registry slug."
        )
    return stripped


def _required_semver(value: object, *, family: str, index: int) -> str:
    version = _optional_semver(value, family=family, index=index)
    if version is None:
        raise ValueError(
            f"relationships.{family}[{index}].version must be a semantic version."
        )
    return version


def _optional_semver(value: object, *, family: str, index: int) -> str | None:
    if value is None:
        return None
    version = str(value).strip()
    if _SEMVER_PATTERN.fullmatch(version) is None:
        raise ValueError(
            f"relationships.{family}[{index}].version must be a semantic version."
        )
    return version


def _optional_version_constraint(value: object, *, family: str, index: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"relationships.{family}[{index}].version_constraint must be a string."
        )
    stripped = value.strip()
    if not stripped or _VERSION_CONSTRAINT_PATTERN.fullmatch(stripped) is None:
        raise ValueError(
            f"relationships.{family}[{index}].version_constraint must be a "
            "comma-separated list of semver comparators."
        )
    return stripped


def _normalize_markers(value: object, *, family: str, index: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"relationships.{family}[{index}].markers must be a list.")

    markers: list[str] = []
    seen: set[str] = set()
    for marker in value:
        if not isinstance(marker, str):
            raise ValueError(
                f"relationships.{family}[{index}].markers must contain strings."
            )
        stripped = marker.strip()
        if not stripped:
            continue
        if _MARKER_PATTERN.fullmatch(stripped) is None:
            raise ValueError(
                f"relationships.{family}[{index}].markers contains an invalid marker."
            )
        if stripped not in seen:
            seen.add(stripped)
            markers.append(stripped)
    return markers
