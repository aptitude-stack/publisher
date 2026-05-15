"""Registry publishing helpers for the publisher CLI."""

from __future__ import annotations

from dataclasses import dataclass
import json
import uuid
from urllib import error, request

from publisher.models import PublishContext


@dataclass(frozen=True, slots=True)
class RegistryPublishResult:
    """Structured result from one registry publish attempt."""

    status_code: int
    body: dict[str, object]
    request_id: str | None


def build_publish_metadata(context: PublishContext) -> dict[str, object]:
    """Build the live metadata JSON expected by the registry API."""
    return {
        "intent": context.delivery_payload.intent,
        "version": context.delivery_payload.version,
        "metadata": context.delivery_payload.metadata,
        "governance": context.delivery_payload.governance,
        "relationships": context.delivery_payload.relationships,
    }


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
