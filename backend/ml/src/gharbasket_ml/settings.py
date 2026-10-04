"""Settings of the ML service (environment variables)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["dev", "test", "staging", "prod"] = "prod"
    # The shared secret the API sends (`Authorization: Bearer`); optional only in local dev.
    ml_service_token: SecretStr | None = None
    # A local stand-in for the R2 registry pointer (tests, laptops); unset: built-in solvers only.
    models_manifest: Path | None = None
    # The OSRM server next to this service (the Space runs it on 127.0.0.1:5000); unset: the
    # routing endpoints answer with the haversine fallback and /health/ai says "not_configured".
    osrm_url: str | None = None

    @model_validator(mode="after")
    def _deployed_needs_a_token(self) -> Self:
        if self.app_env in ("staging", "prod") and self.ml_service_token is None:
            raise ValueError(f"ML_SERVICE_TOKEN required when APP_ENV={self.app_env}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
