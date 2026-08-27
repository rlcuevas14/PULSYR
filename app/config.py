import ipaddress

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_SECRET = "dev-secret-change-in-production"
# Every placeholder we have ever shipped in .env.example or docs — all must fail fast.
_PLACEHOLDER_SECRETS = {_INSECURE_SECRET, "change-me"}
_MIN_SECRET_LENGTH = 32

_PADDLE_ENVIRONMENTS = ("sandbox", "production")
# Prefixes Paddle actually uses today, per environment. Only these are acted on:
# an unrecognised prefix passes, because refusing to start over a prefix Paddle
# introduced later would be a self-inflicted outage over something we do not
# understand.
_PADDLE_KEY_ENVIRONMENT = {
    "pdl_sdbx_": "sandbox",
    "pdl_live_": "production",
    "test_": "sandbox",
    "live_": "production",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # DATABASE_URL is the authoritative variable. docker-compose builds it from DB_PASSWORD;
    # if both are set, DATABASE_URL wins.
    database_url: str = "postgresql+asyncpg://pulso:pulso@db/pulso"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)
    db_statement_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    secret_key: str = _INSECURE_SECRET
    debug: bool = False
    deployment_environment: str = "development"
    release: str = ""

    # Optional — base URL when running behind a reverse proxy (e.g. https://pulsyr.example.com)
    base_url: str = "http://localhost:8000"

    # Optional — AI enrichment (degrade gracefully if absent)
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    # Optional — OAuth sign-in. Same contract as the AI keys: with no keys set the
    # buttons never render and the password form is the only way in, so a
    # self-hosted instance is unaffected by this feature existing.
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    # Open registration. Off by default: being reachable on the internet is not a
    # reason to accept strangers into someone's self-hosted backlog.
    public_signup: bool = False
    # Hosted Free plan. Self-hosted tenants remain unlimited. These are runtime
    # policy values so a limit can be tuned without rewriting subscription rows.
    free_max_projects: int = Field(default=1, ge=1)
    free_max_members: int = Field(default=1, ge=0)
    free_max_tokens_per_project: int = Field(default=3, ge=1)
    free_max_storage_mb: int = Field(default=25, ge=1)
    oauth_rate_limit_attempts: int = Field(default=20, ge=1)
    oauth_rate_limit_window_seconds: int = Field(default=600, ge=60)
    password_rate_limit_attempts: int = Field(default=10, ge=1, le=100)
    password_ip_rate_limit_attempts: int = Field(default=100, ge=1, le=1000)
    webhook_rate_limit_attempts: int = Field(default=600, ge=1, le=10000)
    mcp_rate_limit_attempts: int = Field(default=600, ge=1, le=10000)
    machine_rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    rate_limit_retention_seconds: int = Field(default=86400, ge=3600, le=604800)
    rate_limit_prune_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    # Comma-separated CIDRs for reverse proxies directly connected to Uvicorn.
    # Forwarded address headers are ignored unless the ASGI peer is in this list.
    trusted_proxy_cidrs: str = ""
    request_max_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    webhook_max_body_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    mcp_max_body_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    upload_max_body_bytes: int = Field(default=11 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    # Must match the version printed on pulsyr.dev/terminos/. Signup stamps this on
    # the user row, so a drift here means we recorded consent to a document nobody saw.
    terms_version: str = "2026-08-23"

    # Global fallbacks for webhook secrets (move to per-project settings in a future version)
    sentry_client_secret: str = ""
    github_webhook_secret: str = ""
    # Paddle notification destination secret. Empty on a self-hosted instance: with no
    # secret the billing webhook answers 503 and no paid plan can ever be granted.
    paddle_webhook_secret: str = ""
    # Server-side Paddle key. Empty on a self-hosted install: the billing screen
    # then renders plan and usage and hides every action.
    paddle_api_key: str = ""
    # Public by design: this one is embedded in the page for Paddle.js.
    paddle_client_token: str = ""
    # Selects both the API host and the Paddle.js environment. The value must be
    # exactly "sandbox" or "production": every consumer compares against those
    # strings, so "prod" or "live" would silently mean sandbox. Validated below,
    # together with the keys, because the dangerous direction is a LIVE key
    # against the sandbox host.
    paddle_environment: str = "sandbox"
    sentry_api_token: str = ""
    sentry_org: str = ""

    # Optional application monitoring (separate from the inbound Sentry webhook
    # integration above). PII is disabled and scrubbed again before transport.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    readiness_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    # /metrics is disabled unless this independent bearer token is configured.
    metrics_bearer_token: str = ""

    job_poll_interval_seconds: int = Field(default=10, ge=1, le=300)
    job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    # Each active job owns one database session. Keep this inside the aggregate
    # connection budget across every web replica and worker process.
    job_concurrency: int = Field(default=2, ge=1, le=16)
    job_queue_max_active: int = Field(default=1000, ge=1, le=100000)

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _validate_trusted_proxy_cidrs(cls, value: str) -> str:
        for raw_cidr in value.split(","):
            cidr = raw_cidr.strip()
            if cidr:
                ipaddress.ip_network(cidr, strict=False)
        return value

    @field_validator("metrics_bearer_token")
    @classmethod
    def _validate_metrics_token(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("METRICS_BEARER_TOKEN must not have surrounding whitespace")
        if value and len(value) < _MIN_SECRET_LENGTH:
            raise ValueError("METRICS_BEARER_TOKEN must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _fail_fast_on_insecure_secret(self) -> "Settings":
        if not self.debug and (
            self.secret_key in _PLACEHOLDER_SECRETS or len(self.secret_key) < _MIN_SECRET_LENGTH
        ):
            raise ValueError(
                "SECRET_KEY is a placeholder or shorter than 32 characters — refusing to start "
                "in production (DEBUG=false). Generate one with: "
                "python -c \"import secrets; print(secrets.token_hex(32))\" "
                "and set it in the SECRET_KEY environment variable."
            )
        return self


    @model_validator(mode="after")
    def _paddle_environment_matches_its_keys(self) -> "Settings":
        """Refuse to start on a Paddle environment that contradicts its keys.

        Both consumers of this setting compare against the exact string
        "production" (`paddle._base_url`, and `Paddle.Environment.set` in
        app/static/paddle-checkout.js), so anything else means sandbox. Writing
        "prod" or "live" therefore sends a LIVE api key to the sandbox host,
        where every call 403s, and there is no signal anywhere that says why. It
        is the shape of mistake that only happens on go-live day, which is the
        worst day to spend debugging a silent misconfiguration.

        A key whose prefix we do not recognise is left alone: acting only on
        evidence we understand keeps a future Paddle prefix from bricking boot.
        """
        if self.paddle_environment not in _PADDLE_ENVIRONMENTS:
            raise ValueError(
                f"PADDLE_ENVIRONMENT must be one of {' or '.join(_PADDLE_ENVIRONMENTS)}, "
                f"got {self.paddle_environment!r}. Any other value silently means "
                "sandbox, so a live key would never reach Paddle."
            )
        for name, value in (
            ("PADDLE_API_KEY", self.paddle_api_key),
            ("PADDLE_CLIENT_TOKEN", self.paddle_client_token),
        ):
            for prefix, belongs_to in _PADDLE_KEY_ENVIRONMENT.items():
                if value.startswith(prefix) and belongs_to != self.paddle_environment:
                    raise ValueError(
                        f"{name} is a {belongs_to} credential but PADDLE_ENVIRONMENT is "
                        f"{self.paddle_environment!r}. Refusing to start: the two must "
                        "match or every billing call fails against the wrong host."
                    )
        return self


settings = Settings()
