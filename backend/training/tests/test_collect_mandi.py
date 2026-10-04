"""The mandi collector against a recorded data.gov.in shape, a local lake and a fake API."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pyarrow as pa
import pytest
from gharbasket_training.jobs import collect_mandi as job
from gharbasket_training.lake import LocalLake
from gharbasket_training.settings import Settings
from pydantic import SecretStr

pytestmark = pytest.mark.anyio

# The resource's record shape (fields: state, district, market, commodity, variety, grade,
# arrival_date, min_price, max_price, modal_price; prices in ₹ per quintal).
TODAY = date(2026, 10, 4)


def record(
    market: str, commodity: str, modal: int, day: date = TODAY, variety: str = "Other"
) -> dict:
    return {
        "state": "Maharashtra",
        "district": "Mumbai",
        "market": market,
        "commodity": commodity,
        "variety": variety,
        "grade": "FAQ",
        "arrival_date": day.strftime("%d/%m/%Y"),
        "min_price": str(modal - 200),
        "max_price": str(modal + 200),
        "modal_price": str(modal),
    }


class FakeDataGov:
    """Two pages of records for Mumbai, nothing for Pune; remembers what it was asked."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.pushed: dict[str, Any] | None = None
        self.pages = {
            "Mumbai": [
                [record("Mumbai", "Tomato", 2400), record("Mumbai", "Onion", 3100)],
                [
                    record("Mumbai", "Bhindi(Ladies Finger)", 4000),
                    record("Mumbai", "Jackfruit", 1500),
                ],
            ],
            "Vashi New Mumbai": [[record("Vashi New Mumbai", "Tomato", 2600, variety="Hybrid")]],
            "Pune": [[]],
        }

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        if request.url.host == "api.test":
            assert request.headers["authorization"] == "Bearer op"
            body = json.loads(request.content)
            self.pushed = body
            return httpx2.Response(200, json={"accepted": len(body["items"]), "unknown_slugs": []})
        market = params["filters[market]"]
        pages = self.pages[market]
        total = sum(len(p) for p in pages)
        offset = int(params["offset"])
        seen = 0
        for page in pages:
            if seen == offset:
                return httpx2.Response(200, json={"total": total, "records": page})
            seen += len(page)
        return httpx2.Response(200, json={"total": total, "records": []})


def settings(**overrides: Any) -> Settings:
    return Settings(
        data_gov_in_api_key=SecretStr("k"),
        api_url="https://api.test",
        jobs_token=SecretStr("op"),
        **overrides,
    )


async def test_the_collector_pages_maps_and_writes_todays_parquet(tmp_path: Path) -> None:
    fake = FakeDataGov()
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handle))
    lake = LocalLake(tmp_path)

    summary = await job.run(settings(), http, lake, today=TODAY, push=False)

    assert summary == {
        "day": "2026-10-04",
        "records": 5,
        "rows": 5,
        "mapped": 4,
        "unmapped_commodities": ["Jackfruit"],
        "key": "external/mandi/date=2026-10-04/maharashtra.parquet",
    }
    table = lake.read(summary["key"])
    assert table is not None and table.schema == job.SCHEMA
    rows = {(r["market"], r["commodity"]): r for r in table.to_pylist()}
    assert rows[("Mumbai", "Tomato")]["modal_paise_per_kg"] == 2400  # ₹2400/quintal = 2400 paise/kg
    assert rows[("Mumbai", "Tomato")]["product"] == "tomato"
    assert rows[("Mumbai", "Jackfruit")]["product"] is None
    assert rows[("Vashi New Mumbai", "Tomato")]["variety"] == "Hybrid"
    # Every market asked, with the state filter and our key; paging continued past page one.
    markets = [c["filters[market]"] for c in fake.calls]
    assert markets == ["Mumbai", "Mumbai", "Vashi New Mumbai", "Pune"]
    assert all(c["filters[state]"] == "Maharashtra" and c["api-key"] == "k" for c in fake.calls)


