"""The next-basket ranker: baskets from the simulator's approved boxes and edits, features that
only use earlier weeks and the price list at the cut-off, LambdaRank trained and evaluated
leave-last-basket-out against the baselines, the registry layout the Space reads."""

import hashlib
import json
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from gharbasket_sim import config
from gharbasket_sim.catalog import from_seed_files
from gharbasket_sim.households import sample
from gharbasket_sim.pantry import price_of, weekly_prices
from gharbasket_sim.weeks import cached, rules_planner, simulate
from gharbasket_training import ranker
from gharbasket_training.jobs import promote, train_ranker
from gharbasket_training.lake import LocalLake, open_registry
from gharbasket_training.settings import Settings
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

START = date(2026, 10, 6)  # a Tuesday: the delivery day of week 0
HUB = "hub-1"


def simulated_rows(
    weeks: int = 6, households: int = 12, data_source: str = "synthetic"
) -> dict[str, list[dict[str, Any]]]:
    """Snapshot-shaped cycles, box lines, edits and prices from a few simulated households; a
    week the household skipped is a `skipped` cycle whose drafted box still has lines."""
    catalog = from_seed_files()
    plan = cached(rules_planner)
    cycles, box_lines, edits = [], [], []
    for h in sample(7, config.load(), catalog)[:households]:
        rows = simulate(7, h, catalog, START - timedelta(days=1), weeks, plan, None)
        cycle_of = {}
        for week in rows.weeks:
            cycle_id = f"{data_source}:{h.key}:{week['week']}"
            cycle_of[week["week"]] = cycle_id
            delivery = START + timedelta(weeks=week["week"])
            cycles.append(
                {
                    "id": cycle_id,
                    "household_id": h.key,
                    "hub_id": HUB,
                    "delivery_date": delivery,
                    "cutoff_at": datetime.combine(
                        delivery - timedelta(days=1), datetime.min.time(), UTC
                    ),
                    "status": "locked" if week["ordered"] else "skipped",
                    "data_source": data_source,
                }
            )
        for line in rows.box_lines:
            box_lines.append(
                {
                    "cycle_id": cycle_of[line["week"]],
                    "product_id": line["product"],
                    "reason_code": line["reason"],
                    "source": line["source"],
                    "unit_price_paise": line["unit_price_paise"],
                }
            )
        for edit in rows.edits:
            edits.append(
                {
                    "cycle_id": cycle_of[edit["week"]],
                    "product_id": edit["product"],
                    "action": edit["action"],
                }
            )
    prices = [
        {
            "hub_id": HUB,
            "valid_from": START - timedelta(days=1) + timedelta(weeks=w),
            "product_id": slug,
            "price_paise": price,
        }
        for w in range(weeks)
        for factor in [weekly_prices(catalog, None, w)]
        for slug, p in sorted(catalog.products.items())
        if (price := price_of(p, factor)) is not None
    ]
    return {
        "weekly_cycles": cycles,
        "box_lines": box_lines,
        "edit_feedback": edits,
        "prices": prices,
    }


def baskets_of(tables: dict[str, list[dict[str, Any]]]) -> list[ranker.Basket]:
    return ranker.baskets_from_rows(
        tables["box_lines"], tables["edit_feedback"], tables["weekly_cycles"]
    )


def basket(
    household: str,
    week: int,
    final: set[str],
    drafted: Iterable[str] = (),
) -> ranker.Basket:
    day = START + timedelta(weeks=week)
    return ranker.Basket(
        household, day, frozenset(drafted), frozenset(final), "synthetic", HUB, day
    )


def features(ds: ranker.Dataset) -> dict[tuple[str, date, str], dict[str, float]]:
    return dict(zip(ds.keys, ds.rows, strict=True))


def test_baskets_and_features_use_only_earlier_weeks() -> None:
    tables = simulated_rows(weeks=4, households=6)
    baskets = baskets_of(tables)
    approved = [c for c in tables["weekly_cycles"] if c["status"] == "locked"]
    assert len(baskets) == len(approved) and all(b.final for b in baskets)
    removed = {(e["cycle_id"], e["product_id"]) for e in tables["edit_feedback"]}
    cycle_of = {(c["household_id"], c["delivery_date"]): c["id"] for c in approved}
    for b in baskets:
        for product in b.drafted:
            assert product in b.final or (cycle_of[b.household, b.week], product) in removed

    train, test = ranker.build(baskets, holdout_last=True)

    assert len(test.groups) == 6  # the last basket of each household
    assert len(train.rows) == sum(train.groups) == len(train.labels) == len(train.keys)
    first_week = {k: r for k, r in features(train).items() if k[1] == START}
    assert first_week and all(
        r["frequency"] == 0
        and r["popularity"] == 0
        and r["weeks_since"] == ranker.WEEKS_SINCE_NEVER
        for r in first_week.values()
    )
    assert any(r["drafted"] == 1 for r in train.rows) and any(label == 1 for label in train.labels)
    assert "drafted_rule" not in ranker.FEATURES  # removed lines lose their reason code


