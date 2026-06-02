from __future__ import annotations

from publisher.app.cli import _registry_result_lines
from publisher.registry.client import RegistryPublishResult


def test_registry_result_lines_summarize_validation_errors() -> None:
    result = RegistryPublishResult(
        status_code=422,
        request_id="01a5a686-b715-4875-9c82-431467211b7e",
        body={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Request validation failed.",
                "details": {
                    "errors": [
                        {
                            "loc": ["intent"],
                            "msg": "Input should be 'create_skill' or 'publish_version'",
                        },
                        {
                            "loc": ["metadata", "name"],
                            "msg": "Field required",
                        },
                    ]
                },
            }
        },
    )

    assert _registry_result_lines(result) == [
        ("status", "422"),
        ("request id", "01a5a686-b715-4875-9c82-431467211b7e"),
        ("error code", "INVALID_REQUEST"),
        ("message", "Request validation failed."),
        ("error 1", "intent: Input should be 'create_skill' or 'publish_version'"),
        ("error 2", "metadata.name: Field required"),
    ]
