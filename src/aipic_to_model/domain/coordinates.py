from __future__ import annotations

from math import ceil, floor
from typing import cast

from .common import DomainErrorV1, ErrorCode


def image_to_view(
    x: float, y: float, scale: float, pan_x: float, pan_y: float
) -> tuple[float, float]:
    return x * scale + pan_x, y * scale + pan_y


def view_to_image(
    x: float, y: float, scale: float, pan_x: float, pan_y: float
) -> tuple[float, float]:
    if scale <= 0:
        raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "缩放比例必须大于零。")
    return (x - pan_x) / scale, (y - pan_y) / scale


def normalize_rect(
    rect: dict[str, object], image_width: int, image_height: int
) -> dict[str, object]:
    try:
        x, y, width, height = (
            float(cast(str | float | int, rect[key])) for key in ("x", "y", "width", "height")
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "选区矩形无效。") from error
    left, top = max(0, floor(x)), max(0, floor(y))
    right, bottom = min(image_width, ceil(x + width)), min(image_height, ceil(y + height))
    if right <= left or bottom <= top:
        raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "选区必须有正面积且位于图像内。")
    return {
        "rect_id": str(rect.get("rect_id") or "rect"),
        "label": str(rect.get("label") or ""),
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }
