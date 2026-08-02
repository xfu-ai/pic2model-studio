"""Managed three-view production operations for B02-06."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ..domain.common import new_id
from ..domain.multiview_rules import VIEWS, validate_quality
from ..domain.production_models import MultiviewValidation
from .assets import AssetService
from .selections import SelectionService


class MultiviewService:
    def __init__(self, assets: AssetService, selections: SelectionService, repository: Any) -> None:
        self._assets = assets
        self._selections = selections
        self._repository = repository

    def create_from_base64_views(
        self,
        root: Path,
        project_id: str,
        *,
        source_asset_id: str,
        views: dict[str, str],
        request_id: str,
        prompt_asset_id: str | None = None,
        provider_profile: str | None = None,
        model: str | None = None,
        tool_call_id: str | None = None,
    ) -> str:
        if set(views) != set(VIEWS):
            raise ValueError("three-view generation requires front, side, and back")
        assets: dict[str, dict[str, Any]] = {}
        for view in VIEWS:
            content, suffix = self._decode_image(views[view])
            temporary = root / "temp" / f"multiview-{view}-{new_id()}.{suffix}"
            try:
                temporary.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_bytes(content)
                assets[view] = self._assets.register_derived(
                    root,
                    project_id,
                    temporary,
                    "multiview",
                    f"{request_id}:{view}",
                    parent_asset_id=source_asset_id,
                    input_asset_ids=[source_asset_id]
                    + ([prompt_asset_id] if prompt_asset_id else []),
                    name=f"multiview-{view}.{suffix}",
                    provenance={
                        "source_kind": "tool",
                        "prompt_asset_id": prompt_asset_id,
                        "tool_call_id": tool_call_id,
                        "provider_profile": provider_profile,
                        "model": model,
                        "parameters": {"operation": "multiview.generate", "view": view},
                    },
                )
            finally:
                temporary.unlink(missing_ok=True)
        set_id = self._repository.create_set(
            root / "project.sqlite3",
            project_id=project_id,
            source_asset_id=source_asset_id,
            members={view: str(assets[view]["id"]) for view in VIEWS},
        )
        selection_ids: dict[str, str] = {}
        for view in VIEWS:
            metadata = assets[view]["metadata"]
            selection = self._selections.save(
                root,
                project_id,
                str(assets[view]["id"]),
                [
                    {
                        "rect_id": view,
                        "x": 0,
                        "y": 0,
                        "width": metadata["width"],
                        "height": metadata["height"],
                    }
                ],
                view,
                "agent",
                request_id=f"{request_id}:region:{view}",
                confidence=1.0,
            )
            selection_ids[view] = str(selection["id"])
        self._repository.attach_regions(
            root / "project.sqlite3", set_id=set_id, selection_ids=selection_ids
        )
        return set_id

    def create_sheet_from_base64(
        self,
        root: Path,
        project_id: str,
        *,
        source_asset_id: str,
        image_base64: str,
        request_id: str,
        prompt_asset_id: str | None = None,
        provider_profile: str | None = None,
        model: str | None = None,
        tool_call_id: str | None = None,
    ) -> str:
        """Register one generated horizontal front/side/back sheet for manual cropping."""

        content, suffix = self._decode_image(image_base64)
        temporary = root / "temp" / f"multiview-sheet-{new_id()}.{suffix}"
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(content)
            asset = self._assets.register_derived(
                root,
                project_id,
                temporary,
                "multiview",
                request_id,
                parent_asset_id=source_asset_id,
                input_asset_ids=[source_asset_id]
                + ([prompt_asset_id] if prompt_asset_id else []),
                name=f"multiview-sheet.{suffix}",
                provenance={
                    "source_kind": "tool",
                    "prompt_asset_id": prompt_asset_id,
                    "tool_call_id": tool_call_id,
                    "provider_profile": provider_profile,
                    "model": model,
                    "parameters": {
                        "operation": "multiview.generate",
                        "layout": "horizontal_front_side_back_sheet",
                    },
                },
            )
        finally:
            temporary.unlink(missing_ok=True)
        return str(asset["id"])

    def create_from_existing_views(
        self,
        root: Path,
        project_id: str,
        *,
        source_asset_id: str,
        members: dict[str, str],
        request_id: str,
    ) -> str:
        """Create a durable three-view set from user-selected managed assets.

        This is the desktop equivalent of the recovered app's manual
        front/side/back assignment.  Regions start as the whole image and are
        immediately confirmed because each selected asset is itself a view.
        """
        if set(members) != set(VIEWS):
            raise ValueError("front, side, and back assets are required")
        for asset_id in members.values():
            asset = self._assets.get(root, project_id, asset_id)
            if asset["asset_type"] not in {"source_image", "generated_image", "multiview", "crop"}:
                raise ValueError("three-view members must be managed image assets")
        set_id = self._repository.create_set(
            root / "project.sqlite3", project_id=project_id, source_asset_id=source_asset_id, members=members
        )
        selection_ids: dict[str, str] = {}
        for view in VIEWS:
            asset = self._assets.get(root, project_id, members[view])
            metadata = asset["metadata"]
            selection = self._selections.save(
                root, project_id, members[view], [{"rect_id": view, "x": 0, "y": 0, "width": metadata["width"], "height": metadata["height"]}],
                view, "user", request_id=f"{request_id}:{view}",
            )
            self._selections.confirm(root, project_id, str(selection["id"]), int(selection["revision"]), f"{request_id}:confirm:{view}")
            selection_ids[view] = str(selection["id"])
        self._repository.attach_regions(root / "project.sqlite3", set_id=set_id, selection_ids=selection_ids)
        return set_id

    def confirm_regions(self, root: Path, project_id: str, *, set_id: str, request_id: str) -> None:
        region_ids = self._repository.region_selection_ids(root / "project.sqlite3", set_id=set_id)
        for view in VIEWS:
            selection = self._selections.get(root, project_id, region_ids[view])
            self._selections.confirm(
                root,
                project_id,
                region_ids[view],
                int(selection["revision"]),
                f"{request_id}:{view}",
            )

    def set_regions(
        self,
        root: Path,
        project_id: str,
        *,
        set_id: str,
        regions: dict[str, dict[str, int]],
        request_id: str,
    ) -> None:
        if set(regions) != set(VIEWS):
            raise ValueError("front, side, and back regions are required")
        members = self._repository.current_assets(root / "project.sqlite3", set_id)
        if set(members) != set(VIEWS):
            raise ValueError("three-view set is incomplete")
        selection_ids: dict[str, str] = {}
        for view in VIEWS:
            rect = regions[view]
            selection = self._selections.save(
                root,
                project_id,
                members[view],
                [{"rect_id": view, **rect}],
                view,
                "user",
                request_id=f"{request_id}:{view}",
            )
            selection = self._selections.confirm(
                root,
                project_id,
                str(selection["id"]),
                int(selection["revision"]),
                f"{request_id}:confirm:{view}",
            )
            selection_ids[view] = str(selection["id"])
        self._repository.attach_regions(
            root / "project.sqlite3", set_id=set_id, selection_ids=selection_ids
        )

    def crop_confirmed_views(
        self, root: Path, project_id: str, *, set_id: str, request_id: str
    ) -> dict[str, str]:
        region_ids = self._repository.region_selection_ids(root / "project.sqlite3", set_id=set_id)
        crops: dict[str, str] = {}
        for view in VIEWS:
            crop = self._selections.crop(
                root, project_id, region_ids[view], f"{request_id}:{view}"
            )[0]
            self._repository.regenerate_view(
                root / "project.sqlite3", set_id=set_id, view_name=view, asset_id=str(crop["id"])
            )
            crops[view] = str(crop["id"])
        return crops

    def validate(self, root: Path, *, set_id: str, checks: dict[str, str]) -> MultiviewValidation:
        report = validate_quality(checks)
        self._repository.record_validation(
            root / "project.sqlite3", set_id=set_id, validation=report.model_dump(mode="json")
        )
        return report

    @staticmethod
    def _decode_image(value: str) -> tuple[bytes, str]:
        try:
            content = base64.b64decode(value, validate=True)
            with Image.open(BytesIO(content)) as image:
                image.verify()
            with Image.open(BytesIO(content)) as image:
                format_name = image.format
        except (OSError, ValueError) as error:
            raise ValueError("three-view provider returned an invalid image") from error
        suffix = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(format_name or "")
        if suffix is None:
            raise ValueError("three-view provider returned an unsupported image format")
        return content, suffix
