"""Application settings, read from environment variables.

Infisical injects them (`infisical run -- ...`); a local `.env` file works too.
Unknown variables are ignored, so the same `.env` can also hold Docker Compose settings.
"""

from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_comma(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


CommaSeparated = Annotated[list[str], NoDecode, BeforeValidator(_split_comma)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    # JSON lines for Render and other log collectors; set LOG_JSON=false for readable local output.
    log_json: bool = True
    # Browsers reach the API through same-origin proxies, so CORS is only needed for the
    # Capacitor app (https://localhost). Comma-separated, e.g. "https://localhost".
    cors_allow_origins: CommaSeparated = []

    # Postgres. On Neon, DATABASE_URL is the pooled (PgBouncer) URL used by the app and
    # DATABASE_URL_DIRECT the direct one used by migrations. Locally both point at Docker.
    database_url: str | None = None
    database_url_direct: str | None = None
    # Valkey (Redis protocol). Optional until something needs it; /v1/ready then reports "skipped".
    valkey_url: str | None = None

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "prod"

    @property
    def migrations_url(self) -> str | None:
        return self.database_url_direct or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
