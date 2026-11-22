"""The reviewed load of production's first rows (ADR-0036, handbook 12 §12.9 step 14).

    infisical run --env=prod -- just prod-load            # dry run: the report, nothing written
    infisical run --env=prod -- just prod-load --apply    # the same load, committed

Production starts empty. It gets only the real hub, the real societies, the catalog and price
list, the festival calendar and the staff role grants, from the reviewed files in data/prod/
(data/prod/README.md says what each holds and who fills it; staff phones are filled only in a
private copy, passed with `--folder`, as the repository is public). Every row is
`data_source='real'` (where the table has the column) and is written through the module
services, as `just seed` does.

It only adds what is missing. An existing hub, society, category, product, recipe, festival day
or price list (same hub and date) is kept as it is, even when the files now say otherwise, so
nothing edited in the console is overwritten; a second run changes nothing. A staff role the
person holds is kept, and one granted before and revoked since (its `roles.granted` audit entry)
is not given back. A dry run, the default, runs the same load in a transaction it rolls back.

It refuses, exiting 1, while any value in data/prod/ still starts with `TODO`, listing each one
(before it connects), and while a file does not validate. It refuses, exiting 2, unless the
database is marked `prod` in `platform.environment` (`gharbasket.platform.environment`), the
reverse of `live_guard`: real-looking rows never land in staging or a local database by mistake.
Tests mark their throwaway database `prod` to pass. Messages name the host, never the URL.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Category, Festival, PriceList, Recipe
from gharbasket.modules.community.models import SOCIETY_STATUSES, Society
from gharbasket.modules.community.service import SocietyData, save_society
from gharbasket.modules.hub_ops import service as hub_service
from gharbasket.modules.hub_ops.models import Hub
from gharbasket.modules.hub_ops.schemas import GeoPolygon, HubIn
from gharbasket.modules.identity.models import UserRole
from gharbasket.modules.identity.schemas import normalise_phone
from gharbasket.modules.identity.service import find_or_create_user, grant_role
from gharbasket.modules.identity.staff_admin import mask_phone
from gharbasket.modules.registry import load_modules
from gharbasket.platform.db import create_engine, is_pgbouncer
from gharbasket.platform.environment import PRODUCTION, read_environment
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.geo import LatLng
from gharbasket.platform.models import AuditEntry
from gharbasket.platform.permissions import ROLE_SCOPE
from gharbasket.platform.settings import DatabaseSettings
from gharbasket.tools.seed import seed_catalog

REPO_ROOT = Path(__file__).resolve().parents[5]
PROD_FOLDER = REPO_ROOT / "data" / "prod"
FILES = ("hub.json", "societies.json", "catalog.json", "prices.json", "staff.json")
PLACEHOLDER = "TODO"
MARK = "platform.environment"
ADR = "ADR-0036, docs/adr/0036-production-environment-for-the-pilot.md"


class ProdLoadRefused(Exception):
    """The database is not production, or could not be shown to be."""


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tower(_Entry):
    id: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=60)


class SocietyEntry(_Entry):
    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=80)
    name: str = Field(min_length=2, max_length=120)
    area: str | None = Field(default=None, max_length=120)
    hub: str | None = Field(description="hub.json's slug, or null for no hub yet")
    boundary: list[LatLng] | None = Field(default=None, min_length=3)
    towers: list[Tower] = Field(default_factory=list[Tower])
    flats: int | None = Field(default=None, gt=0)
    unlock_threshold: int = Field(default=20, gt=0)
    status: str = "prospect"

    @field_validator("status")
    @classmethod
    def _known(cls, status: str) -> str:
        if status not in SOCIETY_STATUSES:
            raise ValueError(f"status is one of {', '.join(SOCIETY_STATUSES)}")
        return status


class StaffEntry(_Entry):
    phone: str
    # Partners (riders, packers, kitchen partners, captains) come through M8's verification, and
    # an NGO coordinator needs an NGO, which production gets in the console.
    role: Literal["admin", "catalog_editor", "hub_manager", "franchise_owner"]
    hub: str | None = Field(default=None, description="hub.json's slug, for a hub role")
    name: str | None = Field(default=None, max_length=120)

    _phone = field_validator("phone")(normalise_phone)


@dataclass(frozen=True)
class Files:
    hub: HubIn
    societies: list[SocietyEntry]
    catalog: Path  # the folder of categories, products, nutrition, recipes and festivals
    prices: Path
    valid_from: date
    staff: list[StaffEntry]


def _read(folder: Path, name: str) -> Any:
    return json.loads((folder / name).read_text())


def placeholders(folder: Path) -> list[str]:
    """Every value in the folder's files still starting with TODO, as `file: path: value`."""
    found: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str) and value.lstrip().startswith(PLACEHOLDER):
            found.append(f"{path}: {value}")
        elif isinstance(value, dict):
            for key, inner in value.items():  # pyright: ignore[reportUnknownVariableType]
                walk(inner, f"{path}.{key}")
        elif isinstance(value, list):
            for index, inner in enumerate(value):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                walk(inner, f"{path}[{index}]")

    for name in FILES:
        walk(_read(folder, name), name)
    return found


