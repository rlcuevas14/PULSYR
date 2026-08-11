from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_SECRET = "dev-secret-change-in-production"
# Every placeholder we have ever shipped in .env.example or docs — all must fail fast.
_PLACEHOLDER_SECRETS = {_INSECURE_SECRET, "change-me"}
_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # DATABASE_URL is the authoritative variable. docker-compose builds it from DB_PASSWORD;
    # if both are set, DATABASE_URL wins.
    database_url: str = "postgresql+asyncpg://pulso:pulso@db/pulso"
    secret_key: str = _INSECURE_SECRET
    debug: bool = False

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

    # Global fallbacks for webhook secrets (move to per-project settings in a future version)
    sentry_client_secret: str = ""
    github_webhook_secret: str = ""
    sentry_api_token: str = ""
    sentry_org: str = ""

    job_poll_interval_seconds: int = 10
    job_lease_seconds: int = 300

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


settings = Settings()
