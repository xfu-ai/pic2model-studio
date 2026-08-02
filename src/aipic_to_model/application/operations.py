"""Application-level state machine for journaled cross-file operations.

The repository owns SQLite transactions.  This service owns only the ordered
workflow and compensation decision, so application code never receives a DB
connection while every file/database operation has the same durable phases.
"""

from __future__ import annotations

from collections.abc import Callable


class OperationService:
    """Execute the frozen ``prepared → file_written → db_committed → completed`` flow."""

    def execute(
        self,
        *,
        write_and_verify: Callable[[], None],
        mark_file_written: Callable[[], None],
        commit_database: Callable[[], None],
        compensate_file: Callable[[], None],
    ) -> None:
        """Run one operation without exposing persistence mechanics to application code.

        ``commit_database`` is an infrastructure-owned single transaction.  If
        bytes have moved but that commit fails, the caller's deterministic
        compensation is attempted and the original exception is preserved.
        """
        wrote = False
        try:
            write_and_verify()
            wrote = True
            mark_file_written()
            commit_database()
        except Exception:
            if wrote:
                compensate_file()
            raise
