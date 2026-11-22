"""`just prod-load`: production's reviewed first rows (ADR-0036, handbook 12 §12.9 step 14)."""

import asyncio
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import func, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import PriceList, Product, Recipe
from gharbasket.modules.community.models import Society
from gharbasket.modules.hub_ops.models import Hub
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.platform.db import create_engine
from gharbasket.platform.models import AuditEntry
from gharbasket.tools.prod_load import FILES, PROD_FOLDER, Files, load, read_files
from gharbasket.tools.prod_load import main as prod_load

from .conftest import alembic_config, run_on_server

SEED_CATALOG = PROD_FOLDER.parent / "seeds" / "catalog"
# Made-up numbers, in no real use: the repository is public.
ADMIN, MANAGER = "+919800009001", "+919800009002"


def _corners(lat: float, lng: float, d: float) -> list[dict[str, float]]:
    return [
        {"lat": lat - d, "lng": lng - d},
        {"lat": lat - d, "lng": lng + d},
        {"lat": lat + d, "lng": lng + d},
        {"lat": lat + d, "lng": lng - d},
    ]


@pytest.fixture
def filled(tmp_path: Path) -> Path:
    """data/prod/ as the owner leaves it once every TODO is filled (with made-up values)."""
    prices = json.loads((PROD_FOLDER / "prices.json").read_text())
    files: dict[str, Any] = {
        "hub.json": {
            "slug": "test-hub",
            "name": "Test hub",
            "address": "Plot 1, Sector 1, Kharghar",
            "location": {"lat": 19.03, "lng": 73.03},
            "service_area": _corners(19.03, 73.06, 0.05),
        },
        "societies.json": {
            "societies": [
                {
                    "slug": "test-towers",
                    "name": "Test Towers",
                    "area": "Sector 2, Kharghar",
                    "hub": "test-hub",
                    "boundary": _corners(19.04, 73.07, 0.0006),
                    "towers": [{"id": "a", "label": "A wing"}],
                    "flats": 120,
                    "unlock_threshold": 20,
                    "status": "prospect",
                },
                {"slug": "test-heights", "name": "Test Heights", "hub": None},
            ]
        },
        "catalog.json": {"folder": str(SEED_CATALOG), "reviewed": "test"},
        "prices.json": {**prices, "valid_from": "2026-12-01", "reviewed": "test"},
        "staff.json": {
            "staff": [
                {"phone": ADMIN, "role": "admin", "name": "Test Admin"},
                {"phone": MANAGER, "role": "hub_manager", "hub": "test-hub"},
            ]
        },
    }
    for name, body in files.items():
        (tmp_path / name).write_text(json.dumps(body))
    return tmp_path


def _states(report: dict[str, list[tuple[str, str]]]) -> dict[str, set[str]]:
    return {entity: {state for _, state in rows} for entity, rows in report.items()}