def test_only_approved_and_locked_cycles_are_baskets_and_skips_are_gaps() -> None:
    cycles = [
        {"id": f"c{i}", "household_id": "h", "hub_id": HUB, "data_source": "synthetic"}
        | {"delivery_date": START + timedelta(weeks=i), "status": status}
        for i, status in enumerate(
            ["locked", "skipped", "approved", "cancelled", "draft", "awaiting_approval", "locked"]
        )
    ]
    lines = [
        {"cycle_id": f"c{i}", "product_id": f"p{i}", "source": "rule", "reason_code": "daily_use"}
        for i in range(7)
    ]

    baskets = ranker.baskets_from_rows(lines, [], cycles)

    assert [(b.week, set(b.final)) for b in baskets] == [
        (START, {"p0"}),
        (START + timedelta(weeks=2), {"p2"}),
        (START + timedelta(weeks=6), {"p6"}),
    ]
    train, test = ranker.build(baskets, holdout_last=True)
    rows = features(test)
    # the skipped, cancelled and draft weeks are gaps, not "bought nothing" weeks
    assert rows["h", START + timedelta(weeks=6), "p2"]["frequency"] == 0.5
    assert rows["h", START + timedelta(weeks=6), "p2"]["weeks_since"] == 4.0
    assert all(k[2] not in {"p1", "p3", "p4", "p5"} for k in train.keys + test.keys)


def test_prices_come_from_the_price_list_at_the_cut_off_never_the_final_box() -> None:
    book = ranker.price_book(
        [
            {"hub_id": HUB, "valid_from": START - timedelta(days=8), "product_id": "old"}
            | {"price_paise": 900},
            {"hub_id": HUB, "valid_from": START - timedelta(days=8), "product_id": "milk"}
            | {"price_paise": 100},
            {"hub_id": HUB, "valid_from": START - timedelta(days=1), "product_id": "milk"}
            | {"price_paise": 120},
            {"hub_id": HUB, "valid_from": START - timedelta(days=1), "product_id": "palak"}
            | {"price_paise": 40},
            {"hub_id": HUB, "valid_from": START + timedelta(days=6), "product_id": "milk"}
            | {"price_paise": 999},  # valid after this week's cut-off
        ]
    )
    baskets = [
        basket("h", -1, {"milk", "old", "rare"}),
        # palak and old drafted then removed (label 0), milk kept (label 1)
        basket("h", 0, {"milk"}, drafted={"milk", "palak", "old"}),
    ]

    train, _ = ranker.build(baskets, holdout_last=False, prices=book)

    rows = features(train)
    assert rows["h", START, "milk"]["price_paise"] == 120  # the list valid at the cut-off
    assert rows["h", START, "palak"]["price_paise"] == 40  # removed lines are priced too
    assert rows["h", START, "old"]["price_paise"] == 900  # newest earlier list holding it
    assert rows["h", START, "rare"]["price_paise"] == 0  # in no list
    labels = dict(zip(train.keys, train.labels, strict=True))
    assert labels["h", START, "milk"] == 1 and labels["h", START, "palak"] == 0


def test_neighbours_only_lend_items_from_before_the_week() -> None:
    """A neighbour that joins later (staggered start) must not lend items it bought after the
    basket's week, whatever its list position."""
    baskets = [
        basket("early", 0, {"rice"}),
        basket("early", 1, {"rice", "dal"}),
        basket("early", 2, {"rice", "dal"}),
        # joins in week 2: its basket of week 2 is in the future of early's week 1
        basket("late", 2, {"rice", "dal", "future-item"}),
        basket("late", 3, {"rice", "future-item"}),
    ]

    train, _ = ranker.build(baskets, holdout_last=False)

    early_candidates = {k[2] for k in train.keys if k[0] == "early"}
    assert "future-item" not in early_candidates
    late_week_3 = {k[2] for k in train.keys if k[0] == "late" and k[1] == baskets[4].week}
    assert "future-item" in late_week_3  # its own past is fine


