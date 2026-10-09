"""Simulator v1b: the weeks go through the modules (`--write-db`), so the database holds what a
real pilot would: subscriptions, cycles with the planner's meal plan and lines (M3), the
household's edits as line ops with `edit_feedback`, approvals that become orders (M4), the
cut-off that debits the wallet (M5) or fails and cancels, deliveries through the order state
machine, and the pantry taps as `pantry_feedback`.

The household's behaviour stays the simulator's (weeks.py, pantry.py); the plan and the drafted
lines come from M3 (`generate_cycle`), so the edits are against the box the app would show.
The modules read their clock from `orders.service.now` and `subscriptions.service.now`; the
driver sets them to the simulated instant (T-3 18:00 IST to generate, T-2 for edits, the
cycle's cut-off at T-1 21:00 IST). Each week is generated only after the week before has been
replayed, so M3's carry-over counts what was delivered; the outbox is relayed after the cut-off,
so the cycle mirrors its order through M3's own consumers.
"""

import random
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
from gharbasket_ml.main import create_app as create_ml_app
from gharbasket_ml.settings import Settings as MlSettings
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import wallet
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import jobs as orders_jobs
from gharbasket.modules.orders import service as orders_service
from gharbasket.modules.subscriptions import jobs as _subs_jobs  # noqa: F401 (M3's consumers)
from gharbasket.modules.subscriptions import service as subs_service
from gharbasket.modules.subscriptions.models import BoxLine, WeeklyCycle
from gharbasket.platform import outbox
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings
from gharbasket_sim.households import SimHousehold
from gharbasket_sim.pantry import Line
from gharbasket_sim.weeks import Planned, Rows

IST = ZoneInfo("Asia/Kolkata")
ML_TOKEN = "simulator-ml-token"  # noqa: S105 (in-process only)
MAX_OPS = 20
UNFUNDED = 0.05  # chance a household's wallet is left short at the cut-off this week
FUNDING_BUFFER_PAISE = 20_000


@dataclass
class Outcome:
    """What the database holds for one household-week."""

    cycle_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    status: str = "none"  # the cycle's status at the end of the week
    order_status: str | None = None
    ops: int = 0
    refused: list[str] = field(default_factory=list)  # problem codes met on the way
    feedback: int = 0


@contextmanager
def simulated_time(at: datetime) -> Iterator[None]:
    """The modules' clocks read `at` (UTC) until the block ends."""
    before = orders_service.now, subs_service.now
    orders_service.now = lambda: at  # type: ignore[assignment]
    subs_service.now = lambda: at  # type: ignore[assignment]
    try:
        yield
    finally:
        orders_service.now, subs_service.now = before


def app_settings(planner: str) -> Settings:
    """The API's settings for the simulator: the ML service in process for `cpsat`, the rules
    fallback for `rules`."""
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="dev",
        ml_service_url="http://ml" if planner == "cpsat" else None,
        ml_service_token=SecretStr(ML_TOKEN),
        ml_timeout_s=10.0,
    )


@asynccontextmanager
async def ml_client():
    app = create_ml_app(MlSettings(app_env="test", ml_service_token=SecretStr(ML_TOKEN)))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://ml") as client:
        yield client


def ist(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=IST).astimezone(UTC)


def planned_from_cycle(cycle: WeeklyCycle) -> Planned:
    meals: dict[tuple[int, str], str | None] = {}
    for d, day in enumerate(cycle.meal_plan):
        for meal in day["meals"]:
            meals[(d, meal["slot"])] = meal.get("recipe_slug")
    status = "optimal" if cycle.plan_source == "ml" else "fallback"
    return Planned(status, (), meals, cycle.plan_source)


def lines_from_cycle(rows: list[BoxLine], slugs: dict[uuid.UUID, str]) -> list[Line]:
    return [
        Line(
            slugs[row.product_id],
            row.qty,
            float(row.step_qty) * {"kg": 1000, "l": 1000}.get(row.step_unit, 1),
            row.unit_price_paise,
            row.reason_code,
            row.source,
            slugs.get(row.replaces_product_id) if row.replaces_product_id else None,
        )
        for row in rows
        if row.product_id in slugs
    ]


