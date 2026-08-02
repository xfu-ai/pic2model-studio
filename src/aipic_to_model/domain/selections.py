from __future__ import annotations

from dataclasses import dataclass

from .common import DomainErrorV1, ErrorCode


@dataclass(frozen=True)
class SelectionRectV1:
    rect_id: str
    label: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class SelectionGeometryV1:
    """Canonical original-image geometry; viewport state is intentionally absent."""

    rects: tuple[SelectionRectV1, ...]

    @classmethod
    def from_payload(
        cls, rects: list[dict], image_width: int, image_height: int
    ) -> SelectionGeometryV1:
        if not rects:
            raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "选区不能为空。")
        values: list[SelectionRectV1] = []
        seen: set[str] = set()
        for raw in rects:
            try:
                coordinates = (raw["x"], raw["y"], raw["width"], raw["height"])
                if any(type(value) is not int for value in coordinates):
                    raise ValueError("selection geometry must be integral")
                item = SelectionRectV1(
                    str(raw["rect_id"]),
                    str(raw.get("label", "")),
                    raw["x"],
                    raw["y"],
                    raw["width"],
                    raw["height"],
                )
            except (KeyError, TypeError, ValueError) as error:
                raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "选区坐标无效。") from error
            if (
                not item.rect_id
                or item.rect_id in seen
                or item.x < 0
                or item.y < 0
                or item.width <= 0
                or item.height <= 0
                or item.x + item.width > image_width
                or item.y + item.height > image_height
            ):
                raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "选区超出原图边界。")
            seen.add(item.rect_id)
            values.append(item)
        return cls(tuple(values))

    def as_json(self) -> list[dict[str, int | str]]:
        return [item.__dict__.copy() for item in self.rects]
