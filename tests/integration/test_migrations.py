import hashlib
import sqlite3
from pathlib import Path

import pytest

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.common import DomainErrorV1
from aipic_to_model.infrastructure.sqlite import connection as connection_module
from aipic_to_model.infrastructure.sqlite.connection import (
    connect,
    migrate,
    migrate_app,
    transaction,
)


def test_b01_03_migration_is_repeatable_and_enables_wal_and_foreign_keys(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    migrate(root / "project.sqlite3", root / "recovery")
    connection = connect(root / "project.sqlite3")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 11
    assert (
        connection.execute("SELECT root_path FROM projects WHERE id=?", (project.id,)).fetchone()[0]
        == "."
    )
    connection.close()


def test_b01_03_app_database_migration_is_idempotent(tmp_path: Path):
    path = tmp_path / "app.sqlite3"
    migrate_app(path)
    migrate_app(path)
    connection = connect(path)
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name='recent_projects'"
    ).fetchone()
    assert not connection.execute(
        "SELECT name FROM sqlite_master WHERE name='legacy_migrations'"
    ).fetchone()
    connection.close()


def test_b01_03_historical_v1_project_database_upgrades_to_current_schema(tmp_path: Path):
    database, recovery = tmp_path / "project.sqlite3", tmp_path / "recovery"
    migration = (
        Path(__file__).parents[2]
        / "src"
        / "aipic_to_model"
        / "infrastructure"
        / "sqlite"
        / "migrations"
        / "0001_core.sql"
    )
    content = migration.read_bytes()
    connection = sqlite3.connect(database)
    connection.executescript(content.decode("utf-8"))
    connection.execute(
        "INSERT INTO schema_migrations VALUES(?,?,?)",
        (1, hashlib.sha256(content).hexdigest(), "2026-07-25T00:00:00.000Z"),
    )
    connection.commit()
    connection.close()
    migrate(database, recovery)
    connection = connect(database)
    try:
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations")] == [
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
        ]
        assert (
            connection.execute("SELECT 1 FROM sqlite_master WHERE name='tool_calls_new'").fetchone()
            is None
        )
    finally:
        connection.close()


def test_opening_existing_project_applies_new_candidate_count_migration(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    created = ProjectService().create(root, "Existing")
    database = root / "project.sqlite3"
    connection = connect(database)
    try:
        connection.executescript(
            """
            DROP TRIGGER candidate_group_count_before_ready;
            DROP TABLE candidate_assessments;
            DROP TABLE candidate_items;
            DROP TABLE candidate_groups;
            CREATE TABLE candidate_groups(
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
              source_asset_id TEXT REFERENCES assets(id) ON DELETE RESTRICT,
              prompt_asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
              provider TEXT NOT NULL,
              requested_count INTEGER NOT NULL CHECK(requested_count BETWEEN 2 AND 8),
              request_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN('created','ready','partial_ready','selected','cancelled')),
              warnings_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL
            );
            CREATE TABLE candidate_items(
              group_id TEXT NOT NULL REFERENCES candidate_groups(id) ON DELETE RESTRICT,
              asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
              ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 8),
              selected INTEGER NOT NULL DEFAULT 0 CHECK(selected IN(0,1)),
              PRIMARY KEY(group_id,asset_id),
              UNIQUE(group_id,ordinal)
            );
            CREATE TABLE candidate_assessments(
              group_id TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              evaluation_status TEXT NOT NULL CHECK(evaluation_status IN('evaluated','not_evaluated','failed')),
              short_evaluation TEXT,
              anomalies_json TEXT NOT NULL DEFAULT '[]',
              provider_request_id TEXT,
              created_at TEXT NOT NULL,
              PRIMARY KEY(group_id,asset_id),
              FOREIGN KEY(group_id,asset_id) REFERENCES candidate_items(group_id,asset_id) ON DELETE RESTRICT
            );
            CREATE TRIGGER candidate_group_not_ready_on_insert
            BEFORE INSERT ON candidate_groups WHEN NEW.status<>'created'
            BEGIN SELECT RAISE(ABORT,'candidate_group_must_start_created'); END;
            CREATE TRIGGER candidate_group_min_two_before_ready
            BEFORE UPDATE OF status ON candidate_groups
            WHEN NEW.status IN('ready','partial_ready','selected')
             AND (SELECT COUNT(*) FROM candidate_items WHERE group_id=NEW.id) NOT BETWEEN 2 AND 8
            BEGIN SELECT RAISE(ABORT,'candidate_count_must_be_2_to_8'); END;
            DELETE FROM schema_migrations WHERE version=11;
            """
        )
    finally:
        connection.close()

    assert ProjectService().open(root).id == created.id
    upgraded = connect(database)
    try:
        assert upgraded.execute(
            "SELECT 1 FROM schema_migrations WHERE version=11"
        ).fetchone()
        assert "BETWEEN 1 AND 8" in upgraded.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='candidate_groups'"
        ).fetchone()[0]
    finally:
        upgraded.close()


