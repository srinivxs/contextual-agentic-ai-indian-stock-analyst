"""Typed application configuration.

Settings come from real environment variables first, then an optional ``.env`` file at the repo
root. Values are validated when ``Settings`` is constructed, so a bad deployment crashes at
startup with a clear error instead of misbehaving later. Only variables the code currently uses
are declared; each milestone adds its own (see ``.env.example`` for the full planned list).
"""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/app/core/config.py -> parents[4] is the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[4]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class CommonSettings(BaseSettings):
    """What every process needs: the API and the worker (P9) alike.

    The worker is built from this class alone, so it never receives the Google client secret or
    the session secret: it has no login to handle (least privilege).
    """

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        # .env.example lists variables for later milestones; unknown keys must not break startup.
        extra="ignore",
        # A rejected DATABASE_URL would otherwise be echoed, password included, into the startup
        # error and from there into logs.
        hide_input_in_errors=True,
    )

    app_env: Literal["local", "test", "production"] = "local"
    log_level: LogLevel = "INFO"

    # The RUNTIME role's URL (no DDL rights). Migrations use a separate, privileged URL (P3b).
    database_url: SecretStr
    # Each process may hold at most pool_size + max_overflow connections. The API and the worker
    # add up, and the total must stay well under the server's max_connections.
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    db_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    db_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    # How long /api/readyz waits for `SELECT 1`. Kept below the pool timeout on purpose.
    db_ready_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # --- documents (P9) ---------------------------------------------------------------------
    # Where fetched filings are kept locally: inside the git-ignored data/local/ folder (the project notes:
    # real documents never enter git). In AWS a private S3 bucket takes its place (P9c).
    blob_root: Path = _REPO_ROOT / "data" / "local" / "blobs"

    # --- the worker (P9) ----------------------------------------------------------------------
    # How long an idle worker waits before looking for work again.
    worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    # How long a claimed job is reserved. A worker that dies mid-job loses it after this, and the
    # job is claimed again. Far longer than ingesting one document takes (a few seconds).
    job_lease_seconds: int = Field(default=300, ge=30, le=3600)

    # --- automatic filings (ADR 018) ---------------------------------------------------------
    # Off unless switched on: only then does the worker reach screener.in and www.bseindia.com.
    filings_discovery: bool = False
    # How often each stock's page is read: once a day is plenty for quarterly filings.
    filings_refresh_hours: int = Field(default=24, ge=1, le=168)
    # How far back to fetch: every BSE-hosted filing of the last N years (the owner chose 3).
    filings_years: int = Field(default=3, ge=1, le=5)
    # The largest filing fetched. It is read into memory; annual reports run to tens of megabytes.
    filings_max_bytes: int = Field(default=60 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024)

    # --- embeddings: meaning fingerprints for search (P10, ADR 019) ----------------------------
    # Off unless switched on: only then do the api and the worker call Bedrock (and spend money).
    embeddings_enabled: bool = False
    # ADR 010. boto3 reads the same AWS_REGION variable; ECS sets it in every task.
    aws_region: str = Field(default="ap-south-1", pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")
    embedding_model: str = Field(default="amazon.titan-embed-text-v2:0", pattern=r"\S")
    # The spending cap: the worker stops making fingerprints once the tokens stored for this model
    # reach it. 10 million is about $0.20 at Titan V2's price; the first full run needs about 3.5M.
    embedding_token_budget: int = Field(default=10_000_000, ge=0, le=1_000_000_000)
    # How many Bedrock calls one job makes at the same time (Titan V2 takes one text per call).
    embedding_concurrency: int = Field(default=4, ge=1, le=16)

    @field_validator("database_url")
    @classmethod
    def _require_the_asyncpg_driver(cls, value: SecretStr) -> SecretStr:
        # Any other driver is synchronous and would block the event loop on every query.
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must start with postgresql+asyncpg://")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class Settings(CommonSettings):
    """The API: everything common, plus the public URL, cookies, Google sign-in and sessions."""

    # The origin the BROWSER sees (CloudFront in AWS, the dev server locally). It is the one place
    # redirect URIs and the Origin check come from: never from Host or X-Forwarded-* headers.
    public_base_url: AnyHttpUrl
    # `Secure` cookies are only sent over HTTPS. Off for plain-HTTP localhost, and mandatory in
    # production (see the validator below).
    cookie_secure: bool = False
    # A session lives exactly this long from login. It is never renewed by use.
    session_lifetime_hours: int = Field(default=168, ge=1, le=720)  # default: 7 days

    # --- Google sign-in -------------------------------------------------------------------
    # Required: a deployment that cannot complete a login should fail at startup with a clear
    # message, not when a user first clicks "Sign in".
    #
    # The client ID is NOT a secret: it appears in every authorization URL the browser sees.
    google_client_id: str
    # The client secret authenticates our server at the token endpoint. Never sent to a browser.
    google_client_secret: SecretStr
    # Keys the signature on the short-lived `oauth_login` cookie. A guessable value would let
    # anyone forge `state`, which is exactly what that cookie exists to prevent.
    session_secret: SecretStr = Field(min_length=32)
    # How long a started login may take to come back from Google (10 minutes).
    oauth_login_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    # Timeout for our server-to-server calls to Google, so a slow Google cannot hang a login.
    google_timeout_seconds: float = Field(default=5.0, ge=1, le=120)

    @field_validator("google_client_id")
    @classmethod
    def _require_a_non_blank_client_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("google_client_id must not be blank")
        return value

    @field_validator("google_client_secret")
    @classmethod
    def _require_a_non_blank_client_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            # The message names the field, never the value.
            raise ValueError("google_client_secret must not be blank")
        return value

    @property
    def public_origin(self) -> str:
        """`scheme://host[:port]` of the public URL: what a browser puts in the Origin header."""
        url = self.public_base_url
        default_port = 443 if url.scheme == "https" else 80
        port = f":{url.port}" if url.port and url.port != default_port else ""
        return f"{url.scheme}://{url.host}{port}"

    @property
    def session_lifetime(self) -> timedelta:
        return timedelta(hours=self.session_lifetime_hours)

    @property
    def session_cookie_name(self) -> str:
        # The `__Host-` prefix makes browsers accept the cookie only if it is Secure, has Path=/ and
        # no Domain, so a sibling subdomain cannot overwrite it. Plain-HTTP localhost cannot store
        # a Secure cookie, so development uses the plain name.
        return "__Host-session" if self.app_env == "production" else "session"

    @model_validator(mode="after")
    def _production_must_be_secure(self) -> "Settings":
        if self.app_env == "production":
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if self.public_base_url.scheme != "https":
                raise ValueError("PUBLIC_BASE_URL must be an https URL in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, built once. Tests construct ``Settings`` directly instead."""
    return Settings()


@lru_cache
def get_worker_settings() -> CommonSettings:
    """The worker's settings: the common part only, so it starts without any login secrets."""
    return CommonSettings()
