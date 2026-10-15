"""Next-basket ranker v0 (handbook 08 M-B, M10 §3.7): which candidates belong in a household's
box this week, learnt from what households kept, removed and added.

- **Baskets:** one per household x week whose cycle was **approved or locked** (a draft, skipped
  or cancelled cycle is not a final box; a skipped week is a gap in the household's history).
  The label of a candidate is 1 when it is in the week's final box (kept or added), 0 when it
  was drafted and removed or never taken.
- **Candidates** (TIFU-KNN, simplified): the household's own past items, the frequent items of
  its K nearest households (Jaccard on their baskets *before this week's date*), and this week's
  drafted lines.
- **Features** (as of the week's cut-off, never from the final box): weeks since the item was
  last bought (capped at `WEEKS_SINCE_NEVER`, also the value for never bought, the same cap the
  serving schema documents), share of past baskets it was in, whether it was drafted this week,
  whether it was removed last time, the item's share among all households last week (cohort
  popularity), and the unit price from the hub's price list valid at the cut-off (else the
  newest earlier list holding the item, else 0).
- **Dropped in v0: `drafted_rule`** (drafted by the rule table, `reason_code = daily_use`). The
  reason code is only known for lines still in the final box: a removed line loses it, so the
  feature was 1 almost only for kept items and leaked the label. A later version can restore it
  from a snapshot of the draft taken before the household edits it.
- `build` also emits `in_last_basket` (in the household's previous approved or locked basket),
  which the "repeat last week" baseline ranks on; it is not a model feature.
- **Model:** LightGBM LambdaRank grouped by basket; **leave-last-basket-out** evaluation with
  NDCG@10, Recall@10 and Recall@|basket| (R-precision; eval protocol §7, 8 Oct 2026) against
  "repeat last week" and "top frequency" (eval protocol §4). One model per `data_source`:
  synthetic and pilot baskets are never pooled.
- The model and its card go to the registry (`<name>/<version>/` under `models/` in the
  `gb-mlflow` bucket, ADR-0025); promotion (`latest.json`) is a separate, manual step.
"""

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

K_NEIGHBOURS = 5
NEIGHBOUR_ITEMS = 10
# Weeks since an item was last bought: capped here, and this value when it never was. The
# serving schema (backend/ml RankCandidate.weeks_since) documents and applies the same cap.
WEEKS_SINCE_NEVER = 52.0
FEATURES = (
    "weeks_since",
    "frequency",
    "drafted",
    "removed_last",
    "popularity",
    "price_paise",
)
MODEL_NAME = "basket_ranker"
FINAL_STATUSES = frozenset({"approved", "locked"})
IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Basket:
    household: str
    week: date  # the delivery day
    drafted: frozenset[str]  # products on the draft (kept, removed or swapped out)
    final: frozenset[str]  # products in the approved box
    data_source: str
    hub: str | None = None
    as_of: date | None = None  # the cut-off day: prices are read as of it


@dataclass(frozen=True)
class PriceBook:
    """The catalog's price lists per hub, oldest first: `[(valid_from, {product: paise})]`."""

    lists: dict[str, list[tuple[date, dict[str, int]]]] = field(default_factory=dict)

    def as_of(self, hub: str | None, day: date, product: str) -> int:
        """The price in the newest list valid on `day` that holds the product, else 0."""
        for valid_from, prices in reversed(self.lists.get(str(hub), [])):
            if valid_from <= day and product in prices:
                return prices[product]
        return 0


def price_book(rows: Iterable[dict[str, Any]]) -> PriceBook:
    """From snapshot-shaped `prices` rows (hub_id, valid_from, product_id, price_paise)."""
    by_hub: dict[str, dict[date, dict[str, int]]] = defaultdict(dict)
    for row in rows:
        day = row["valid_from"]
        by_hub[str(row["hub_id"])].setdefault(day, {})[str(row["product_id"])] = int(
            row["price_paise"]
        )
    return PriceBook({hub: sorted(lists.items()) for hub, lists in sorted(by_hub.items())})


