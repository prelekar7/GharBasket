"""Settings of the ML service (environment variables)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["dev", "test", "staging", "prod"] = "prod"
    # The shared secret the API sends (`Authorization: Bearer`); optional only in local dev.
    ml_service_token: SecretStr | None = None
    # A local stand-in for the R2 registry pointer (tests, laptops); unset: built-in solvers only.
    models_manifest: Path | None = None
    # The learned models' folder (`<name>/latest.json`, `<name>/<version>/model.txt`), what the
    # training job writes to R2 and the image or a mount makes local; unset: fallbacks only.
    models_dir: Path | None = None
    # How often the learned models are re-read from MODELS_DIR (ADR-0025: the Space polls
    # latest.json every 10 minutes), so a promotion after startup is served without a restart.
    models_reload_s: float = Field(default=600.0, gt=0)
    # The OSRM server next to this service (the Space runs it on 127.0.0.1:5000); unset: the
    # routing endpoints answer with the haversine fallback and /health/ai says "not_configured".
    osrm_url: str | None = None
    # Ghar Saathi (M13): the Anthropic key (a Space secret) and the model (ADR-0013: Opus 5.5
    # until the S8 evaluation); unset key: /assistant/turn answers 503 and the API says so.
    anthropic_api_key: SecretStr | None = None
    assistant_model: str = "claude-opus-5-5"
    # How long /route/solve waits for OSRM's table before it prices with haversine instead. The
    # API's gateway allows time_limit_s plus ML_ROUTE_OVERHEAD_S (6 s), which must cover this.
    osrm_timeout_s: float = Field(default=2.5, gt=0, le=5)

    @model_validator(mode="after")
    def _deployed_needs_a_token(self) -> Self:
        if self.app_env in ("staging", "prod") and self.ml_service_token is None:
            raise ValueError(f"ML_SERVICE_TOKEN required when APP_ENV={self.app_env}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
