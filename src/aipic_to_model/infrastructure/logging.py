from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SECRET = re.compile(
    r"(?im)(authorization|api[_-]?key|token|password|signature|sig|x-amz-signature)\s*[=:]\s*(?:Bearer\s+)?[^\r\n]*"
)
_PATH = re.compile(r"(?i)[a-z]:\\[^\s,]+")
_POSIX_PATH = re.compile(r"(?<![\w:])/(?:[^\s/?#]+/)+[^\s?#,]+")
_SIGNATURE_QUERY = re.compile(r"(?i)([?&](?:x-amz-signature|signature|sig)=)[^&#\s]+")
_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def redact(value: object) -> str:
    text = _SECRET.sub("[REDACTED]", str(value))
    text = _SIGNATURE_QUERY.sub(r"\1[REDACTED]", text)
    return _POSIX_PATH.sub("[PROJECT_PATH]", _PATH.sub("[PROJECT_PATH]", text))


def redact_structure(value: object) -> object:
    """Return a JSON-safe audit projection without secret-shaped values.

    Provenance and tool arguments are persisted and exported, so redaction
    must retain their structure rather than stringify the whole payload.  An
    unknown nested key with a secret-shaped name is never retained.
    """
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if any(
                token in name.lower()
                for token in (
                    "key",
                    "token",
                    "secret",
                    "password",
                    "authorization",
                    "signature",
                    "sig",
                )
            ):
                result[name] = "[REDACTED]"
            else:
                result[name] = redact_structure(item)
        return result
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def append_log(root: Path, component: str, message: object, **fields: object) -> None:
    if not _COMPONENT.fullmatch(component):
        raise ValueError("invalid log component")
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(UTC) - timedelta(days=14)
    for old in log_dir.glob("*.log"):
        if datetime.fromtimestamp(old.stat().st_mtime, UTC) < cutoff:
            old.unlink()
    target = log_dir / f"{component}.log"
    record = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": str(fields.pop("level", "INFO")),
        "component": component,
        "project_id": fields.pop("project_id", None),
        "conversation_id": fields.pop("conversation_id", None),
        "run_id": fields.pop("run_id", None),
        "tool_call_id": fields.pop("tool_call_id", None),
        "job_id": fields.pop("job_id", None),
        "provider_request_id": fields.pop("provider_request_id", None),
        "correlation_id": fields.pop("correlation_id", None),
        "error_code": fields.pop("error_code", None),
        "duration_ms": fields.pop("duration_ms", None),
        "message": redact(message),
    }
    record.update({key: redact(value) for key, value in fields.items()})
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    if target.exists() and target.stat().st_size + len(line.encode()) > 10 * 1024 * 1024:
        (log_dir / f"{component}.4.log").unlink(missing_ok=True)
        for index in range(3, 0, -1):
            previous, following = (
                log_dir / f"{component}.{index}.log",
                log_dir / f"{component}.{index + 1}.log",
            )
            if previous.exists():
                previous.replace(following)
        target.replace(log_dir / f"{component}.1.log")
    with target.open("a", encoding="utf-8") as stream:
        stream.write(line)
