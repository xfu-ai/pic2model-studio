import platform
import shutil
import time
from pathlib import Path

from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.events import EventService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.selections import SelectionService
from aipic_to_model.infrastructure.sqlite.repositories import EventRepository


def test_b01_12_500_assets_and_10000_events_replay_are_ordered(tmp_path: Path):
    """The fixture uses real managed imports and the production event counter."""
    root = tmp_path / "project"
    project = ProjectService().create(root, "Performance")
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2), "white").save(source)
    assets = AssetService()
    started = time.perf_counter()
    first = None
    for index in range(500):
        imported = assets.import_file(root, project.id, source, "source_image", f"import-{index}")
        first = first or imported
    elapsed = time.perf_counter() - started
    assert len(assets.list_by_group(root, project.id)) >= 500
    query_samples = []
    for _ in range(100):
        query_started = time.perf_counter()
        assets.list_by_group(root, project.id)
        query_samples.append(time.perf_counter() - query_started)
    query_samples.sort()
    assert query_samples[94] < 0.1
    assert first is not None
    write_samples = []
    for index in range(20):
        command_started = time.perf_counter()
        assets.set_current(root, project.id, first["id"], "user", f"current-{index}")
        assets.hide(root, project.id, first["id"], bool(index % 2), f"hide-{index}")
        write_samples.append(time.perf_counter() - command_started)
    write_samples.sort()
    assert write_samples[18] < 0.1
    selections = SelectionService()
    selection_samples = []
    for index in range(50):
        selection_started = time.perf_counter()
        selections.save(
            root,
            project.id,
            first["id"],
            [{"rect_id": str(index), "label": "fixture", "x": 0, "y": 0, "width": 1, "height": 1}],
            "fixture",
            "user",
        )
        selection_samples.append(time.perf_counter() - selection_started)
    selection_samples.sort()
    assert selection_samples[47] < 0.1
    open_samples = []
    for _ in range(20):
        open_started = time.perf_counter()
        assert ProjectService().open(root).id == project.id
        open_samples.append(time.perf_counter() - open_started)
    open_samples.sort()
    assert open_samples[18] < 0.1

    EventRepository().append_named_many_committed(
        root / "project.sqlite3",
        project.id,
        [
            {
                "event_type": "project.metadata.changed",
                "payload": {"changed_fields": [], "request_id": f"event-{index}"},
            }
            for index in range(10_000)
        ],
    )
    cursor = None
    replayed = []
    while True:
        page = EventService(EventRepository()).replay_project(root, project.id, cursor, 1000)
        replayed.extend(page["items"])
        if not page["items"]:
            break
        cursor = page["next_cursor"]
    sequence = [item["sequence_no"] for item in replayed]
    assert sequence == sorted(sequence)
    assert len(sequence) >= 10_000 and len(sequence) == len(set(sequence))
    moved = tmp_path / "moved"
    move_started = time.perf_counter()
    shutil.move(str(root), str(moved))
    assert ProjectService().open(moved).id == project.id
    assert time.perf_counter() - move_started < 1
    # The generous end-to-end import budget prevents a slow filesystem from
    # masking an ordering regression; non-I/O read methods are covered above.
    assert elapsed < 60, {"platform": platform.platform(), "seconds": elapsed}
