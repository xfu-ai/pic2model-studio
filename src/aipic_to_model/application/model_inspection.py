"""Deterministic, offline GLB authenticity checks and model capability extraction."""

from __future__ import annotations

import json
import math
from typing import Any, Literal, cast

from ..domain.production_models import (
    AnimationSummary,
    Capability,
    MeshSummary,
    ModelCapabilitySet,
    ModelInspection,
)

MAX_GLB_BYTES = 200 * 1024 * 1024
_JSON_CHUNK = 0x4E4F534A


class GLBValidationError(ValueError):
    """Safe internal failure: callers expose only MODEL3D_PARSE_FAILED."""


def _capability(
    available: bool, *, reason: str | None = None, tool: str | None = None
) -> Capability:
    return Capability(available=available, reason=reason, tool_name=tool)


def _capabilities(
    *, parseable: bool, has_animations: bool, preview_renderer: bool, optimizer_available: bool
) -> ModelCapabilitySet:
    if not parseable:
        unavailable = _capability(False, reason="Model failed authenticity validation.")
        return ModelCapabilitySet(
            standard_views=unavailable,
            camera_modes=unavailable,
            environment=unavailable,
            background=unavailable,
            wireframe=unavailable,
            material_channels=unavailable,
            animation=unavailable,
            inspect=_capability(True, tool="model3d.inspect"),
            render_preview=_capability(
                False, reason="Model is not parseable.", tool="model3d.render_preview"
            ),
            optimize=_capability(False, reason="Model is not parseable.", tool="model3d.optimize"),
            regenerate=_capability(True, tool="model3d.generate"),
            open_containing_folder=_capability(True, tool="open_containing_folder"),
        )
    return ModelCapabilitySet(
        standard_views=_capability(True),
        camera_modes=_capability(True),
        environment=_capability(True),
        background=_capability(True),
        wireframe=_capability(True),
        material_channels=_capability(True),
        animation=_capability(
            has_animations,
            reason=None if has_animations else "The model contains no animations.",
        ),
        inspect=_capability(True, tool="model3d.inspect"),
        render_preview=_capability(
            preview_renderer,
            reason=None if preview_renderer else "No approved local PreviewRenderer is available.",
            tool="model3d.render_preview",
        ),
        optimize=_capability(
            optimizer_available,
            reason=None if optimizer_available else "Optimization is not configured.",
            tool="model3d.optimize",
        ),
        regenerate=_capability(True, tool="model3d.generate"),
        open_containing_folder=_capability(True, tool="open_containing_folder"),
    )


