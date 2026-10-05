"""Next-basket ranker v0 (handbook 08 M-B, M10 §3.7): which candidates belong in a household's
box this week, learnt from what households kept, removed and added.

- **Baskets:** one per household x week; the label of a candidate is 1 when it is in the
  week's final box (kept or added), 0 when it was drafted and removed or never taken.
- **Candidates** (TIFU-KNN, simplified): the household's own past items, the frequent items of
  its K nearest households (Jaccard on past baskets), and this week's drafted lines.
- **Features:** weeks since the item was last bought, share of past weeks it was bought, whether
  it was drafted this week (and why), whether it was removed last time, the item's share among
  all households last week (cohort popularity), the unit price.
- **Model:** LightGBM LambdaRank grouped by basket; **leave-last-basket-out** evaluation with
  NDCG@10 and Recall@10 against "repeat last week" and "top frequency" (eval protocol §4).
- The model and its card go to the registry (`models/basket_ranker/<version>/`), the Space
  serves it at `POST /basket/rank`.
"""

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

K_NEIGHBOURS = 5
NEIGHBOUR_ITEMS = 10
FEATURES = (
    "weeks_since",
    "frequency",
    "drafted",
    "drafted_rule",
    "removed_last",
    "popularity",
    "price_paise",
)
MODEL_NAME = "basket_ranker"


@dataclass(frozen=True)
class Basket:
    household: str
    week: date  # the delivery day
    drafted: dict[str, str]  # product → reason of the drafted line
    final: set[str]  # products in the approved box
    prices: dict[str, int]


@dataclass
class Dataset:
    rows: list[dict[str, float]] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    groups: list[int] = field(default_factory=list)  # candidates per basket, in order
    keys: list[tuple[str, date, str]] = field(default_factory=list)  # (household, week, product)


def baskets_from_rows(box_lines: list[dict[str, Any]], edits: list[dict[str, Any]]) -> list[Basket]:
    """From snapshot-shaped rows: box_lines (household_id, delivery_date, product_id,
    reason_code, source, unit_price_paise) are the final boxes; edits (household_id,
    delivery_date, product_id, action) say what was drafted and removed."""
    final: dict[tuple[str, date], dict[str, Any]] = defaultdict(dict)
    for line in box_lines:
        final[str(line["household_id"]), line["delivery_date"]][str(line["product_id"])] = line
    removed: dict[tuple[str, date], set[str]] = defaultdict(set)
    for edit in edits:
        if edit["action"] in ("removed", "swapped"):
            removed[str(edit["household_id"]), edit["delivery_date"]].add(str(edit["product_id"]))
    baskets = []
    for key in sorted(final):
        lines = final[key]
        drafted = {
            p: str(line["reason_code"]) for p, line in lines.items() if line["source"] != "customer"
        }
        for p in removed.get(key, ()):
            drafted.setdefault(p, "meal_plan")  # drafted, then taken out
        baskets.append(
            Basket(
                key[0],
                key[1],
                drafted,
                set(lines),
                {p: int(line["unit_price_paise"]) for p, line in lines.items()},
            )
        )
    return baskets


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def neighbours(history: dict[str, set[str]], household: str) -> list[str]:
    mine = history.get(household, set())
    scored = [(_jaccard(mine, items), h) for h, items in history.items() if h != household]
    return [h for _, h in sorted(scored, reverse=True)[:K_NEIGHBOURS]]