def test_the_training_rows_do_not_depend_on_the_hash_seed(tmp_path: Path) -> None:
    tables = simulated_rows(weeks=4, households=8)
    baskets = [
        {
            "household": b.household,
            "week": b.week.isoformat(),
            "drafted": sorted(b.drafted),
            "final": sorted(b.final),
            "data_source": b.data_source,
        }
        for b in baskets_of(tables)
    ]
    source = tmp_path / "baskets.json"
    source.write_text(json.dumps(baskets))
    script = f"""
import json, sys
from datetime import date
from gharbasket_training import ranker
raw = json.loads(open({str(source)!r}).read())
baskets = [ranker.Basket(b["household"], date.fromisoformat(b["week"]), frozenset(b["drafted"]),
           frozenset(b["final"]), b["data_source"]) for b in raw]
train, test = ranker.build(baskets, holdout_last=True)
print(json.dumps([[str(k) for k in train.keys], train.rows, train.labels, train.groups,
                  [str(k) for k in test.keys], test.rows]))
"""
    outputs = [
        hashlib.sha256(
            subprocess.run(
                [sys.executable, "-c", script],
                env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                check=True,
            ).stdout.encode()
        ).hexdigest()
        for seed in ("1", "2")
    ]
    assert outputs[0] == outputs[1]


def test_repeat_last_week_ranks_the_previous_approved_basket_across_a_skipped_week() -> None:
    cycles = [
        {"id": f"c{i}", "household_id": "h", "hub_id": HUB, "data_source": "synthetic"}
        | {"delivery_date": START + timedelta(weeks=i), "status": status}
        for i, status in enumerate(["locked", "locked", "skipped", "locked"])
    ]
    finals = [{"atta", "dal"}, {"milk", "curd"}, {"ignored"}, {"milk"}]
    lines = [
        {"cycle_id": f"c{i}", "product_id": p, "source": "rule"}
        for i, final in enumerate(finals)
        for p in sorted(final)
    ]
    _, test = ranker.build(ranker.baskets_from_rows(lines, [], cycles), holdout_last=True)

    rows = {k[2]: r for k, r in features(test).items()}
    assert rows["milk"]["in_last_basket"] == 1 and rows["curd"]["in_last_basket"] == 1
    assert rows["atta"]["in_last_basket"] == 0
    assert rows["milk"]["weeks_since"] == 2.0  # two calendar weeks: a threshold of 1 missed it
    scores = ranker.baseline_scores(test, "repeat_last_week")
    order = [test.keys[i][2] for i in sorted(range(len(scores)), key=lambda i: -scores[i])]
    assert set(order[:2]) == {"milk", "curd"}


def test_weeks_since_is_capped_for_long_gaps() -> None:
    baskets = [
        basket("h", 0, {"rice"}),
        basket("h", 60, {"dal"}, drafted={"dal"}),
        basket("h", 61, {"rice"}),
    ]
    train, _ = ranker.build(baskets, holdout_last=False)
    rows = features(train)
    assert rows["h", START + timedelta(weeks=61), "rice"]["weeks_since"] == 52.0
    assert rows["h", START + timedelta(weeks=60), "dal"]["weeks_since"] == 52.0  # never bought


def test_lambdarank_trains_and_is_scored_against_the_baselines(tmp_path: Path) -> None:
    tables = simulated_rows()
    train, test = ranker.build(
        baskets_of(tables), holdout_last=True, prices=ranker.price_book(tables["prices"])
    )

    model = ranker.train(train, rounds=50)
    ours = ranker.evaluate(test, ranker.predict(model, test))
    repeat = ranker.evaluate(test, ranker.baseline_scores(test, "repeat_last_week"))
    top = ranker.evaluate(test, ranker.baseline_scores(test, "top_frequency"))

    for scores in (ours, repeat, top):
        for metric in ("ndcg_at_10", "recall_at_10", "recall_at_basket", "candidate_coverage"):
            assert 0 <= scores[metric] <= 1
        assert scores["baskets"] == len(test.groups)
    assert ours["candidate_coverage"] == repeat["candidate_coverage"]
    assert ours["ndcg_at_10"] >= 0.5  # drafted items and repeats carry most of the signal
    card = ranker.card(
        model, {"lambdarank": ours, "repeat_last_week": repeat}, "test", len(train.rows), "pilot"
    )
    assert abs(sum(card["feature_importance_gain"].values()) - 1) < 0.01
    assert card["data_source"] == "pilot" and card["weeks_since_cap"] == 52
    ranker.save(model, card, tmp_path / "v")
    assert (tmp_path / "v" / "model.txt").exists() and json.loads(
        (tmp_path / "v" / "card.json").read_text()
    )["version"] == "test"


def test_ndcg_and_recall_follow_the_textbook() -> None:
    assert ranker.ndcg_at(10, [1, 1, 0]) == 1.0 and ranker.ndcg_at(10, [0, 0, 1]) < 1.0
    assert ranker.recall_at(2, [1, 0, 1, 1]) == 1 / 3 and ranker.recall_at(2, [0, 0]) == 0.0
    assert ranker.recall_at_basket([1, 0, 1, 1]) == 2 / 3  # top 3 of a 3-item basket
    assert ranker.recall_at_basket([0, 0]) == 0.0


