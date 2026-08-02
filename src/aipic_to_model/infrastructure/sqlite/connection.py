from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ...domain.common import DomainErrorV1, ErrorCode, utc_now
from ..fs.atomic_io import atomic_write_text

# Version 2 was previously emitted with a migration that disabled foreign
# keys while rebuilding ``tool_calls``.  Existing databases keep that recorded
# checksum; the replacement script is used only for databases that have not
# yet applied version 2.  Accepting the known checksum preserves history
# without rewriting an applied migration row.
_COMPATIBLE_MIGRATION_CHECKSUMS: dict[int, frozenset[str]] = {
    2: frozenset({"1f7cfab56bd3fffa410f81c780520226dd185ab6fd8c17ed1962ca85dd72e7c3"})
}


def _project_migrations_are_current(path: Path, migration_files: list[Path]) -> bool:
    if not path.is_file():
        return False
    try:
        connection = connect(path, read_only=True)
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if exists is None:
                return False
            applied = {
                int(row["version"]): str(row["checksum"])
                for row in connection.execute("SELECT version,checksum FROM schema_migrations")
            }
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    for sql_file in migration_files:
        version = int(sql_file.name[:4])
        checksum = hashlib.sha256(sql_file.read_bytes()).hexdigest()
        recorded = applied.get(version)
        if recorded is None or (
            recorded != checksum
            and recorded not in _COMPATIBLE_MIGRATION_CHECKSUMS.get(version, frozenset())
        ):
            return False
    return True


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open SQLite with the minimum safe pragmas for its intended access mode."""
    if read_only:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
    else:
        connection = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if not read_only:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _busy(error: sqlite3.OperationalError) -> bool:
    return "busy" in str(error).lower() or "locked" in str(error).lower()


def _storage_error(error: sqlite3.OperationalError) -> DomainErrorV1 | None:
    message = str(error).lower()
    if "full" in message or "disk i/o" in message or "readonly" in message:
        return DomainErrorV1(
            ErrorCode.LOCAL_STORAGE_UNAVAILABLE,
            "本地数据库存储不可用，操作未完成。",
            True,
            retry_after_seconds=5,
        )
    return None


@contextmanager
def transaction(connection: sqlite3.Connection, immediate: bool = False):
    statement = "BEGIN IMMEDIATE" if immediate else "BEGIN"
    for attempt in range(3):
        try:
            connection.execute(statement)
            break
        except sqlite3.OperationalError as error:
            if not _busy(error):
                mapped = _storage_error(error)
                if mapped is not None:
                    raise mapped from error
                raise
            if attempt == 2:
                raise DomainErrorV1(
                    ErrorCode.DATABASE_BUSY,
                    "项目数据库正忙，请稍后重试。",
                    True,
                    retry_after_seconds=1,
                ) from error
            time.sleep(0.05 * (attempt + 1))
    try:
        yield connection
        connection.commit()
    except sqlite3.OperationalError as error:
        connection.rollback()
        if _busy(error):
            raise DomainErrorV1(
                ErrorCode.DATABASE_BUSY,
                "项目数据库正忙，请稍后重试。",
                True,
                retry_after_seconds=1,
            ) from error
        mapped = _storage_error(error)
        if mapped is not None:
            raise mapped from error
        raise
    except Exception:
        connection.rollback()
        raise


def migrate(path: Path, recovery: Path) -> None:
    migration_files = sorted(
        (Path(__file__).parent / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")
    )
    if _project_migrations_are_current(path, migration_files):
        return
    recovery.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    backup: Path | None = None
    if existed:
        # WAL state is checkpointed before copying so the backup is a standalone,
        # consistent SQLite file rather than a main-db file missing recent pages.
        checkpoint = connect(path)
        try:
            checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            checkpoint.close()
        backup = recovery / f"project-before-migration-{utc_now().replace(':', '-')}.sqlite3"
        shutil.copy2(path, backup)
    connection: sqlite3.Connection | None = None
    try:
        connection = connect(path)
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        for sql_file in migration_files:
            version = int(sql_file.name[:4])
            content = sql_file.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            row = (
                connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version=?", (version,)
                ).fetchone()
                if table
                else None
            )
            if row:
                if row[0] != checksum and row[0] not in _COMPATIBLE_MIGRATION_CHECKSUMS.get(
                    version, frozenset()
                ):
                    raise DomainErrorV1(ErrorCode.MIGRATION_FAILED, "迁移校验和不一致。")
                continue
            connection.executescript(content.decode("utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations VALUES(?,?,?)", (version, checksum, utc_now())
            )
            table = True
    except Exception as error:
        if connection is not None:
            connection.close()
            connection = None
        report: dict[str, object] = {"status": "failed", "error": type(error).__name__}
        if backup is not None:
            shutil.copy2(backup, path)
            for sidecar in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
                sidecar.unlink(missing_ok=True)
            report.update({"recovered": True, "backup": backup.name})
        elif path.exists():
            failed = recovery / (f"failed-new-database-{utc_now().replace(':', '-')}.sqlite3")
            os.replace(path, failed)
            retained_sidecars: list[str] = []
            for suffix in ("-wal", "-shm"):
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists():
                    retained = failed.with_name(failed.name + suffix)
                    os.replace(sidecar, retained)
                    retained_sidecars.append(retained.name)
            report.update(
                {
                    "recovered": False,
                    "failed_database": failed.name,
                    "retained_sidecars": retained_sidecars,
                }
            )
        atomic_write_text(
            recovery / f"migration-failure-{utc_now().replace(':', '-')}.json",
            json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        )
        raise DomainErrorV1(ErrorCode.MIGRATION_FAILED, "数据库迁移失败。", True) from error
    finally:
        if connection is not None:
            connection.close()


def migrate_app(path: Path) -> None:
    """Apply the isolated non-sensitive application database migration once."""
    migration_files = sorted(
        (Path(__file__).parent / "app_migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        for sql_file in migration_files:
            version = int(sql_file.name[:4])
            content = sql_file.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            row = (
                connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version=?", (version,)
                ).fetchone()
                if exists
                else None
            )
            if row is None:
                connection.executescript(content.decode("utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations VALUES(?,?,?)", (version, checksum, utc_now())
                )
                exists = True
            elif row[0] != checksum:
                raise DomainErrorV1(ErrorCode.MIGRATION_FAILED, "应用数据库迁移校验和不一致。")
    finally:
        connection.close()
