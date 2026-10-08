"""Point-in-time household features (`intel.feature_snapshots`, eval protocol §3): everything
is computed from rows the household had *before* `as_of` (strictly: a row stamped exactly at
`as_of` is not seen), so a model trained on a snapshot never sees its future. The leakage test in
tests/test_snapshots.py holds this to account.

`as_of` is an order cut-off (`latest_cutoff`): 21:00 IST on the evening before a delivery, the
moment M4 locks the boxes (`orders.api.cutoff_for`). The nightly run takes the latest cut-off at
or before the run, so its features describe the household as the planner saw it then.

Features (v0, per household as of a cut-off):
- weeks_observed: locked boxes delivered before the cut-off
- cycles, approved_share, skipped_share: how the household answers its weekly box
- edits_per_box: line edits per generated cycle
- weekly_use_<slug>: mean base units a week of each daily-use item over the locked boxes
- taps_ran_out, taps_wasted: the pantry taps given
"""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product
from gharbasket.modules.identity.models import Household
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions.models import (
    BoxLine,
    EditFeedback,
    PantryFeedback,
    WeeklyCycle,
)
from gharbasket.modules.subscriptions.rules import DAILY_USE_PER_ADULT_WEEK
from gharbasket_training.settings import Settings

BASE = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "piece": 1, "bunch": 1, "pack": 1}
IST = ZoneInfo("Asia/Kolkata")


def latest_cutoff(at: datetime) -> datetime:
    """The latest order cut-off at or before the instant `at` (21:00 IST every evening)."""
    today = at.astimezone(IST).date()
    tonight = orders_api.cutoff_for(today + timedelta(days=1))
    return tonight if tonight <= at else orders_api.cutoff_for(today)


async def build(session: AsyncSession, as_of: datetime) -> dict[uuid.UUID, dict[str, Any]]:
    """Features per household from what existed before `as_of` (households that still exist)."""
    cutoff_date = as_of.astimezone(IST).date()
    cycles = (
        (
            await session.execute(
                select(WeeklyCycle).where(
                    WeeklyCycle.generated_at < as_of,
                    WeeklyCycle.delivery_date < cutoff_date,
                    WeeklyCycle.household_id.in_(select(Household.id)),
                )
            )
        )
        .scalars()
        .all()
    )
    by_household: dict[uuid.UUID, list[WeeklyCycle]] = defaultdict(list)
    for c in cycles:
        by_household[c.household_id].append(c)
    cycle_ids = [c.id for c in cycles]
    locked = {c.id for c in cycles if c.status == "locked"}
    daily_use = {
        p.id: p
        for p in (
            await session.execute(
                select(Product).where(Product.slug.in_(list(DAILY_USE_PER_ADULT_WEEK)))
            )
        ).scalars()
    }
    use: dict[uuid.UUID, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    if cycle_ids:
        lines = (
            await session.execute(
                select(BoxLine, WeeklyCycle.household_id)
                .join(WeeklyCycle, WeeklyCycle.id == BoxLine.cycle_id)
                .where(BoxLine.cycle_id.in_(cycle_ids))
            )
        ).all()
        for line, household_id in lines:
            if line.cycle_id in locked and line.product_id in daily_use:
                slug = daily_use[line.product_id].slug
                use[household_id][slug].append(line.qty * line.step_qty * BASE[line.step_unit])
    edits: dict[uuid.UUID, int] = defaultdict(int)
    taps: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if cycle_ids:
        for _, household_id in (
            await session.execute(
                select(EditFeedback.cycle_id, WeeklyCycle.household_id)
                .join(WeeklyCycle, WeeklyCycle.id == EditFeedback.cycle_id)
                .where(EditFeedback.cycle_id.in_(cycle_ids), EditFeedback.at < as_of)
            )
        ).all():
            edits[household_id] += 1
        for household_id, signal in (
            await session.execute(
                select(PantryFeedback.household_id, PantryFeedback.signal).where(
                    PantryFeedback.cycle_id.in_(cycle_ids), PantryFeedback.at < as_of
                )
            )
        ).all():
            taps[household_id][signal] += 1
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for household_id, mine in by_household.items():
        n = len(mine)
        features: dict[str, Any] = {
            "as_of": as_of.isoformat(),
            "weeks_observed": sum(c.status == "locked" for c in mine),
            "cycles": n,
            "approved_share": round(sum(c.approved_at is not None for c in mine) / n, 3),
            "skipped_share": round(sum(c.status == "skipped" for c in mine) / n, 3),
            "edits_per_box": round(edits[household_id] / n, 3),
            "taps_ran_out": taps[household_id]["ran_out"],
            "taps_wasted": taps[household_id]["wasted"],
        }
        for slug, values in sorted(use[household_id].items()):
            features[f"weekly_use_{slug}"] = round(sum(values) / len(values), 1)
        out[household_id] = features
    return out


async def push(
    http: httpx2.AsyncClient,
    settings: Settings,
    as_of: datetime,
    features: dict[uuid.UUID, dict[str, Any]],
) -> int:
    """Store the features through `POST /v1/internal/intel/feature-snapshots`."""
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the features")
    body = {
        "as_of": as_of.isoformat(),
        "items": [
            {"subject_type": "household", "subject_id": str(hid), "features": f}
            for hid, f in features.items()
        ],
    }
    response = await http.post(
        f"{settings.api_url.rstrip('/')}/v1/internal/intel/feature-snapshots",
        json=body,
        headers={"Authorization": f"Bearer {settings.jobs_token.get_secret_value()}"},
        timeout=settings.http_timeout_s,
    )
    response.raise_for_status()
    return int(response.json()["accepted"])
