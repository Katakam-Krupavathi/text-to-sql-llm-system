from functools import lru_cache
from typing import List, Literal, Optional, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    APP_NAME: str = "text-to-sql-agent"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/text_to_sql_db"
    READONLY_DATABASE_URL: Optional[str] = "postgresql+asyncpg://sql_readonly:readonly_password@localhost:5432/text_to_sql_db"
    SQL_DIALECT: str = "postgres"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30

    # Rate Limiting & Safety
    RATE_LIMIT_PER_MINUTE: int = 60
    MAX_QUERY_ROWS: int = 500
    QUERY_TIMEOUT_SECONDS: int = 5
    AUDIT_LOG_DB_PATH: str = "vector_cache/audit.db"

    # Multi-LLM Provider Router Settings
    LLM_PROVIDER: str = "openai"  # Primary default provider
    LLM_PROVIDER_ORDER: Union[List[str], str] = ["anthropic", "openai", "gemini", "groq"]
    LLM_TEMPERATURE: float = 0.0
    MAX_RETRIES: int = 3

    # Provider Models & API Keys
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o"

    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20240620"

    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-1.5-pro"

    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Backward compatibility
    LLM_MODEL: str = "gpt-4o"

    # Retrieval / Embedding Backend
    EMBEDDING_BACKEND: Literal["keyword", "openai"] = "keyword"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    @field_validator("LLM_PROVIDER_ORDER", mode="before")
    @classmethod
    def parse_provider_order(cls, v):
        if isinstance(v, str):
            return [item.strip().lower() for item in v.split(",") if item.strip()]
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def effective_readonly_db_url(self) -> str:
        """Returns the read-only DB URL if set, otherwise falls back to DATABASE_URL."""
        return self.READONLY_DATABASE_URL or self.DATABASE_URL

    def get_provider_api_key(self, provider: str) -> Optional[str]:
        mapping = {
            "openai": self.OPENAI_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "gemini": self.GEMINI_API_KEY,
            "groq": self.GROQ_API_KEY,
        }
        return mapping.get(provider.lower())

    def get_eligible_providers(self) -> List[str]:
        """Returns list of providers from LLM_PROVIDER_ORDER that have configured API keys."""
        order = self.LLM_PROVIDER_ORDER if isinstance(self.LLM_PROVIDER_ORDER, list) else ["openai", "anthropic", "gemini", "groq"]
        eligible = []
        for p in order:
            p_clean = p.strip().lower()
            key = self.get_provider_api_key(p_clean)
            if key and key.strip():
                eligible.append(p_clean)
        # If no provider has a key configured, return the provider order as fallback (for mock/test environments)
        return eligible if eligible else order


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
