"""Small independently generated GLB fixtures for offline tests."""

from __future__ import annotations

import json
import struct


def minimal_test_glb() -> bytes:
    """Build a glTF 2.0 binary containing one indexed triangle."""

    positions = struct.pack(
        "<9f",
        -0.6,
        -0.4,
        0.0,
        0.6,
        -0.4,
        0.0,
        0.0,
        0.7,
        0.0,
    )
    indices = struct.pack("<3H", 0, 1, 2)
    binary = positions + indices
    binary += b"\x00" * (-len(binary) % 4)
    document = {
        "asset": {
            "version": "2.0",
            "generator": "FormWeaver Studio controlled fixture builder",
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "Controlled Fixture"}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "indices": 1,
                        "mode": 4,
                    }
                ]
            }
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [-0.6, -0.4, 0.0],
                "max": [0.6, 0.7, 0.0],
            },
            {
                "bufferView": 1,
                "componentType": 5123,
                "count": 3,
                "type": "SCALAR",
            },
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {
                "buffer": 0,
                "byteOffset": len(positions),
                "byteLength": len(indices),
                "target": 34963,
            },
        ],
        "buffers": [{"byteLength": len(binary)}],
    }
    json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    body = (
        struct.pack("<I4s", len(json_bytes), b"JSON")
        + json_bytes
        + struct.pack("<I4s", len(binary), b"BIN\x00")
        + binary
    )
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body