def test_the_index_compares_today_with_the_median_of_earlier_days(tmp_path: Path) -> None:
    lake = LocalLake(tmp_path)
    for back in range(1, 11):  # ten earlier days of tomato at ₹2000, onion only on 3 days
        day = TODAY - timedelta(days=back)
        rows = [{"market": "Mumbai", "product": "tomato", "commodity": "Tomato", "modal": 2000}]
        if back <= 3:
            rows.append(
                {"market": "Mumbai", "product": "onion", "commodity": "Onion", "modal": 3000}
            )
        lake.write(job.key_for(day, "Maharashtra"), _table(rows, day))
    lake.write(
        job.key_for(TODAY, "Maharashtra"),
        _table(
            [
                {"market": "Mumbai", "product": "tomato", "commodity": "Tomato", "modal": 3240},
                {"market": "Mumbai", "product": "onion", "commodity": "Onion", "modal": 3300},
                {"market": "Mumbai", "product": None, "commodity": "Jackfruit", "modal": 1500},
            ],
            TODAY,
        ),
    )
    lake.write(
        job.key_for(TODAY - timedelta(days=40), "Maharashtra"),
        _table(
            [{"market": "Mumbai", "product": "tomato", "commodity": "Tomato", "modal": 100}],
            TODAY - timedelta(days=40),
        ),
    )

    index = job.price_index(lake, today=TODAY, state="Maharashtra")

    assert [(r.product, r.modal_paise_per_kg, r.index_4w) for r in index] == [
        ("onion", 3300, 1.0),  # fewer than 5 earlier days: no evidence of a spike
        ("tomato", 3240, 1.62),  # 62 % above the median; the 40-day-old file is out of the window
    ]


async def test_push_index_sends_the_rows_to_the_api_and_the_run_reports_it(tmp_path: Path) -> None:
    fake = FakeDataGov()
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handle))
    lake = LocalLake(tmp_path)

    summary = await job.run(settings(), http, lake, today=TODAY, push=True)

    assert summary["index_rows"] == 4 and summary["accepted"] == 4
    assert fake.pushed is not None
    assert [(i["slug"], i["market"], i["index_4w"]) for i in fake.pushed["items"]] == [
        ("bhindi", "Mumbai", 1.0),
        ("onion", "Mumbai", 1.0),
        ("tomato", "Mumbai", 1.0),
        ("tomato", "Vashi New Mumbai", 1.0),
    ]  # no earlier days in this lake: every index is 1.0
    assert fake.pushed["items"][2]["day"] == "2026-10-04"


async def test_missing_configuration_is_refused(tmp_path: Path) -> None:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500)))
    with pytest.raises(RuntimeError, match="DATA_GOV_IN_API_KEY"):
        await job.run(Settings(), http, LocalLake(tmp_path), today=TODAY, push=False)
    with pytest.raises(RuntimeError, match="API_URL"):
        await job.push_index(http, Settings(data_gov_in_api_key=SecretStr("k")), [])
    with pytest.raises(httpx2.HTTPStatusError):
        await job.fetch(http, settings(), state="Maharashtra", market="Mumbai")


def test_normalise_skips_rows_without_a_price() -> None:
    table, unmapped = job.normalise(
        [
            record("Mumbai", "Tomato", 2400) | {"modal_price": "0"},
            record("Mumbai", "tomato ", 2500),
        ],
        job.commodity_map(),
    )
    assert table.num_rows == 1 and table.to_pylist()[0]["product"] == "tomato"
    assert unmapped == set()
    assert job._paise_per_kg("abc") is None


def _table(rows: list[dict[str, Any]], day: date) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "day": day,
                "state": "Maharashtra",
                "district": "Mumbai",
                "market": r["market"],
                "commodity": r["commodity"],
                "variety": "Other",
                "product": r["product"],
                "min_paise_per_kg": r["modal"] - 100,
                "max_paise_per_kg": r["modal"] + 100,
                "modal_paise_per_kg": r["modal"],
            }
            for r in rows
        ],
        schema=job.SCHEMA,
    )
