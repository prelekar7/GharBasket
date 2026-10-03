"""Give a signed-up user a staff role from the command line, e.g. the very first admin.

    just grant-role +919800000001 admin
    just grant-role +919800000002 hub_manager --scope-id <hub uuid>
    infisical run --env=staging -- just grant-role +919800000001 admin --shared

The person must have signed in once (that creates their account). On a shared database
(staging) `--shared` is required, so nobody changes the team's data by accident.
"""

import argparse
import asyncio
import uuid
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.schemas import RoleGrantIn
from gharbasket.modules.identity.service import grant_role
from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[5]


async def run(phone: str, role: str, scope_id: uuid.UUID | None, database_url: str) -> str:
    request = RoleGrantIn(phone_e164=phone, role=role, scope_id=scope_id)  # pyright: ignore[reportArgumentType]
    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            grant = await grant_role(
                session,
                phone_e164=request.phone_e164,
                role=request.role,
                scope_id=request.scope_id,
                granted_by=None,
            )
            await session.commit()
            return f"granted {grant.role} to user {grant.user_id}"
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Give a signed-up user a staff role.")
    parser.add_argument("phone")
    parser.add_argument("role")
    parser.add_argument("--scope-id", type=uuid.UUID, default=None)
    parser.add_argument("--shared", action="store_true", help="allow a non-local database")
    args = parser.parse_args(argv)

    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        parser.error("set DATABASE_URL (see .env.example)")
    if not is_local_database(url) and not args.shared:
        parser.error("this is a shared database; add --shared if you really mean it")
    try:
        print(asyncio.run(run(args.phone, args.role, args.scope_id, url)))
    except ProblemError as problem:
        print(f"refused: {problem.detail}")
        return 1
    except ValueError as invalid:
        print(f"refused: {invalid}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
