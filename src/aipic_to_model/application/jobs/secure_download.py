"""Offline-verifiable security policy for Provider-controlled artifact downloads."""

from __future__ import annotations

import hashlib
import ipaddress
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..model_inspection import validate_glb_bytes


class UntrustedDownload(ValueError):
    pass


APPROVED_GLB_ARTIFACT_MIME_TYPES = frozenset(
    {
        "application/octet-stream",
        # Observed from the Tripo v3 signed GLB artifact endpoint on 2026-07-26.
        "binary/octet-stream",
        "model/gltf-binary",
    }
)


@dataclass(frozen=True)
class DownloadResponse:
    """An adapter-owned response; its URL must never be persisted or returned."""

    url: str
    resolved_ips: tuple[str, ...]
    peer_ip: str
    status_code: int
    content_type: str | None
    chunks: Iterable[bytes]
    content_range: str | None = None


@dataclass(frozen=True)
class DownloadReceipt:
    artifact_host: str
    content_type: str
    sha256: str
    size_bytes: int
    resumed: bool


def validate_artifact_url(
    url: str, *, allowed_hosts: frozenset[str], resolved_ips: tuple[str, ...]
) -> str:
    """Validate each hop before connecting; callers pin the verified peer IP."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise UntrustedDownload("SECURITY_UNTRUSTED_URL")
    host = parsed.hostname.lower()
    # ``*`` is an explicit operational escape hatch for deployments that
    # temporarily disable Provider artifact host pinning.  All remaining
    # HTTPS, public-IP, peer-pinning, redirect, MIME, size, and GLB checks
    # continue to apply.
    if ("*" not in allowed_hosts and host not in allowed_hosts) or parsed.port not in {
        None,
        443,
    }:
        raise UntrustedDownload("SECURITY_UNTRUSTED_URL")
    if not resolved_ips:
        raise UntrustedDownload("SECURITY_UNTRUSTED_URL")
    for value in resolved_ips:
        address = ipaddress.ip_address(value)
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            raise UntrustedDownload("SECURITY_UNTRUSTED_URL")
    # Only a short opaque host fingerprint belongs in DB/resume_json.
    return host


def validate_glb_header(content: bytes, *, maximum_bytes: int) -> None:
    if not 12 <= len(content) <= maximum_bytes or content[:4] != b"glTF":
        raise UntrustedDownload("MODEL3D_PARSE_FAILED")
    version = int.from_bytes(content[4:8], "little")
    declared_size = int.from_bytes(content[8:12], "little")
    if version != 2 or declared_size != len(content):
        raise UntrustedDownload("MODEL3D_PARSE_FAILED")


def download_glb_to_part(
    response: DownloadResponse,
    *,
    part_path: Path,
    part_root: Path,
    allowed_hosts: frozenset[str],
    maximum_bytes: int,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> DownloadReceipt:
    """Write one verified GLB to a managed ``.part`` file.

    Redirects are rejected before consuming a byte.  A retry is only allowed
    when the adapter supplies a matching byte-range response; an incomplete
    or invalid download is deliberately left as ``.part`` and never becomes a
    managed asset.
    """
    root = part_root.resolve()
    destination = part_path.resolve()
    if root not in {destination, *destination.parents} or destination.suffix != ".part":
        raise UntrustedDownload("SECURITY_UNTRUSTED_URL")
    host = validate_artifact_url(
        response.url, allowed_hosts=allowed_hosts, resolved_ips=response.resolved_ips
    )
    if response.peer_ip not in response.resolved_ips:
        raise UntrustedDownload("SECURITY_UNTRUSTED_URL")
    # Redirects are never followed; only an explicit 206 retry can differ
    # from a fresh 200 response and it is range-checked below.
    if response.status_code not in {200, 206}:
        raise UntrustedDownload("SECURITY_UNTRUSTED_URL")
    mime = (response.content_type or "").split(";", 1)[0].strip().lower()
    if mime not in APPROVED_GLB_ARTIFACT_MIME_TYPES:
        raise UntrustedDownload("MODEL3D_PARSE_FAILED")
    existing = destination.stat().st_size if destination.exists() else 0
    resumed = existing > 0
    if resumed:
        if response.status_code != 206 or response.content_range != f"bytes {existing}-":
            raise UntrustedDownload("DOWNLOAD_RESUME_INVALID")
        mode = "ab"
    else:
        if response.status_code != 200:
            raise UntrustedDownload("DOWNLOAD_RESUME_INVALID")
        mode = "wb"
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = existing
    with destination.open(mode) as stream:
        for chunk in response.chunks:
            if not isinstance(chunk, bytes):
                raise UntrustedDownload("MODEL3D_PARSE_FAILED")
            size += len(chunk)
            if size > maximum_bytes or (expected_size is not None and size > expected_size):
                raise UntrustedDownload("DOWNLOAD_TOO_LARGE")
            stream.write(chunk)
    if expected_size is not None and size != expected_size:
        raise UntrustedDownload("DOWNLOAD_INTERRUPTED")
    content = destination.read_bytes()
    validate_glb_header(content, maximum_bytes=maximum_bytes)
    try:
        validate_glb_bytes(content, maximum_bytes=maximum_bytes)
    except ValueError as error:
        raise UntrustedDownload("MODEL3D_PARSE_FAILED") from error
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise UntrustedDownload("MODEL3D_PARSE_FAILED")
    return DownloadReceipt(
        artifact_host=host,
        content_type=mime,
        sha256=digest,
        size_bytes=size,
        resumed=resumed,
    )


def promote_verified_part(*, part_path: Path, final_path: Path, managed_root: Path) -> None:
    """Atomically promote a verified part, without permitting arbitrary paths."""
    root = managed_root.resolve()
    source, target = part_path.resolve(), final_path.resolve()
    if (
        root not in {source, *source.parents}
        or root not in {target, *target.parents}
        or source.suffix != ".part"
        or target.exists()
    ):
        raise UntrustedDownload("SECURITY_UNTRUSTED_URL")
    os.replace(source, target)
