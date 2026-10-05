"""Serving the next-basket ranker (M10 §3.7 `POST /basket/rank`): a LightGBM LambdaRank model
from the registry scores candidates with the features the training job built; without a model
the fallback ranks by frequency then popularity (`source: "fallback"`).

The registry is a folder (`MODELS_DIR`, in the Space image or mounted) holding
`basket_ranker/latest.json` → `{"version": "..."}` and `basket_ranker/<version>/model.txt` with
its `card.json`; the training job writes the same layout to R2 (ADR-0025).
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from gharbasket_ml.schemas import RankCandidate, RankedOut, RankRequest, RankResponse

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
class Loaded:
    version: str
    booster: Any
    card: dict[str, Any]


@lru_cache(maxsize=4)
def load(models_dir: Path | None) -> Loaded | None:
    if models_dir is None:
        return None
    pointer = models_dir / MODEL_NAME / "latest.json"
    if not pointer.exists():
        return None
    version = json.loads(pointer.read_text())["version"]
    folder = models_dir / MODEL_NAME / version
    if not (folder / "model.txt").exists():
        return None
    import lightgbm as lgb

    booster = lgb.Booster(model_file=str(folder / "model.txt"))
    card = json.loads((folder / "card.json").read_text()) if (folder / "card.json").exists() else {}
    return Loaded(version, booster, card)


def _row(c: RankCandidate) -> list[float]:
    return [
        float(c.weeks_since),
        float(c.frequency),
        float(c.drafted),
        float(c.drafted_rule),
        float(c.removed_last),
        float(c.popularity),
        float(c.price_paise),
    ]


def rank(request: RankRequest, models_dir: Path | None) -> RankResponse:
    loaded = load(models_dir)
    if loaded is not None:
        scores = loaded.booster.predict([_row(c) for c in request.candidates])
        source, model = "ml", f"{MODEL_NAME}-{loaded.version}"
    else:
        scores = [c.frequency + 0.001 * c.popularity for c in request.candidates]
        source, model = "fallback", "frequency-v0"
    order = sorted(range(len(request.candidates)), key=lambda i: (-float(scores[i]), i))
    return RankResponse(
        source=source,  # pyright: ignore[reportArgumentType]
        model=model,
        ranked=[
            RankedOut(
                product=request.candidates[i].product, score=round(float(scores[i]), 4), rank=r + 1
            )
            for r, i in enumerate(order[: request.top_k])
        ],
    )