def ops_between(
    before: list[Line], after: list[Line], ids: dict[str, uuid.UUID]
) -> list[subs_service.Op]:
    """The line ops that turn M3's draft into the household's box (M3 §3.1)."""
    was = {line.product: line for line in before}
    now = {line.product: line for line in after}
    ops: list[subs_service.Op] = []
    swapped: set[str] = set()
    for slug, line in sorted(now.items()):
        if line.replaces and line.replaces in was and slug not in was:
            ops.append(subs_service.Op("substitute", ids[line.replaces], substitute_id=ids[slug]))
            swapped.add(line.replaces)
    for slug, line in sorted(was.items()):
        if slug in swapped:
            continue
        if slug not in now:
            ops.append(subs_service.Op("remove", ids[slug]))
        elif now[slug].qty != line.qty:
            ops.append(subs_service.Op("qty", ids[slug], qty=min(now[slug].qty, MAX_OPS)))
    for slug, line in sorted(now.items()):
        if slug not in was and not (line.replaces and line.replaces in swapped):
            ops.append(subs_service.Op("add", ids[slug], qty=min(line.qty, MAX_OPS)))
    return ops


async def subscribe_all(
    session: AsyncSession,
    households: list[SimHousehold],
    created: dict[str, tuple[uuid.UUID, bool]],
) -> None:
    """Every simulated household subscribes (once) and sets its Budget Lock if it has one."""
    for h in households:
        household_id = created[h.key][0]
        if await subs_service.subscription_of(session, household_id) is None:
            await subs_service.subscribe(
                session, household_id, plan_code="autopilot", payment_mode="wallet"
            )
    await session.flush()


async def generate_week(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    *,
    household_id: uuid.UUID,
    delivery: date,
    plan_catalog: catalog_api.PlanCatalog,
    slugs: dict[uuid.UUID, str],
) -> tuple[Planned, list[Line], WeeklyCycle | None]:
    """M3 drafts the week's cycle at T-3 18:00 IST, as `generate_weekly_plans` would, after the
    weeks before it have been replayed (so its carry-over sees what they delivered). The plan
    and lines feed the household's behaviour."""
    with simulated_time(ist(delivery - timedelta(days=3), 18, 0)):
        generated = await subs_service.generate_cycle(
            session,
            http,
            settings,
            household_id=household_id,
            delivery_date=delivery,
            catalog=plan_catalog,
        )
    await session.flush()
    if generated is None:  # paused, no delivery day: the family cooks on its own
        return Planned("none", (), {}, "none"), [], None
    cycle = generated.cycle
    return planned_from_cycle(cycle), lines_from_cycle(generated.lines, slugs), cycle


async def relay_events(session: AsyncSession) -> None:
    """Deliver the outbox as the relay does after each commit in production: M3's consumers
    mirror `orders.locked` and `orders.cancelled` on the cycle (subscriptions/jobs.py)."""
    while (await outbox.relay(session)).sent:
        pass