def build(baskets: list[Basket], *, holdout_last: bool) -> tuple[Dataset, Dataset]:
    """Train and evaluation sets. With `holdout_last`, each household's last basket is the
    evaluation set (leave-last-basket-out); the features of a basket use only earlier weeks."""
    by_household: dict[str, list[Basket]] = defaultdict(list)
    for b in sorted(baskets, key=lambda b: (b.household, b.week)):
        by_household[b.household].append(b)
    weeks = sorted({b.week for b in baskets})
    popularity: dict[date, Counter[str]] = {}
    households_in_week: dict[date, int] = {}
    for b in baskets:
        popularity.setdefault(b.week, Counter()).update(b.final)
        households_in_week[b.week] = households_in_week.get(b.week, 0) + 1
    train, test = Dataset(), Dataset()
    for household, mine in by_household.items():
        for i, basket in enumerate(mine):
            past = mine[:i]
            is_last = i == len(mine) - 1
            if holdout_last and len(mine) < 2:
                continue
            target = test if (holdout_last and is_last) else train
            history = {h: set().union(*(b.final for b in bs[:i])) for h, bs in by_household.items()}
            own = set().union(*(b.final for b in past)) if past else set()
            near = Counter()
            for h in neighbours(history, household):
                near.update(history[h])
            candidates = (
                own | {p for p, _ in near.most_common(NEIGHBOUR_ITEMS)} | set(basket.drafted)
            )
            if not candidates:
                continue
            last_week = max((w for w in weeks if w < basket.week), default=None)
            pop = popularity.get(last_week, Counter()) if last_week else Counter()
            n_last = households_in_week.get(last_week, 1) if last_week else 1
            last_basket = past[-1] if past else None
            for product in sorted(candidates):
                bought = [b for b in past if product in b.final]
                weeks_since = (basket.week - bought[-1].week).days / 7 if bought else len(weeks) + 1
                price = basket.prices.get(product) or next(
                    (b.prices[product] for b in reversed(past) if product in b.prices), 0
                )
                target.rows.append(
                    {
                        "weeks_since": float(weeks_since),
                        "frequency": len(bought) / len(past) if past else 0.0,
                        "drafted": float(product in basket.drafted),
                        "drafted_rule": float(basket.drafted.get(product) == "daily_use"),
                        "removed_last": float(
                            last_basket is not None
                            and product in last_basket.drafted
                            and product not in last_basket.final
                        ),
                        "popularity": pop[product] / max(n_last, 1),
                        "price_paise": float(price),
                    }
                )
                target.labels.append(int(product in basket.final))
                target.keys.append((household, basket.week, product))
            target.groups.append(len(candidates))
    return train, test


def _matrix(ds: Dataset) -> np.ndarray:
    return np.array([[row[f] for f in FEATURES] for row in ds.rows], dtype=float)


def train(ds: Dataset, *, rounds: int = 200, seed: int = 7) -> Any:
    import lightgbm as lgb

    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [10],
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_data_in_leaf": 5,
        "verbose": -1,
        "seed": seed,
        "deterministic": True,
        "num_threads": 1,
    }
    data = lgb.Dataset(_matrix(ds), label=ds.labels, group=ds.groups, feature_name=list(FEATURES))
    return lgb.train(params, data, num_boost_round=rounds)


def ndcg_at(k: int, ranked_labels: list[int]) -> float:
    dcg = sum(label / math.log2(i + 2) for i, label in enumerate(ranked_labels[:k]))
    ideal = sorted(ranked_labels, reverse=True)
    idcg = sum(label / math.log2(i + 2) for i, label in enumerate(ideal[:k]))
    return dcg / idcg if idcg else 0.0


def recall_at(k: int, ranked_labels: list[int]) -> float:
    positives = sum(ranked_labels)
    return sum(ranked_labels[:k]) / positives if positives else 0.0


def evaluate(ds: Dataset, scores: np.ndarray, k: int = 10) -> dict[str, float]:
    ndcgs, recalls = [], []
    start = 0
    for size in ds.groups:
        labels = ds.labels[start : start + size]
        order = np.argsort(-scores[start : start + size], kind="stable")
        ranked = [labels[i] for i in order]
        ndcgs.append(ndcg_at(k, ranked))
        recalls.append(recall_at(k, ranked))
        start += size
    return {
        f"ndcg_at_{k}": round(float(np.mean(ndcgs)), 4) if ndcgs else 0.0,
        f"recall_at_{k}": round(float(np.mean(recalls)), 4) if recalls else 0.0,
        "baskets": float(len(ds.groups)),
    }


def baseline_scores(ds: Dataset, kind: str) -> np.ndarray:
    """`repeat_last_week`: what the household bought last time ranks first (then frequency);
    `top_frequency`: the household's most frequent items first."""
    if kind == "repeat_last_week":
        return np.array(
            [(1.0 if row["weeks_since"] <= 1.0 else 0.0) + row["frequency"] for row in ds.rows]
        )
    return np.array([row["frequency"] + 0.001 * row["popularity"] for row in ds.rows])


def predict(model: Any, ds: Dataset) -> np.ndarray:
    return np.asarray(model.predict(_matrix(ds)), dtype=float)


def card(
    model: Any, metrics: dict[str, dict[str, float]], version: str, n_train: int
) -> dict[str, Any]:
    importance = dict(
        zip(FEATURES, (int(x) for x in model.feature_importance("gain")), strict=True)
    )
    total = sum(importance.values()) or 1
    return {
        "name": MODEL_NAME,
        "version": version,
        "kind": "lightgbm-lambdarank",
        "features": list(FEATURES),
        "feature_importance_gain": {k: round(v / total, 4) for k, v in importance.items()},
        "metrics": metrics,
        "train_rows": n_train,
        "data_source": "synthetic",
    }


def save(model: Any, card_json: dict[str, Any], directory: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    model.save_model(str(directory / "model.txt"))
    (directory / "card.json").write_text(json.dumps(card_json, indent=2) + "\n")
