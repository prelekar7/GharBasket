"""Shared fixtures: the developer's shell (`infisical run`, a sourced `.env`) never leaks the
registry settings into the tests, so no test reads a real bucket or a real models folder."""

import pytest

_REGISTRY_ENV = (
    "S3_ENDPOINT_URL",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_REGION",
    "S3_BUCKET_MLFLOW",
    "MODELS_DIR",
    "MODELS_MANIFEST",
    "MODELS_CACHE_DIR",
)


@pytest.fixture(autouse=True)
def no_registry_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _REGISTRY_ENV:
        monkeypatch.delenv(name, raising=False)
