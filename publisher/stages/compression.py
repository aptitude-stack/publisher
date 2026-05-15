"""Phase 7: compress the delivery package for transport or storage."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from publisher.models import PublishContext
from publisher.stages.base import PublisherStage


class CompressionStage(PublisherStage):
    """Compress the final delivery payload using Zstandard on Python 3.12."""

    name = "compression"

    def run(self, context: PublishContext) -> None:
        self._reset_compression_state(context)
        payload_bytes = self._serialize_payload(context)

        try:
            compressed_bytes = self._compress_payload(payload_bytes)
        except ModuleNotFoundError:
            context.compression.notes.append(
                "Compression backend unavailable: install the 'zstandard' package to enable phase 7."
            )
            context.add_snapshot(
                stage_name=self.name,
                status="failed",
                data={
                    "algorithm": context.compression.algorithm,
                    "available": context.compression.available,
                },
                messages=context.compression.notes[:],
            )
            return

        compressed_artifact_path = self._write_compressed_artifact(context, compressed_bytes)
        manifest_artifact_path = self._write_compression_manifest(context)
        context.add_snapshot(
            stage_name=self.name,
            status="completed",
            data={
                "algorithm": context.compression.algorithm,
                "compressed_artifact_path": compressed_artifact_path,
                "manifest_artifact_path": manifest_artifact_path,
                "uncompressed_size": context.compression.uncompressed_size,
                "compressed_size": context.compression.compressed_size,
                "compression_ratio": context.compression.compression_ratio,
            },
            messages=[
                "Compression stage serialized the delivery payload and compressed it with Zstandard.",
                "Compressed package artifact is ready for transport or storage.",
            ],
        )

    def _reset_compression_state(self, context: PublishContext) -> None:
        """Reset compression outputs before generating a new package."""
        context.compression.algorithm = "zstd"
        context.compression.available = False
        context.compression.compressed_artifact_path = None
        context.compression.manifest_artifact_path = None
        context.compression.uncompressed_size = None
        context.compression.compressed_size = None
        context.compression.compression_ratio = None
        context.compression.notes = [
            "Compression is implemented with the Python 3.12-compatible 'zstandard' package.",
        ]

    def _serialize_payload(self, context: PublishContext) -> bytes:
        """Serialize the delivery payload into deterministic JSON bytes."""
        payload = {
            "slug": context.delivery_payload.slug,
            "version": context.delivery_payload.version,
            "intent": context.delivery_payload.intent,
            "content": context.delivery_payload.content,
            "metadata": context.delivery_payload.metadata,
            "governance": context.delivery_payload.governance,
            "relationships": context.delivery_payload.relationships,
        }
        serialized = json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
        payload_bytes = serialized.encode("utf-8")
        context.compression.uncompressed_size = len(payload_bytes)
        return payload_bytes

    def _compress_payload(self, payload_bytes: bytes) -> bytes:
        """Compress the serialized payload with Zstandard."""
        try:
            import zstandard as zstd
        except ModuleNotFoundError:
            return self._compress_with_zstd_binary(payload_bytes)

        compressor = zstd.ZstdCompressor(level=3)
        compressed = compressor.compress(payload_bytes)
        return compressed

    def _compress_with_zstd_binary(self, payload_bytes: bytes) -> bytes:
        """Fallback to the system `zstd` binary when the Python module is unavailable."""
        result = subprocess.run(
            ["zstd", "--quiet", "--stdout"],
            input=payload_bytes,
            check=True,
            capture_output=True,
        )
        return result.stdout

    def _write_compressed_artifact(self, context: PublishContext, compressed_bytes: bytes) -> str:
        """Write the compressed payload as a .zst artifact."""
        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "07_delivery_package.zst"
        artifact_path.write_bytes(compressed_bytes)

        context.compression.available = True
        context.compression.compressed_artifact_path = str(artifact_path)
        context.compression.compressed_size = len(compressed_bytes)
        uncompressed_size = context.compression.uncompressed_size or 0
        if uncompressed_size > 0:
            context.compression.compression_ratio = round(len(compressed_bytes) / uncompressed_size, 3)
        else:
            context.compression.compression_ratio = 0.0
        return str(artifact_path)

    def _write_compression_manifest(self, context: PublishContext) -> str:
        """Write a small JSON manifest describing the compression result."""
        artifacts_dir = Path(context.artifacts_dir or ".publisher_artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = artifacts_dir / "07_compression.json"
        artifact = {
            "algorithm": context.compression.algorithm,
            "available": context.compression.available,
            "compressed_artifact_path": context.compression.compressed_artifact_path,
            "uncompressed_size": context.compression.uncompressed_size,
            "compressed_size": context.compression.compressed_size,
            "compression_ratio": context.compression.compression_ratio,
            "notes": context.compression.notes,
        }
        artifact_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        context.compression.manifest_artifact_path = str(artifact_path)
        return str(artifact_path)
