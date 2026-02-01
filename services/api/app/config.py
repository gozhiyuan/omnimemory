"""Application configuration using pydantic-settings."""

from functools import lru_cache
import json
from typing import Literal, Optional

from pydantic import AnyUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the API service."""

    _env_paths = [
        ".env",
        ".env.dev",  # Legacy fallback
        "../.env",
        "../.env.dev",
        "../../.env",
        "../../.env.dev",
        "../../../.env",
        "../../../.env.dev",
    ]

    model_config = SettingsConfigDict(
        env_file=_env_paths,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # App metadata
    api_title: str = "Lifelog API"
    api_version: str = "0.1.0"

    # Storage + data services
    redis_url: str = Field(..., description="Redis connection URL")
    qdrant_url: AnyUrl = Field(..., description="Qdrant HTTP endpoint")
    qdrant_collection: str = "lifelog-items-v2"
    embedding_dimension: int = Field(default=3072, ge=1, description="Embedding vector size")
    embedding_provider: Literal["gemini", "none"] = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_batch_size: int = Field(default=16, ge=1)
    embedding_timeout_seconds: int = 30
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "lifelog"
    postgres_user: str = "lifelog"
    postgres_password: str = "lifelog"

    # Supabase (optional for managed storage)
    supabase_url: Optional[AnyUrl] = Field(default=None)
    supabase_service_role_key: Optional[str] = Field(default=None)

    # S3-compatible storage (RustFS/MinIO/AWS)
    s3_endpoint_url: Optional[AnyUrl] = Field(default=None)
    s3_public_url: Optional[AnyUrl] = Field(default=None)  # Public URL for presigned URLs (browser-accessible)
    s3_access_key_id: Optional[str] = None
    s3_secret_access_key: Optional[str] = None
    s3_region: str = "us-east-1"
    s3_force_path_style: bool = True

    # Storage buckets/strategy
    storage_provider: Literal["supabase", "memory", "s3"] = "s3"
    bucket_originals: str = "originals"
    bucket_previews: str = "previews"
    bucket_thumbnails: str = "thumbnails"

    presigned_url_ttl_seconds: int = 15 * 60
    dashboard_cache_ttl_seconds: int = Field(default=60, ge=0)
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    web_app_url: str = "http://localhost:3000"

    # Auth (OIDC)
    auth_enabled: bool = False
    oidc_issuer_url: Optional[AnyUrl] = None
    oidc_audience: Optional[str] = None
    oidc_jwks_url: Optional[AnyUrl] = None
    oidc_user_id_claim: str = "sub"
    oidc_email_claim: str = "email"
    oidc_name_claim: str = "name"
    oidc_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    oidc_leeway_seconds: int = 60

    # Device auth (ESP32)
    device_token_secret: str = Field(default="dev-device-token-secret")
    device_pairing_code_ttl_minutes: int = Field(default=10, ge=1)

    # Google Photos OAuth
    google_photos_client_id: Optional[str] = None
    google_photos_client_secret: Optional[str] = None
    google_photos_redirect_uri: Optional[str] = None
    google_photos_scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
            "https://www.googleapis.com/auth/photoslibrary.readonly",
        ]
    )

    # OCR settings
    ocr_provider: Literal["google_cloud_vision", "none"] = "google_cloud_vision"
    ocr_google_api_key: Optional[str] = None
    ocr_language_hints_raw: str = Field(default="", alias="OCR_LANGUAGE_HINTS")
    ocr_timeout_seconds: int = 15

    # VLM settings
    vlm_provider: Literal["gemini", "none"] = "gemini"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash-lite"
    vlm_temperature: float = 0.2
    vlm_max_output_tokens: int = 2048
    vlm_timeout_seconds: int = 30

    # Chunked transcript + context output sizing
    transcription_max_output_tokens: int = 4096
    transcription_storage_max_bytes: int = Field(default=500_000, ge=1)

    # Video/audio understanding settings
    video_understanding_provider: Literal["gemini", "none"] = "gemini"
    video_understanding_model: str = "gemini-2.5-flash-lite"
    video_understanding_temperature: float = 0.2
    video_understanding_timeout_seconds: int = 60
    video_understanding_max_bytes: int = Field(default=19_000_000, ge=1)
    video_understanding_max_duration_sec: int = Field(default=20 * 60, ge=1)

    audio_understanding_provider: Literal["gemini", "none"] = "gemini"
    audio_understanding_model: str = "gemini-2.5-flash-lite"
    audio_understanding_temperature: float = 0.2
    audio_understanding_timeout_seconds: int = 60
    audio_understanding_max_bytes: int = Field(default=19_000_000, ge=1)
    audio_vad_enabled: bool = True
    audio_vad_silence_db: float = Field(default=-35.0)
    audio_vad_min_silence_sec: float = Field(default=0.4, ge=0.0)
    audio_vad_padding_sec: float = Field(default=0.15, ge=0.0)
    audio_vad_min_segment_sec: float = Field(default=0.8, ge=0.0)

    # Chat/RAG settings
    chat_provider: Literal["gemini", "none"] = "gemini"
    chat_model: str = "gemini-2.5-flash"
    chat_temperature: float = 0.7
    chat_max_output_tokens: int = Field(default=512, ge=64)
    chat_timeout_seconds: int = 30
    chat_context_limit: int = Field(default=12, ge=1)
    chat_history_limit: int = Field(default=6, ge=0)
    chat_entity_extraction_enabled: bool = True

    # Agent settings
    agent_enabled: bool = True
    agent_prompt_model: str = "gemini-2.5-pro"
    agent_prompt_temperature: float = 0.4
    agent_image_provider: Literal["gemini", "none"] = "none"
    agent_image_model: str = "gemini-2.5-flash-image"
    agent_image_timeout_seconds: int = 60

    # Media extraction settings
    media_max_bytes: int = Field(default=1_000_000_000, ge=1)
    media_chunk_target_bytes: int = Field(default=10_000_000, ge=1)
    media_chunk_max_chunks: int = Field(default=240, ge=1)
    video_max_duration_sec: int = Field(default=5 * 60, ge=1)
    audio_max_duration_sec: int = Field(default=60 * 60, ge=1)
    video_chunk_duration_sec: int = Field(default=60, ge=1)
    audio_chunk_duration_sec: int = Field(default=300, ge=1)
    audio_sample_rate_hz: int = Field(default=16000, ge=8000)
    audio_channels: int = Field(default=1, ge=1)
    video_keyframe_mode: Literal["scene", "interval"] = "scene"
    video_scene_threshold: float = Field(default=0.3, ge=0.0)
    video_keyframe_interval_sec: int = Field(default=5, ge=1)
    video_max_keyframes: int = Field(default=60, ge=1)
    video_vlm_max_frames: int = Field(default=24, ge=1)
    video_keyframes_always: bool = True
    video_vlm_concurrency: int = Field(default=2, ge=1)
    video_preview_enabled: bool = False
    video_preview_duration_sec: int = Field(default=8, ge=1)
    video_preview_max_width: int = Field(default=640, ge=64)
    video_preview_fps: int = Field(default=12, ge=1)
    video_preview_bitrate_kbps: int = Field(default=600, ge=50)
    ingest_batch_limit: int = Field(default=200, ge=1)

    # Episode merge + semantic merge settings
    semantic_merge_enabled: bool = True
    semantic_merge_min_jaccard: float = Field(default=0.6, ge=0.0, le=1.0)
    episode_merge_enabled: bool = True
    episode_merge_max_gap_minutes: int = Field(default=90, ge=1)
    episode_merge_similarity_threshold: float = Field(default=0.78, ge=0.0, le=1.0)
    device_episode_merge_window_minutes: int = Field(default=5, ge=0)
    memory_graph_enabled: bool = True

    # Maps/Geocoding settings
    maps_geocoding_provider: Literal["google_maps", "none"] = "google_maps"
    maps_google_api_key: Optional[str] = None
    maps_timeout_seconds: int = 15

    # Pipeline logging
    pipeline_log_details: bool = Field(default=False, alias="PIPELINE_LOG_DETAILS")
    pipeline_reprocess_duplicates: bool = Field(default=False, alias="PIPELINE_REPROCESS_DUPLICATES")
    dedupe_near_window_minutes: int = Field(default=10, ge=1, alias="DEDUPE_NEAR_WINDOW_MINUTES")
    dedupe_near_hamming_threshold: int = Field(
        default=5,
        ge=0,
        alias="DEDUPE_NEAR_HAMMING_THRESHOLD",
    )

    # OpenClaw integration
    openclaw_enabled: bool = False
    openclaw_gateway_url: Optional[str] = None
    openclaw_sync_memory: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_strings(cls, values):
        for key in (
            "supabase_url",
            "supabase_service_role_key",
            "s3_endpoint_url",
            "s3_access_key_id",
            "s3_secret_access_key",
            "ocr_google_api_key",
            "gemini_api_key",
            "ocr_language_hints_raw",
            "maps_google_api_key",
        ):
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

    @property
    def ocr_language_hints(self) -> list[str]:
        raw = (self.ocr_language_hints_raw or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
        return [lang.strip() for lang in raw.split(",") if lang.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""

    return Settings()