def test_b01_03_migrations_never_disable_foreign_keys() -> None:
    directory = (
        Path(__file__).parents[2]
        / "src"
        / "aipic_to_model"
        / "infrastructure"
        / "sqlite"
        / "migrations"
    )
    scripts = "\n".join(
        path.read_text(encoding="utf-8") for path in directory.glob("*.sql")
    ).lower()
    forbidden = "foreign_keys" + "=" + "off"
    assert forbidden not in scripts.replace(" ", "")


def test_b01_03_safe_parent_rebuild_preserves_foreign_key_children(
    tmp_path: Path,
) -> None:
    database, recovery = tmp_path / "project.sqlite3", tmp_path / "recovery"
    migration = (
        Path(__file__).parents[2]
        / "src"
        / "aipic_to_model"
        / "infrastructure"
        / "sqlite"
        / "migrations"
        / "0001_core.sql"
    )
    content = migration.read_bytes()
    connection = sqlite3.connect(database)
    connection.executescript(content.decode("utf-8"))
    connection.execute(
        "INSERT INTO schema_migrations VALUES(?,?,?)",
        (1, hashlib.sha256(content).hexdigest(), "2026-07-25T00:00:00.000Z"),
    )
    connection.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES('p','P','t','t')"
    )
    connection.execute("INSERT INTO event_counters VALUES('p',1)")
    connection.execute(
        "INSERT INTO tool_calls("
        "id,tool_name,tool_version,arguments_json,arguments_hash,"
        "idempotency_key,risk_level,status"
        ") VALUES('call','fixture','1','{}','h','key','read_only','queued')"
    )
    connection.execute(
        "INSERT INTO tool_idempotency VALUES('key','fixture','1','queued','call',NULL,NULL,'t')"
    )
    connection.commit()
    connection.close()
    migrate(database, recovery)
    upgraded = connect(database)
    try:
        assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            upgraded.execute("SELECT owner_tool_call_id FROM tool_idempotency").fetchone()[0]
            == "call"
        )
    finally:
        upgraded.close()


def test_b01_03_known_legacy_v2_checksum_upgrades_without_history_rewrite(
    tmp_path: Path,
) -> None:
    database, recovery = tmp_path / "project.sqlite3", tmp_path / "recovery"
    migrate(database, recovery)
    connection = connect(database)
    connection.execute(
        "UPDATE schema_migrations SET checksum=? WHERE version=2",
        ("1f7cfab56bd3fffa410f81c780520226dd185ab6fd8c17ed1962ca85dd72e7c3",),
    )
    connection.execute("DELETE FROM schema_migrations WHERE version=3")
    connection.execute("DROP TABLE tool_requests")
    connection.close()
    migrate(database, recovery)
    upgraded = connect(database)
    try:
        assert upgraded.execute(
            "SELECT checksum FROM schema_migrations WHERE version=2"
        ).fetchone()[0] == ("1f7cfab56bd3fffa410f81c780520226dd185ab6fd8c17ed1962ca85dd72e7c3")
        assert upgraded.execute("SELECT 1 FROM sqlite_master WHERE name='tool_requests'").fetchone()
    finally:
        upgraded.close()


