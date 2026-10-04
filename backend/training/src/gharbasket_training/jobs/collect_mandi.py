"""Daily wholesale prices from Agmarknet (data.gov.in) for our products, into the lake, plus a
4-week price index pushed to the API.

    python -m gharbasket_training.jobs.collect_mandi --out data/external/mandi  # local folder
    python -m gharbasket_training.jobs.collect_mandi --push-index                # Actions: S3, API

The data.gov.in resource is a snapshot that keeps no history, so this job *is* the history.
Prices are ₹ per quintal; ₹ X per quintal is exactly X paise per kg, which is what we store.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pyarrow as pa

from gharbasket_training.lake import Lake, open_lake
from gharbasket_training.settings import Settings, get_settings

RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
API = f"https://api.data.gov.in/resource/{RESOURCE}"
PAGE = 1000
PREFIX = "external/mandi"
INDEX_WEEKS = 4
COMMODITIES = Path(__file__).resolve().parents[1] / "data" / "commodities.json"

SCHEMA = pa.schema(
    [
        ("day", pa.date32()),
        ("state", pa.string()),
        ("district", pa.string()),
        ("market", pa.string()),
        ("commodity", pa.string()),
        ("variety", pa.string()),
        ("product", pa.string()),  # our slug, or null when unmapped
        ("min_paise_per_kg", pa.int64()),
        ("max_paise_per_kg", pa.int64()),
        ("modal_paise_per_kg", pa.int64()),
    ]
)


def commodity_map() -> dict[str, str | None]:
    raw = json.loads(COMMODITIES.read_text())["map"]
    return {name.strip().lower(): slug for name, slug in raw.items()}


def _paise_per_kg(rupees_per_quintal: Any) -> int | None:
    try:
        value = float(rupees_per_quintal)
    except (TypeError, ValueError):
        return None
    return round(value) if value > 0 else None


def _day(raw: str) -> date:
    return datetime.strptime(raw, "%d/%m/%Y").date()


async def fetch(
    http: httpx2.AsyncClient, settings: Settings, *, state: str, market: str
) -> list[dict[str, Any]]:
    """Every record for one market today (the resource only holds the current snapshot)."""
    assert settings.data_gov_in_api_key is not None
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = await http.get(
            API,
            params={
                "api-key": settings.data_gov_in_api_key.get_secret_value(),
                "format": "json",
                "limit": PAGE,
                "offset": offset,
                "filters[state]": state,
                "filters[market]": market,
            },
            timeout=settings.http_timeout_s,
        )
        response.raise_for_status()
        page = response.json()
        batch = page.get("records", [])
        records.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(page.get("total", 0)):
            return records


def normalise(
    records: list[dict[str, Any]], names: dict[str, str | None]
) -> tuple[pa.Table, set[str]]:
    """Rows in our schema (one per market, commodity and variety) and the unmapped names."""
    rows: list[dict[str, Any]] = []
    unmapped: set[str] = set()
    for r in records:
        commodity = str(r.get("commodity", "")).strip()
        slug = names.get(commodity.lower())
        if commodity.lower() not in names:
            unmapped.add(commodity)
        modal = _paise_per_kg(r.get("modal_price"))
        if modal is None:
            continue
        rows.append(
            {
                "day": _day(str(r["arrival_date"])),
                "state": str(r.get("state", "")),
                "district": str(r.get("district", "")),
                "market": str(r.get("market", "")),
                "commodity": commodity,
                "variety": str(r.get("variety", "")),
                "product": slug,
                "min_paise_per_kg": _paise_per_kg(r.get("min_price")),
                "max_paise_per_kg": _paise_per_kg(r.get("max_price")),
                "modal_paise_per_kg": modal,
            }
        )
    return pa.Table.from_pylist(rows, schema=SCHEMA), unmapped


def key_for(day: date, state: str) -> str:
    return f"{PREFIX}/date={day.isoformat()}/{state.lower().replace(' ', '-')}.parquet"


@dataclass(frozen=True)
class IndexRow:
    product: str
    day: date
    market: str
    modal_paise_per_kg: int
    index_4w: float  # today's modal price over the median of the previous weeks (1.0 = usual)


def price_index(lake: Lake, *, today: date, state: str, weeks: int = INDEX_WEEKS) -> list[IndexRow]:
    """Today's price of each (product, market) against the median of the last `weeks` weeks.

    Only mapped products and markets with at least 5 earlier days count; with less history
    the index is 1.0 (no evidence of a spike), which the planner treats as "usual".
    """
    history: dict[tuple[str, str], list[int]] = defaultdict(list)
    todays: dict[tuple[str, str], int] = {}
    start = today - timedelta(weeks=weeks)
    for key in lake.keys(f"{PREFIX}/"):
        day = date.fromisoformat(key.split("date=")[1].split("/")[0])
        if (
            day < start
            or day > today
            or not key.endswith(f"{state.lower().replace(' ', '-')}.parquet")
        ):
            continue
        table = lake.read(key)
        if table is None:
            continue
        for row in table.to_pylist():
            if row["product"] is None:
                continue
            at = (row["product"], row["market"])
            if day == today:
                todays[at] = row["modal_paise_per_kg"]  # the last variety listed wins; fine
            else:
                history[at].append(row["modal_paise_per_kg"])
    out = []
    for (product, market), modal in sorted(todays.items()):
        past = history.get((product, market), [])
        ratio = modal / statistics.median(past) if len(past) >= 5 else 1.0
        out.append(IndexRow(product, today, market, modal, round(ratio, 3)))
    return out


async def push_index(http: httpx2.AsyncClient, settings: Settings, rows: list[IndexRow]) -> int:
    """Hand the index to the API (`POST /v1/internal/intel/price-index`); returns rows accepted."""
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the index")
    body = {
        "items": [
            {
                "slug": r.product,
                "day": r.day.isoformat(),
                "market": r.market,
                "modal_paise_per_kg": r.modal_paise_per_kg,
                "index_4w": r.index_4w,
            }
            for r in rows
        ]
    }
    response = await http.post(
        f"{settings.api_url.rstrip('/')}/v1/internal/intel/price-index",
        json=body,
        headers={"Authorization": f"Bearer {settings.jobs_token.get_secret_value()}"},
        timeout=settings.http_timeout_s,
    )
    response.raise_for_status()
    return int(response.json()["accepted"])


async def run(
    settings: Settings,
    http: httpx2.AsyncClient,
    lake: Lake,
    *,
    today: date,
    push: bool,
) -> dict[str, Any]:
    if settings.data_gov_in_api_key is None:
        raise RuntimeError(
            "set DATA_GOV_IN_API_KEY (register at data.gov.in; the sample key gives 10 rows)"
        )
    names = commodity_map()
    records: list[dict[str, Any]] = []
    for market in settings.markets:
        records.extend(await fetch(http, settings, state=settings.mandi_state, market=market))
    table, unmapped = normalise(records, names)
    key = key_for(today, settings.mandi_state)
    lake.write(key, table)
    summary: dict[str, Any] = {
        "day": today.isoformat(),
        "records": len(records),
        "rows": table.num_rows,
        "mapped": table.num_rows - table.column("product").null_count,
        "unmapped_commodities": sorted(unmapped),
        "key": key,
    }
    if push:
        rows = price_index(lake, today=today, state=settings.mandi_state)
        summary["index_rows"] = len(rows)
        summary["accepted"] = await push_index(http, settings, rows)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    parser = argparse.ArgumentParser(description="Collect today's mandi prices into the lake.")
    parser.add_argument("--out", type=Path, default=None, help="local lake folder instead of S3")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--push-index", action="store_true", help="also push the index to the API")
    args = parser.parse_args(argv)
    settings = get_settings()

    async def go() -> dict[str, Any]:
        async with httpx2.AsyncClient() as http:
            lake = open_lake(settings, args.out)
            return await run(settings, http, lake, today=args.today, push=args.push_index)

    summary = asyncio.run(go())
    print(json.dumps(summary))
    return 0 if summary["rows"] else 1


if __name__ == "__main__":
    sys.exit(main())
