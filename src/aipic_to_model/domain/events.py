from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from .errors import DomainErrorV1, ErrorCode
from .ids import canonical_json


@dataclass(frozen=True)
class EventEnvelopeV1:
    event_id: str
    event_type: str
    event_version: int
    project_id: str
    sequence_no: int
    payload: dict
    created_at: str
    conversation_id: str | None = None
    run_id: str | None = None
    entity_id: str | None = None


class EventCursorCodec:
    @staticmethod
    def encode(project_id: str, sequence_no: int) -> str:
        return (
            base64.urlsafe_b64encode(
                canonical_json(
                    {"v": 1, "project_id": project_id, "sequence_no": sequence_no}
                ).encode()
            )
            .decode()
            .rstrip("=")
        )

    @staticmethod
    def decode(cursor: str, project_id: str) -> int:
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            body = json.loads(raw)
            if body != {
                "v": 1,
                "project_id": project_id,
                "sequence_no": body.get("sequence_no"),
            } or not isinstance(body["sequence_no"], int):
                raise ValueError
            return body["sequence_no"]
        except Exception as error:
            raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "事件游标无效。") from error
