from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from ..domain.common import DomainErrorV1, ErrorCode, canonical_json, new_id
from ..domain.coordinates import normalize_rect
from ..domain.selections import SelectionGeometryV1
from .assets import AssetService
from .ports import FilesystemPort, SelectionRepositoryPort


class SelectionService:
    """Selection use cases; repository operations own SQLite connections and transactions."""

    def __init__(
        self,
        repository: SelectionRepositoryPort,
        filesystem: FilesystemPort,
        assets: AssetService,
    ) -> None:
        self._repository = repository
        self._filesystem = filesystem
        self._assets = assets

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["rects"] = json.loads(result.pop("geometry_json"))["rects"]
        result["confirmed_by_user"] = bool(result["confirmed_by_user"])
        return result

    def save(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        rects: list[dict[str, Any]],
        label: str,
        source: str,
        status: str = "draft",
        selection_id: str | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        if status not in {"draft", "edited"}:
            raise DomainErrorV1(
                ErrorCode.INVALID_SELECTION,
                "Selection save only accepts draft or edited state; use confirm to confirm.",
            )
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise DomainErrorV1(
                ErrorCode.INVALID_SELECTION,
                "Selection confidence must be between 0 and 1.",
            )
        payload_hash = canonical_json(
            {
                "asset_id": asset_id,
                "rects": rects,
                "label": label,
                "confidence": confidence,
                "source": source,
                "status": status,
                "selection_id": selection_id,
                "expected_revision": expected_revision,
            }
        )
        asset = self._repository.editable_metadata(root / "project.sqlite3", project_id, asset_id)
        if asset is None:
            raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "Selection asset is unavailable.")
        metadata = json.loads(asset["metadata_json"])
        normalized = [normalize_rect(rect, metadata["width"], metadata["height"]) for rect in rects]
        normalized = SelectionGeometryV1.from_payload(
            normalized, metadata["width"], metadata["height"]
        ).as_json()
        return self._repository.save_committed(
            root / "project.sqlite3",
            project_id=project_id,
            asset_id=asset_id,
            selection_id=selection_id or new_id(),
            expected_revision=expected_revision,
            rects=normalized,
            label=label,
            confidence=confidence,
            source=source,
            status=status,
            payload_hash=payload_hash,
            request_id=request_id,
        )

    def get(
        self,
        root: Path,
        project_id: str,
        selection_id: str,
        *,
        read_only: bool = False,
    ) -> dict[str, Any]:
        row = self._repository.get(
            root / "project.sqlite3", project_id, selection_id, read_only=read_only
        )
        if row is None:
            raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "Selection does not exist.")
        return self._public(row)

    def list_for_asset(self, root: Path, project_id: str, asset_id: str) -> list[dict[str, Any]]:
        return [
            self.get(root, project_id, selection_id)
            for selection_id in self._repository.ids_for_asset(
                root / "project.sqlite3", project_id, asset_id
            )
        ]

    def confirm(
        self,
        root: Path,
        project_id: str,
        selection_id: str,
        expected_revision: int,
        request_id: str | None = None,
        *,
        include_event: bool = False,
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        payload = canonical_json(
            {"selection_id": selection_id, "expected_revision": expected_revision}
        )
        committed = self._repository.confirm_committed(
            root / "project.sqlite3",
            project_id=project_id,
            selection_id=selection_id,
            expected_revision=expected_revision,
            payload_hash=payload,
            request_id=request_id,
        )
        if include_event:
            return committed
        selection = committed.get("selection")
        if not isinstance(selection, dict):
            raise DomainErrorV1(
                ErrorCode.INVALID_SELECTION,
                "Selection confirmation result is invalid.",
            )
        return selection

    def cancel_step(
        self,
        root: Path,
        project_id: str,
        selection_id: str | None,
        action_id: str,
        run_id: str | None = None,
    ) -> None:
        self._filesystem.require_writable_root(root)
        self._repository.cancel_committed(
            root / "project.sqlite3",
            project_id=project_id,
            selection_id=selection_id,
            action_id=action_id,
            run_id=run_id,
        )

    def _selection_and_image(
        self, root: Path, project_id: str, selection_id: str
    ) -> tuple[dict[str, Any], Path]:
        selection = self.get(root, project_id, selection_id)
        asset = self._repository.source_asset(
            root / "project.sqlite3", project_id, selection["asset_id"]
        )
        if asset is None:
            raise DomainErrorV1(
                ErrorCode.INVALID_SELECTION, "Selection source image is unavailable."
            )
        return selection, root / asset["relative_path"]

    def render_annotation(
        self,
        root: Path,
        project_id: str,
        selection_id: str,
        request_id: str,
        *,
        outline: tuple[int, int, int, int] = (255, 64, 64, 255),
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        selection, source = self._selection_and_image(root, project_id, selection_id)
        temporary = root / "temp" / f"annotation-{selection_id}.png"
        try:
            with Image.open(source) as original:
                canvas = original.convert("RGBA")
                drawing = ImageDraw.Draw(canvas)
                for rect in selection["rects"]:
                    x, y = int(rect["x"]), int(rect["y"])
                    right, bottom = x + int(rect["width"]), y + int(rect["height"])
                    drawing.rectangle((x, y, right, bottom), outline=outline, width=3)
                    drawing.text((x + 3, y + 3), str(rect["label"]), fill=outline)
                canvas.save(temporary, "PNG")
            return self._assets.register_derived(
                root,
                project_id,
                temporary,
                "annotation",
                request_id,
                parent_asset_id=selection["asset_id"],
                input_asset_ids=[selection["asset_id"]],
                provenance={
                    "selection_ids": [selection_id],
                    "parameters": {"operation": "render_annotation"},
                },
            )
        finally:
            temporary.unlink(missing_ok=True)

    def crop(
        self, root: Path, project_id: str, selection_id: str, request_id: str
    ) -> list[dict[str, Any]]:
        self._filesystem.require_writable_root(root)
        selection, source = self._selection_and_image(root, project_id, selection_id)
        if selection["status"] != "confirmed":
            raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "Crop requires a confirmed selection.")
        temporary_paths: list[Path] = []
        try:
            with Image.open(source) as original:
                for index, rect in enumerate(selection["rects"]):
                    path = root / "temp" / f"crop-{selection_id}-{index}.png"
                    original.crop(
                        (
                            int(rect["x"]),
                            int(rect["y"]),
                            int(rect["x"]) + int(rect["width"]),
                            int(rect["y"]) + int(rect["height"]),
                        )
                    ).save(path, "PNG")
                    temporary_paths.append(path)
            return [
                self._assets.register_derived(
                    root,
                    project_id,
                    path,
                    "crop",
                    f"{request_id}:crop:{index}",
                    parent_asset_id=selection["asset_id"],
                    input_asset_ids=[selection["asset_id"]],
                    provenance={
                        "selection_ids": [selection_id],
                        "parameters": {"operation": "crop", "rect_index": index},
                    },
                )
                for index, path in enumerate(temporary_paths)
            ]
        finally:
            for path in temporary_paths:
                path.unlink(missing_ok=True)
