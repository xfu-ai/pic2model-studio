from __future__ import annotations

import pytest

from aipic_to_model.application.jobs.secure_download import (
    DownloadResponse,
    UntrustedDownload,
    download_glb_to_part,
    promote_verified_part,
    validate_artifact_url,
    validate_glb_header,
)


def test_download_policy_rejects_private_redirect_and_wrong_magic() -> None:
    allowed = frozenset({"artifacts.example.test"})
    assert (
        validate_artifact_url(
            "https://artifacts.example.test/model", allowed_hosts=allowed, resolved_ips=("8.8.8.8",)
        )
        == "artifacts.example.test"
    )
    for url, ips in (
        ("http://artifacts.example.test/model", ("8.8.8.8",)),
        ("https://localhost/model", ("8.8.8.8",)),
        ("https://artifacts.example.test/model", ("127.0.0.1",)),
    ):
        with pytest.raises(UntrustedDownload):
            validate_artifact_url(url, allowed_hosts=allowed, resolved_ips=ips)
    validate_glb_header(b"glTF" + b"\x02\x00\x00\x00" + b"\x0c\x00\x00\x00", maximum_bytes=1024)
    with pytest.raises(UntrustedDownload):
        validate_glb_header(b"not-glb", maximum_bytes=1024)


def test_wildcard_host_mode_still_rejects_non_https_and_private_ips() -> None:
    assert (
        validate_artifact_url(
            "https://dynamic-cdn.example/model.glb",
            allowed_hosts=frozenset({"*"}),
            resolved_ips=("8.8.8.8",),
        )
        == "dynamic-cdn.example"
    )
    for url, ips in (
        ("http://dynamic-cdn.example/model.glb", ("8.8.8.8",)),
        ("https://dynamic-cdn.example/model.glb", ("127.0.0.1",)),
    ):
        with pytest.raises(UntrustedDownload, match="SECURITY_UNTRUSTED_URL"):
            validate_artifact_url(url, allowed_hosts=frozenset({"*"}), resolved_ips=ips)


def _glb() -> bytes:
    document = b'{"asset":{"version":"2.0"}}'
    padding = b" " * ((4 - len(document) % 4) % 4)
    payload = document + padding
    length = 12 + 8 + len(payload)
    return (
        b"glTF"
        + b"\x02\x00\x00\x00"
        + length.to_bytes(4, "little")
        + len(payload).to_bytes(4, "little")
        + b"JSON"
        + payload
    )


def test_download_rejects_redirect_rebinding_bad_mime_and_never_promotes_part(tmp_path) -> None:
    root, part, final = (
        tmp_path / "managed",
        tmp_path / "managed" / "model.part",
        tmp_path / "managed" / "model.glb",
    )
    root.mkdir()
    common = {
        "url": "https://artifacts.example.test/private-signed-url?do-not-persist=true",
        "resolved_ips": ("8.8.8.8",),
        "peer_ip": "8.8.8.8",
        "chunks": [_glb()],
    }
    for response in (
        DownloadResponse(**common, status_code=302, content_type="model/gltf-binary"),
        DownloadResponse(
            **{**common, "resolved_ips": ("127.0.0.1",), "peer_ip": "127.0.0.1"},
            status_code=200,
            content_type="model/gltf-binary",
        ),
        DownloadResponse(**common, status_code=200, content_type="text/html"),
    ):
        with pytest.raises(UntrustedDownload):
            download_glb_to_part(
                response,
                part_path=part,
                part_root=root,
                allowed_hosts=frozenset({"artifacts.example.test"}),
                maximum_bytes=1024,
            )
    assert not final.exists()


def test_interrupted_download_only_resumes_with_matching_range_and_verified_glb(tmp_path) -> None:
    root, part, final = (
        tmp_path / "managed",
        tmp_path / "managed" / "model.part",
        tmp_path / "managed" / "model.glb",
    )
    root.mkdir()
    response = DownloadResponse(
        url="https://artifacts.example.test/model",
        resolved_ips=("8.8.8.8",),
        peer_ip="8.8.8.8",
        status_code=200,
        content_type="model/gltf-binary",
        chunks=[_glb()[:6]],
    )
    with pytest.raises(UntrustedDownload, match="DOWNLOAD_INTERRUPTED"):
        download_glb_to_part(
            response,
            part_path=part,
            part_root=root,
            allowed_hosts=frozenset({"artifacts.example.test"}),
            maximum_bytes=1024,
            expected_size=len(_glb()),
        )
    with pytest.raises(UntrustedDownload, match="DOWNLOAD_RESUME_INVALID"):
        download_glb_to_part(
            response,
            part_path=part,
            part_root=root,
            allowed_hosts=frozenset({"artifacts.example.test"}),
            maximum_bytes=1024,
            expected_size=len(_glb()),
        )
    receipt = download_glb_to_part(
        DownloadResponse(
            url="https://artifacts.example.test/model",
            resolved_ips=("8.8.8.8",),
            peer_ip="8.8.8.8",
            status_code=206,
            content_type="binary/octet-stream",
            content_range="bytes 6-",
            chunks=[_glb()[6:]],
        ),
        part_path=part,
        part_root=root,
        allowed_hosts=frozenset({"artifacts.example.test"}),
        maximum_bytes=1024,
        expected_size=len(_glb()),
    )
    assert receipt.resumed and receipt.size_bytes == len(_glb())
    assert receipt.content_type == "binary/octet-stream"
    promote_verified_part(part_path=part, final_path=final, managed_root=root)
    assert final.read_bytes() == _glb()


def test_download_rejects_dns_rebinding_and_hash_mismatch(tmp_path) -> None:
    root, part = tmp_path / "managed", tmp_path / "managed" / "model.part"
    root.mkdir()
    response = DownloadResponse(
        url="https://artifacts.example.test/model",
        resolved_ips=("8.8.8.8",),
        peer_ip="1.1.1.1",
        status_code=200,
        content_type="model/gltf-binary",
        chunks=[_glb()],
    )
    with pytest.raises(UntrustedDownload, match="SECURITY_UNTRUSTED_URL"):
        download_glb_to_part(
            response,
            part_path=part,
            part_root=root,
            allowed_hosts=frozenset({"artifacts.example.test"}),
            maximum_bytes=1024,
        )


def test_download_rejects_header_only_glb_before_promotion(tmp_path) -> None:
    root, part = tmp_path / "managed", tmp_path / "managed" / "model.part"
    root.mkdir()
    with pytest.raises(UntrustedDownload, match="MODEL3D_PARSE_FAILED"):
        download_glb_to_part(
            DownloadResponse(
                url="https://artifacts.example.test/model",
                resolved_ips=("8.8.8.8",),
                peer_ip="8.8.8.8",
                status_code=200,
                content_type="model/gltf-binary",
                chunks=[b"glTF" + b"\x02\x00\x00\x00" + b"\x0c\x00\x00\x00"],
            ),
            part_path=part,
            part_root=root,
            allowed_hosts=frozenset({"artifacts.example.test"}),
            maximum_bytes=1024,
        )
    part.unlink()
    with pytest.raises(UntrustedDownload, match="MODEL3D_PARSE_FAILED"):
        download_glb_to_part(
            DownloadResponse(
                url="https://artifacts.example.test/model",
                resolved_ips=("8.8.8.8",),
                peer_ip="8.8.8.8",
                status_code=200,
                content_type="model/gltf-binary",
                chunks=[_glb()],
            ),
            part_path=part,
            part_root=root,
            allowed_hosts=frozenset({"artifacts.example.test"}),
            maximum_bytes=1024,
            expected_sha256="0" * 64,
        )
