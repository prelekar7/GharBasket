"""Serving the next-basket ranker (M10 §3.7 `POST /basket/rank`): a LightGBM LambdaRank model
from the registry scores candidates with the features the training job built; without a usable
model the fallback ranks by frequency then popularity (`source: "fallback"`).

The registry is a folder (`MODELS_DIR`, in the Space image or mounted) holding
`basket_ranker/latest.json` → `{"version": "..."}` and `basket_ranker/<version>/model.txt` with
its `card.json`; the training job writes the same layout (`--out`) and to R2 (ADR-0025).

The model is re-read every `MODELS_RELOAD_S` (10 minutes by default), so a version promoted after
startup is picked up and a missing model is not remembered for ever. A pointer or model that
cannot be read, or whose features differ from `FEATURES`, never fails a request: the last good
model keeps serving, or the fallback answers.
"""

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gharbasket_ml.schemas import (
    FeatureContribution,
    RankCandidate,
    RankedOut,
    RankRequest,
    RankResponse,
)

log = logging.getLogger(__name__)

FEATURES = (
    "weeks_since",
    "frequency",
    "drafted",
    "removed_last",
    "popularity",
    "price_paise",
)
# The training job's cap on weeks since last bought, and its value for never bought.
WEEKS_SINCE_NEVER = 52.0
TOP_FEATURES = 3  # per ranked item, as the training job's explanations
MODEL_NAME = "basket_ranker"
RELOAD_S = 600.0


@dataclass(frozen=True)
class Loaded:
    version: str
    booster: Any
    card: dict[str, Any]


class BadModel(Exception):
    """The registry holds something we cannot serve."""


_cache: dict[Path, tuple[float, Loaded | None]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _read(models_dir: Path) -> Loaded | None:
    """The promoted model, None when nothing is promoted; BadModel when it cannot be served."""
    pointer = models_dir / MODEL_NAME / "latest.json"
    if not pointer.exists():
        return None
    try:
        version = json.loads(pointer.read_text())["version"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise BadModel(f"unreadable pointer {pointer}: {e}") from e
    if not isinstance(version, str) or not version or "/" in version or version.startswith("."):
        raise BadModel(f"bad version in {pointer}: {version!r}")
    folder = models_dir / MODEL_NAME / version
    if not (folder / "model.txt").exists():
        raise BadModel(f"{folder / 'model.txt'} is missing")
    import lightgbm as lgb

    try:
        booster = lgb.Booster(model_file=str(folder / "model.txt"))
    except Exception as e:  # lightgbm raises its own LightGBMError on a corrupt file
        raise BadModel(f"cannot load {folder / 'model.txt'}: {e}") from e
    if list(booster.feature_name()) != list(FEATURES):
        raise BadModel(f"{version} expects features {booster.feature_name()}, not {FEATURES}")
    try:
        card = (
            json.loads((folder / "card.json").read_text())
            if (folder / "card.json").exists()
            else {}
        )
    except (OSError, ValueError):
        card = {}
    return Loaded(version, booster, card)


def load(
    models_dir: Path | None,
    *,
    reload_s: float = RELOAD_S,
    clock: Callable[[], float] = time.monotonic,
) -> Loaded | None:
    """The model to serve, re-read at most every `reload_s` seconds. A bad registry keeps the
    last good model (or None) until the next read; it never raises."""
    if models_dir is None:
        return None
    now = clock()
    with _lock:
        hit = _cache.get(models_dir)
        if hit is not None and now - hit[0] < reload_s:
            return hit[1]
        try:
            loaded = _read(models_dir)
        except BadModel as e:
            log.warning("basket ranker not reloaded: %s", e)
            loaded = hit[1] if hit is not None else None
        except Exception:  # a filesystem surprise must not fail the request either
            log.exception("basket ranker not reloaded")
            loaded = hit[1] if hit is not None else None
        _cache[models_dir] = (now, loaded)
        return loaded


def _row(c: RankCandidate) -> list[float]:
    return [
        min(float(c.weeks_since), WEEKS_SINCE_NEVER),
        float(c.frequency),
        float(c.drafted),
        float(c.removed_last),
        float(c.popularity),
        float(c.price_paise),
    ]


def _model_scores(loaded: Loaded, request: RankRequest) -> list[float] | None:
    try:
        raw = loaded.booster.predict([_row(c) for c in request.candidates])
        scores = [float(x) for x in raw]
    except Exception:
        log.exception("basket ranker %s failed to predict; falling back", loaded.version)
        return None
    if len(scores) != len(request.candidates) or not all(math.isfinite(s) for s in scores):
        log.warning("basket ranker %s returned unusable scores; falling back", loaded.version)
        return None
    return scores


def _top_features(
    loaded: Loaded, rows: list[list[float]]
) -> list[list[FeatureContribution]] | None:
    """The `TOP_FEATURES` largest |SHAP| contributions of each row, from LightGBM's native
    `pred_contrib=True` (TreeSHAP; the last column is the expected value). None on any failure:
    an explanation never costs the ranking."""
    try:
        raw = loaded.booster.predict(rows, pred_contrib=True)
        out = []
        for row in raw:
            pairs = sorted(
                zip(FEATURES, (float(x) for x in row), strict=False), key=lambda p: -abs(p[1])
            )
            if not all(math.isfinite(c) for _, c in pairs):
                return None
            out.append(
                [
                    FeatureContribution(feature=f, contribution=round(c, 4))
                    for f, c in pairs[:TOP_FEATURES]
                ]
            )
        return out
    except Exception:
        log.exception("basket ranker %s failed to explain; answering without", loaded.version)
        return None


def rank(request: RankRequest, models_dir: Path | None, reload_s: float = RELOAD_S) -> RankResponse:
    loaded = load(models_dir, reload_s=reload_s)
    scores = _model_scores(loaded, request) if loaded is not None else None
    if loaded is not None and scores is not None:
        source, model = "ml", f"{MODEL_NAME}-{loaded.version}"
    else:
        scores = [c.frequency + 0.001 * c.popularity for c in request.candidates]
        source, model = "fallback", "frequency-v0"
    order = sorted(range(len(request.candidates)), key=lambda i: (-scores[i], i))[: request.top_k]
    explained = (
        _top_features(loaded, [_row(request.candidates[i]) for i in order])
        if source == "ml" and loaded is not None
        else None
    )
    return RankResponse(
        source=source,  # pyright: ignore[reportArgumentType]
        model=model,
        ranked=[
            RankedOut(
                product=request.candidates[i].product,
                score=round(scores[i], 4),
                rank=r + 1,
                top_features=None if explained is None else explained[r],
            )
            for r, i in enumerate(order)
        ],
    )
