"""`python -m gharbasket_training.jobs.train_ranker` (weekly, Sun 03:00 IST on Actions; ADR-0025):
train the next-basket ranker on every household's approved and locked boxes and their edits
(read-only role), one model per `data_source` (synthetic and pilot are never pooled), evaluate
each leave-last-basket-out against the baselines, write the models and their cards to the
registry (`s3://gb-mlflow/models/basket_ranker/<version>-<data_source>/`) and the metrics to the
API.

The job does **not** promote: the Space serves what `basket_ranker/latest.json` points at, and
moving that pointer is a manual decision after reading the card (ADR-0025). Until `just promote`
exists, `--promote <data_source>` points `latest.json` at the version this run trained for that
source; it is off by default.

    uv run --directory backend/training python -m gharbasket_training.jobs.train_ranker \\
        [--out data/models] [--push] [--version 2026-10-05] [--promote synthetic]

`--out` is a local folder with the layout the Space's `MODELS_DIR` expects
(`basket_ranker/latest.json`, `basket_ranker/<version>/model.txt`).
"""

import argparse
import asyncio
import json
import sys
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine
from gharbasket_training import ranker, snapshots
from gharbasket_training.lake import Registry, open_registry
from gharbasket_training.settings import Settings

INPUTS = ("box_lines", "edit_feedback", "weekly_cycles", "prices")
# Cycles say `real` or `synthetic`; model_metrics says `live` for real households' data.
METRICS_SOURCE = {"real": "live"}


@dataclass(frozen=True)
class Trained:
    data_source: str
    version: str
    model: Any
    card: dict[str, Any]


async def rows(session: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    """The snapshot tables the ranker reads, as rows (everything created before now)."""
    by_name = {t.name: t for t in snapshots.TABLES}
    until = datetime.now(UTC)
    out: dict[str, list[dict[str, Any]]] = {}
    for name in INPUTS:
        table = by_name[name]
        records = (await session.execute(table.query(until))).all()
        out[name] = [dict(zip(table.column_names, r, strict=True)) for r in records]
    return out


def fit(tables: dict[str, list[dict[str, Any]]], version: str) -> list[Trained]:
    """One model per data source found in the rows; a source without enough baskets is left
    out. Versions are `<version>-<data_source>`."""
    baskets = ranker.baskets_from_rows(
        tables["box_lines"], tables["edit_feedback"], tables["weekly_cycles"]
    )
    book = ranker.price_book(tables["prices"])
    by_source: dict[str, list[ranker.Basket]] = defaultdict(list)
    for basket in baskets:
        by_source[basket.data_source].append(basket)
    out = []
    for source, mine in sorted(by_source.items()):
        train, test = ranker.build(mine, holdout_last=True, prices=book)
        if not train.groups:
            continue
        model = ranker.train(train)
        metrics: dict[str, dict[str, float]] = {}
        if test.groups:
            metrics["lambdarank"] = ranker.evaluate(test, ranker.predict(model, test))
            for kind in ("repeat_last_week", "top_frequency"):
                metrics[kind] = ranker.evaluate(test, ranker.baseline_scores(test, kind))
        tagged = f"{version}-{source}"
        out.append(
            Trained(
                source,
                tagged,
                model,
                ranker.card(model, metrics, tagged, len(train.rows), source),
            )
        )
    if not out:
        raise RuntimeError("no baskets to train on yet (run the simulator or wait for data)")
    return out


def publish(registry: Registry, trained: Trained) -> str:
    """The model and its card under `<name>/<version>/`; never the `latest.json` pointer."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.txt"
        trained.model.save_model(str(path))
        registry.lake.write_bytes(
            registry.key(ranker.MODEL_NAME, trained.version, "model.txt"), path.read_bytes()
        )
    registry.lake.write_bytes(
        registry.key(ranker.MODEL_NAME, trained.version, "card.json"),
        (json.dumps(trained.card, indent=2) + "\n").encode(),
    )
    return registry.key(ranker.MODEL_NAME, trained.version)


def promote(registry: Registry, version: str) -> None:
    """Point `<name>/latest.json` at a published version (the manual step, ADR-0025)."""
    if registry.lake.read_bytes(registry.key(ranker.MODEL_NAME, version, "model.txt")) is None:
        raise RuntimeError(f"{ranker.MODEL_NAME} {version} is not in the registry")
    registry.lake.write_bytes(
        registry.key(ranker.MODEL_NAME, "latest.json"),
        (
            json.dumps({"version": version, "promoted_at": datetime.now(UTC).isoformat()}) + "\n"
        ).encode(),
    )


async def push_metrics(http: httpx2.AsyncClient, settings: Settings, card: dict[str, Any]) -> int:
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the metrics")
    items = [
        {
            "model_name": f"ranker_{name}",
            "version": card["version"],
            "metric": metric,
            "value": value,
            "split": "holdout",
            "data_source": METRICS_SOURCE.get(card["data_source"], card["data_source"]),
            "details": {"baskets": scores.get("baskets")},
        }
        for name, scores in card["metrics"].items()
        for metric, value in scores.items()
        if metric != "baskets"
    ]
    answer = await http.post(
        f"{settings.api_url.rstrip('/')}/v1/internal/intel/model-metrics",
        json={"items": items},
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
            tables = await rows(session)
    finally:
        await engine.dispose()
    trained = fit(tables, args.version)
    if args.promote and args.promote not in {t.data_source for t in trained}:
        raise RuntimeError(f"nothing trained for data_source={args.promote!r} to promote")
    registry = open_registry(settings, args.out)
    summary: dict[str, Any] = {"models": {}}
    for t in trained:
        summary["models"][t.data_source] = {
            "model": publish(registry, t),
            "metrics": t.card["metrics"],
            "rows": t.card["train_rows"],
        }
        if args.promote == t.data_source:
            promote(registry, t.version)
            summary["promoted"] = t.version
    if args.push:
        async with httpx2.AsyncClient() as http:
            summary["pushed"] = sum([await push_metrics(http, settings, t.card) for t in trained])
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and publish the next-basket ranker.")
    parser.add_argument("--out", type=Path, help="a local MODELS_DIR instead of the bucket")
    parser.add_argument("--version", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--push", action="store_true")
    parser.add_argument(
        "--promote",
        metavar="DATA_SOURCE",
        help="also point latest.json at this run's model for that source (off by default)",
    )
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(Settings(), args)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