@dataclass
class Dataset:
    rows: list[dict[str, float]] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    groups: list[int] = field(default_factory=list)  # candidates per basket, in order
    keys: list[tuple[str, date, str]] = field(default_factory=list)  # (household, week, product)
    basket_sizes: list[int] = field(default_factory=list)  # final-box size per basket, in order


def _cutoff_day(cycle: dict[str, Any]) -> date:
    cutoff = cycle.get("cutoff_at")
    if isinstance(cutoff, datetime):
        return (cutoff.astimezone(IST) if cutoff.tzinfo else cutoff).date()
    return cycle["delivery_date"]


def baskets_from_rows(
    box_lines: list[dict[str, Any]],
    edits: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
) -> list[Basket]:
    """From snapshot-shaped rows: cycles (id, household_id, hub_id, delivery_date, cutoff_at,
    status, data_source) say which weeks have a final box (approved or locked only); box_lines
    (cycle_id, product_id, source) are those boxes; edits (cycle_id, product_id, action) say
    what was drafted and then removed or swapped out."""
    final_cycles = {str(c["id"]): c for c in cycles if c["status"] in FINAL_STATUSES}
    lines: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for line in box_lines:
        cycle_id = str(line["cycle_id"])
        if cycle_id in final_cycles:
            lines[cycle_id][str(line["product_id"])] = line
    removed: dict[str, set[str]] = defaultdict(set)
    for edit in edits:
        if edit["action"] in ("removed", "swapped"):
            removed[str(edit["cycle_id"])].add(str(edit["product_id"]))
    baskets = []
    for cycle_id, cycle in final_cycles.items():
        box = lines.get(cycle_id)
        if not box:
            continue
        drafted = {p for p, line in box.items() if line["source"] != "customer"}
        drafted |= removed.get(cycle_id, set())
        baskets.append(
            Basket(
                household=str(cycle["household_id"]),
                week=cycle["delivery_date"],
                drafted=frozenset(drafted),
                final=frozenset(box),
                data_source=str(cycle["data_source"]),
                hub=None if cycle.get("hub_id") is None else str(cycle["hub_id"]),
                as_of=_cutoff_day(cycle),
            )
        )
    return sorted(baskets, key=lambda b: (b.data_source, b.household, b.week))


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def neighbours(history: dict[str, set[str]], household: str) -> list[str]:
    mine = history.get(household, set())
    scored = [(-_jaccard(mine, items), h) for h, items in history.items() if h != household]
    return [h for _, h in sorted(scored)[:K_NEIGHBOURS]]


def build(
    baskets: list[Basket], *, holdout_last: bool, prices: PriceBook | None = None
) -> tuple[Dataset, Dataset]:
    """Train and evaluation sets of one data source. With `holdout_last`, each household's last
    basket is the evaluation set (leave-last-basket-out); the features of a basket use only
    baskets of earlier weeks (its own and its neighbours') and prices valid at its cut-off."""
    sources = {b.data_source for b in baskets}
    if len(sources) > 1:
        raise ValueError(f"one data source per model, got {sorted(sources)}")
    book = prices or PriceBook()
    by_household: dict[str, list[Basket]] = defaultdict(list)
    for b in sorted(baskets, key=lambda b: (b.household, b.week)):
        by_household[b.household].append(b)
    weeks = sorted({b.week for b in baskets})
    popularity: dict[date, Counter[str]] = {}
    households_in_week: dict[date, int] = {}
    for b in baskets:
        popularity.setdefault(b.week, Counter()).update(sorted(b.final))
        households_in_week[b.week] = households_in_week.get(b.week, 0) + 1
    train, test = Dataset(), Dataset()
    for household, mine in sorted(by_household.items()):
        if holdout_last and len(mine) < 2:
            continue
        for i, basket in enumerate(mine):
            past = mine[:i]
            target = test if (holdout_last and i == len(mine) - 1) else train
            history = {
                h: set().union(*(b.final for b in bs if b.week < basket.week))
                for h, bs in by_household.items()
            }
            own = history[household]
            near: Counter[str] = Counter()
            for h in neighbours(history, household):
                near.update(sorted(history[h]))
            top_near = sorted(near.items(), key=lambda kv: (-kv[1], kv[0]))[:NEIGHBOUR_ITEMS]
            candidates = own | {p for p, _ in top_near} | set(basket.drafted)
            if not candidates:
                continue
            last_week = max((w for w in weeks if w < basket.week), default=None)
            pop = popularity.get(last_week, Counter()) if last_week else Counter()
            n_last = households_in_week.get(last_week, 1) if last_week else 1
            last_basket = past[-1] if past else None
            as_of = basket.as_of or basket.week
            for product in sorted(candidates):
                bought = [b for b in past if product in b.final]
                weeks_since = (
                    min((basket.week - bought[-1].week).days / 7, WEEKS_SINCE_NEVER)
                    if bought
                    else WEEKS_SINCE_NEVER
                )
                target.rows.append(
                    {
                        "weeks_since": float(weeks_since),
                        "frequency": len(bought) / len(past) if past else 0.0,
                        "drafted": float(product in basket.drafted),
                        "removed_last": float(
                            last_basket is not None
                            and product in last_basket.drafted
                            and product not in last_basket.final
                        ),
                        "popularity": pop[product] / max(n_last, 1),
                        "price_paise": float(book.as_of(basket.hub, as_of, product)),
                        "in_last_basket": float(
                            last_basket is not None and product in last_basket.final
                        ),
                    }
                )
                target.labels.append(int(product in basket.final))
                target.keys.append((household, basket.week, product))
            target.groups.append(len(candidates))
            target.basket_sizes.append(len(basket.final))
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


