"""The next-basket ranker: baskets from the simulator's boxes and edits, features that only use
earlier weeks, LambdaRank trained and evaluated leave-last-basket-out against the baselines,
the registry layout the Space reads."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from gharbasket_sim import config
from gharbasket_sim.catalog import from_seed_files
from gharbasket_sim.households import sample
from gharbasket_sim.weeks import cached, rules_planner, simulate
from gharbasket_training import ranker
from gharbasket_training.jobs import train_ranker
from gharbasket_training.lake import LocalLake

START = date(2026, 10, 6)  # a Tuesday: the delivery day of week 0


def simulated_rows(
    weeks: int = 6, households: int = 12
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Snapshot-shaped box lines and edits from a few simulated households."""
    catalog = from_seed_files()
    plan = cached(rules_planner)
    box_lines, edits = [], []
    for h in sample(7, config.load(), catalog)[:households]:
        rows = simulate(7, h, catalog, START - timedelta(days=1), weeks, plan, None)
        for line in rows.box_lines:
            box_lines.append(
                {
                    "household_id": h.key,
                    "delivery_date": START + timedelta(weeks=line["week"]),
                    "product_id": line["product"],
                    "reason_code": line["reason"],
                    "source": line["source"],
                    "unit_price_paise": line["unit_price_paise"],
                }
            )
        for edit in rows.edits:
            edits.append(
                {
                    "household_id": h.key,
                    "delivery_date": START + timedelta(weeks=edit["week"]),
                    "product_id": edit["product"],
                    "action": edit["action"],
                }
            )
    return box_lines, edits


def test_baskets_and_features_use_only_earlier_weeks() -> None:
    box_lines, edits = simulated_rows(weeks=4, households=6)
    baskets = ranker.baskets_from_rows(box_lines, edits)
    assert len(baskets) == 6 * 4 and all(b.final for b in baskets)
    removed = {
        (e["household_id"], e["delivery_date"], e["product_id"])
        for e in edits
        if e["action"] == "removed"
    }
    for b in baskets:
        for product in b.drafted:
            assert product in b.final or (b.household, b.week, product) in removed

    train, test = ranker.build(baskets, holdout_last=True)

    assert len(test.groups) == 6 and len(train.groups) == 18  # the last basket of each household
    assert len(train.rows) == sum(train.groups) == len(train.labels) == len(train.keys)
    first_week = {k: r for k, r in zip(train.keys, train.rows, strict=True) if k[1] == START}
    assert first_week and all(
        r["frequency"] == 0 and r["popularity"] == 0 for r in first_week.values()
    )
    assert any(r["drafted"] == 1 for r in train.rows) and any(label == 1 for label in train.labels)


def test_lambdarank_trains_and_is_scored_against_the_baselines(tmp_path: Path) -> None:
    box_lines, edits = simulated_rows()
    baskets = ranker.baskets_from_rows(box_lines, edits)
    train, test = ranker.build(baskets, holdout_last=True)

    model = ranker.train(train, rounds=50)
    ours = ranker.evaluate(test, ranker.predict(model, test))
    repeat = ranker.evaluate(test, ranker.baseline_scores(test, "repeat_last_week"))
    top = ranker.evaluate(test, ranker.baseline_scores(test, "top_frequency"))

    for scores in (ours, repeat, top):
        assert 0 <= scores["ndcg_at_10"] <= 1 and 0 <= scores["recall_at_10"] <= 1
        assert scores["baskets"] == len(test.groups)
    assert ours["ndcg_at_10"] >= 0.5  # drafted items and repeats carry most of the signal
    card = ranker.card(
        model, {"lambdarank": ours, "repeat_last_week": repeat}, "test", len(train.rows)
    )
    assert abs(sum(card["feature_importance_gain"].values()) - 1) < 0.01
    ranker.save(model, card, tmp_path / "v")
    assert (tmp_path / "v" / "model.txt").exists() and json.loads(
        (tmp_path / "v" / "card.json").read_text()
    )["version"] == "test"


def test_ndcg_and_recall_follow_the_textbook() -> None:
    assert ranker.ndcg_at(10, [1, 1, 0]) == 1.0 and ranker.ndcg_at(10, [0, 0, 1]) < 1.0
    assert ranker.recall_at(2, [1, 0, 1, 1]) == 1 / 3 and ranker.recall_at(2, [0, 0]) == 0.0


def test_the_job_publishes_the_registry_layout(tmp_path: Path) -> None:
    box_lines, edits = simulated_rows(weeks=5, households=8)
    model, card = train_ranker.fit(box_lines, edits, "2026-10-05")
    lake = LocalLake(tmp_path)

    where = train_ranker.publish(lake, model, card, "2026-10-05")

    assert where == "models/basket_ranker/2026-10-05"
    pointer = json.loads((tmp_path / "models/basket_ranker/latest.json").read_text())
    assert pointer["version"] == "2026-10-05"
    assert lake.read_bytes("models/basket_ranker/2026-10-05/model.txt")
    assert lake.read_bytes("models/basket_ranker/nowhere/model.txt") is None
    assert set(card["metrics"]) == {"lambdarank", "repeat_last_week", "top_frequency"}
