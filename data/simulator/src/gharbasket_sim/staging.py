"""The helpers behind `scripts/staging-demo.sh` (handbook 12, staging demo data).

    uv run python -m gharbasket_sim.staging host                  # the database host to confirm
    uv run python -m gharbasket_sim.staging window --weeks 8      # "<first Monday> <weeks>"
    uv run python -m gharbasket_sim.staging estimate --weeks 8 --adoption 0.015
    uv run python -m gharbasket_sim.staging unlock --adoption 0.015  # 5: a society would go live
    uv run python -m gharbasket_sim.staging counts                # what the database holds

The window is the last `--weeks` full weeks before this IST week, so every simulated delivery is in
the past and the current week stays with staging's own jobs. A week the simulator already replayed
(a locked, skipped or cancelled cycle of a +91 98001 household) is never simulated again: without
`--add-weeks` such a window is refused (exit 3); with it, only the weeks after the latest one are
simulated (exit 4 when there are none). Simulated households count towards their society's unlock
(M9 §3.6), so a run that would take an `unlocking` society to its threshold is refused (exit 5):
it would go live for its real households too. Nothing here writes, and nothing prints the URL.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine
from gharbasket.platform.settings import DatabaseSettings
from gharbasket_sim.households import PHONE_PREFIX, societies

REPO_ROOT = Path(__file__).resolve().parents[4]
IST = ZoneInfo("Asia/Kolkata")
EXIT_ALREADY_SIMULATED = 3
EXIT_NOTHING_TO_ADD = 4
EXIT_WOULD_UNLOCK = 5
# Measured locally through a proxy that adds a 60 ms round trip (Mumbai to Neon in Singapore):
# about 280 statements, 170 new rows and 30 s per household-week with the statement cache on
# (`create_engine(cache_statements=True)`; about 3 s of that is planning). A statement then costs
# about 1.6 round trips: 0.08 s at 50 ms, 0.13 s at 80 ms.
STATEMENTS_PER_HOUSEHOLD_WEEK = 280
SECONDS_PER_STATEMENT = (0.08, 0.13)
PLANNING_SECONDS = 3.0
ROWS_PER_HOUSEHOLD_WEEK = 170  # cycles, box lines, ops, orders, ledger, outbox, inbox: all tables

COUNTS = {
    "households (synthetic)": (
        "SELECT count(*) FROM identity.households WHERE data_source = 'synthetic'"
    ),
    "households (real)": "SELECT count(*) FROM identity.households WHERE data_source = 'real'",
    "weekly cycles (synthetic)": (
        "SELECT count(*) FROM subscriptions.weekly_cycles WHERE data_source = 'synthetic'"
    ),
    "orders (synthetic)": "SELECT count(*) FROM orders.orders WHERE data_source = 'synthetic'",
    "box lines (synthetic)": (
        "SELECT count(*) FROM subscriptions.box_lines l JOIN subscriptions.weekly_cycles c "
        "ON c.id = l.cycle_id WHERE c.data_source = 'synthetic'"
    ),
    "edit feedback (synthetic)": (
        "SELECT count(*) FROM subscriptions.edit_feedback f JOIN subscriptions.weekly_cycles c "
        "ON c.id = f.cycle_id WHERE c.data_source = 'synthetic'"
    ),
    "pantry taps (synthetic)": (
        "SELECT count(*) FROM subscriptions.pantry_feedback f JOIN subscriptions.weekly_cycles c "
        "ON c.id = f.cycle_id WHERE c.data_source = 'synthetic'"
    ),
}
# The weeks the simulator itself replayed: a cycle of one of its households that reached an end.
# Cycles staging's weekly job drafted (awaiting approval) do not count; the simulator takes them
# over if it simulates their week.
REPLAYED = ("locked", "skipped", "cancelled", "payment_failed")
SIMULATED_WEEKS = (
    "SELECT DISTINCT c.week_start FROM subscriptions.weekly_cycles c "
    "JOIN identity.households h ON h.id = c.household_id "
    "JOIN identity.users u ON u.id = h.owner_user_id "
    "WHERE c.data_source = 'synthetic' AND u.phone_e164 LIKE '+9198001%' "  # PHONE_PREFIX
    "AND c.status IN ('locked', 'skipped', 'cancelled', 'payment_failed') "  # REPLAYED
    "ORDER BY c.week_start"
)


class AlreadySimulated(Exception):
    """The simulator replayed weeks in the window already: it would replay them again."""


class NothingToAdd(Exception):
    """`--add-weeks`, but every week up to the last full one already has synthetic cycles."""


@dataclass(frozen=True)
class Window:
    start: date  # a Monday
    weeks: int


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def today_ist() -> date:
    return datetime.now(IST).date()


def plan_window(today: date, weeks: int, simulated: Collection[date], *, add_weeks: bool) -> Window:
    """The weeks to simulate: the last `weeks` full weeks before `today`'s, minus what is there.

    `simulated` are the Mondays the simulator already replayed (`SIMULATED_WEEKS`).
    """
    if weeks < 1:
        raise ValueError("weeks must be at least 1")
    last = monday_of(today) - timedelta(weeks=1)
    first = last - timedelta(weeks=weeks - 1)
    taken = sorted(d for d in simulated if first <= d <= last)
    if not add_weeks:
        if taken:
            raise AlreadySimulated(
                f"{len(taken)} of the weeks {first} to {last} were simulated already "
                f"(latest {taken[-1]}): run again with --add-weeks to add only the weeks after it"
            )
        return Window(first, weeks)
    before = [d for d in simulated if d <= last]
    start = max(first, max(before) + timedelta(weeks=1)) if before else first
    if start > last:
        raise NothingToAdd(f"every week up to {last} was simulated already")
    return Window(start, (last - start).days // 7 + 1)


def planned_phones(adoption: float) -> dict[uuid.UUID, list[str]]:
    """The phones `just sim --adoption` onboards, by society, numbered as households.sample does
    (society by society, one number after another)."""
    out: dict[uuid.UUID, list[str]] = {}
    n = 0
    for society in societies():
        for _ in range(round((society.flats or 0) * adoption)):
            n += 1
            out.setdefault(society.id, []).append(f"{PHONE_PREFIX}{n:05d}")
    return out


def expected_households(adoption: float) -> int:
    """How many households `just sim --adoption` onboards (households.sample)."""
    return sum(len(phones) for phones in planned_phones(adoption).values())


@dataclass(frozen=True)
class Unlock:
    slug: str
    joined: int  # households in the society now
    new: int  # the run's households that are not there yet
    threshold: int

    @property
    def would_go_live(self) -> bool:
        return self.new > 0 and self.joined + self.new >= self.threshold


async def unlock_plan(session: AsyncSession, adoption: float) -> list[Unlock]:
    """Each `unlocking` society the run adds households to, with its count and threshold."""
    planned = planned_phones(adoption)
    if not planned:
        return []
    phones = [phone for ps in planned.values() for phone in ps]
    ids = bindparam("ids", expanding=True)
    societies_rows = await session.execute(
        text(
            "SELECT id, slug, unlock_threshold FROM community.societies "
            "WHERE id IN :ids AND status = 'unlocking' AND live_at IS NULL ORDER BY slug"
        ).bindparams(ids),
        {"ids": list(planned)},
    )
    joined_rows = await session.execute(
        text(
            "SELECT society_id, count(*) FROM identity.households "
            "WHERE society_id IN :ids GROUP BY society_id"
        ).bindparams(ids),
        {"ids": list(planned)},
    )
    joined = {society_id: int(n) for society_id, n in joined_rows}
    onboarded_rows = await session.execute(
        text(
            "SELECT u.phone_e164 FROM identity.users u "
            "JOIN identity.households h ON h.owner_user_id = u.id WHERE u.phone_e164 IN :phones"
        ).bindparams(bindparam("phones", expanding=True)),
        {"phones": phones},
    )
    onboarded = {phone for (phone,) in onboarded_rows}
    return [
        Unlock(
            slug,
            joined.get(society_id, 0),
            sum(phone not in onboarded for phone in planned[society_id]),
            int(threshold),
        )
        for society_id, slug, threshold in societies_rows
    ]


async def _unlocking(url: str, adoption: float) -> list[Unlock]:
    engine = create_engine(url, pooled=False)
    try:
        async with AsyncSession(engine) as session:
            return await unlock_plan(session, adoption)
    finally:
        await engine.dispose()


def unlocking(url: str, adoption: float) -> list[Unlock]:
    return asyncio.run(_unlocking(url, adoption))


def estimate(households: int, weeks: int) -> str:
    hh_weeks = households * weeks
    statements = hh_weeks * STATEMENTS_PER_HOUSEHOLD_WEEK
    low, high = (
        round((statements * per + hh_weeks * PLANNING_SECONDS) / 60)
        for per in SECONDS_PER_STATEMENT
    )
    return (
        f"{households} households x {weeks} weeks = {hh_weeks} household-weeks: about "
        f"{statements} statements and {hh_weeks * ROWS_PER_HOUSEHOLD_WEEK} rows, "
        f"{low} to {high} minutes against Neon"
    )


def database_url() -> str:
    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        raise SystemExit("set DATABASE_URL (see .env.example)")
    return url


async def _read(url: str, sql: Sequence[str]) -> list[list[tuple[object, ...]]]:
    engine = create_engine(url, pooled=False)
    try:
        async with engine.connect() as connection:
            return [[tuple(row) for row in await connection.execute(text(q))] for q in sql]
    finally:
        await engine.dispose()


def simulated_weeks(url: str) -> list[date]:
    (rows,) = asyncio.run(_read(url, [SIMULATED_WEEKS]))
    return [row[0] for row in rows if isinstance(row[0], date)]


def counts(url: str) -> dict[str, int]:
    results = asyncio.run(_read(url, list(COUNTS.values())))
    return {label: int(str(rows[0][0])) for label, rows in zip(COUNTS, results, strict=True)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Helpers for scripts/staging-demo.sh.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("host", help="the host of DATABASE_URL_DIRECT (else DATABASE_URL)")
    window = commands.add_parser("window", help="print '<first Monday> <weeks>' to simulate")
    window.add_argument("--weeks", type=int, default=6)
    window.add_argument("--add-weeks", action="store_true")
    window.add_argument("--today", type=date.fromisoformat, help="default: today in IST")
    sized = commands.add_parser("estimate", help="households, rows and minutes of a run")
    sized.add_argument("--weeks", type=int, default=6)
    sized.add_argument("--adoption", type=float, required=True)
    unlock = commands.add_parser("unlock", help="refuse (exit 5) if a society would go live")
    unlock.add_argument("--adoption", type=float, required=True)
    commands.add_parser("counts", help="synthetic and real rows the demo cares about")
    args = parser.parse_args(argv)
    if getattr(args, "weeks", 1) < 1:
        parser.error("--weeks must be at least 1")

    if args.command == "estimate":
        print(estimate(expected_households(args.adoption), args.weeks))
        return 0
    url = database_url()
    if args.command == "host":
        print(make_url(url).host or "")
        return 0
    if args.command == "counts":
        for label, n in counts(url).items():
            print(f"  {label:<28} {n}")
        return 0
    if args.command == "unlock":
        found = unlocking(url, args.adoption)
        for u in found:
            verdict = "WOULD GO LIVE" if u.would_go_live else "stays unlocking"
            print(f"  {u.slug}: {u.joined} joined + {u.new} new of {u.threshold}: {verdict}")
        if any(u.would_go_live for u in found):
            print(
                "refused: simulated households would unlock a society for its real households "
                "(M9 §3.6); use a lower --adoption",
                file=sys.stderr,
            )
            return EXIT_WOULD_UNLOCK
        return 0
    today = args.today or today_ist()
    try:
        planned = plan_window(today, args.weeks, simulated_weeks(url), add_weeks=args.add_weeks)
    except AlreadySimulated as exc:
        print(exc, file=sys.stderr)
        return EXIT_ALREADY_SIMULATED
    except NothingToAdd as exc:
        print(exc, file=sys.stderr)
        return EXIT_NOTHING_TO_ADD
    print(planned.start.isoformat(), planned.weeks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