async def replay_week(
    session: AsyncSession,
    settings: Settings,
    rng: random.Random,
    h: SimHousehold,
    *,
    user_id: uuid.UUID,
    cycle: WeeklyCycle | None,
    delivery: date,
    week: int,
    rows: Rows,
    ids: dict[str, uuid.UUID],
    slugs: dict[uuid.UUID, str],
) -> Outcome:
    """Send one simulated week through M3, M4 and M5."""
    out = Outcome()
    if cycle is None:
        return out
    out.cycle_id = cycle.id
    box = next(b for b in rows.boxes if b["household"] == h.key and b["week"] == week)
    final = [
        Line(
            r["product"],
            r["qty"],
            r["step"],
            r["unit_price_paise"],
            r["reason"],
            r["source"],
            r["replaces"],
        )
        for r in rows.box_lines
        if r["household"] == h.key and r["week"] == week
    ]
    cutoff = cycle.cutoff_at  # T-1 21:00 IST (orders.api.cutoff_for)
    with simulated_time(cutoff - timedelta(hours=35)):  # T-2 10:00 IST
        drafted = lines_from_cycle(await subs_service.lines_of(session, cycle.id), slugs)
        ops = ops_between(drafted, final, ids)
        for start in range(0, len(ops), MAX_OPS):
            chunk = ops[start : start + MAX_OPS]
            try:
                async with session.begin_nested():
                    await subs_service.edit_lines(
                        session, cycle, chunk, expected_version=cycle.version
                    )
                out.ops += len(chunk)
            except ProblemError as exc:
                out.refused.append(exc.code)
                break
        try:
            async with session.begin_nested():
                if box["ordered"]:
                    await subs_service.approve(session, cycle, expected_version=cycle.version)
                else:
                    await subs_service.skip(session, cycle)
        except ProblemError as exc:
            out.refused.append(exc.code)
    await session.flush()
    out.status = cycle.status
    if cycle.order_id is None:
        return out
    out.order_id = cycle.order_id

    # The cut-off: the wallet is topped up to cover the box, unless this is an unfunded week.
    order = await orders_api.get(session, cycle.order_id, for_update=True)
    if rng.random() >= UNFUNDED and order.status == "approved":
        balance = await wallet.balance(session, user_id)
        short = order.total_paise + FUNDING_BUFFER_PAISE - balance
        room = wallet.CAP_PAISE - balance
        if short > 0 and room > 0:
            try:
                async with session.begin_nested():
                    await wallet.credit_topup(
                        session,
                        user_id,
                        min(short, room),
                        payment_ref=f"sim:{h.key}:{delivery.isoformat()}",
                        topup_id=uuid.uuid5(uuid.NAMESPACE_URL, f"sim:{h.key}:{delivery}"),
                    )
            except ProblemError as exc:  # the monthly cap: the household pays what it can
                out.refused.append(exc.code)
    with simulated_time(cutoff):
        if order.status == "approved":
            grace_until = (cutoff + orders_jobs.GRACE).isoformat()
            await orders_jobs.lock_or_fail(session, order, grace_until=grace_until)
    with simulated_time(cutoff + orders_jobs.GRACE):
        if order.status == "payment_failed":
            await orders_service.cancel(session, order, actor="system", reason="payment")
    await relay_events(session)  # the cycle follows its order: locked, or cancelled
    if order.status == "locked":
        for status in ("packed", "dispatched", "at_society", "delivered"):
            await orders_api.transition(session, order.id, status, actor="hub")
        taps = {
            ids[r["product"]]: r["signal"]
            for r in rows.pantry
            if r["household"] == h.key and r["week"] == week and r["signal"] and r["product"] in ids
        }
        on_box = {line.product_id for line in await subs_service.lines_of(session, cycle.id)}
        taps = {pid: signal for pid, signal in taps.items() if pid in on_box}
        if taps:
            out.feedback = await subs_service.record_feedback(
                session, cycle, taps, today=delivery + timedelta(days=1)
            )
    await session.flush()
    out.status = cycle.status
    out.order_status = (await orders_api.get(session, order.id)).status
    return out


async def delivery_dates(
    session: AsyncSession, society_id: uuid.UUID, first_monday: date, weeks: int
) -> list[date]:
    """The society's first delivery day in each simulated week (the seed uses Tuesdays)."""
    days = await subs_service.delivery_days(session, society_id)
    out: list[date] = []
    for w in range(weeks):
        monday = first_monday + timedelta(weeks=w)
        day = subs_service.next_delivery(days, monday - timedelta(days=1))
        if day is None:
            raise LookupError(f"society {society_id} has no delivery day: run `just seed`")
        ahead = (day.weekday - monday.weekday()) % 7
        out.append(monday + timedelta(days=ahead))
    return out


def outcome_row(h: SimHousehold, week: int, outcome: Outcome) -> dict[str, Any]:
    return {
        "household": h.key,
        "week": week,
        "cycle_id": str(outcome.cycle_id) if outcome.cycle_id else None,
        "order_id": str(outcome.order_id) if outcome.order_id else None,
        "cycle_status": outcome.status,
        "order_status": outcome.order_status,
        "ops": outcome.ops,
        "refused": sorted(set(outcome.refused)),
        "feedback": outcome.feedback,
    }
