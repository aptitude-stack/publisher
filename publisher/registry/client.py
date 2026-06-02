"""Registry publishing helpers for the publisher CLI."""

from __future__ import annotations

from dataclasses import dataclass
import json
import uuid
from urllib import error, request

from publisher.domain.models import PublishContext


@dataclass(frozen=True, slots=True)
class RegistryPublishResult:
    """Structured result from one registry publish attempt."""

    status_code: int
    body: dict[str, object]
    request_id: str | None


@dataclass(frozen=True, slots=True)
class RelationshipCheckIssue:
    """One relationship target existence warning for CLI output."""

    kind: str
    family: str
    slug: str
    version: str | None
    message: str


def build_publish_metadata(context: PublishContext) -> dict[str, object]:
    """Build the live metadata JSON expected by the registry API."""
    return {
        "intent": context.delivery_payload.intent,
        "version": context.delivery_payload.version,
        "metadata": context.delivery_payload.metadata,
        "governance": context.delivery_payload.governance,
        "relationships": context.delivery_payload.relationships,
    }


def check_relationship_references(
    *,
    registry_url: str,
    token: str,
    relationships: dict[str, object],
    timeout: int = 10,
) -> list[RelationshipCheckIssue]:
    """Check relationship targets against the registry fetch endpoints."""
    issues: list[RelationshipCheckIssue] = []
    for family, selector in _relationship_selectors(relationships):
        slug = selector["slug"]
        version = selector.get("version")
        if isinstance(version, str):
            issue = _check_exact_relationship(
                registry_url=registry_url,
                token=token,
                family=family,
                slug=slug,
                version=version,
                timeout=timeout,
            )
        else:
            issue = _check_skill_relationship(
                registry_url=registry_url,
                token=token,
                family=family,
                slug=slug,
                timeout=timeout,
            )
        if issue is not None:
            issues.append(issue)
    return issues


def _relationship_selectors(
    relationships: dict[str, object],
) -> list[tuple[str, dict[str, str]]]:
    selectors: list[tuple[str, dict[str, str]]] = []
    for family in ("depends_on", "extends", "conflicts_with", "overlaps_with"):
        items = relationships.get(family, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            slug = item.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            selector = {"slug": slug}
            version = item.get("version")
            if isinstance(version, str) and version:
                selector["version"] = version
            selectors.append((family, selector))
    return selectors


def _check_exact_relationship(
    *,
    registry_url: str,
    token: str,
    family: str,
    slug: str,
    version: str,
    timeout: int,
) -> RelationshipCheckIssue | None:
    url = f"{registry_url.rstrip('/')}/skills/{slug}/{version}"
    try:
        _get_json(url=url, token=token, timeout=timeout)
    except error.HTTPError as exc:
        if exc.code == 404:
            return RelationshipCheckIssue(
                kind="missing",
                family=family,
                slug=slug,
                version=version,
                message=f"Relationship target {slug}@{version} was not found.",
            )
        return _unavailable_issue(
            family=family,
            slug=slug,
            version=version,
            reason=f"registry returned HTTP {exc.code}",
        )
    except OSError as exc:
        return _unavailable_issue(
            family=family,
            slug=slug,
            version=version,
            reason=str(exc),
        )
    return None


def _check_skill_relationship(
    *,
    registry_url: str,
    token: str,
    family: str,
    slug: str,
    timeout: int,
) -> RelationshipCheckIssue | None:
    url = f"{registry_url.rstrip('/')}/skills/{slug}"
    try:
        payload = _get_json(url=url, token=token, timeout=timeout)
    except error.HTTPError as exc:
        if exc.code == 404:
            return RelationshipCheckIssue(
                kind="missing",
                family=family,
                slug=slug,
                version=None,
                message=f"No visible versions found for relationship target {slug}.",
            )
        return _unavailable_issue(
            family=family,
            slug=slug,
            version=None,
            reason=f"registry returned HTTP {exc.code}",
        )
    except OSError as exc:
        return _unavailable_issue(
            family=family,
            slug=slug,
            version=None,
            reason=str(exc),
        )

    versions = payload.get("versions") if isinstance(payload, dict) else None
    if isinstance(versions, list) and versions:
        return None
    return RelationshipCheckIssue(
        kind="missing",
        family=family,
        slug=slug,
        version=None,
        message=f"No visible versions found for relationship target {slug}.",
    )


def _get_json(*, url: str, token: str, timeout: int) -> dict[str, object]:
    http_request = request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with request.urlopen(http_request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _unavailable_issue(
    *,
    family: str,
    slug: str,
    version: str | None,
    reason: str,
) -> RelationshipCheckIssue:
    coordinate = slug if version is None else f"{slug}@{version}"
    return RelationshipCheckIssue(
        kind="unavailable",
        family=family,
        slug=slug,
        version=version,
        message=f"Could not verify relationship target {coordinate}: {reason}.",
    )


def publish_to_registry(
    *,
    registry_url: str,
    token: str,
    context: PublishContext,
    bundle_bytes: bytes,
) -> RegistryPublishResult:
    """Upload a skill version to the registry as multipart form data."""
    metadata = build_publish_metadata(context)
    url = f"{registry_url.rstrip('/')}/skills/{context.identity.slug}"
    content_type, body = _encode_multipart_form(
        metadata_json=json.dumps(metadata, ensure_ascii=True),
        bundle_bytes=bundle_bytes,
    )
    http_request = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": "application/json",
        },
    )

    try:
        with request.urlopen(http_request) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return RegistryPublishResult(
                status_code=response.status,
                body=payload,
                request_id=response.headers.get("X-Request-ID"),
            )
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": {"message": raw}}
        return RegistryPublishResult(
            status_code=exc.code,
            body=payload,
            request_id=exc.headers.get("X-Request-ID"),
        )


def _encode_multipart_form(*, metadata_json: str, bundle_bytes: bytes) -> tuple[str, bytes]:
    boundary = f"aptitude-publisher-{uuid.uuid4().hex}"
    boundary_bytes = boundary.encode("ascii")
    body = bytearray()

    def add_part(headers: list[tuple[str, str]], payload: bytes) -> None:
        body.extend(b"--" + boundary_bytes + b"\r\n")
        for key, value in headers:
            body.extend(f"{key}: {value}\r\n".encode("utf-8"))
        body.extend(b"\r\n")
        body.extend(payload)
        body.extend(b"\r\n")

    add_part(
        [
            ("Content-Disposition", 'form-data; name="metadata"'),
            ("Content-Type", "application/json"),
        ],
        metadata_json.encode("utf-8"),
    )
    add_part(
        [
            (
                "Content-Disposition",
                'form-data; name="bundle"; filename="skill.tar.zst"',
            ),
            ("Content-Type", "application/zstd"),
        ],
        bundle_bytes,
    )
    body.extend(b"--" + boundary_bytes + b"--\r\n")
    return f"multipart/form-data; boundary={boundary}", bytes(body)
