"""Catch requests that write to the database and never commit.

In production `get_session` gives each request its own session and rolls back whatever the request
did not commit. Tests hand every request the test's own session instead (joined to an outer
transaction that is rolled back after the test), so an uncommitted write still looks saved to the
next request and to the test's assertions. This guard closes that gap: when a request's handler
finishes without an exception and the session holds a write that no commit followed, the test
fails, naming the request.

How a write is seen:
- every INSERT/UPDATE/DELETE the session's connection sends (ORM flushes, Core statements and
  `text()` alike) that touches a row marks the session as holding uncommitted writes (an
  `ON CONFLICT DO NOTHING` that hit a duplicate, or an UPDATE that matched nothing, wrote nothing);
- the end of the session's outermost transaction (`commit()`, which in the tests' savepoint mode
  releases a savepoint, or `rollback()`) clears the mark;
- a savepoint (`begin_nested()`) that is rolled back restores the mark it started with, because
  its writes are gone; one that is released keeps them, still waiting for the real commit;
- objects added, changed or deleted but not yet flushed when the handler returns count too.

Requests that raise are left alone: the session override rolls them back, as production does.
Writes a test makes through `db_session` between requests are not requests and are not checked.
"""

import re
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from fastapi import Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

_WRITE = re.compile(r"\s*(INSERT|UPDATE|DELETE|MERGE|TRUNCATE)\b", re.IGNORECASE)
_CTE_WRITE = re.compile(r"\s*WITH\b.*\b(INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE | re.DOTALL)


def is_write(statement: str) -> bool:
    return bool(_WRITE.match(statement) or _CTE_WRITE.match(statement))


class CommitGuard:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.uncommitted = False
        self.violations: list[str] = []
        self._at_savepoint: list[bool] = []  # the mark when each open savepoint began
        self._rolled_back = False  # how the transaction that is ending ended

        sync_session = session.sync_session
        event.listen(sync_session, "after_transaction_create", self._transaction_began)
        event.listen(sync_session, "after_commit", self._committed)
        event.listen(sync_session, "after_rollback", self._rolled_back_)
        event.listen(sync_session, "after_transaction_end", self._transaction_ended)
        # The tests' session is bound to one connection (tests/conftest.py `db_session`).
        connection = session.bind
        assert isinstance(connection, AsyncConnection)
        event.listen(connection.sync_connection, "after_cursor_execute", self._statement)

    def _statement(self, _conn: Any, cursor: Any, statement: str, *_args: Any) -> None:
        # rowcount is -1 when the driver cannot tell (executemany): count that as a write.
        if is_write(statement) and cursor.rowcount != 0:
            self.uncommitted = True

    def _transaction_began(self, _session: Session, transaction: SessionTransaction) -> None:
        if transaction.nested:
            self._at_savepoint.append(self.uncommitted)

    def _committed(self, _session: Session) -> None:
        self._rolled_back = False

    def _rolled_back_(self, _session: Session) -> None:
        self._rolled_back = True

    def _transaction_ended(self, _session: Session, transaction: SessionTransaction) -> None:
        if transaction.parent is None:
            self.uncommitted = False  # committed, or rolled back on purpose
            self._at_savepoint.clear()
        elif transaction.nested:
            before = self._at_savepoint.pop() if self._at_savepoint else self.uncommitted
            if self._rolled_back:
                self.uncommitted = before

    def _unflushed(self) -> bool:
        session = self.session
        return bool(
            session.new or session.deleted or any(session.is_modified(obj) for obj in session.dirty)
        )

    def override(self) -> Callable[[Request], AsyncIterator[AsyncSession]]:
        """A replacement for `get_session` that hands out the test's session and checks it."""

        async def session_override(request: Request) -> AsyncIterator[AsyncSession]:
            # What the test set up through the session belongs to the test, not to this request.
            await self.session.flush()
            self.uncommitted = False
            try:
                yield self.session
            except Exception:
                # Like the real dependency: a request that fails leaves nothing behind.
                await self.session.rollback()
                raise
            if self.uncommitted or self._unflushed():
                self.violations.append(
                    f"{request.method} {request.url.path} wrote to the database without "
                    "committing; in production get_session rolls that write back"
                )

        return session_override

    def check(self) -> None:
        if self.violations:
            pytest.fail("\n".join(self.violations), pytrace=False)
