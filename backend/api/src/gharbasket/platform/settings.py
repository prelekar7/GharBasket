"""Application settings, read from environment variables.

Infisical injects them (`infisical run -- ...`); a local `.env` file works too.
Unknown variables are ignored, so the same `.env` can also hold Docker Compose settings.

Safe by default: without APP_ENV the app runs as "prod" (docs hidden, dev conveniences off),
and connection strings and tokens are SecretStr so they never show up in a repr or a log line.
"""

from functools import lru_cache
from typing import Annotated, Any, Literal, Self

from pydantic import BeforeValidator, SecretStr, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_comma(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _upper(value: Any) -> Any:
    return value.upper() if isinstance(value, str) else value


CommaSeparated = Annotated[list[str], NoDecode, BeforeValidator(_split_comma)]
LogLevel = Annotated[Literal["DEBUG", "INFO", "WARNING", "ERROR"], BeforeValidator(_upper)]


class DatabaseSettings(BaseSettings):
    """Just the database URLs: all that migrations and one-off scripts need."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Postgres. On Neon, DATABASE_URL is the pooled (PgBouncer) URL used by the app and
    # DATABASE_URL_DIRECT the direct one used by migrations. Locally both point at Docker.
    database_url: SecretStr | None = None
    database_url_direct: SecretStr | None = None

    @property
    def migrations_url(self) -> str | None:
        url = self.database_url_direct or self.database_url
        return url.get_secret_value() if url else None


class Settings(DatabaseSettings):
    app_env: Literal["dev", "test", "staging", "prod"] = "prod"
    log_level: LogLevel = "INFO"
    # JSON lines for Render and other log collectors; set LOG_JSON=false for readable local output.
    log_json: bool = True
    # Browsers reach the API through same-origin proxies, so CORS is only needed for the
    # Capacitor app (https://localhost). Comma-separated, e.g. "https://localhost".
    cors_allow_origins: CommaSeparated = []

    # Valkey (Redis protocol). Optional until something needs it; /v1/ready then reports "skipped".
    valkey_url: SecretStr | None = None
    # Protects operator endpoints (/v1/ready, and later /v1/internal/jobs/*). Required on
    # staging and prod; open locally so `curl localhost:8000/v1/ready` just works.
    jobs_token: SecretStr | None = None

    @model_validator(mode="after")
    def _deployed_environments_need_their_secrets(self) -> Self:
        if not self.is_local and self.jobs_token is None:
            raise ValueError(f"JOBS_TOKEN is required when APP_ENV={self.app_env}")
        return self

    @property
    def is_local(self) -> bool:
        """A developer machine or the test suite, never a deployed environment."""
        return self.app_env in ("dev", "test")

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
