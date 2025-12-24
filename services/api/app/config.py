"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Literal, Optional

from pydantic import AnyUrl, Field, field_validator, model_validator
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
    qdrant_collection: str = "lifelog-items"
    embedding_dimension: int = Field(default=1536, ge=1, description="Embedding vector size")
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
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_strings(cls, values):
        for key in ("supabase_url", "supabase_service_role_key"):
            if values.get(key) == "":
                values[key] = None
        return values

    @field_validator("bucket_originals", "bucket_previews", "bucket_thumbnails", mode="before")
    @classmethod
    def _trim_bucket_names(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""

    return Settings()