def parse_glb_document(content: bytes, *, maximum_bytes: int = MAX_GLB_BYTES) -> dict[str, Any]:
    """Parse only the GLB container JSON; never execute embedded resources."""
    if not 20 <= len(content) <= maximum_bytes or content[:4] != b"glTF":
        raise GLBValidationError("invalid GLB header")
    if int.from_bytes(content[4:8], "little") != 2:
        raise GLBValidationError("unsupported GLB version")
    if int.from_bytes(content[8:12], "little") != len(content):
        raise GLBValidationError("truncated GLB")
    offset, document = 12, None
    while offset < len(content):
        if offset + 8 > len(content):
            raise GLBValidationError("truncated GLB chunk")
        length = int.from_bytes(content[offset : offset + 4], "little")
        kind = int.from_bytes(content[offset + 4 : offset + 8], "little")
        offset += 8
        end = offset + length
        if length % 4 or end > len(content):
            raise GLBValidationError("invalid GLB chunk length")
        if kind == _JSON_CHUNK and document is None:
            try:
                decoded = json.loads(content[offset:end].rstrip(b" \t\r\n\x00").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise GLBValidationError("invalid GLB JSON") from error
            if not isinstance(decoded, dict):
                raise GLBValidationError("GLB JSON must be an object")
            document = decoded
        offset = end
    if not isinstance(document, dict):
        raise GLBValidationError("missing GLB JSON chunk")
    asset = document.get("asset")
    if not isinstance(asset, dict) or not str(asset.get("version", "")).startswith("2."):
        raise GLBValidationError("missing glTF 2 asset declaration")
    return document


def validate_glb_bytes(content: bytes, *, maximum_bytes: int = MAX_GLB_BYTES) -> None:
    parse_glb_document(content, maximum_bytes=maximum_bytes)


def _objects(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and math.isfinite(float(value)) else None


def _accessor_bounds(
    accessors: list[dict[str, Any]], index: object
) -> tuple[list[float], list[float]] | None:
    if not isinstance(index, int) or not 0 <= index < len(accessors):
        return None
    accessor = accessors[index]
    low, high = accessor.get("min"), accessor.get("max")
    if not isinstance(low, list) or not isinstance(high, list) or len(low) != 3 or len(high) != 3:
        return None
    values = ([_number(value) for value in low], [_number(value) for value in high])
    if any(value is None for group in values for value in group):
        return None
    return (
        [float(cast(float, value)) for value in values[0]],
        [float(cast(float, value)) for value in values[1]],
    )


def _animation_duration(animation: dict[str, Any], accessors: list[dict[str, Any]]) -> float:
    durations: list[float] = []
    for sampler in _objects(animation, "samplers"):
        index = sampler.get("input")
        if not isinstance(index, int) or not 0 <= index < len(accessors):
            continue
        maximum = accessors[index].get("max")
        values = maximum if isinstance(maximum, list) else [maximum]
        if values and (value := _number(values[0])) is not None:
            durations.append(value)
    return max(durations, default=0.0)


def inspect_glb(
    content: bytes,
    *,
    local_relative_path: str,
    source_job_id: str | None = None,
    preview_renderer: bool = False,
    optimizer_available: bool = True,
) -> ModelInspection:
    """Return a complete DTO even for a corrupt managed file, without raw diagnostics."""
    try:
        document = parse_glb_document(content)
    except GLBValidationError:
        unavailable = _capability(False, reason="Material data is unavailable.")
        return ModelInspection(
            parseable=False,
            format="glb",
            size_bytes=len(content),
            material_channels={
                "base_color": unavailable,
                "normal": unavailable,
                "roughness": unavailable,
                "metalness": unavailable,
            },
            capabilities=_capabilities(
                parseable=False,
                has_animations=False,
                preview_renderer=False,
                optimizer_available=False,
            ),
            source_job_id=source_job_id,
            local_relative_path=local_relative_path,
            diagnostics_ref="inspection:unparseable",
        )
    accessors, materials, meshes = (
        _objects(document, "accessors"),
        _objects(document, "materials"),
        _objects(document, "meshes"),
    )
    mesh_summaries: list[MeshSummary] = []
    vertices = triangles = 0
    low_values: list[list[float]] = []
    high_values: list[list[float]] = []
    for index, mesh in enumerate(meshes):
        mesh_vertices = mesh_triangles = 0
        material_indexes: set[int] = set()
        for primitive in _objects(mesh, "primitives"):
            attributes = primitive.get("attributes", {})
            position = attributes.get("POSITION") if isinstance(attributes, dict) else None
            if isinstance(position, int) and 0 <= position < len(accessors):
                mesh_vertices += int(accessors[position].get("count", 0) or 0)
                if bounds := _accessor_bounds(accessors, position):
                    low_values.append(bounds[0])
                    high_values.append(bounds[1])
            indices = primitive.get("indices")
            count = (
                int(accessors[indices].get("count", 0) or 0)
                if isinstance(indices, int) and 0 <= indices < len(accessors)
                else 0
            )
            mode = int(primitive.get("mode", 4) or 4)
            mesh_triangles += (
                count // 3 if mode == 4 else max(count - 2, 0) if mode in {5, 6} else 0
            )
            if isinstance(primitive.get("material"), int):
                material_indexes.add(primitive["material"])
        vertices += mesh_vertices
        triangles += mesh_triangles
        mesh_summaries.append(
            MeshSummary(
                name=str(mesh.get("name") or f"mesh-{index + 1}"),
                vertex_count=mesh_vertices,
                triangle_count=mesh_triangles,
                material_count=len(material_indexes),
            )
        )
    material_keys = {
        "base_color": any(
            isinstance(item.get("pbrMetallicRoughness"), dict)
            and "baseColorTexture" in item["pbrMetallicRoughness"]
            for item in materials
        ),
        "normal": any("normalTexture" in item for item in materials),
        "roughness": any(
            isinstance(item.get("pbrMetallicRoughness"), dict)
            and "metallicRoughnessTexture" in item["pbrMetallicRoughness"]
            for item in materials
        ),
        "metalness": any(
            isinstance(item.get("pbrMetallicRoughness"), dict)
            and "metallicRoughnessTexture" in item["pbrMetallicRoughness"]
            for item in materials
        ),
    }
    channels: dict[Literal["base_color", "normal", "roughness", "metalness"], Capability] = {
        "base_color": _capability(
            material_keys["base_color"],
            reason=None
            if material_keys["base_color"]
            else "The model has no matching texture channel.",
        ),
        "normal": _capability(
            material_keys["normal"],
            reason=None
            if material_keys["normal"]
            else "The model has no matching texture channel.",
        ),
        "roughness": _capability(
            material_keys["roughness"],
            reason=None
            if material_keys["roughness"]
            else "The model has no matching texture channel.",
        ),
        "metalness": _capability(
            material_keys["metalness"],
            reason=None
            if material_keys["metalness"]
            else "The model has no matching texture channel.",
        ),
    }
    animations = _objects(document, "animations")
    bounds_xyz: tuple[float, float, float] | None = None
    if low_values:
        bounds_xyz = (
            max(high[0] for high in high_values) - min(low[0] for low in low_values),
            max(high[1] for high in high_values) - min(low[1] for low in low_values),
            max(high[2] for high in high_values) - min(low[2] for low in low_values),
        )
    return ModelInspection(
        parseable=True,
        format="glb",
        size_bytes=len(content),
        vertex_count=vertices,
        triangle_count=triangles,
        meshes=mesh_summaries,
        material_count=len(materials),
        texture_count=len(_objects(document, "textures")),
        bounds_xyz=bounds_xyz,
        bounds_unit="meters" if low_values else None,
        skeleton_count=len(_objects(document, "skins")),
        animations=[
            AnimationSummary(
                name=str(item.get("name") or f"animation-{index + 1}"),
                duration_seconds=_animation_duration(item, accessors),
            )
            for index, item in enumerate(animations)
        ],
        material_channels=channels,
        capabilities=_capabilities(
            parseable=True,
            has_animations=bool(animations),
            preview_renderer=preview_renderer,
            optimizer_available=optimizer_available,
        ),
        source_job_id=source_job_id,
        local_relative_path=local_relative_path,
        diagnostics_ref="inspection:ok",
    )
