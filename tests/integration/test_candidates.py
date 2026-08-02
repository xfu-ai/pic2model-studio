from __future__ import annotations

from pathlib import Path

import pytest

from aipic_to_model.infrastructure.sqlite.candidate_repository import (
    CandidateDraft,
    CandidateRepository,
)
from aipic_to_model.infrastructure.sqlite.connection import connect, migrate


def _database(tmp_path: Path, count: int = 2) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    database = root / "project.sqlite3"
    migrate(database, root / "recovery")
    connection = connect(database)
    try:
        connection.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES('project-1','test','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
        )
        connection.execute("INSERT INTO event_counters VALUES('project-1',1)")
        _asset(connection, "prompt-1", "prompt", "prompt", 1)
        for ordinal in range(1, count + 1):
            _asset(connection, f"image-{ordinal}", f"image-{ordinal}", "generated_image", ordinal)
    finally:
        connection.close()
    return database


def _asset(connection, asset_id: str, family: str, asset_type: str, version: int) -> None:
    connection.execute(
        """INSERT INTO assets(id,project_id,asset_family_id,asset_type,name,version_no,relative_path,
        mime_type,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            asset_id,
            "project-1",
            family,
            asset_type,
            asset_id,
            version,
            f"assets/{asset_id}.png",
            "image/png",
            1,
            asset_id.ljust(64, "0"),
            "2026-01-01T00:00:00Z",
        ),
    )


def test_candidate_group_requires_two_to_eight_and_persists_selection(tmp_path: Path) -> None:
    database = _database(tmp_path, 2)
    repository = CandidateRepository()
    group_id = repository.create(
        database,
        project_id="project-1",
        prompt_asset_id="prompt-1",
        source_asset_id=None,
        provider="fake",
        request={"model": "fake-image", "candidate_count": 2},
        items=[
            CandidateDraft("image-1", "fake", "fake-image", {"candidate_count": 2}),
            CandidateDraft("image-2", "fake", "fake-image", {"candidate_count": 2}),
        ],
    )
    created = repository.get(database, group_id)
    assert created.status == "ready"
    assert [item.evaluation_status for item in created.items] == ["not_evaluated", "not_evaluated"]
    repository.select(
        database,
        project_id="project-1",
        group_id=group_id,
        asset_ids=["image-2"],
        selection_mode="single_continue",
    )
    assert repository.get(database, group_id).selected_asset_ids == ["image-2"]
    connection = connect(database, read_only=True)
    try:
        assert [
            row[0]
            for row in connection.execute("SELECT event_type FROM events ORDER BY sequence_no")
        ] == [
            "candidate.created",
            "candidate.selected",
        ]
    finally:
        connection.close()


@pytest.mark.parametrize("count", [0, 9])
def test_candidate_count_rejects_invalid_range_before_persistence(
    tmp_path: Path, count: int
) -> None:
    database = _database(tmp_path, 2)
    repository = CandidateRepository()
    with pytest.raises(ValueError, match="1 to 8"):
        repository.create(
            database,
            project_id="project-1",
            prompt_asset_id="prompt-1",
            source_asset_id=None,
            provider="fake",
            request={"model": "fake"},
            items=[CandidateDraft("image-1", "fake", "fake", {})] * count,
        )