def test_the_committed_folder_holds_no_phone_number_and_is_refused_for_its_todos(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in FILES:
        assert not re.search(r"\+?91[ -]?[6-9]\d{9}", (PROD_FOLDER / name).read_text()), name

    assert prod_load([]) == 1  # before it reads DATABASE_URL: nothing connects

    err = capsys.readouterr().err
    for where in (
        "hub.json.address",
        "societies.json.societies[0].boundary",
        "staff.json.staff[0].phone",
    ):
        assert f"{where}: TODO" in err
    count = err.count(": TODO")
    assert f"refused: {count} placeholders (TODO) remain" in err


def test_one_placeholder_left_is_named_and_refused(
    filled: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    staff = json.loads((filled / "staff.json").read_text())
    staff["staff"][1]["phone"] = "TODO: the hub manager's number"
    (filled / "staff.json").write_text(json.dumps(staff))

    assert prod_load(["--folder", str(filled)]) == 1

    err = capsys.readouterr().err
    assert "staff.json.staff[1].phone: TODO: the hub manager's number" in err
    assert "refused: 1 placeholders (TODO) remain" in err


@pytest.mark.parametrize(
    ("file", "change", "says"),
    [
        ("societies.json", lambda f: f["societies"][1].update(hub="other"), "hub is not test-hub"),
        ("staff.json", lambda f: f["staff"][1].pop("hub"), "a hub_manager takes hub"),
        ("staff.json", lambda f: f["staff"][0].update(role="rider"), "role"),
        ("prices.json", lambda f: f["items"][0].update(slug="nothing"), "nothing is not a product"),
    ],
)
def test_files_that_do_not_validate_are_refused(
    filled: Path, capsys: pytest.CaptureFixture[str], file: str, change: Any, says: str
) -> None:
    body = json.loads((filled / file).read_text())
    change(body)
    (filled / file).write_text(json.dumps(body))

    assert prod_load(["--folder", str(filled)]) == 1

    err = capsys.readouterr().err
    assert "refused: the files do not validate" in err and says in err


@pytest.mark.anyio
@pytest.mark.services
async def test_a_load_adds_real_rows_and_a_second_changes_nothing(
    db_session: AsyncSession, filled: Path
) -> None:
    files = read_files(filled)

    first = await load(db_session, files)

    assert set().union(*_states(first).values()) == {"add"}
    assert len(first["products"]) == len(
        json.loads((SEED_CATALOG / "products.json").read_text())["products"]
    )
    hub = await db_session.scalar(select(Hub).where(Hub.slug == "test-hub"))
    assert hub is not None and hub.data_source == "real"
    societies = (await db_session.scalars(select(Society).where(Society.slug.like("test-%")))).all()
    assert {(s.slug, s.data_source, s.hub_id) for s in societies} == {
        ("test-towers", "real", hub.id),
        ("test-heights", "real", None),
    }
    assert set((await db_session.scalars(select(Product.data_source))).all()) == {"real"}
    assert set((await db_session.scalars(select(Recipe.data_source))).all()) == {"real"}
    price_list = await db_session.scalar(select(PriceList).where(PriceList.hub_id == hub.id))
    assert price_list is not None and price_list.data_source == "real"
    users = {u.phone_e164: u for u in (await db_session.scalars(select(User))).all()}
    assert users[ADMIN].data_source == "real" and users[ADMIN].name == "Test Admin"
    roles = (await db_session.scalars(select(UserRole).where(UserRole.role != "customer"))).all()
    assert {(r.user_id, r.role, r.scope_id) for r in roles} == {
        (users[ADMIN].id, "admin", None),
        (users[MANAGER].id, "hub_manager", hub.id),
    }
    audits = await db_session.scalar(select(func.count()).select_from(AuditEntry))

    again = await load(db_session, files)

    assert set().union(*_states(again).values()) == {"kept"}
    assert await db_session.scalar(select(func.count()).select_from(AuditEntry)) == audits


@pytest.mark.anyio
@pytest.mark.services
async def test_rows_edited_in_the_console_are_kept_and_a_revoked_role_is_not_given_back(
    db_session: AsyncSession, filled: Path
) -> None:
    files = read_files(filled)
    await load(db_session, files)
    hub = await db_session.scalar(select(Hub).where(Hub.slug == "test-hub"))
    assert hub is not None
    hub.name = "Edited hub"
    towers = await db_session.scalar(select(Society).where(Society.slug == "test-towers"))
    assert towers is not None
    towers.name = "Edited Towers"
    await db_session.delete(
        await db_session.scalar(select(Society).where(Society.slug == "test-heights"))
    )
    manager = await db_session.scalar(
        select(UserRole).where(UserRole.role == "hub_manager", UserRole.scope_id == hub.id)
    )
    await db_session.delete(manager)  # what the console's revoke does (staff_admin.revoke_grant)
    await db_session.flush()

    report = await load(db_session, files)

    await db_session.refresh(hub)
    await db_session.refresh(towers)
    assert hub.name == "Edited hub" and towers.name == "Edited Towers"
    assert dict(report["societies"]) == {"test-towers": "kept", "test-heights": "add"}
    assert dict(report["staff"]) == {
        "******9001 admin": "kept",
        "******9002 hub_manager": "kept: revoked in the console, not given back",
    }
    assert not await db_session.scalar(select(UserRole.id).where(UserRole.role == "hub_manager"))


@pytest.fixture
def prod_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway database for the command, which commits for real (as test_seed's)."""
    name = f"{make_url(database_url).database}_prod_load"
    url = make_url(database_url).set(database=name).render_as_string(hide_password=False)
    run_on_server(database_url, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    run_on_server(database_url, f"CREATE DATABASE {name}")
    try:
        command.upgrade(alembic_config(url), "head")
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("DATABASE_URL_DIRECT", url)
        yield url
    finally:
        run_on_server(database_url, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _sql(url: str, statement: str) -> Any:
    async def go() -> Any:
        engine = create_engine(url, pooled=False)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(statement))
                return result.scalar() if result.returns_rows else None
        finally:
            await engine.dispose()

    return asyncio.run(go())


@pytest.mark.services
def test_the_command_refuses_any_database_not_marked_prod_then_dry_runs_and_applies_once(
    prod_database: str, filled: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = ["--folder", str(filled)]
    hubs = "SELECT count(*) FROM hub_ops.hubs"

    assert prod_load([*folder, "--apply"]) == 2  # a local database is never marked
    assert (
        "refused: localhost is not marked in platform.environment, not prod"
        in capsys.readouterr().err
    )
    for mark in ("dev", "staging"):
        _sql(prod_database, "DELETE FROM platform.environment")
        _sql(prod_database, f"INSERT INTO platform.environment (name) VALUES ('{mark}')")
        assert prod_load([*folder, "--apply"]) == 2
        assert (
            f"refused: localhost is marked {mark} in platform.environment"
            in capsys.readouterr().err
        )
    assert _sql(prod_database, hubs) == 0

    _sql(prod_database, "UPDATE platform.environment SET name = 'prod'")
    assert prod_load(folder) == 0  # the default is a dry run
    out = capsys.readouterr().out
    assert re.search(r"hub +test-hub +would add", out)
    assert "societies: 2 to add, 0 kept" in out and "staff: 2 to add, 0 kept" in out
    assert "dry run on localhost: nothing was written" in out
    assert _sql(prod_database, hubs) == 0

    assert prod_load([*folder, "--apply"]) == 0
    out = capsys.readouterr().out
    assert "societies: 2 added, 0 kept" in out and "loaded into localhost" in out
    assert _sql(prod_database, hubs) == 1
    assert (
        _sql(prod_database, "SELECT count(*) FROM community.societies WHERE data_source <> 'real'")
        == 0
    )

    assert prod_load([*folder, "--apply"]) == 0
    out = capsys.readouterr().out
    assert " 0 added" in out and not re.search(r" [1-9]\d* added", out)


def test_an_unreachable_database_is_refused_naming_only_its_host(
    filled: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "postgresql://app:secret-pw@127.0.0.1:1/neondb"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL_DIRECT", url)

    assert prod_load(["--folder", str(filled), "--apply"]) == 2

    err = capsys.readouterr().err
    assert "refused: could not read the platform.environment mark of 127.0.0.1" in err
    assert "secret-pw" not in err and "neondb" not in err


def test_files_is_what_read_files_returns(filled: Path) -> None:
    files = read_files(filled)

    assert isinstance(files, Files)
    assert files.hub.service_area is not None
    ring = files.hub.service_area.coordinates[0]
    assert ring[0] == ring[-1] and len(ring) == 5  # closed, as GeoJSON wants it, in [lng, lat]
    assert ring[0] == pytest.approx([73.01, 18.98])  # pyright: ignore[reportUnknownMemberType]
