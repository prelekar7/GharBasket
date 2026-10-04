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

    # Where results go: the API's internal endpoints, with the operator token.
    api_url: str | None = None
    jobs_token: SecretStr | None = None

    http_timeout_s: float = Field(default=60.0, gt=0)

    @property
    def markets(self) -> list[str]:
        return [m.strip() for m in self.mandi_markets.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
