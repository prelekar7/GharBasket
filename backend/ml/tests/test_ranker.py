"""Serving the next-basket ranker: the fallback order, a model from the registry folder, the
endpoint and the registry listing."""

import json
from pathlib import Path

import httpx2
import lightgbm as lgb
import numpy as np
import pytest
from gharbasket_ml import ranker, registry
from gharbasket_ml.main import create_app
from gharbasket_ml.schemas import RankRequest
from gharbasket_ml.settings import Settings
from pydantic import SecretStr

TOKEN = "ml-test-token"


def candidates() -> RankRequest:
    return RankRequest.model_validate(
        {
            "candidates": [
                {
                    "product": "milk",
                    "weeks_since": 1,
                    "frequency": 0.9,
                    "drafted": True,
                    "drafted_rule": True,
                },
                {"product": "palak", "weeks_since": 3, "frequency": 0.2, "drafted": True},
                {"product": "chikoo", "weeks_since": 9, "frequency": 0.0, "popularity": 0.4},
            ],
            "top_k": 2,
        }
    )


def tiny_registry(root: Path) -> Path:
    """A LambdaRank model that prefers drafted items, saved the way the training job does."""
    rng = np.random.default_rng(7)
    x = rng.random((40, len(ranker.FEATURES)))
    y = (x[:, 2] > 0.5).astype(int)  # the "drafted" column decides
    data = lgb.Dataset(x, label=y, group=[10, 10, 10, 10], feature_name=list(ranker.FEATURES))
    booster = lgb.train(
        {"objective": "lambdarank", "verbose": -1, "min_data_in_leaf": 2, "num_threads": 1},
        data,
        20,
    )
    folder = root / "basket_ranker" / "v1"
    folder.mkdir(parents=True)
    booster.save_model(str(folder / "model.txt"))
    (folder / "card.json").write_text(json.dumps({"version": "v1"}))
    (root / "basket_ranker" / "latest.json").write_text(json.dumps({"version": "v1"}))
    return root


def test_without_a_model_the_fallback_ranks_by_frequency() -> None:
    answer = ranker.rank(candidates(), None)
    assert answer.source == "fallback" and [r.product for r in answer.ranked] == ["milk", "palak"]
    assert answer.ranked[0].rank == 1 and len(answer.ranked) == 2


def test_a_registry_model_is_loaded_and_listed(tmp_path: Path) -> None:
    ranker.load.cache_clear()
    root = tiny_registry(tmp_path)
    answer = ranker.rank(candidates(), root)
    assert answer.source == "ml" and answer.model == "basket_ranker-v1"
    assert {r.product for r in answer.ranked} <= {"milk", "palak", "chikoo"}
    served = registry.served_models(None, root)
    assert ("basket_ranker", "v1", "learned", "registry") in {
        (m.name, m.version, m.kind, m.source) for m in served
    }
    assert ranker.load(tmp_path / "empty") is None
    ranker.load.cache_clear()


@pytest.mark.anyio
async def test_the_endpoint_ranks_with_the_token() -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    body = candidates().model_dump()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        ok = await c.post("/basket/rank", json=body, headers={"Authorization": f"Bearer {TOKEN}"})
        no = await c.post("/basket/rank", json=body)
    assert ok.status_code == 200 and ok.json()["source"] == "fallback" and no.status_code == 401
