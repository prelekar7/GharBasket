"""Settings of the training jobs (environment variables; GitHub repository secrets in Actions)."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # data.gov.in (Agmarknet mandi prices). Unset: the collector refuses to run.
    data_gov_in_api_key: SecretStr | None = None
    mandi_markets: str = "Mumbai,Vashi New Mumbai,Pune"
    mandi_state: str = "Maharashtra"

    # The lake: SeaweedFS locally, Cloudflare R2 in Actions (S3 API, region "auto").
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "auto"
    s3_bucket_lake: str = "gb-lake"
    # The model registry (ADR-0025): models/<name>/<version>/ and models/<name>/latest.json.
    s3_bucket_mlflow: str = "gb-mlflow"

    # Where results go: the API's internal endpoints, with the operator token.
    api_url: str | None = None
    jobs_token: SecretStr | None = None

    # The database, read with a read-only role in Actions (snapshots, features).
    database_url_ro: SecretStr | None = None
    database_url: SecretStr | None = None

    # data.gov.in can take minutes to answer a filtered query at busy times.
    http_timeout_s: float = Field(default=180.0, gt=0)
    # A slow or failing data.gov.in page is tried this many times, waiting longer each time.
    http_attempts: int = Field(default=3, ge=1, le=10)
    retry_wait_s: float = Field(default=20.0, ge=0)

    @property
    def markets(self) -> list[str]:
        return [m.strip() for m in self.mandi_markets.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