def test_b01_03_new_database_failure_retains_failed_copy_for_diagnosis(
    tmp_path: Path, monkeypatch
) -> None:
    database, recovery = tmp_path / "project.sqlite3", tmp_path / "recovery"
    real_connect = connection_module.connect

    class FailingConnection:
        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def executescript(self, _script: str):
            self._connection.execute("CREATE TABLE failed_diagnostic(value TEXT)")
            raise sqlite3.OperationalError("injected migration failure")

    monkeypatch.setattr(
        connection_module,
        "connect",
        lambda path, **kwargs: FailingConnection(real_connect(path, **kwargs)),
    )
    with pytest.raises(DomainErrorV1):
        connection_module.migrate(database, recovery)
    assert not database.exists()
    failed = list(recovery.glob("failed-new-database-*.sqlite3"))
    assert len(failed) == 1
    diagnostic = sqlite3.connect(failed[0])
    try:
        assert diagnostic.execute(
            "SELECT 1 FROM sqlite_master WHERE name='failed_diagnostic'"
        ).fetchone()
    finally:
        diagnostic.close()


def test_b01_03_project_create_preserves_migration_recovery_outside_staging(
    tmp_path: Path, monkeypatch
) -> None:
    service = ProjectService()

    def fail_migration(_database: Path, recovery: Path) -> None:
        recovery.mkdir(parents=True, exist_ok=True)
        (recovery / "failed-new-database.sqlite3").write_bytes(b"diagnostic")
        raise DomainErrorV1("MIGRATION_FAILED", "injected")

    monkeypatch.setattr(service._filesystem, "migrate", fail_migration)
    with pytest.raises(DomainErrorV1):
        service.create(tmp_path / "failed-project", "Failure")
    retained = list(tmp_path.glob(".failed-project.*.migration-recovery"))
    assert len(retained) == 1
    assert (retained[0] / "failed-new-database.sqlite3").read_bytes() == b"diagnostic"


def test_b01_03_migration_failure_restores_backup_and_writes_recovery_report(tmp_path: Path):
    root = tmp_path / "project"
    ProjectService().create(root, "Demo")
    db, recovery = root / "project.sqlite3", root / "recovery"
    connection = connect(db)
    connection.execute("UPDATE schema_migrations SET checksum='corrupt' WHERE version=1")
    connection.close()
    with pytest.raises(DomainErrorV1):
        migrate(db, recovery)
    connection = connect(db)
    assert (
        connection.execute("SELECT checksum FROM schema_migrations WHERE version=1").fetchone()[0]
        == "corrupt"
    )
    connection.close()
    reports = list(recovery.glob("migration-failure-*.json"))
    backups = list(recovery.glob("project-before-migration-*.sqlite3"))
    assert reports and backups


def test_b01_03_transaction_retries_busy_begin_and_exposes_retryable_error(monkeypatch):
    class BusyOnce:
        def __init__(self, failures: int):
            self.failures, self.calls, self.committed, self.rolled_back = failures, 0, False, False

        def execute(self, statement: str):
            self.calls += 1
            if statement.startswith("BEGIN") and self.calls <= self.failures:
                raise sqlite3.OperationalError("database is locked")

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

    monkeypatch.setattr(
        "aipic_to_model.infrastructure.sqlite.connection.time.sleep", lambda _: None
    )
    retried = BusyOnce(1)
    with transaction(retried, immediate=True):
        pass
    assert retried.calls == 2 and retried.committed
    exhausted = BusyOnce(3)
    with pytest.raises(DomainErrorV1) as error, transaction(exhausted, immediate=True):
        pass
    assert error.value.code == "DATABASE_BUSY" and error.value.recoverable


def test_b01_03_real_sqlite_writer_lock_returns_retryable_busy(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Busy")
    primary = connect(root / "project.sqlite3")
    primary.execute("BEGIN EXCLUSIVE")
    from aipic_to_model.infrastructure.sqlite import repositories as repositories_module

    original_connect = repositories_module.connect

    def short_timeout(path: Path):
        connection = original_connect(path)
        connection.execute("PRAGMA busy_timeout=1")
        return connection

    monkeypatch.setattr("aipic_to_model.infrastructure.sqlite.repositories.connect", short_timeout)
    try:
        with pytest.raises(DomainErrorV1) as error:
            AssetService().hide(root, project.id, "missing", True, "busy-command")
    finally:
        primary.rollback()
        primary.close()
    assert error.value.code == "DATABASE_BUSY" and error.value.recoverable
