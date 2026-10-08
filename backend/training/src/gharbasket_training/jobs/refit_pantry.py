"""`python -m gharbasket_training.jobs.refit_pantry` (weekly, Sun 03:03 IST on Actions; M10 §3.3
v1, ADR-0025): refit the pantry twin's cohort priors from what the serving twin observes (each
locked box's daily-use share of milk and curd and its tap, read-only role), one candidate per
`data_source` (synthetic and real are never pooled), score it out-of-sample against the rule
table and upload it to the registry as a candidate version
(`s3://gb-mlflow/models/pantry_priors/<version>-<data_source>/priors.json`).

The job does **not** promote and never touches `latest.json` or `intel.pantry_priors`: serving
changes only through `just promote pantry_priors <version>-<data_source>`, which moves the
pointer and pushes that version's priors to the API. `--push` sends the out-of-sample metrics
to `POST /v1/internal/intel/model-metrics`, nothing else.

    uv run --directory backend/training python -m gharbasket_training.jobs.refit_pantry \\
        [--out data/models] [--push] [--version 2026-10-05]

`--out` is a local folder with the registry layout without the `models/` prefix
(`pantry_priors/<version>-<data_source>/priors.json`), as `train_ranker --out` writes.
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.modules.subscriptions import rules
from gharbasket.modules.subscriptions.models import WeeklyCycle
from gharbasket.modules.subscriptions.planning import BASE_FACTOR
from gharbasket.platform.db import create_engine
from gharbasket_training import pantry_priors
from gharbasket_training.jobs.train_ranker import METRICS_SOURCE
from gharbasket_training.lake import Registry, open_registry
from gharbasket_training.settings import Settings

# The job's rows in model_metrics; the eval harness's pantry rows have their own names.
METRIC_MODELS = {"learned_priors": "pantry_priors", "rule_table": "pantry_priors_rule_table"}


async def observations(session: AsyncSession) -> list[pantry_priors.Observation]:
    """Every household's locked boxes as the serving twin sees them: M3's own
    `daily_use_history` (the daily-use share of each twin product and the tap), with the
    household's adult equivalents and the cycle's data source. The step is the product's, the
    one M3 sends the twin with these observations."""
    catalog = await catalog_api.plan_catalog(session, None)
    households = (
        await session.execute(
            select(WeeklyCycle.household_id)
            .where(WeeklyCycle.status == "locked")
            .distinct()
            .order_by(WeeklyCycle.household_id)
        )
    ).scalars()
    out: list[pantry_priors.Observation] = []
    for household_id in list(households):
        profile = await identity_api.household_plan_profile(session, household_id)
        if profile is None:
            continue
        equivalents = rules.adult_equivalents(
            [rules.Member(band) for band in profile.member_age_bands]
        )
        for row in await subscriptions_api.daily_use_history(session, profile, catalog):
            product = catalog.products[row.product_id]
            out.append(
                pantry_priors.Observation(
                    str(household_id),
                    equivalents,
                    product.slug,
                    row.delivery_date.toordinal() // 7,
                    row.share,
                    row.signal,
                    product.step_qty * BASE_FACTOR[product.step_unit],
                    row.data_source,
                )
            )
    return out


def candidates(observed: list[pantry_priors.Observation], version: str) -> list[dict[str, Any]]:
    """One registry document per data source: the priors fitted on all of that source's weeks
    and the out-of-sample metrics (refit on each household's weeks before its last, scored on
    the last, against the rule table as M3 serves it). Versions are `<version>-<data_source>`."""
    by_source: dict[str, list[pantry_priors.Observation]] = defaultdict(list)
    for o in observed:
        by_source[o.data_source].append(o)
    out = []
    for source, mine in sorted(by_source.items()):
        metrics = pantry_priors.evaluate(mine)
        fitted = pantry_priors.fit(mine, f"{version}-{source}")
        out.append(pantry_priors.as_document(fitted, metrics, source))
    return out


def publish(registry: Registry, document: dict[str, Any]) -> str:
    """The candidate's priors and card under `pantry_priors/<version>/`; never the pointer."""
    registry.lake.write_bytes(
        registry.key(pantry_priors.MODEL_NAME, document["version"], "priors.json"),
        (json.dumps(document, indent=2) + "\n").encode(),
    )
    return registry.key(pantry_priors.MODEL_NAME, document["version"])


async def push_metrics(
    http: httpx2.AsyncClient, settings: Settings, document: dict[str, Any]
) -> int:
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the metrics")
    metrics = [
        {
            "model_name": METRIC_MODELS[name],
            "version": document["version"],
            "metric": "mae_pct",
            "value": scores["mae_pct"],
            "split": "holdout",
            "data_source": METRICS_SOURCE.get(document["data_source"], document["data_source"]),
            "details": {
                "households_weeks": scores["households_weeks"],
                "holdout": "each household's last observed week, refit without it",
            },
        }
        for name, scores in document["metrics"].items()
        if scores["mae_pct"] == scores["mae_pct"]  # not NaN
    ]
    if not metrics:
        return 0
    answer = await http.post(
        f"{settings.api_url.rstrip('/')}/v1/internal/intel/model-metrics",
        json={"items": metrics},
        headers={"Authorization": f"Bearer {settings.jobs_token.get_secret_value()}"},
        timeout=settings.http_timeout_s,
    )
    answer.raise_for_status()
    return int(answer.json()["accepted"])


async def run(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    url = settings.database_url_ro or settings.database_url
    if url is None:
        raise RuntimeError("set DATABASE_URL_RO (or DATABASE_URL) to read the database")
    engine = create_engine(url.get_secret_value(), pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            observed = await observations(session)
    finally:
        await engine.dispose()
    if not observed:
        raise RuntimeError("no locked boxes yet (run the simulator or wait for data)")
    documents = candidates(observed, args.version)
    registry = open_registry(settings, args.out)
    summary: dict[str, Any] = {"candidates": {}}
    for document in documents:
        beats, why = pantry_priors.beats_rule_table(document["metrics"])
        summary["candidates"][document["data_source"]] = {
            "model": publish(registry, document),
            "priors": len(document["items"]),
            "skipped": document["skipped"],
            "metrics": document["metrics"],
            "beats_rule_table": beats,
            "why": why,
        }
    if args.push:
        async with httpx2.AsyncClient() as http:
            summary["pushed_metrics"] = sum(
                [await push_metrics(http, settings, d) for d in documents]
            )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refit the pantry twin's cohort priors into candidate registry versions "
        "(promotion is `just promote pantry_priors <version>-<data_source>`)."
    )
    parser.add_argument("--out", type=Path, help="a local registry folder instead of the bucket")
    parser.add_argument("--push", action="store_true", help="send the metrics to the API")
    parser.add_argument("--version", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(Settings(), args)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