def read_files(folder: Path) -> Files:
    """The folder's files, validated; ValueError names what is wrong."""
    raw = _read(folder, "hub.json")
    corners = [LatLng.model_validate(c) for c in raw.pop("service_area", None) or []]
    ring = [[c.lng, c.lat] for c in [*corners, *corners[:1]]]
    area = GeoPolygon(type="Polygon", coordinates=[ring]) if corners else None
    hub = HubIn.model_validate({**raw, "service_area": area})

    societies = [
        SocietyEntry.model_validate(s) for s in _read(folder, "societies.json")["societies"]
    ]
    staff = [StaffEntry.model_validate(s) for s in _read(folder, "staff.json")["staff"]]
    slugs = [society.slug for society in societies]
    if len(set(slugs)) != len(slugs):
        raise ValueError("societies.json: two societies share a slug")
    for society in societies:
        if society.hub not in (None, hub.slug):
            raise ValueError(f"societies.json: {society.slug}'s hub is not {hub.slug}")
    for person in staff:
        hub_role = ROLE_SCOPE[person.role] == "hub"
        if person.hub != (hub.slug if hub_role else None):
            needs = f"hub {hub.slug!r}" if hub_role else "no hub"
            raise ValueError(f"staff.json: a {person.role} takes {needs}")

    catalog = (folder / _read(folder, "catalog.json")["folder"]).resolve()
    products = {p["slug"] for p in _read(catalog, "products.json")["products"]}
    prices = _read(folder, "prices.json")
    valid_from = date.fromisoformat(prices["valid_from"])
    for item in prices["items"]:
        if item["slug"] not in products:
            raise ValueError(f"prices.json: {item['slug']} is not a product of {catalog}")
        if not isinstance(item["price_paise"], int) or item["price_paise"] <= 0:
            raise ValueError(f"prices.json: {item['slug']} needs a price_paise above 0")
    return Files(hub, societies, catalog, folder / "prices.json", valid_from, staff)


# entity -> [(key, state)]: state is "add", "kept" or "kept: <why>".
Report = dict[str, list[tuple[str, str]]]


async def load_hub(session: AsyncSession, body: HubIn) -> tuple[Hub, str]:
    hub = await session.scalar(select(Hub).where(Hub.slug == body.slug))
    if hub is not None:
        return hub, "kept"
    return await hub_service.create_hub(session, body, data_source="real"), "add"


async def load_societies(
    session: AsyncSession, societies: list[SocietyEntry], hub: Hub
) -> list[tuple[str, str]]:
    done: list[tuple[str, str]] = []
    for entry in societies:
        if await session.scalar(select(Society.id).where(Society.slug == entry.slug)):
            done.append((entry.slug, "kept"))
            continue
        data = SocietyData(
            id=await session.scalar(text("SELECT uuidv7()")),
            slug=entry.slug,
            name=entry.name,
            area=entry.area,
            boundary=None if entry.boundary is None else [(c.lat, c.lng) for c in entry.boundary],
            towers=[tower.model_dump() for tower in entry.towers],
            flats=entry.flats,
            unlock_threshold=entry.unlock_threshold,
            status=entry.status,
            hub_id=hub.id if entry.hub else None,
            data_source="real",
        )
        await save_society(session, data)
        done.append((entry.slug, "add"))
    return done


async def load_staff(
    session: AsyncSession, staff: list[StaffEntry], hub: Hub
) -> list[tuple[str, str]]:
    """Each person's account (made now if they have not signed in yet) and role."""
    done: list[tuple[str, str]] = []
    for person in staff:
        scope_id = hub.id if person.hub else None
        key = f"{mask_phone(person.phone)} {person.role}"
        user, created = await find_or_create_user(session, person.phone)
        if created and person.name:
            user.name = person.name
        scope = UserRole.scope_id.is_(None) if scope_id is None else UserRole.scope_id == scope_id
        held = select(UserRole.id).where(
            UserRole.user_id == user.id, UserRole.role == person.role, UserRole.status == "verified"
        )
        given = AuditEntry.after["scope_id"].astext
        granted_before = select(AuditEntry.id).where(
            AuditEntry.entity_type == "user",
            AuditEntry.entity_id == str(user.id),
            AuditEntry.action == "roles.granted",
            AuditEntry.after["role"].astext == person.role,
            given.is_(None) if scope_id is None else given == str(scope_id),
        )
        if await session.scalar(held.where(scope)):
            done.append((key, "kept"))
        elif await session.scalar(granted_before.limit(1)):
            done.append((key, "kept: revoked in the console, not given back"))
        else:
            await grant_role(
                session,
                phone_e164=person.phone,
                role=person.role,
                scope_id=scope_id,
                granted_by=None,
            )
            done.append((key, "add"))
    return done


