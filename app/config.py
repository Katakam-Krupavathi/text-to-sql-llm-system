from functools import lru_cache
from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    APP_NAME: str = "text-to-sql-agent"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/text_to_sql_db"
    READONLY_DATABASE_URL: Optional[str] = None
    SQL_DIALECT: str = "postgres"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30

    # LLM Settings
    LLM_PROVIDER: Literal["openai", "anthropic"] = "openai"
    LLM_MODEL: str = "gpt-4o"
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    LLM_TEMPERATURE: float = 0.0
    MAX_RETRIES: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def effective_readonly_db_url(self) -> str:
        """Returns the read-only DB URL if set, otherwise falls back to DATABASE_URL."""
        return self.READONLY_DATABASE_URL or self.DATABASE_URL


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
