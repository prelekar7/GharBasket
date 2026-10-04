"""Fill the local database with synthetic people (one for each role) and societies (ADR-0008).

    just seed

Every row is `data_source='synthetic'`, so it never counts in real metrics. Running it again
changes nothing. It writes through the module services, like the simulator will, so the same
rules hold as for a real sign-in: a new phone becomes a customer, staff roles are audit-logged.

The phones +91 98000 01001 to 01011 are reserved for this seed. Sign in as any of them locally
with `dev_phone` (`POST /v1/auth/exchange`, APP_ENV=dev). The societies are made-up names in real
Kharghar, Kamothe and Panvel sectors, with approximate boundaries. Later module PRs add their rows
here (households, catalog) and use the hub, society and NGO ids below.

Only for the local Docker database: shared databases (staging) are always refused.
"""

import argparse
import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.service import SocietyData, save_society
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


def _square(lat: float, lng: float) -> list[tuple[float, float]]:
    """About 130 m x 150 m around a centre: enough for a demo boundary."""
    d_lat, d_lng = 0.0006, 0.0007
    return [
        (lat - d_lat, lng - d_lng),
        (lat - d_lat, lng + d_lng),
        (lat + d_lat, lng + d_lng),
        (lat + d_lat, lng - d_lng),
    ]


def _wings(*letters: str) -> list[dict[str, str]]:
    return [{"id": letter.lower(), "label": f"{letter} wing"} for letter in letters]


def _society_id(n: int) -> uuid.UUID:
    return uuid.UUID(f"0199a000-0000-7000-8000-00000000b{n:03d}")


SOCIETIES = (
    SocietyData(
        id=SEED_SOCIETY_ID,
        slug="sunrise-towers-kharghar",
        name="Sunrise Towers",
        area="Sector 20, Kharghar",
        boundary=_square(19.0395, 73.0675),
        towers=_wings("A", "B", "C"),
        flats=240,
        unlock_threshold=20,
        status="live",
        hub_id=SEED_HUB_ID,
    ),
    SocietyData(
        id=_society_id(2),
        slug="green-valley-heights-kharghar",
        name="Green Valley Heights",
        area="Sector 35, Kharghar",
        boundary=_square(19.0265, 73.0590),
        towers=_wings("A", "B"),
        flats=160,
        unlock_threshold=25,
        status="unlocking",
        hub_id=SEED_HUB_ID,
    ),
    SocietyData(
        id=_society_id(3),
        slug="shree-ganesh-chs-kharghar",
        name="Shree Ganesh CHS",
        area="Sector 12, Kharghar",
        boundary=_square(19.0430, 73.0720),
        towers=[{"id": "main", "label": "Main building"}],
        flats=48,
        unlock_threshold=15,
        status="unlocking",
        hub_id=SEED_HUB_ID,
    ),
    SocietyData(
        id=_society_id(4),
        slug="panchavati-residency-kamothe",
        name="Panchavati Residency",
        area="Sector 7, Kamothe",
        boundary=_square(19.0190, 73.0960),
        towers=_wings("A", "B", "C", "D"),
        flats=320,
        unlock_threshold=20,
        status="prospect",
        hub_id=SEED_HUB_ID,
    ),
    SocietyData(
        id=_society_id(5),
        slug="krishna-kunj-new-panvel",
        name="Krishna Kunj",
        area="Sector 17, New Panvel",
        boundary=_square(19.0005, 73.1185),
        towers=_wings("A", "B"),
        flats=96,
        unlock_threshold=20,
        status="live",
        hub_id=SEED_HUB_ID,
    ),
    # No boundary drawn yet: addresses are accepted anywhere until a hub manager draws one.
    SocietyData(
        id=_society_id(6),
        slug="sai-darshan-apartments-panvel",
        name="Sai Darshan Apartments",
        area="Old Panvel",
        boundary=None,
        towers=[],
        flats=None,
        unlock_threshold=20,
        status="prospect",
        hub_id=None,
    ),
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


async def seed_societies(session: AsyncSession) -> list[tuple[str, bool]]:
    """Create or refresh the synthetic societies: (slug, created) each. Commits nothing."""
    saved: list[tuple[str, bool]] = []
    for data in SOCIETIES:
        society, created = await save_society(session, replace(data, data_source="synthetic"))
        saved.append((society.slug, created))
    return saved


async def run(database_url: str) -> tuple[list[Seeded], list[tuple[str, bool]]]:
    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            seeded = await seed(session)
            societies = await seed_societies(session)
            await session.commit()
            return seeded, societies
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

    seeded, societies = asyncio.run(run(url))
    for person in seeded:
        state = "new" if person.created else "kept"
        print(f"{person.phone}  {person.name:<16} {', '.join(person.roles):<26} {state}")
    print(f"{len(seeded)} synthetic people; sign in with dev_phone (APP_ENV=dev)")
    for slug, created in societies:
        print(f"{slug:<32} {'new' if created else 'kept'}")
    print(f"{len(societies)} synthetic societies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
