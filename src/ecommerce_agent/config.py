from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Ecommerce Data Analysis Agent"
    environment: str = "local"
    database_url: str = "postgresql://agent:agent@localhost:5432/ecommerce_agent"
    deepseek_api_key: str | None = None
    llm_provider: str = "deepseek"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_max_tokens: int = Field(default=1024, ge=128, le=8192)
    llm_request_timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    sql_max_rows: int = Field(default=200, ge=1, le=1000)
    sql_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    sql_max_length: int = Field(default=20000, ge=1000, le=100000)
    sql_max_retries: int = Field(default=2, ge=0, le=5)
    audit_log_path: str = "logs/audit.jsonl"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
