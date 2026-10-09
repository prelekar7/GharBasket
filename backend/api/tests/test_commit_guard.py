"""The test guard that fails a request which writes and never commits (tests/commit_guard.py)."""

from collections.abc import AsyncIterator

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import DbSession, get_session
from gharbasket.platform.models import IdempotencyKey

from .commit_guard import CommitGuard, is_write

pytestmark = [pytest.mark.anyio, pytest.mark.services]


def test_writes_are_told_from_reads() -> None:
    assert is_write("INSERT INTO t VALUES (1)")
    assert is_write("  update t SET x = 1")
    assert is_write("WITH gone AS (DELETE FROM t RETURNING x) SELECT count(*) FROM gone")
    assert not is_write("SELECT x FROM t FOR UPDATE")
    assert not is_write("SAVEPOINT sa_savepoint_1")
    assert not is_write("WITH a AS (SELECT 1) SELECT * FROM a")


def _app(guard: CommitGuard) -> FastAPI:
    app = FastAPI()

    async def insert(session: AsyncSession, key: str) -> None:
        await session.execute(
            text(
                "INSERT INTO platform.idempotency_keys (scope, key, request_hash) "
                "VALUES ('guard', :key, 'h') ON CONFLICT DO NOTHING"
            ),
            {"key": key},
        )

    @app.post("/forgets")
    async def forgets(session: DbSession) -> None:
        await insert(session, "forgets")

    @app.post("/commits")
    async def commits(session: DbSession) -> None:
        await insert(session, "commits")
        await session.commit()

    @app.post("/writes-after-commit")
    async def writes_after_commit(session: DbSession) -> None:
        await session.commit()
        await insert(session, "late")

    @app.post("/fails")
    async def fails(session: DbSession) -> None:
        await insert(session, "fails")
        raise RuntimeError("boom")

    @app.post("/duplicate")
    async def duplicate(session: DbSession) -> None:
        await insert(session, "commits")  # already there: nothing is written

    @app.post("/savepoint-undone")
    async def savepoint_undone(session: DbSession) -> None:
        try:
            async with session.begin_nested():
                await insert(session, "undone")
                raise ValueError
        except ValueError:
            pass

    @app.post("/savepoint-kept")
    async def savepoint_kept(session: DbSession) -> None:
        async with session.begin_nested():
            await insert(session, "kept")

    @app.post("/unflushed")
    async def unflushed(session: DbSession) -> None:
        session.add(IdempotencyKey(scope="guard", key="unflushed", request_hash="h"))

    app.dependency_overrides[get_session] = guard.override()
    return app


@pytest.fixture
async def guarded(
    db_session: AsyncSession,
) -> AsyncIterator[tuple[CommitGuard, httpx2.AsyncClient]]:
    guard = CommitGuard(db_session)
    transport = httpx2.ASGITransport(app=_app(guard), raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://t") as client:
        yield guard, client


async def test_only_writes_left_uncommitted_by_a_successful_request_are_reported(
    guarded: tuple[CommitGuard, httpx2.AsyncClient], db_session: AsyncSession
) -> None:
    guard, client = guarded
    for path in (
        "/commits",
        "/fails",
        "/duplicate",
        "/savepoint-undone",
        "/forgets",
        "/savepoint-kept",
        "/writes-after-commit",
        "/unflushed",
    ):
        await client.post(path)
        # A test's own writes between requests are not checked.
        await db_session.execute(text("UPDATE platform.idempotency_keys SET status_code = 200"))

    assert guard.violations == [
        f"POST {path} wrote to the database without committing; in production get_session "
        "rolls that write back"
        for path in ("/forgets", "/savepoint-kept", "/writes-after-commit", "/unflushed")
    ]
    with pytest.raises(pytest.fail.Exception, match="POST /forgets"):
        guard.check()
