"""Settings of the ML service (environment variables)."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["dev", "test", "staging", "prod"] = "prod"
    # The shared secret the API sends (`Authorization: Bearer`); optional only in local dev.
    ml_service_token: SecretStr | None = None

    @model_validator(mode="after")
    def _deployed_needs_a_token(self) -> Self:
        if self.app_env in ("staging", "prod") and self.ml_service_token is None:
            raise ValueError(f"ML_SERVICE_TOKEN required when APP_ENV={self.app_env}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
