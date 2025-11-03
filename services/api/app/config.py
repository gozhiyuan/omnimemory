"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Literal, Optional

from pydantic import AnyUrl, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the API service."""

    _env_paths = [
        ".env.dev",
        ".env",
        "../.env.dev",
        "../.env",
        "../../.env.dev",
        "../../.env",
        "../../../.env.dev",
        "../../../.env",
    ]

    model_config = SettingsConfigDict(
        env_file=_env_paths,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App metadata
    api_title: str = "Lifelog API"
    api_version: str = "0.1.0"

    # Storage + data services
    redis_url: str = Field(..., description="Redis connection URL")
    qdrant_url: AnyUrl = Field(..., description="Qdrant HTTP endpoint")
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "lifelog"
    postgres_user: str = "lifelog"
    postgres_password: str = "lifelog"

    # Supabase (optional for local filesystem development)
    supabase_url: Optional[AnyUrl] = Field(default=None)
    supabase_service_role_key: Optional[str] = Field(default=None)

    # Storage buckets/strategy
    storage_provider: Literal["supabase", "memory"] = "memory"
    bucket_originals: str = "originals"
    bucket_previews: str = "previews"
    bucket_thumbnails: str = "thumbnails"

    presigned_url_ttl_seconds: int = 15 * 60

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_strings(cls, values):
        for key in ("supabase_url", "supabase_service_role_key"):
            if values.get(key) == "":
                values[key] = None
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""

    return Settings()
