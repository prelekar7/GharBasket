"""`python -m gharbasket_training.jobs.refit_pantry` (weekly, Sun 03:03 IST on Actions; M10 §6):
refit the pantry twin's cohort priors from every household's locked boxes and taps (read-only
role), score them leave-last-week-out against the rule table, write `priors.json` to the
registry (`models/pantry_priors/<version>/` with `latest.json`) and hand the priors and the
metrics to the API (`POST /v1/internal/intel/pantry-priors`, `.../model-metrics`).

    uv run --directory backend/training python -m gharbasket_training.jobs.refit_pantry \\
        --out data/models [--push] [--version 2026-10-05]
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product
from gharbasket.modules.identity.models import Household, HouseholdMember
from gharbasket.modules.subscriptions import rules
from gharbasket.modules.subscriptions.models import BoxLine, PantryFeedback, WeeklyCycle
from gharbasket.modules.subscriptions.planning import BASE_FACTOR
from gharbasket.platform.db import create_engine
from gharbasket_training import pantry_priors
from gharbasket_training.lake import Lake, open_lake
from gharbasket_training.settings import Settings


async def observations(session: AsyncSession) -> tuple[list[pantry_priors.Observation], str]:
    """Every daily-use line of a locked box with its tap, and the household's cohort."""
    bands = (
        await session.execute(select(HouseholdMember.household_id, HouseholdMember.age_band))
    ).all()
    members: dict[Any, list[str]] = {}
    for household_id, band in bands:
        members.setdefault(household_id, []).append(band)
    sources = dict((await session.execute(select(Household.id, Household.data_source))).all())
    slugs = dict((await session.execute(select(Product.id, Product.slug))).all())
    daily = set(rules.DAILY_USE_PER_ADULT_WEEK)
    rows = (
        await session.execute(
            select(
                WeeklyCycle.household_id,
                WeeklyCycle.delivery_date,
                BoxLine.product_id,
                BoxLine.qty,
                BoxLine.step_qty,
                BoxLine.step_unit,
                PantryFeedback.signal,
            )
            .join(WeeklyCycle, WeeklyCycle.id == BoxLine.cycle_id)
            .outerjoin(
                PantryFeedback,
                (PantryFeedback.cycle_id == BoxLine.cycle_id)
                & (PantryFeedback.product_id == BoxLine.product_id),
            )
            .where(WeeklyCycle.status == "locked")
            .order_by(WeeklyCycle.delivery_date)
        )
    ).all()
    out = []
    for household_id, day, product_id, qty, step_qty, step_unit, signal in rows:
        slug = slugs.get(product_id)
        if slug not in daily:
            continue
        equivalents = rules.adult_equivalents(
            [rules.Member(band) for band in members.get(household_id, ["18_59", "18_59"])]
        )
        step = step_qty * BASE_FACTOR[step_unit]
        out.append(
            pantry_priors.Observation(
                str(household_id),
                pantry_priors.cohort_of(equivalents),
                slug,
                day.toordinal() // 7,
                qty * step,
                signal,
                step,
            )
        )
    kinds = {sources.get(h, "synthetic") for h in {o.household for o in out}}
    return out, ("mixed" if len(kinds) > 1 else next(iter(kinds), "synthetic"))


def publish(lake: Lake, document: dict[str, Any]) -> str:
    prefix = f"models/{pantry_priors.MODEL_NAME}/{document['version']}"
    lake.write_bytes(f"{prefix}/priors.json", (json.dumps(document, indent=2) + "\n").encode())
    lake.write_bytes(
        f"models/{pantry_priors.MODEL_NAME}/latest.json",
        (
            json.dumps(
                {"version": document["version"], "promoted_at": datetime.now(UTC).isoformat()}
            )
            + "\n"
        ).encode(),
    )
    return prefix


async def push(http: httpx2.AsyncClient, settings: Settings, document: dict[str, Any]) -> int:
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the priors")
    base = settings.api_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.jobs_token.get_secret_value()}"}
    answer = await http.post(
        f"{base}/v1/internal/intel/pantry-priors",
        json={"version": document["version"], "items": document["items"]},
        headers=headers,
        timeout=settings.http_timeout_s,
    )
    answer.raise_for_status()
    metrics = [
        {
            "model_name": f"pantry_{name}",
            "version": document["version"],
            "metric": "mae_pct",
            "value": scores["mae_pct"],
            "split": "holdout",
            "data_source": document["data_source"],
            "details": {"households_weeks": scores["households_weeks"]},
        }
        for name, scores in document["metrics"].items()
        if scores["mae_pct"] == scores["mae_pct"]  # not NaN
    ]
    if metrics:
        scored = await http.post(
            f"{base}/v1/internal/intel/model-metrics",
            json={"items": metrics},
            headers=headers,
            timeout=settings.http_timeout_s,
        )
        scored.raise_for_status()
    return int(answer.json()["accepted"])


async def run(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    url = settings.database_url_ro or settings.database_url
    if url is None:
        raise RuntimeError("set DATABASE_URL_RO (or DATABASE_URL) to read the database")
    engine = create_engine(url.get_secret_value(), pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            observed, data_source = await observations(session)
    finally:
        await engine.dispose()
    if not observed:
        raise RuntimeError("no locked boxes yet (run the simulator or wait for data)")
    fitted = pantry_priors.fit(observed, args.version)
    metrics = {
        "rule_table": pantry_priors.holdout_mae(observed, None),
        "learned_priors": pantry_priors.holdout_mae(observed, fitted.by_key()),
    }
    document = pantry_priors.as_document(fitted, metrics, data_source)
    lake = open_lake(settings, args.out)
    summary: dict[str, Any] = {
        "model": publish(lake, document),
        "priors": len(fitted.priors),
        "skipped": fitted.skipped,
        "metrics": metrics,
    }
    if args.push:
        async with httpx2.AsyncClient() as http:
            summary["pushed"] = await push(http, settings, document)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refit the pantry twin's cohort priors.")
    parser.add_argument("--out", type=lambda p: __import__("pathlib").Path(p), default=None)
    parser.add_argument("--push", action="store_true", help="store through the API")
    parser.add_argument("--version", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(Settings(), args)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