def recall_at_basket(ranked_labels: list[int]) -> float:
    """Recall@|basket| (R-precision): the share of the basket's items among the top |basket|
    candidates, where |basket| is the number of positives among the candidates."""
    return recall_at(sum(ranked_labels), ranked_labels)


def evaluate(ds: Dataset, scores: np.ndarray, k: int = 10) -> dict[str, float]:
    """Means over baskets. Recall counts the basket's items that are candidates; the share of
    the final box the candidates cover at all is `candidate_coverage` (the same for every
    ranker on one dataset)."""
    ndcgs, recalls, r_precisions, coverage = [], [], [], []
    start = 0
    for size, basket_size in zip(ds.groups, ds.basket_sizes, strict=True):
        labels = ds.labels[start : start + size]
        order = np.argsort(-scores[start : start + size], kind="stable")
        ranked = [labels[i] for i in order]
        ndcgs.append(ndcg_at(k, ranked))
        recalls.append(recall_at(k, ranked))
        r_precisions.append(recall_at_basket(ranked))
        coverage.append(sum(labels) / basket_size if basket_size else 0.0)
        start += size

    def mean(values: list[float]) -> float:
        return round(float(np.mean(values)), 4) if values else 0.0

    return {
        f"ndcg_at_{k}": mean(ndcgs),
        f"recall_at_{k}": mean(recalls),
        "recall_at_basket": mean(r_precisions),
        "candidate_coverage": mean(coverage),
        "baskets": float(len(ds.groups)),
    }


def baseline_scores(ds: Dataset, kind: str) -> np.ndarray:
    """`repeat_last_week`: what was in the household's previous approved or locked basket ranks
    first (then frequency), however many weeks ago it was; `top_frequency`: the household's most
    frequent items first."""
    if kind == "repeat_last_week":
        return np.array([row["in_last_basket"] + row["frequency"] for row in ds.rows])
    return np.array([row["frequency"] + 0.001 * row["popularity"] for row in ds.rows])


def predict(model: Any, ds: Dataset) -> np.ndarray:
    return np.asarray(model.predict(_matrix(ds)), dtype=float)


def card(
    model: Any,
    metrics: dict[str, dict[str, float]],
    version: str,
    n_train: int,
    data_source: str,
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
        "weeks_since_cap": WEEKS_SINCE_NEVER,
        "feature_importance_gain": {k: round(v / total, 4) for k, v in importance.items()},
        "metrics": metrics,
        "train_rows": n_train,
        "data_source": data_source,
    }


def save(model: Any, card_json: dict[str, Any], directory: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    model.save_model(str(directory / "model.txt"))
    (directory / "card.json").write_text(json.dumps(card_json, indent=2) + "\n")
