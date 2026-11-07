"""Runtime configuration, read from environment variables only (never hardcoded secrets)."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "development"

    # Storage
    database_url: str = "postgresql+asyncpg://scout:scout@localhost:5432/scout"
    mongo_url: str | None = None  # unset -> raw payloads go to .devdata/raw (dev only)
    mongo_db: str = "scout"
    redis_url: str = "redis://localhost:6379/0"

    # Auth (Supabase). The API accepts only tokens for OWNER_EMAIL.
    supabase_url: str | None = None
    owner_email: str | None = None
    # Local development only: lets the dashboard run before Supabase is set up.
    # The API refuses to start if this is set while ENV=production.
    dev_auth_token: SecretStr | None = None

    # Fernet key used to encrypt stored tokens (see docs/SETUP.md for how to generate one).
    encryption_key: SecretStr | None = None

    # Telegram
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_webhook_secret: SecretStr | None = None

    # Aggregators (each one is off until its key is set and it is enabled in settings)
    adzuna_app_id: str | None = None
    adzuna_app_key: SecretStr | None = None

    # Outbound HTTP identity: honest user agent, as the spec requires.
    http_user_agent: str = "ScoutJobBot/0.1 (personal job search; single user)"
    http_timeout_seconds: float = 15.0

    # Gemini (Phase 3+). Model names come from env, never hardcoded.
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    gemini_embedding_model: str | None = None
    # Comma-separated models to try, in order, when the main one stays overloaded (503).
    gemini_fallback_models: str = ""
    # Langfuse tracing (optional): every Gemini call becomes a trace when both keys are set.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"  # EU; https://us.cloud.langfuse.com for US

    # Comma-separated web origins allowed to call the API.
    cors_origins: str = "http://localhost:3000"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
