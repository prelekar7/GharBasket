"""Application settings, read from environment variables.

Infisical injects them (`infisical run -- ...`); a local `.env` file works too.
Unknown variables are ignored, so the same `.env` can also hold Docker Compose settings.

Safe by default: without APP_ENV the app runs as "prod" (docs hidden, dev conveniences off),
and connection strings and tokens are SecretStr so they never show up in a repr or a log line.
"""

import json
from functools import lru_cache
from typing import Annotated, Any, Literal, Self

from pydantic import AliasChoices, BeforeValidator, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_comma(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _upper(value: Any) -> Any:
    return value.upper() if isinstance(value, str) else value


CommaSeparated = Annotated[list[str], NoDecode, BeforeValidator(_split_comma)]

# Used only when APP_ENV is dev or test and no key is configured, so local runs and tests need no
# secrets. Deployed environments refuse to start without their own keys.
_DEV_SIGNING_KEY = "gharbasket-dev-only-jwt-signing-key-never-deployed"
_DEV_REFRESH_PEPPER = "gharbasket-dev-only-refresh-token-pepper-never-deployed"
_DEV_KYC_KEY = "gharbasket-dev-only-kyc-encryption-key-never-deployed"
_DEV_KYC_PEPPER = "gharbasket-dev-only-kyc-hash-pepper-never-deployed"
MIN_SECRET_BYTES = 32
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
    # The commit this build came from (Render sets RENDER_GIT_COMMIT); /v1/health reports it, so
    # the deploy workflow can tell when staging runs the new commit.
    git_commit: Annotated[str | None, BeforeValidator(lambda value: value or None)] = Field(
        default=None, validation_alias=AliasChoices("GIT_COMMIT", "RENDER_GIT_COMMIT")
    )
    # Browsers reach the API through same-origin proxies, so CORS is only needed for the
    # Capacitor app (https://localhost). Comma-separated, e.g. "https://localhost".
    cors_allow_origins: CommaSeparated = []

    # Valkey (Redis protocol). Optional until something needs it; /v1/ready then reports "skipped".
    valkey_url: SecretStr | None = None
    # Protects operator endpoints (/v1/ready, and later /v1/internal/jobs/*). Required on
    # staging and prod; open locally so `curl localhost:8000/v1/ready` just works.
    jobs_token: SecretStr | None = None

    # Sessions (ADR-0024): access JWTs are HS256 with a key id ("kid"), so the key can be rotated:
    # set a new JWT_SIGNING_KEY + JWT_KEY_ID and move the old pair to JWT_RETIRED_KEYS
    # ("k1:<key>,k0:<key>") until the last old token has expired (15 minutes).
    jwt_signing_key: SecretStr | None = None
    jwt_key_id: str = Field(default="k1", pattern=r"^[a-z0-9-]{1,16}$")
    jwt_retired_keys: SecretStr | None = None
    jwt_issuer: str = "gharbasket-api"
    jwt_audience: str = "gharbasket-apps"
    # Refresh tokens are stored only as HMAC-SHA256(pepper, token).
    refresh_token_pepper: SecretStr | None = None
    # The ML service (backend/ml, M10). Unset: the rule-based fallbacks answer (intel_gateway).
    ml_service_url: str | None = None
    ml_service_token: SecretStr | None = None
    ml_timeout_s: float = Field(default=3.0, gt=0, le=10)
    # Ghar Saathi (M13): the LLM loop runs in the ML service; a turn may take longer than a
    # solver call (the service gives up after its own ASSISTANT_TURN_TIMEOUT_S, 25 s, first).
    # Tokens (uncached in + out) a user may spend a day.
    assistant_timeout_s: float = Field(default=30.0, gt=0, le=120)
    assistant_daily_tokens: int = Field(default=60_000, gt=0)
    # /route/solve may take time_limit_s plus this: the ML service's OSRM timeout
    # (OSRM_TIMEOUT_S, 2.5 s) plus a margin for the checker and the network.
    ml_route_overhead_s: float = Field(default=6.0, gt=0, le=30)
    # /slots/price for a customer's quote (M10 §3.8), shorter because a customer is waiting; the
    # gateway uses at most ML_TIMEOUT_S. It covers the ML service's SLOT_OSRM_TIMEOUT_S (1 s).
    ml_slot_timeout_s: float = Field(default=2.0, gt=0, le=10)
    # The pantry twin (M10 §3.3) drafts the perishable daily-use quantities only when this is on
    # (PANTRY_TWIN_ENABLED=true); off, the family-size rule table does. It is switched on only
    # once the eval harness shows the twin's pre-registered target holds (MAE ≤ 15 % after 4 weeks).
    pantry_twin_enabled: bool = False
    # Green slots (M10 §3.8, M3 §3.6): off, M3's slot-quote and slot-move endpoints answer 404
    # `subscriptions.green_slots_off` and nothing else changes (on in staging for MS-D).
    green_slots_enabled: bool = False
    # The tiffin assignment's fairness weight, paise per portion (M8 §3.4; M10's knee is 300).
    partners_fairness_lambda: float = Field(default=300.0, ge=0, le=10_000)
    # The public web app: referral links are PUBLIC_WEB_URL/s/<society slug>?ref=<code> (M9 §3.3).
    public_web_url: str = "https://gharbasket.in"
    # Kindness points each side of a referral earns on the neighbour's first delivered box (M9
    # §3.3, owner 2026-10-09): half a donated meal each.
    referral_points_referrer: int = Field(default=250, ge=0, le=5000)
    referral_points_referee: int = Field(default=250, ge=0, le=5000)
    # The eval protocol's label for rows marked `real` (orders, cycles): `pilot` during the pilot,
    # `live` after launch. Monitoring writes metrics under it, never pooled with `synthetic`.
    real_data_source: Literal["pilot", "live"] = "pilot"
    # Browser apps (console, PWA) sign in with httpOnly cookies through same-origin proxies; a
    # cookie-authenticated request that changes something must come from one of these origins.
    # Comma-separated, e.g. "https://gharbasket-console.vercel.app,https://gharbasket.vercel.app".
    web_origins: CommaSeparated = []
    # Razorpay (M5 top-ups), test mode only: live keys are refused, no real money moves in the
    # prototype. Unset locally: top-ups use dev orders, paid with POST /v1/dev/razorpay/pay.
    razorpay_key_id: str | None = Field(default=None, pattern=r"^rzp_test_[A-Za-z0-9]+$")
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr | None = None
    # Refund to source (M5 §3.3): only top-ups captured this recently can take money back.
    razorpay_refund_window_days: int = Field(default=180, ge=1, le=365)
    # Kindness points one donated meal ("a meal for a child") takes (M5 §3.6, `goal.points`).
    kindness_goal: int = Field(default=500, ge=1, le=100_000)
    # Who pays partners their weekly payouts (M5 §3.5). The prototype builds only `simulated` (no
    # real money moves); `razorpay_route` is for the pilot and refused until it is built.
    payouts_provider: Literal["simulated", "razorpay_route"] = "simulated"
    # Phone sign-in: the apps verify the OTP with Firebase and send us Firebase's ID token.
    # Locally it may be unset; then only dev login (APP_ENV=dev) can sign in.
    firebase_project_id: str | None = None
    # Private uploads (M0 §3.1): SeaweedFS locally, Cloudflare R2 on staging, over the S3 API.
    # Unset: POST /v1/files/presign answers 503 platform.storage_unavailable.
    s3_endpoint_url: str | None = None
    # The host the apps PUT to and read from; default S3_ENDPOINT_URL. The Android emulator
    # reaches the laptop as http://10.0.2.2:8333.
    s3_public_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "auto"
    s3_bucket_private: str = "gb-private"
    # Firebase Cloud Messaging (M11 pushes): the service account key file's JSON, as one value.
    # Unset: pushes are skipped (the inbox still gets every message).
    fcm_service_account: SecretStr | None = None
    # WhatsApp Cloud API (M11): off unless all four are set; until then WhatsApp is skipped. The
    # webhook needs the app secret (signatures) and the verify token (Meta's handshake).
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = Field(default=None, pattern=r"^[0-9]{5,30}$")
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    # KYC numbers (M8 §3.1, handbook 13 §13.5) are stored as pgp_sym_encrypt(number, key), the key
    # named by KYC_KEY_VERSION for rotation, and as HMAC-SHA256(pepper, number) for duplicate
    # checks. Required on staging and prod; local runs use dev-only values.
    kyc_encryption_key: SecretStr | None = None
    kyc_key_version: int = Field(default=1, ge=1, le=32767)
    kyc_hash_pepper: SecretStr | None = None

    @model_validator(mode="after")
    def _deployed_environments_need_their_secrets(self) -> Self:
        if not self.is_local:
            required = {
                "JOBS_TOKEN": self.jobs_token,
                "JWT_SIGNING_KEY": self.jwt_signing_key,
                "REFRESH_TOKEN_PEPPER": self.refresh_token_pepper,
                "FIREBASE_PROJECT_ID": self.firebase_project_id,
                "KYC_ENCRYPTION_KEY": self.kyc_encryption_key,
                "KYC_HASH_PEPPER": self.kyc_hash_pepper,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError(f"{', '.join(missing)} required when APP_ENV={self.app_env}")
        if self.payouts_provider == "razorpay_route":
            raise ValueError(
                "PAYOUTS_PROVIDER=razorpay_route is not built in the prototype (M5 §3.5, pilot "
                "only); use PAYOUTS_PROVIDER=simulated"
            )
        if self.fcm_service_account is not None:
            _ = self.fcm_credentials  # a broken key fails at start, not at the first push
        for name, secret in (
            ("JWT_SIGNING_KEY", self.jwt_signing_key),
            ("REFRESH_TOKEN_PEPPER", self.refresh_token_pepper),
            ("KYC_ENCRYPTION_KEY", self.kyc_encryption_key),
            ("KYC_HASH_PEPPER", self.kyc_hash_pepper),
            *((f"JWT_RETIRED_KEYS ({kid})", key) for kid, key in self._retired_pairs()),
        ):
            if secret is not None and len(secret.get_secret_value().encode()) < MIN_SECRET_BYTES:
                raise ValueError(f"{name} must be at least {MIN_SECRET_BYTES} bytes")
        return self

    def _retired_pairs(self) -> list[tuple[str, SecretStr]]:
        if self.jwt_retired_keys is None:
            return []
        pairs: list[tuple[str, SecretStr]] = []
        for item in self.jwt_retired_keys.get_secret_value().split(","):
            kid, sep, key = item.strip().partition(":")
            if not sep or not kid or not key:
                raise ValueError("JWT_RETIRED_KEYS must look like 'k0:<key>,k1:<key>'")
            pairs.append((kid, SecretStr(key)))
        return pairs

    @property
    def jwt_verification_keys(self) -> dict[str, bytes]:
        """Every key a valid access token may be signed with, by key id."""
        keys = {kid: key.get_secret_value().encode() for kid, key in self._retired_pairs()}
        keys[self.jwt_key_id] = self.jwt_active_key
        return keys

    @property
    def jwt_active_key(self) -> bytes:
        key = self.jwt_signing_key.get_secret_value() if self.jwt_signing_key else _DEV_SIGNING_KEY
        return key.encode()

    @property
    def refresh_pepper(self) -> bytes:
        pepper = self.refresh_token_pepper
        return (pepper.get_secret_value() if pepper else _DEV_REFRESH_PEPPER).encode()

    @property
    def kyc_key(self) -> str:
        """The pgcrypto key for KYC numbers; send it only as a bind parameter."""
        key = self.kyc_encryption_key
        return key.get_secret_value() if key else _DEV_KYC_KEY

    @property
    def kyc_pepper(self) -> bytes:
        pepper = self.kyc_hash_pepper
        return (pepper.get_secret_value() if pepper else _DEV_KYC_PEPPER).encode()

    @property
    def is_local(self) -> bool:
        """A developer machine or the test suite, never a deployed environment."""
        return self.app_env in ("dev", "test")

    @property
    def allowed_web_origins(self) -> frozenset[str]:
        """WEB_ORIGINS, plus the local dev servers (Vite 5173, Next.js 3000) on a dev machine."""
        local = {"http://localhost:5173", "http://localhost:3000"} if self.is_local else set()
        return frozenset(self.web_origins) | local

    @property
    def dev_login_enabled(self) -> bool:
        """Sign in with just a phone number, no OTP: only ever on a developer's own machine."""
        return self.app_env == "dev"

    @property
    def razorpay_enabled(self) -> bool:
        return self.razorpay_key_id is not None and self.razorpay_key_secret is not None

    @property
    def fcm_enabled(self) -> bool:
        return self.fcm_service_account is not None

    @property
    def whatsapp_enabled(self) -> bool:
        return None not in (
            self.whatsapp_token,
            self.whatsapp_phone_number_id,
            self.whatsapp_app_secret,
            self.whatsapp_verify_token,
        )

    @property
    def fcm_credentials(self) -> dict[str, Any]:
        """The service account: client_email, private_key, token_uri and project_id."""
        if self.fcm_service_account is None:
            raise ValueError("FCM_SERVICE_ACCOUNT is not set")
        try:
            account = json.loads(self.fcm_service_account.get_secret_value())
        except ValueError:
            raise ValueError("FCM_SERVICE_ACCOUNT must be the key file's JSON") from None
        needed = ("client_email", "private_key", "token_uri", "project_id")
        if not isinstance(account, dict) or any(k not in account for k in needed):
            raise ValueError(f"FCM_SERVICE_ACCOUNT needs {', '.join(needed)}")
        return account

    @property
    def storage_enabled(self) -> bool:
        return bool(self.s3_endpoint_url and self.s3_access_key and self.s3_secret_key)

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "prod"


_in_use: Settings | None = None


@lru_cache
def _from_environment() -> Settings:
    return Settings()


def use_settings(settings: Settings | None) -> None:
    """Make these the settings `get_settings()` returns: `create_app` and the job runner call it,
    so module code outside a request (a refund made inside a job) sees the settings the app was
    built with, not a second set read from the environment. `None` goes back to the environment."""
    global _in_use
    _in_use = settings


def get_settings() -> Settings:
    return _in_use if _in_use is not None else _from_environment()
