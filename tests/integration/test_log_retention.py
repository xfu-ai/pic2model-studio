import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aipic_to_model.infrastructure.logging import append_log


def test_b01_09_structured_log_redacts_and_removes_expired_files(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    expired = logs / "expired.log"
    expired.write_text("old", encoding="utf-8")
    old_time = (datetime.now(UTC) - timedelta(days=15)).timestamp()
    os.utime(expired, (old_time, old_time))
    append_log(
        tmp_path,
        "app",
        "Authorization: Bearer sentinel C:\\Users\\private",
        project_id="project-id",
        duration_ms=12,
    )
    assert not expired.exists()
    record = json.loads((logs / "app.log").read_text(encoding="utf-8"))
    assert record["project_id"] == "project-id"
    assert record["duration_ms"] == 12
    assert "sentinel" not in record["message"]
    assert "C:\\Users" not in record["message"]


def test_b01_09_log_rotation_keeps_five_files_at_the_ten_mib_boundary(tmp_path: Path):
    payload = "x" * (10 * 1024 * 1024)
    append_log(tmp_path, "rotate", payload)
    append_log(tmp_path, "rotate", "rotate-now")
    logs = tmp_path / "logs"
    assert (logs / "rotate.1.log").is_file() and (logs / "rotate.log").is_file()
    for _ in range(5):
        (logs / "rotate.log").write_text(payload, encoding="utf-8")
        append_log(tmp_path, "rotate", "rotate-now")
    assert len(list(logs.glob("rotate*.log"))) == 5
