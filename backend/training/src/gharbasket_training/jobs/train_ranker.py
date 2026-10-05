"""`python -m gharbasket_training.jobs.train_ranker` (weekly, Sun 03:00 IST on Actions; ADR-0025):
train the next-basket ranker on every household's boxes and edits (read-only role), evaluate it
leave-last-basket-out against the baselines, write the model and its card to the registry
(`models/basket_ranker/<version>/` with `latest.json`) and the metrics to the API.

    uv run --directory backend/training python -m gharbasket_training.jobs.train_ranker \\
        --out data/models [--push] [--version 2026-10-05]
"""

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine
from gharbasket_training import ranker, snapshots
from gharbasket_training.lake import Lake, open_lake
from gharbasket_training.settings import Settings


async def rows(session: AsyncSession) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_name = {t.name: t for t in snapshots.TABLES}
    out = []
    for name in ("box_lines", "edit_feedback"):
        table = by_name[name]
        records = (await session.execute(table.query())).all()
        out.append([dict(zip(table.columns, r, strict=True)) for r in records])
    return out[0], out[1]


def fit(
    box_lines: list[dict[str, Any]], edits: list[dict[str, Any]], version: str
) -> tuple[Any, dict[str, Any]]:
    baskets = ranker.baskets_from_rows(box_lines, edits)
    train, test = ranker.build(baskets, holdout_last=True)
    if not train.groups:
        raise RuntimeError("no baskets to train on yet (run the simulator or wait for data)")
    model = ranker.train(train)
    metrics: dict[str, dict[str, float]] = {}
    if test.groups:
        metrics["lambdarank"] = ranker.evaluate(test, ranker.predict(model, test))
        for kind in ("repeat_last_week", "top_frequency"):
            metrics[kind] = ranker.evaluate(test, ranker.baseline_scores(test, kind))
    return model, ranker.card(model, metrics, version, len(train.rows))


def publish(lake: Lake, model: Any, card: dict[str, Any], version: str) -> str:
    prefix = f"models/{ranker.MODEL_NAME}/{version}"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.txt"
        model.save_model(str(path))
        lake.write_bytes(f"{prefix}/model.txt", path.read_bytes())
    lake.write_bytes(f"{prefix}/card.json", (json.dumps(card, indent=2) + "\n").encode())
    lake.write_bytes(
        f"models/{ranker.MODEL_NAME}/latest.json",
        (
            json.dumps({"version": version, "promoted_at": datetime.now(UTC).isoformat()}) + "\n"
        ).encode(),
    )
    return prefix


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
            "data_source": card["data_source"],
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
            box_lines, edits = await rows(session)
    finally:
        await engine.dispose()
    model, card = fit(box_lines, edits, args.version)
    lake = open_lake(settings, args.out)
    where = publish(lake, model, card, args.version)
    summary: dict[str, Any] = {
        "model": where,
        "metrics": card["metrics"],
        "rows": card["train_rows"],
    }
    if args.push:
        async with httpx2.AsyncClient() as http:
            summary["pushed"] = await push_metrics(http, settings, card)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and publish the next-basket ranker.")
    parser.add_argument("--out", type=Path, help="a local registry folder instead of the bucket")
    parser.add_argument("--version", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(Settings(), args)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