async def _keys(session: AsyncSession, *columns: Any) -> set[Any]:
    rows = await session.execute(select(*columns))
    return {row[0] if len(columns) == 1 else tuple(row) for row in rows}


async def load(session: AsyncSession, files: Files) -> Report:
    """The whole load in the caller's transaction; commits nothing."""
    hub, hub_state = await load_hub(session, files.hub)
    report: Report = {"hub": [(hub.slug, hub_state)]}
    report["societies"] = await load_societies(session, files.societies, hub)

    catalog = {
        "categories": ([c["slug"] for c in _read(files.catalog, "categories.json")["categories"]],
                       (Category.slug,)),
        "recipes": ([r["slug"] for r in _read(files.catalog, "recipes.json")["recipes"]],
                    (Recipe.slug,)),
        "festivals": ([(date.fromisoformat(f["date"]), f["code"], f["region"])
                       for f in _read(files.catalog, "festivals.json")["festivals"]],
                      (Festival.day, Festival.code, Festival.region)),
    }  # fmt: skip
    before = {entity: await _keys(session, *columns) for entity, (_, columns) in catalog.items()}
    list_exists = select(PriceList.id).where(
        PriceList.hub_id == hub.id, PriceList.valid_from == files.valid_from
    )
    price_list = "kept" if await session.scalar(list_exists) else "add"
    products = await seed_catalog(
        session,
        keep_existing=True,
        folder=files.catalog,
        prices_file=files.prices,
        hub_id=hub.id,
        data_source="real",
    )
    for entity, (keys, _) in catalog.items():
        report[entity] = [(str(k), "kept" if k in before[entity] else "add") for k in keys]
    report["products"] = [(slug, "add" if created else "kept") for slug, created in products]
    report["price list"] = [(f"{hub.slug} from {files.valid_from}", price_list)]
    report["staff"] = await load_staff(session, files.staff, hub)
    return report


async def run(database_url: str, files: Files, *, apply: bool) -> Report:
    """Check the database is production, load, and commit only with `apply`."""
    load_modules()
    host = make_url(database_url).host or "the database"
    engine = create_engine(
        database_url, pooled=False, cache_statements=not is_pgbouncer(database_url)
    )
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                mark = await read_environment(await session.connection())
            except (OSError, SQLAlchemyError) as exc:
                raise ProdLoadRefused(
                    f"refused: could not read the {MARK} mark of {host} ({type(exc).__name__}), "
                    f"so it cannot be shown to be production and nothing is loaded ({ADR})"
                ) from None
            if mark != PRODUCTION:
                said = f"marked {mark}" if mark else "not marked"
                raise ProdLoadRefused(
                    f"refused: {host} is {said} in {MARK}, not {PRODUCTION}; the production load "
                    f"writes real rows only into production ({ADR})"
                )
            report = await load(session, files)
            await (session.commit() if apply else session.rollback())
            return report
    finally:
        await engine.dispose()


SHOWN = ("hub", "societies", "price list", "staff")  # listed row by row; the rest as counts


def print_report(report: Report, *, apply: bool) -> None:
    for entity, rows in report.items():
        if entity in SHOWN:
            for key, state in rows:
                print(f"{entity:<11} {key:<40} {state if apply or state != 'add' else 'would add'}")
        added = sum(state == "add" for _, state in rows)
        print(f"{entity}: {added} {'added' if apply else 'to add'}, {len(rows) - added} kept")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load production's reviewed first rows.")
    parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
    parser.add_argument("--folder", type=Path, default=PROD_FOLDER, help="default: data/prod")
    args = parser.parse_args(argv)

    if found := placeholders(args.folder):
        for value in found:
            print(value, file=sys.stderr)
        print(
            f"refused: {len(found)} placeholders (TODO) remain in {args.folder}; fill them "
            "(data/prod/README.md) and run again",
            file=sys.stderr,
        )
        return 1
    try:
        files = read_files(args.folder)
    except (ValueError, KeyError) as invalid:
        print(f"refused: the files do not validate: {invalid}", file=sys.stderr)
        return 1
    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        parser.error("set DATABASE_URL (see .env.example)")
    host = make_url(url).host
    try:
        report = asyncio.run(run(url, files, apply=args.apply))
    except ProdLoadRefused as refused:
        print(refused, file=sys.stderr)
        return 2
    except ProblemError as problem:
        print(f"refused: {problem.detail}", file=sys.stderr)
        return 1
    print_report(report, apply=args.apply)
    if args.apply:
        print(f"loaded into {host}")
    else:
        print(f"dry run on {host}: nothing was written; run again with --apply to load")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
