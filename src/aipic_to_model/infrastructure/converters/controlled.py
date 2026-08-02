"""Fixed-template subprocess converters; no Tool input becomes a command."""

from __future__ import annotations

import json
import os
import signal
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ...application.jobs.model_conversion import BackendAttempt

MAX_CONVERTER_MEMORY_BYTES = 2 * 1024 * 1024 * 1024


class _WindowsJob:
    """Best-effort Windows Job Object with memory, CPU, and tree-kill limits."""

    def __init__(self, process: subprocess.Popen[bytes], timeout_seconds: int) -> None:
        self._handle = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class BASIC_LIMITS(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class EXTENDED_LIMITS(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BASIC_LIMITS),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            limits = EXTENDED_LIMITS()
            limits.BasicLimitInformation.PerProcessUserTimeLimit = timeout_seconds * 10_000_000
            limits.BasicLimitInformation.ActiveProcessLimit = 32
            limits.BasicLimitInformation.LimitFlags = 0x2 | 0x8 | 0x100 | 0x200 | 0x2000
            limits.ProcessMemoryLimit = MAX_CONVERTER_MEMORY_BYTES
            limits.JobMemoryLimit = MAX_CONVERTER_MEMORY_BYTES
            process_handle = process._handle  # type: ignore[attr-defined]
            if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ) or not kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(process_handle)):
                kernel32.CloseHandle(handle)
                return
            self._handle = handle
            self._kernel32 = kernel32
        except AttributeError, OSError, TypeError:
            self._handle = None

    def terminate(self) -> bool:
        if self._handle is None:
            return False
        self._kernel32.TerminateJobObject(self._handle, 1)
        return True

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _terminate_tree(process: subprocess.Popen[bytes], job: _WindowsJob) -> None:
    if job.terminate():
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            shell=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_controlled(command: list[str], *, cwd: Path, timeout_seconds: int) -> int | None:
    """Run a fixed command with no captured output and a bounded process tree."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    job = _WindowsJob(process, timeout_seconds)
    try:
        try:
            return process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _terminate_tree(process, job)
            process.wait(timeout=10)
            return None
    finally:
        job.close()


@dataclass(frozen=True)
class ApprovedConverterSettings:
    """Locally configured Blender executable for controlled model export."""

    blender_executable: Path | None = None


def _approved_executable(path: Path | None) -> Path | None:
    if path is None:
        return None
    resolved = path.resolve()
    if not resolved.is_absolute() or str(resolved).startswith("\\\\") or not resolved.is_file():
        return None
    return resolved


class BlenderBackend:
    name: Literal["blender"] = "blender"

    def __init__(self, settings: ApprovedConverterSettings) -> None:
        self._executable = _approved_executable(settings.blender_executable) or _discover_blender()

    def convert(self, source: Path, destination: Path, *, timeout_seconds: int) -> BackendAttempt:
        if self._executable is None:
            return BackendAttempt(
                self.name, "skipped", "No approved Blender executable is configured."
            )
        try:
            with source.open("rb") as input_file:
                magic = input_file.read(4)
            if magic != b"glTF":
                return BackendAttempt(self.name, "failed", "Blender input was not a GLB.")
        except OSError:
            return BackendAttempt(self.name, "failed", "Blender input could not be read.")
        script = destination.parent / "blender_export.py"
        script.write_text(
            "import bpy,sys\n"
            "src,dst=sys.argv[sys.argv.index('--')+1:]\n"
            "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
            "loaded=bpy.ops.import_scene.gltf(filepath=src)\n"
            "if 'FINISHED' not in loaded: raise RuntimeError('GLB import failed')\n"
            "if not any(obj.type == 'MESH' for obj in bpy.context.scene.objects): raise RuntimeError('GLB contained no mesh')\n"
            "saved=bpy.ops.export_scene.fbx(filepath=dst,add_leaf_bones=False)\n"
            "if 'FINISHED' not in saved: raise RuntimeError('FBX export failed')\n",
            encoding="utf-8",
        )
        try:
            try:
                returncode = _run_controlled(
                    [
                        str(self._executable),
                        "--background",
                        "--factory-startup",
                        "--python",
                        str(script),
                        "--",
                        str(source),
                        str(destination),
                    ],
                    cwd=destination.parent,
                    timeout_seconds=timeout_seconds,
                )
            except OSError:
                return BackendAttempt(self.name, "failed", "Process could not be started.")
        finally:
            script.unlink(missing_ok=True)
        if returncode is None:
            return BackendAttempt(self.name, "failed", "Process timed out.")
        return BackendAttempt(
            self.name,
            "succeeded" if returncode == 0 else "failed",
            "completed" if returncode == 0 else "Process failed.",
        )


class GeometryFbxBackend:
    name: Literal["geometry_fbx"] = "geometry_fbx"

    def convert(self, source: Path, destination: Path, *, timeout_seconds: int) -> BackendAttempt:
        del timeout_seconds
        try:
            vertices, triangles = _extract_glb_geometry(source)
            polygon_indices = [
                value
                for face in triangles
                for value in (face[0], face[1], -face[2] - 1)
            ]
            if not vertices or not polygon_indices:
                return BackendAttempt(self.name, "failed", "No mesh geometry was available.")
            destination.write_text(
                _ascii_fbx(vertices, polygon_indices),
                encoding="ascii",
                newline="\n",
            )
        except (ImportError, OSError, ValueError, TypeError):
            return BackendAttempt(self.name, "failed", "Pure-Python geometry conversion failed.")
        return BackendAttempt(self.name, "succeeded", "completed with geometry-only ASCII FBX")


def _extract_glb_geometry(source: Path) -> tuple[list[float], list[tuple[int, int, int]]]:
    """Read uncompressed triangle primitives without an optional exporter dependency."""
    content = source.read_bytes()
    if len(content) < 20 or content[:4] != b"glTF":
        raise ValueError("not a GLB")
    _, version, total = struct.unpack_from("<4sII", content, 0)
    if version != 2 or total != len(content):
        raise ValueError("unsupported GLB")
    document: dict[str, object] | None = None
    binary = b""
    offset = 12
    while offset + 8 <= len(content):
        length, chunk_type = struct.unpack_from("<II", content, offset)
        payload = content[offset + 8 : offset + 8 + length]
        offset += 8 + length
        if chunk_type == 0x4E4F534A:
            document = json.loads(payload.rstrip(b"\x00 \t\r\n").decode("utf-8"))
        elif chunk_type == 0x004E4942:
            binary = payload
    if not isinstance(document, dict) or not binary:
        raise ValueError("GLB chunks are incomplete")
    accessors = document.get("accessors")
    views = document.get("bufferViews")
    meshes = document.get("meshes")
    if not isinstance(accessors, list) or not isinstance(views, list) or not isinstance(meshes, list):
        raise ValueError("GLB geometry tables are missing")

    def accessor_values(index: int) -> list[tuple[float | int, ...]]:
        accessor = accessors[index]
        if not isinstance(accessor, dict) or "bufferView" not in accessor:
            raise ValueError("sparse GLB accessors are not supported by the fallback")
        view = views[int(accessor["bufferView"])]
        if not isinstance(view, dict) or int(view.get("buffer", 0)) != 0:
            raise ValueError("external GLB buffers are not supported")
        component_type = int(accessor["componentType"])
        formats = {5121: "B", 5123: "H", 5125: "I", 5126: "f"}
        components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
        fmt = formats.get(component_type)
        width = components.get(str(accessor["type"]))
        if fmt is None or width is None:
            raise ValueError("unsupported GLB accessor")
        component_size = struct.calcsize(fmt)
        stride = int(view.get("byteStride", component_size * width))
        start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
        count = int(accessor["count"])
        unpack = struct.Struct("<" + fmt * width)
        return [unpack.unpack_from(binary, start + item * stride) for item in range(count)]

    vertices: list[float] = []
    triangles: list[tuple[int, int, int]] = []
    for mesh in meshes:
        if not isinstance(mesh, dict) or not isinstance(mesh.get("primitives"), list):
            continue
        for primitive in mesh["primitives"]:
            if not isinstance(primitive, dict) or int(primitive.get("mode", 4)) != 4:
                continue
            attributes = primitive.get("attributes")
            if not isinstance(attributes, dict) or "POSITION" not in attributes:
                continue
            positions = accessor_values(int(attributes["POSITION"]))
            base = len(vertices) // 3
            for position in positions:
                if len(position) != 3:
                    raise ValueError("POSITION must be VEC3")
                vertices.extend(float(value) for value in position)
            if "indices" in primitive:
                raw_indices = [int(value[0]) for value in accessor_values(int(primitive["indices"]))]
            else:
                raw_indices = list(range(len(positions)))
            if len(raw_indices) % 3:
                raise ValueError("triangle index count is invalid")
            triangles.extend(
                (base + raw_indices[item], base + raw_indices[item + 1], base + raw_indices[item + 2])
                for item in range(0, len(raw_indices), 3)
            )
    if not vertices or not triangles:
        raise ValueError("GLB has no supported triangle mesh")
    return vertices, triangles


def _discover_blender() -> Path | None:
    configured = os.environ.get("BLENDER_EXE")
    candidates: list[Path] = [Path(configured)] if configured else []
    discovered = shutil.which("blender")
    if discovered:
        candidates.append(Path(discovered))
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates.extend(
            sorted(
                (program_files / "Blender Foundation").glob("Blender */blender.exe"),
                reverse=True,
            )
        )
    return next(
        (candidate.resolve() for candidate in candidates if candidate.is_file()),
        None,
    )


def _ascii_fbx(vertices: list[float], polygon_indices: list[int]) -> str:
    """Write the project's deliberately limited geometry-only FBX fallback."""
    vertex_values = ",".join(f"{value:.9g}" for value in vertices)
    index_values = ",".join(str(value) for value in polygon_indices)
    polygon_count = len(polygon_indices) // 3
    return (
        "; FBX 7.4.0 project file\n"
        "FBXHeaderExtension:  {\n"
        "  FBXHeaderVersion: 1003\n"
        "  FBXVersion: 7400\n"
        "}\n"
        "GlobalSettings:  {\n"
        "  Version: 1000\n"
        "  Properties70:  {\n"
        "    P: \"UpAxis\", \"int\", \"Integer\", \"\",1\n"
        "    P: \"UpAxisSign\", \"int\", \"Integer\", \"\",1\n"
        "    P: \"FrontAxis\", \"int\", \"Integer\", \"\",2\n"
        "    P: \"FrontAxisSign\", \"int\", \"Integer\", \"\",-1\n"
        "    P: \"CoordAxis\", \"int\", \"Integer\", \"\",0\n"
        "    P: \"CoordAxisSign\", \"int\", \"Integer\", \"\",1\n"
        "    P: \"UnitScaleFactor\", \"double\", \"Number\", \"\",1\n"
        "  }\n"
        "}\n"
        "Definitions:  {\n"
        "  Version: 100\n"
        "  Count: 2\n"
        "  ObjectType: \"Geometry\" { Count: 1 }\n"
        "  ObjectType: \"Model\" { Count: 1 }\n"
        "}\n"
        "Objects:  {\n"
        "  Geometry: 1, \"Geometry::AIPicMesh\", \"Mesh\" {\n"
        "    GeometryVersion: 124\n"
        f"    Vertices: *{len(vertices)} {{ a: {vertex_values} }}\n"
        f"    PolygonVertexIndex: *{len(polygon_indices)} {{ a: {index_values} }}\n"
        f"    PolygonCount: {polygon_count}\n"
        "  }\n"
        "  Model: 2, \"Model::AIPicMesh\", \"Mesh\" {\n"
        "    Version: 232\n"
        "    Properties70:  {\n"
        "      P: \"Lcl Translation\", \"Lcl Translation\", \"\", \"A\",0,0,0\n"
        "      P: \"Lcl Rotation\", \"Lcl Rotation\", \"\", \"A\",0,0,0\n"
        "      P: \"Lcl Scaling\", \"Lcl Scaling\", \"\", \"A\",1,1,1\n"
        "    }\n"
        "  }\n"
        "}\n"
        "Connections:  {\n"
        "  C: \"OO\",1,2\n"
        "  C: \"OO\",2,0\n"
        "}\n"
    )


def default_conversion_backends(
    settings: ApprovedConverterSettings | None = None,
) -> tuple[BlenderBackend, GeometryFbxBackend]:
    return (
        BlenderBackend(settings or ApprovedConverterSettings()),
        GeometryFbxBackend(),
    )