def test_the_job_trains_per_source_publishes_without_promoting(tmp_path: Path) -> None:
    synthetic = simulated_rows(weeks=4, households=6)
    real = simulated_rows(weeks=4, households=6, data_source="real")
    tables = {name: synthetic[name] + real[name] for name in synthetic}

    trained = train_ranker.fit(tables, "2026-10-05")

    assert [(t.data_source, t.version) for t in trained] == [
        ("real", "2026-10-05-real"),
        ("synthetic", "2026-10-05-synthetic"),
    ]
    for t in trained:  # no pooling: each model saw only its own source's baskets
        assert t.card["data_source"] == t.data_source
        assert set(t.card["metrics"]) == {"lambdarank", "repeat_last_week", "top_frequency"}
    registry = open_registry(Settings(), tmp_path)

    where = train_ranker.publish(registry, trained[1])

    assert where == "basket_ranker/2026-10-05-synthetic"  # the MODELS_DIR layout
    assert (tmp_path / "basket_ranker/2026-10-05-synthetic/model.txt").exists()
    assert not (tmp_path / "basket_ranker/latest.json").exists()  # promotion is manual
    with pytest.raises(SystemExit, match="not in the registry"):
        promote.promote(registry, "basket_ranker", "2026-10-05-real")
    # `just promote basket_ranker <v> --out D` finds what `train_ranker --out D` wrote.
    assert (
        promote.main(["basket_ranker", "2026-10-05-synthetic", "--out", str(tmp_path), "--force"])
        == 0
    )
    pointer = json.loads((tmp_path / "basket_ranker/latest.json").read_text())
    assert pointer["version"] == "2026-10-05-synthetic"
    lake = LocalLake(tmp_path)
    assert lake.read_bytes("basket_ranker/nowhere/model.txt") is None


def test_the_ml_service_loads_what_the_job_writes_to_out(tmp_path: Path) -> None:
    """`--out X` then `MODELS_DIR=X`: the Space's loader finds and serves the model."""
    from gharbasket_ml import ranker as serving
    from gharbasket_ml.schemas import RankRequest

    trained = train_ranker.fit(simulated_rows(weeks=4, households=6), "v")
    registry = open_registry(Settings(), tmp_path)
    train_ranker.publish(registry, trained[0])
    promote.promote(registry, "basket_ranker", trained[0].version, force=True)
    assert tuple(serving.FEATURES) == ranker.FEATURES
    assert serving.WEEKS_SINCE_NEVER == ranker.WEEKS_SINCE_NEVER

    serving.clear_cache()
    answer = serving.rank(
        RankRequest.model_validate(
            {"candidates": [{"product": "milk", "weeks_since": 1, "frequency": 0.9}]}
        ),
        tmp_path,
    )
    serving.clear_cache()
    assert answer.source == "ml" and answer.model == "basket_ranker-v-synthetic"


@pytest.mark.anyio
async def test_metrics_carry_the_rows_data_source() -> None:
    sent: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"accepted": len(sent[-1]["items"])})

    card = {
        "version": "v-real",
        "data_source": "real",
        "metrics": {"lambdarank": {"ndcg_at_10": 0.9, "recall_at_basket": 0.8, "baskets": 3.0}},
    }
    settings = Settings(api_url="http://api", jobs_token=SecretStr("t"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        accepted = await train_ranker.push_metrics(http, settings, card)

    assert accepted == 2
    assert {i["data_source"] for i in sent[0]["items"]} == {"live"}  # real households' rows
    assert {i["metric"] for i in sent[0]["items"]} == {"ndcg_at_10", "recall_at_basket"}


@pytest.mark.anyio
@pytest.mark.services
async def test_the_job_reads_cycles_boxes_edits_and_prices(db_session: AsyncSession) -> None:
    from gharbasket_sim.run import simulate_world

    from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)
    await simulate_world(
        db_session,
        seed=7,
        weeks=2,
        start=date(2026, 10, 5),
        planner="rules",
        adoption=0.02,
        write_db=True,
    )

    tables = await train_ranker.rows(db_session)

    assert tables["weekly_cycles"] and tables["box_lines"] and tables["prices"]
    baskets = baskets_of(tables)
    final = {str(c["id"]) for c in tables["weekly_cycles"] if c["status"] in ("approved", "locked")}
    assert baskets and len(baskets) <= len(final)
    assert {b.data_source for b in baskets} == {"synthetic"}
    book = ranker.price_book(tables["prices"])
    assert all(book.as_of(b.hub, b.as_of or b.week, p) > 0 for b in baskets for p in b.final)
