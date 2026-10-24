"""Runtime configuration, read from environment variables (or a local .env file)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "production" makes the voice webhook refuse to run without a shared secret.
    environment: str = "development"
    log_level: str = "INFO"
    # When false, collected patient data is masked in logs.
    log_pii: bool = True

    # SQLite by default; any SQLAlchemy URL works (Postgres in production).
    database_url: str = "sqlite:///./data/patients.db"
    seed_demo_data: bool = False

    # Shared secret the voice platform sends in the X-Vapi-Secret header.
    vapi_webhook_secret: str = ""
    # Optional: if set, /patients endpoints require an X-API-Key header.
    api_key: str = ""

    clinic_name: str = "Riverside Family Clinic"

    # Used only by scripts/create_vapi_assistant.py.
    public_base_url: str = ""
    vapi_api_key: str = ""
    vapi_assistant_id: str = ""
    vapi_voice_provider: str = "openai"
    vapi_voice_id: str = "alloy"
    vapi_llm_provider: str = "openai"
    vapi_llm_model: str = "gpt-4o"
    # "en" is the most reliable for U.S. callers. "multi" adds Spanish but can mis-detect
    # accented English as another language (see app/voice/prompt.py).
    vapi_transcriber_language: str = "en"

    @property
    def bilingual(self) -> bool:
        return self.vapi_transcriber_language == "multi"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def sqlalchemy_url(self) -> str:
        """Normalise the URL styles hosting platforms hand out to the psycopg 3 driver."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
