"""Bundle helpers for publishing skill folders to the registry."""

from __future__ import annotations

import tarfile
from io import BytesIO
from pathlib import Path
import subprocess

from publisher.models import PublishContext

_BUNDLE_ROOT = "skill-bundle"


def build_bundle_bytes(context: PublishContext) -> bytes:
    """Create a deterministic `.tar.zst` bundle from the discovered skill folder."""
    skill_root = Path(context.inventory.skill_root or context.source.file_path)
    entries: list[tuple[str, bytes]] = []
    for path in sorted(skill_root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(skill_root)
        if str(relative_path).startswith(".publisher_artifacts/"):
            continue
        archive_path = f"{_BUNDLE_ROOT}/{relative_path.as_posix()}"
        entries.append((archive_path, path.read_bytes()))
    return _compress_entries(entries)


def _compress_entries(entries: list[tuple[str, bytes]]) -> bytes:
    """Tar and zstd-compress the provided archive entries."""
    tar_buffer = BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
        for path, payload in entries:
            info = tarfile.TarInfo(path)
            info.size = len(payload)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, BytesIO(payload))

    try:
        import zstandard as zstd
    except ModuleNotFoundError as exc:
        return _compress_with_zstd_binary(tar_buffer.getvalue(), exc)

    compressor = zstd.ZstdCompressor()
    return compressor.compress(tar_buffer.getvalue())


def _compress_with_zstd_binary(tar_bytes: bytes, original_error: ModuleNotFoundError) -> bytes:
    """Fallback to the system `zstd` binary when the Python module is unavailable."""
    try:
        result = subprocess.run(
            ["zstd", "--quiet", "--stdout"],
            input=tar_bytes,
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Bundle compression requires the 'zstandard' package or a working `zstd` binary."
        ) from exc
    return result.stdout
