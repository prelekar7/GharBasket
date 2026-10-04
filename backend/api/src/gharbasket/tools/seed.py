"""Fill the local database with synthetic people, one for each role (ADR-0008).

    just seed

Every row is `data_source='synthetic'`, so it never counts in real metrics. Running it again
changes nothing. It writes through the module services, like the simulator will, so the same
rules hold as for a real sign-in: a new phone becomes a customer, staff roles are audit-logged.

The phones +91 98000 01001 to 01011 are reserved for this seed. Sign in as any of them locally
with `dev_phone` (`POST /v1/auth/exchange`, APP_ENV=dev). Later module PRs add their rows here
(societies, households, catalog); the hub, society and NGO ids below are the ones they will use.

Only for the local Docker database: shared databases (staging) are always refused.
"""

import argparse
import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.service import find_or_create_user, grant_role
from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[5]

# Fixed ids, so every machine gets the same world and later seeds can point at these rows.
SEED_HUB_ID = uuid.UUID("0199a000-0000-7000-8000-00000000a001")  # Kharghar hub
SEED_SOCIETY_ID = uuid.UUID("0199a000-0000-7000-8000-00000000b001")  # Sunrise Towers
SEED_NGO_ID = uuid.UUID("0199a000-0000-7000-8000-00000000c001")


@dataclass(frozen=True)
class Person:
    phone: str
    name: str
    locale: str = "en-IN"
    senior_mode: bool = False
    # Customer comes with every sign-up; these are the extra staff roles and their scope.
    roles: tuple[tuple[str, uuid.UUID | None], ...] = field(default=())


PEOPLE = (
    Person("+919800001001", "Priya Sharma"),
    Person("+919800001002", "Sunita Patil", locale="mr-IN"),
    Person("+919800001003", "Ramesh Kulkarni", locale="mr-IN", senior_mode=True),
    Person("+919800001004", "Meena Jadhav", locale="hi-IN", roles=(("captain", SEED_SOCIETY_ID),)),
    Person(
        "+919800001005", "Lata Shinde", locale="mr-IN", roles=(("kitchen_partner", SEED_HUB_ID),)
    ),
    Person("+919800001006", "Arjun Pawar", locale="hi-IN", roles=(("rider", SEED_HUB_ID),)),
    Person("+919800001007", "Kavita More", locale="mr-IN", roles=(("packer", SEED_HUB_ID),)),
    Person("+919800001008", "Vikram Desai", roles=(("hub_manager", SEED_HUB_ID),)),
    Person("+919800001009", "Sanjay Mehta", roles=(("franchise_owner", SEED_HUB_ID),)),
    Person("+919800001010", "Asha Fernandes", roles=(("ngo_coordinator", SEED_NGO_ID),)),
    Person("+919800001011", "Neha Iyer", roles=(("admin", None),)),
)


@dataclass(frozen=True)
class Seeded:
    phone: str
    name: str
    roles: tuple[str, ...]
    created: bool


async def seed(session: AsyncSession) -> list[Seeded]:
    """Create or refresh the synthetic people. Commits nothing; the caller does."""
    seeded: list[Seeded] = []
    for person in PEOPLE:
        user, created = await find_or_create_user(session, person.phone)
        user.name = person.name
        user.locale = person.locale
        user.senior_mode = person.senior_mode
        user.data_source = "synthetic"
        await session.flush()
        for role, scope_id in person.roles:
            try:
                await grant_role(
                    session,
                    phone_e164=person.phone,
                    role=role,
                    scope_id=scope_id,
                    granted_by=None,
                )
            except ProblemError as problem:
                if problem.code != "identity.role_exists":
                    raise
        roles = ("customer", *(role for role, _ in person.roles))
        seeded.append(Seeded(person.phone, person.name, roles, created))
    return seeded


async def run(database_url: str) -> list[Seeded]:
    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            seeded = await seed(session)
            await session.commit()
            return seeded
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill the local database with synthetic data.")
    parser.parse_args(argv)

    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        parser.error("set DATABASE_URL (see .env.example)")
    if not is_local_database(url):
        parser.error("seed data is only for the local Docker database")

    seeded = asyncio.run(run(url))
    for person in seeded:
        state = "new" if person.created else "kept"
        print(f"{person.phone}  {person.name:<16} {', '.join(person.roles):<26} {state}")
    print(f"{len(seeded)} synthetic people; sign in with dev_phone (APP_ENV=dev)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
