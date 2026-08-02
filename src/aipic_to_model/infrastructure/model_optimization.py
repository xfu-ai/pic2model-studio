"""Approved offline GLB geometry optimization.

The adapter works only from managed bytes. It deliberately supports a safe,
well-defined subset of static GLB: one indexed triangle primitive with ordinary
per-vertex attributes. Unsupported animation, skinning, morph targets, or
interleaved buffers fail safely rather than silently dropping data.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

import fast_simplification
import numpy as np

from ..application.model_inspection import parse_glb_document
from ..domain.provider_models import (
    ErrorCategory,
    ErrorDetail,
    ProviderResult,
    RecommendedAction,
)

_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_DTYPES = {5121: np.dtype("uint8"), 5123: np.dtype("<u2"), 5125: np.dtype("<u4"), 5126: np.dtype("<f4")}
_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


class _UnsupportedModel(ValueError):
    pass


class FastSimplificationGlbOptimizer:
    """Simplify static GLB geometry without pulling a large scene runtime into the sidecar."""

    def optimize(
        self,
        content: bytes,
        *,
        target_triangles: int | None,
        max_texture_bytes: int | None,
        on_progress: Callable[[str, int, dict[str, int]], None] | None = None,
    ) -> ProviderResult:
        # Texture budgeting is intentionally not faked. Geometry simplification
        # retains existing material/image references unchanged.
        del max_texture_bytes
        try:
            document = parse_glb_document(content)
            output, source_triangles = self._simplify(
                content, document, target_triangles, on_progress=on_progress
            )
        except (KeyError, TypeError, ValueError, _UnsupportedModel):
            return ProviderResult(
                ok=False,
                stage="postprocessing",
                retryable=True,
                error=ErrorDetail(
                    code="MODEL_OPTIMIZATION_UNSUPPORTED",
                    category=ErrorCategory.FORMAT_UNSUPPORTED,
                    user_message=(
                        "The local optimizer supports static indexed triangle meshes only; "
                        "animations, skins, morph targets, and interleaved data are preserved unchanged."
                    ),
                    recoverable=True,
                    failed_object="model",
                    failed_step="optimization",
                    safe_to_retry=True,
                    recommended_action=RecommendedAction.OPEN_DETAILS,
                ),
            )
        return ProviderResult(
            ok=True,
            stage="postprocessing",
            retryable=False,
            payload={"glb_bytes": output, "source_triangles": source_triangles},
        )

    def _simplify(
        self,
        content: bytes,
        document: dict[str, object],
        target_triangles: int | None,
        *,
        on_progress: Callable[[str, int, dict[str, int]], None] | None,
    ) -> tuple[bytes, int]:
        if document.get("animations") or document.get("skins"):
            raise _UnsupportedModel("animated or skinned GLB")
        meshes = document.get("meshes")
        if not isinstance(meshes, list) or len(meshes) != 1 or not isinstance(meshes[0], dict):
            raise _UnsupportedModel("one mesh is required")
        primitives = meshes[0].get("primitives")
        if not isinstance(primitives, list) or len(primitives) != 1 or not isinstance(primitives[0], dict):
            raise _UnsupportedModel("one primitive is required")
        primitive = primitives[0]
        if primitive.get("mode", 4) != 4 or primitive.get("targets"):
            raise _UnsupportedModel("triangle primitive without morph targets is required")
        attributes = primitive.get("attributes")
        index_accessor = primitive.get("indices")
        if not isinstance(attributes, dict) or not isinstance(attributes.get("POSITION"), int) or not isinstance(index_accessor, int):
            raise _UnsupportedModel("indexed positions are required")
        binary = _binary_chunk(content)
        accessors = document.get("accessors")
        views = document.get("bufferViews")
        if not isinstance(accessors, list) or not isinstance(views, list):
            raise _UnsupportedModel("accessors are required")
        positions = _read_accessor(binary, accessors, views, attributes["POSITION"])
        indices = _read_accessor(binary, accessors, views, index_accessor)
        if positions.dtype != np.dtype("<f4") or positions.shape[1:] != (3,) or indices.ndim != 1:
            raise _UnsupportedModel("float32 positions and scalar indices are required")
        if len(indices) % 3 or not len(indices):
            raise _UnsupportedModel("triangle indices are required")
        faces = indices.reshape(-1, 3).astype(np.int32, copy=False)
        source_triangles = len(faces)
        desired = target_triangles or max(1, int(source_triangles * 0.75))
        if desired >= source_triangles:
            raise _UnsupportedModel("the target must reduce the triangle count")
        _report_progress(
            on_progress,
            "geometry_simplification",
            5,
            source_triangles=source_triangles,
            target_triangles=desired,
        )
        simplification = fast_simplification.simplify(
            positions, faces, target_count=desired, return_collapses=True
        )
        if len(simplification) != 3:
            raise _UnsupportedModel("simplifier did not return collapse history")
        simplified_positions, simplified_faces, collapses = cast(
            tuple[np.ndarray, np.ndarray, np.ndarray], simplification
        )
        if not len(simplified_faces) or len(simplified_faces) >= source_triangles:
            raise _UnsupportedModel("no geometry reduction was produced")
        _report_progress(
            on_progress,
            "attribute_mapping",
            20,
            source_triangles=source_triangles,
            output_triangles=len(simplified_faces),
        )
        output_indexes = _source_indexes_from_collapses(
            positions,
            faces,
            collapses,
            simplified_positions,
            simplified_faces,
        )
        mutable = json.loads(json.dumps(document))
        geometry_accessor_indexes = [*attributes.values(), index_accessor]
        geometry_view_indexes = {
            _accessor_buffer_view(accessors, accessor_index)
            for accessor_index in geometry_accessor_indexes
        }
        compact_binary, mutable_views = _copy_embedded_images(
            mutable, binary, views, geometry_view_indexes
        )
        mutable["bufferViews"] = mutable_views
        mutable_accessors: list[object] = []
        mutable["accessors"] = mutable_accessors
        next_attributes: dict[str, int] = {}
        for name, accessor_index in attributes.items():
            if not isinstance(name, str) or not isinstance(accessor_index, int):
                raise _UnsupportedModel("invalid attribute accessor")
            values = _read_accessor(binary, accessors, views, accessor_index)
            if values.shape[0] != positions.shape[0]:
                raise _UnsupportedModel("all attributes must be per-vertex")
            next_attributes[name] = _append_accessor(
                compact_binary, mutable_views, mutable_accessors, values[output_indexes]
            )
        index_values = simplified_faces.reshape(-1).astype(
            np.uint16 if len(simplified_positions) <= 65535 else np.uint32
        )
        next_indices = _append_accessor(
            compact_binary, mutable_views, mutable_accessors, index_values
        )
        next_primitive = mutable["meshes"][0]["primitives"][0]
        assert isinstance(next_primitive, dict)
        next_primitive["attributes"] = next_attributes
        next_primitive["indices"] = next_indices
        buffers = mutable.get("buffers")
        if not isinstance(buffers, list) or len(buffers) != 1 or not isinstance(buffers[0], dict):
            raise _UnsupportedModel("one binary buffer is required")
        buffers[0]["byteLength"] = len(compact_binary)
        _report_progress(
            on_progress,
            "writing_glb",
            95,
            source_triangles=source_triangles,
            output_triangles=len(simplified_faces),
        )
        return _build_glb(mutable, compact_binary), source_triangles


def _binary_chunk(content: bytes) -> bytes:
    offset = 12
    while offset + 8 <= len(content):
        length = int.from_bytes(content[offset : offset + 4], "little")
        kind = int.from_bytes(content[offset + 4 : offset + 8], "little")
        offset += 8
        if kind == _BIN_CHUNK:
            return content[offset : offset + length]
        offset += length
    raise _UnsupportedModel("GLB has no binary chunk")


def _read_accessor(binary: bytes, accessors: list[object], views: list[object], index: int) -> np.ndarray:
    if not 0 <= index < len(accessors) or not isinstance(accessors[index], dict):
        raise _UnsupportedModel("invalid accessor")
    accessor = accessors[index]
    view_index, component, kind, count = (
        accessor.get("bufferView"), accessor.get("componentType"), accessor.get("type"), accessor.get("count")
    )
    if not isinstance(view_index, int) or component not in _DTYPES or kind not in _WIDTHS or not isinstance(count, int) or count < 1:
        raise _UnsupportedModel("unsupported accessor")
    if accessor.get("sparse") or not 0 <= view_index < len(views) or not isinstance(views[view_index], dict):
        raise _UnsupportedModel("sparse or invalid buffer view")
    view = views[view_index]
    if view.get("byteStride") or view.get("buffer", 0) != 0:
        raise _UnsupportedModel("interleaved or external buffer data")
    dtype, width = _DTYPES[component], _WIDTHS[kind]
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    size = count * width * dtype.itemsize
    if start < 0 or start + size > len(binary):
        raise _UnsupportedModel("accessor is outside the binary buffer")
    values = np.frombuffer(binary, dtype=dtype, count=count * width, offset=start)
    return values.reshape(count, width) if width > 1 else values.copy()


def _accessor_buffer_view(accessors: list[object], index: object) -> int:
    if not isinstance(index, int) or not 0 <= index < len(accessors):
        raise _UnsupportedModel("invalid geometry accessor")
    accessor = accessors[index]
    view_index = accessor.get("bufferView") if isinstance(accessor, dict) else None
    if not isinstance(view_index, int):
        raise _UnsupportedModel("geometry accessor has no buffer view")
    return view_index


def _copy_embedded_images(
    document: dict[str, object],
    binary: bytes,
    views: list[object],
    geometry_view_indexes: set[int],
) -> tuple[bytearray, list[object]]:
    """Copy only image bytes that remain referenced after geometry replacement."""
    images = document.get("images", [])
    if not isinstance(images, list):
        raise _UnsupportedModel("images must be a list")
    retained_indexes: list[int] = []
    for image in images:
        if not isinstance(image, dict) or "bufferView" not in image:
            continue
        view_index = image["bufferView"]
        if not isinstance(view_index, int) or view_index in geometry_view_indexes:
            raise _UnsupportedModel("image buffer view is invalid")
        if view_index not in retained_indexes:
            retained_indexes.append(view_index)
    compact_binary = bytearray()
    compact_views: list[object] = []
    view_mapping: dict[int, int] = {}
    for old_index in retained_indexes:
        if not 0 <= old_index < len(views) or not isinstance(views[old_index], dict):
            raise _UnsupportedModel("embedded image buffer view is invalid")
        view = views[old_index]
        start = int(view.get("byteOffset", 0))
        length = view.get("byteLength")
        if start < 0 or not isinstance(length, int) or length < 0 or start + length > len(binary):
            raise _UnsupportedModel("embedded image buffer view is outside the binary buffer")
        _pad(compact_binary)
        replacement = dict(view)
        replacement["buffer"] = 0
        replacement["byteOffset"] = len(compact_binary)
        replacement["byteLength"] = length
        compact_binary.extend(binary[start : start + length])
        view_mapping[old_index] = len(compact_views)
        compact_views.append(replacement)
    for image in images:
        if isinstance(image, dict) and isinstance(image.get("bufferView"), int):
            image["bufferView"] = view_mapping[image["bufferView"]]
    return compact_binary, compact_views


def _source_indexes_from_collapses(
    points: np.ndarray,
    faces: np.ndarray,
    collapses: np.ndarray,
    simplified_points: np.ndarray,
    simplified_faces: np.ndarray,
) -> np.ndarray:
    """Select one original attribute record for each simplified vertex.

    ``fast_simplification`` exposes the topology-collapse history. Replaying it
    gives the exact original-to-output index mapping in linear time, avoiding
    the former all-pairs nearest-neighbour scan that made high-poly models
    impractical to optimize.
    """
    replay_points, replay_faces, source_to_output = fast_simplification.replay_simplification(
        points.astype(np.float32, copy=True), faces.astype(np.int32, copy=True), collapses
    )
    if not np.array_equal(replay_faces, simplified_faces) or not np.allclose(
        replay_points, simplified_points, rtol=0, atol=1e-5
    ):
        raise _UnsupportedModel("simplification replay does not match the output mesh")
    if source_to_output.shape != (len(points),) or np.any(source_to_output < 0):
        raise _UnsupportedModel("simplification replay returned an invalid attribute mapping")
    result = np.full(len(simplified_points), len(points), dtype=np.intp)
    np.minimum.at(result, source_to_output, np.arange(len(points), dtype=np.intp))
    if np.any(result == len(points)):
        raise _UnsupportedModel("simplification replay left an output vertex unmapped")
    return result


def _report_progress(
    callback: Callable[[str, int, dict[str, int]], None] | None,
    stage: str,
    percent: int,
    **details: int,
) -> None:
    if callback is not None:
        callback(stage, percent, details)


def _append_accessor(
    binary: bytearray, views: list[object], accessors: list[object], values: np.ndarray
) -> int:
    _pad(binary)
    contiguous = np.ascontiguousarray(values)
    offset = len(binary)
    data = contiguous.tobytes()
    binary.extend(data)
    view_index = len(views)
    views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
    width = contiguous.shape[1] if contiguous.ndim == 2 else 1
    type_name = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}.get(width)
    component = {np.dtype("uint8"): 5121, np.dtype("<u2"): 5123, np.dtype("<u4"): 5125, np.dtype("<f4"): 5126}.get(contiguous.dtype)
    if type_name is None or component is None:
        raise _UnsupportedModel("attribute data type is unsupported")
    index = len(accessors)
    accessor: dict[str, object] = {"bufferView": view_index, "componentType": component, "count": len(contiguous), "type": type_name}
    if type_name == "VEC3" and component == 5126:
        accessor["min"] = np.min(contiguous, axis=0).astype(float).tolist()
        accessor["max"] = np.max(contiguous, axis=0).astype(float).tolist()
    accessors.append(accessor)
    return index


def _pad(content: bytearray) -> None:
    content.extend(b"\x00" * ((-len(content)) % 4))


def _build_glb(document: dict[str, object], binary: bytearray) -> bytes:
    json_chunk = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((-len(json_chunk)) % 4)
    _pad(binary)
    body = (
        len(json_chunk).to_bytes(4, "little")
        + _JSON_CHUNK.to_bytes(4, "little")
        + json_chunk
        + len(binary).to_bytes(4, "little")
        + _BIN_CHUNK.to_bytes(4, "little")
        + bytes(binary)
    )
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(body)).to_bytes(4, "little") + body
