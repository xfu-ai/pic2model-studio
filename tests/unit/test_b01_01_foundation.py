from pathlib import Path

import pytest

from aipic_to_model.domain.common import DomainErrorV1, ErrorCode, EventCursorCodec, canonical_json
from aipic_to_model.infrastructure.fs import atomic_io
from aipic_to_model.infrastructure.fs.atomic_io import atomic_write_text


def test_b01_01_canonical_json_and_atomic_failure(tmp_path: Path):
    target = tmp_path / "x.txt"
    atomic_write_text(target, "old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert canonical_json({"b": 1.0, "a": [2]}) == '{"a":[2],"b":1}'
    cursor = EventCursorCodec.encode("p", 3)
    assert EventCursorCodec.decode(cursor, "p") == 3


def test_b01_01_atomic_write_failure_keeps_original_and_cleans_temp(tmp_path: Path, monkeypatch):
    target = tmp_path / "x.txt"
    atomic_write_text(target, "old")

    def fail_replace(_source, _target):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    with pytest.raises(DomainErrorV1) as error:
        atomic_write_text(target, "new")
    assert error.value.code == ErrorCode.LOCAL_STORAGE_UNAVAILABLE
    assert error.value.recoverable is True
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".x.txt.*.tmp"))


def test_b01_01_atomic_new_write_never_replaces_existing_target(tmp_path: Path):
    target = tmp_path / "target.bin"
    target.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        atomic_io.atomic_write_new_bytes(target, b"replacement")
    assert target.read_bytes() == b"original"
