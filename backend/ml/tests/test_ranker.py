"""Serving the next-basket ranker: the fallback order, a model from the registry folder, the
endpoint and the registry listing, and every way the registry can be wrong."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    ranker.clear_cache()
    yield
    ranker.clear_cache()


def candidates() -> RankRequest:
    return RankRequest.model_validate(
        {
            "candidates": [
                {"product": "milk", "weeks_since": 1, "frequency": 0.9, "drafted": True},
                {"product": "palak", "weeks_since": 3, "frequency": 0.2, "drafted": True},
                {"product": "chikoo", "weeks_since": 52, "frequency": 0.0, "popularity": 0.4},
            ],
            "top_k": 2,
        }
    )


def tiny_registry(
    root: Path, version: str = "v1", features: tuple[str, ...] = ranker.FEATURES
) -> Path:
    """A LambdaRank model that prefers drafted items, saved the way the training job does."""
    rng = np.random.default_rng(7)
    x = rng.random((40, len(features)))
    y = (x[:, 2] > 0.5).astype(int)  # the "drafted" column decides
    data = lgb.Dataset(x, label=y, group=[10, 10, 10, 10], feature_name=list(features))
    booster = lgb.train(
        {"objective": "lambdarank", "verbose": -1, "min_data_in_leaf": 2, "num_threads": 1},
        data,
        20,
    )
    folder = root / "basket_ranker" / version
    folder.mkdir(parents=True)
    booster.save_model(str(folder / "model.txt"))
    (folder / "card.json").write_text(json.dumps({"version": version}))
    (root / "basket_ranker" / "latest.json").write_text(json.dumps({"version": version}))
    return root


def test_without_a_model_the_fallback_ranks_by_frequency() -> None:
    answer = ranker.rank(candidates(), None)
    assert answer.source == "fallback" and [r.product for r in answer.ranked] == ["milk", "palak"]
    assert answer.ranked[0].rank == 1 and len(answer.ranked) == 2


def test_a_registry_model_is_loaded_and_listed(tmp_path: Path) -> None:
    root = tiny_registry(tmp_path)
    answer = ranker.rank(candidates(), root)
    assert answer.source == "ml" and answer.model == "basket_ranker-v1"
    assert {r.product for r in answer.ranked} <= {"milk", "palak", "chikoo"}
    served = registry.served_models(None, root)
    assert ("basket_ranker", "v1", "learned", "registry") in {
        (m.name, m.version, m.kind, m.source) for m in served
    }
    assert ranker.load(tmp_path / "empty") is None


def test_a_ranked_item_carries_its_top_three_shap_features(tmp_path: Path) -> None:
    root = tiny_registry(tmp_path)
    answer = ranker.rank(candidates(), root)
    loaded = ranker.load(root)
    assert loaded is not None and answer.source == "ml"

    request = candidates()
    by_product = {c.product: ranker._row(c) for c in request.candidates}
    for item in answer.ranked:
        top = item.top_features
        assert top is not None and len(top) == ranker.TOP_FEATURES
        contrib = loaded.booster.predict([by_product[item.product]], pred_contrib=True)[0]
        expected = sorted(zip(ranker.FEATURES, contrib[:-1], strict=True), key=lambda p: -abs(p[1]))
        assert [t.feature for t in top] == [f for f, _ in expected[:3]]
        assert [t.contribution for t in top] == [round(float(c), 4) for _, c in expected[:3]]
        assert abs(top[0].contribution) >= abs(top[-1].contribution)
    # the tiny model splits on "drafted"; the explanation of a drafted item leads with it
    assert answer.ranked[0].top_features[0].feature == "drafted"  # pyright: ignore[reportOptionalSubscript]


def test_the_fallback_has_no_explanation_and_the_old_fields_are_unchanged() -> None:
    answer = ranker.rank(candidates(), None)
    assert all(r.top_features is None for r in answer.ranked)
    assert set(answer.model_dump()["ranked"][0]) == {"product", "score", "rank", "top_features"}


def test_a_failing_explanation_keeps_the_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tiny_registry(tmp_path)
    plain = ranker.rank(candidates(), root)
    loaded = ranker.load(root)
    assert loaded is not None
    real = loaded.booster.predict

    def no_contrib(data: Any, **kwargs: Any) -> Any:
        if kwargs.get("pred_contrib"):
            raise RuntimeError("boom")
        return real(data)

    monkeypatch.setattr(loaded.booster, "predict", no_contrib)
    answer = ranker.rank(candidates(), root)
    assert answer.source == "ml" and all(r.top_features is None for r in answer.ranked)
    assert [(r.product, r.score) for r in answer.ranked] == [
        (r.product, r.score) for r in plain.ranked
    ]
    monkeypatch.setattr(
        loaded.booster,
        "predict",
        lambda data, **kw: [[float("nan")] * 7] * len(data) if kw else real(data),
    )
    assert all(r.top_features is None for r in ranker.rank(candidates(), root).ranked)


def test_weeks_since_beyond_the_cap_is_served_as_the_cap() -> None:
    long_ago = RankRequest.model_validate(
        {"candidates": [{"product": "x", "weeks_since": 400, "frequency": 0}]}
    )
    assert ranker._row(long_ago.candidates[0])[0] == ranker.WEEKS_SINCE_NEVER
    with pytest.raises(ValueError):
        RankRequest.model_validate(
            {
                "candidates": [
                    {"product": "x", "weeks_since": 1, "frequency": 0, "drafted_rule": True}
                ]
            }
        )


@pytest.mark.parametrize(
    "pointer",
    ["{not json", json.dumps(["v1"]), json.dumps({"version": 7}), json.dumps({"v": "v1"})],
)
def test_a_malformed_pointer_falls_back_and_is_reported(tmp_path: Path, pointer: str) -> None:
    root = tiny_registry(tmp_path)
    (root / "basket_ranker" / "latest.json").write_text(pointer)

    answer = ranker.rank(candidates(), root)

    assert answer.source == "fallback" and answer.model == "frequency-v0"
    served = {(m.name, m.version) for m in registry.served_models(None, root)}
    assert ("basket_ranker", registry.UNREADABLE) in served


def test_a_pointer_to_a_missing_version_or_outside_the_folder_falls_back(tmp_path: Path) -> None:
    root = tiny_registry(tmp_path)
    for version in ("v9", "../v1", ""):
        ranker.clear_cache()
        (root / "basket_ranker" / "latest.json").write_text(json.dumps({"version": version}))
        assert ranker.rank(candidates(), root).source == "fallback"


def test_a_corrupt_model_file_falls_back(tmp_path: Path) -> None:
    root = tiny_registry(tmp_path)
    (root / "basket_ranker" / "v1" / "model.txt").write_text("tree\nnot a model at all\n")
    assert ranker.rank(candidates(), root).source == "fallback"


def test_a_model_with_other_features_is_not_served(tmp_path: Path) -> None:
    """A v0 model trained with `drafted_rule` (seven features) must not be fed six columns."""
    old = (*ranker.FEATURES[:3], "drafted_rule", *ranker.FEATURES[3:])
    root = tiny_registry(tmp_path, features=old)
    assert ranker.load(root) is None
    assert ranker.rank(candidates(), root).source == "fallback"


def test_a_failing_predict_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tiny_registry(tmp_path)
    loaded = ranker.load(root)
    assert loaded is not None

    def broken(_: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(loaded.booster, "predict", broken)
    assert ranker.rank(candidates(), root).source == "fallback"
    monkeypatch.setattr(loaded.booster, "predict", lambda _: [float("nan")] * 3)
    assert ranker.rank(candidates(), root).source == "fallback"


def test_a_model_published_after_startup_is_picked_up_after_the_reload_interval(
    tmp_path: Path,
) -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    assert ranker.load(tmp_path, reload_s=600, clock=clock) is None  # nothing promoted yet
    tiny_registry(tmp_path, "v1")
    now[0] = 300.0
    assert ranker.load(tmp_path, reload_s=600, clock=clock) is None  # still cached
    now[0] = 601.0
    first = ranker.load(tmp_path, reload_s=600, clock=clock)
    assert first is not None and first.version == "v1"

    # a promotion to a broken version keeps the last good model serving
    (tmp_path / "basket_ranker" / "latest.json").write_text("{oops")
    now[0] = 1300.0
    kept = ranker.load(tmp_path, reload_s=600, clock=clock)
    assert kept is not None and kept.version == "v1"


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


@pytest.mark.anyio
async def test_health_and_models_survive_a_bad_registry(tmp_path: Path) -> None:
    root = tiny_registry(tmp_path)
    (root / "basket_ranker" / "latest.json").write_text("{not json")
    (tmp_path / "manifest.json").write_text("[1, 2]")
    app = create_app(
        Settings(
            app_env="test",
            ml_service_token=SecretStr(TOKEN),
            models_dir=root,
            models_manifest=tmp_path / "manifest.json",
        )
    )
    auth = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        health = await c.get("/health/ai", headers=auth)
        models = await c.get("/models", headers=auth)
        ranked = await c.post("/basket/rank", json=candidates().model_dump(), headers=auth)
    assert health.status_code == 200 and models.status_code == 200
    assert {"name": "basket_ranker", "version": "unreadable"}.items() <= next(
        m for m in models.json() if m["name"] == "basket_ranker"
    ).items()
    assert ranked.status_code == 200 and ranked.json()["source"] == "fallback"
