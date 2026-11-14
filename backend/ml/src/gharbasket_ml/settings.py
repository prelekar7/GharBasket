"""Settings of the ML service (environment variables)."""

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    AliasChoices,
    BeforeValidator,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_TOKEN_BYTES = 32  # the same floor as the API's secrets (platform/settings.py)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["dev", "test", "staging", "prod"] = "prod"
    # The shared secret the API sends (`Authorization: Bearer`); optional only in local dev.
    ml_service_token: SecretStr | None = None
    # A local stand-in for the R2 registry pointer (tests, laptops); unset: built-in solvers only.
    models_manifest: Path | None = None
    # The learned models' folder (`<name>/latest.json`, `<name>/<version>/model.txt`), the
    # registry layout without the `models/` prefix. Set: served as it is and R2 is not read.
    # Unset: the models synced from R2 (below) when the S3 settings are set, else fallbacks only.
    models_dir: Path | None = None
    # How often the learned models are re-read from MODELS_DIR and the R2 pointers are followed
    # (ADR-0025: every 10 minutes), so a promotion after startup is served without a restart.
    models_reload_s: float = Field(default=600.0, gt=0)
    # The R2 model registry (`models/<name>/latest.json` and `models/<name>/<version>/` in
    # S3_BUCKET_MLFLOW), read only, as `backend/training` writes it. All three of the endpoint
    # and the key pair set (and MODELS_DIR unset): the served models are synced into
    # MODELS_CACHE_DIR in the background (`model_sync.py`).
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "auto"
    s3_bucket_mlflow: str = "gb-mlflow"
    models_cache_dir: Path = Path(tempfile.gettempdir()) / "gharbasket-models"
    # One registry request (connect, each read) may take this long; a whole sync at most
    # MODELS_SYNC_DEADLINE_S, after which it gives up and the models it has keep serving.
    models_sync_timeout_s: float = Field(default=10.0, gt=0, le=60)
    models_sync_deadline_s: float = Field(default=120.0, gt=0, le=600)
    # The OSRM server next to this service (the full image runs it on 127.0.0.1:5000; staging has
    # none); unset: the routing endpoints answer with the haversine fallback and /health/ai says
    # "not_configured".
    osrm_url: str | None = None
    # Ghar Saathi (M13): the Anthropic key (a host secret; not set on staging, ADR-0030) and the
    # model (ADR-0013: Opus 5.5 until the S8 evaluation); unset key: /assistant/turn answers 503
    # and the API says so.
    anthropic_api_key: SecretStr | None = None
    assistant_model: str = "claude-opus-5-5"
    # One Anthropic request may take this long (one retry); the whole turn (up to four rounds)
    # this long, below the API's ASSISTANT_TIMEOUT_S (30 s), so the API never waits on a turn
    # this service has already given up on.
    assistant_request_timeout_s: float = Field(default=12.0, gt=0, le=60)
    assistant_turn_timeout_s: float = Field(default=25.0, gt=0, le=110)
    # How long /route/solve waits for OSRM's table before it prices with haversine instead. The
    # API's gateway allows time_limit_s plus ML_ROUTE_OVERHEAD_S (6 s), which must cover this.
    osrm_timeout_s: float = Field(default=2.5, gt=0, le=5)
    # The same for /slots/price, shorter because a customer is waiting (M10 §3.8); the API's
    # ML_SLOT_TIMEOUT_S (2 s) covers it.
    slot_osrm_timeout_s: float = Field(default=1.0, gt=0, le=2)
    # Traces (M0 §6.2), as the API's: exported only when the endpoint is set (the base URL); the
    # headers are a secret. The resource carries the deployed commit (Render's RENDER_GIT_COMMIT).
    otel_exporter_otlp_endpoint: Annotated[str | None, BeforeValidator(lambda v: v or None)] = None
    otel_exporter_otlp_headers: SecretStr | None = None
    otel_service_name: str = "gharbasket-ml"
    otel_traces_sampler_arg: float = Field(default=1.0, ge=0, le=1)
    git_commit: Annotated[str | None, BeforeValidator(lambda v: v or None)] = Field(
        default=None, validation_alias=AliasChoices("GIT_COMMIT", "RENDER_GIT_COMMIT")
    )

    @property
    def registry_sync_enabled(self) -> bool:
        """True when the models come from R2: the S3 settings are set and MODELS_DIR is not."""
        return self.models_dir is None and bool(
            self.s3_endpoint_url and self.s3_access_key and self.s3_secret_key
        )

    @property
    def registry_sync_warning(self) -> str | None:
        """Why a half-set registry configuration is not followed (logged once at startup)."""
        given = [
            name
            for name, value in (
                ("S3_ENDPOINT_URL", self.s3_endpoint_url),
                ("S3_ACCESS_KEY", self.s3_access_key),
                ("S3_SECRET_KEY", self.s3_secret_key),
            )
            if value
        ]
        if not given:
            return None
        if self.models_dir is not None:
            return f"MODELS_DIR is set, so {', '.join(given)} are ignored: R2 is not read"
        if len(given) < 3:
            return f"only {', '.join(given)} of the three S3 settings set: R2 is not read"
        return None

    @property
    def served_models_dir(self) -> Path | None:
        """The folder the ranker and `/models` read: MODELS_DIR, else the R2 cache, else None."""
        if self.models_dir is not None:
            return self.models_dir
        return self.models_cache_dir if self.registry_sync_enabled else None

    @field_validator("s3_endpoint_url")
    @classmethod
    def _endpoint_is_http(cls, value: str | None) -> str | None:
        if value and not value.lower().startswith(("http://", "https://")):
            raise ValueError("S3_ENDPOINT_URL must be an http:// or https:// URL")
        return value or None

    @model_validator(mode="after")
    def _deployed_needs_a_token(self) -> Self:
        if self.is_deployed and self.ml_service_token is None:
            raise ValueError(f"ML_SERVICE_TOKEN required when APP_ENV={self.app_env}")
        if (
            self.is_deployed
            and self.ml_service_token is not None
            and len(self.ml_service_token.get_secret_value().encode()) < MIN_TOKEN_BYTES
        ):
            raise ValueError(f"ML_SERVICE_TOKEN must be at least {MIN_TOKEN_BYTES} bytes")
        return self

    @property
    def is_deployed(self) -> bool:
        return self.app_env in ("staging", "prod")


@lru_cache
def get_settings() -> Settings:
    return Settings()
