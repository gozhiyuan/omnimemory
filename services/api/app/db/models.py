"""SQLAlchemy models for the core database tables."""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


DEFAULT_TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[Optional[str]] = mapped_column(String(320), unique=True, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    connections: Mapped[list["DataConnection"]] = relationship(back_populates="user")
    items: Mapped[list["SourceItem"]] = relationship(back_populates="user")


class DataConnection(Base):
    __tablename__ = "data_connections"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="connections")
    items: Mapped[list["SourceItem"]] = relationship(back_populates="connection")


class SourceItem(Base):
    __tablename__ = "source_items"
    __table_args__ = (
        CheckConstraint("item_type IN ('photo','video','audio','document')", name="source_items_item_type_check"),
        CheckConstraint(
            "processing_status IN ('pending','processing','completed','failed')",
            name="source_items_processing_status_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    connection_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("data_connections.id", ondelete="SET NULL"), nullable=True
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    canonical_item_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True
    )
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    phash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    event_time_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    event_time_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    processing_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="items")
    connection: Mapped[Optional[DataConnection]] = relationship(back_populates="items")
    processed_content: Mapped[list["ProcessedContent"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    derived_artifacts: Mapped[list["DerivedArtifact"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ProcessedContent(Base):
    __tablename__ = "processed_content"
    __table_args__ = (
        CheckConstraint(
            "content_role IN ('metadata','caption','transcription','ocr')",
            name="processed_content_role_check",
        ),
        UniqueConstraint("item_id", "content_role", name="processed_content_item_role_idx"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    item_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("source_items.id", ondelete="CASCADE"))
    content_role: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    item: Mapped[SourceItem] = relationship(back_populates="processed_content")


class DerivedArtifact(Base):
    __tablename__ = "derived_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "source_item_id",
            "artifact_type",
            "producer",
            "producer_version",
            "input_fingerprint",
            name="derived_artifacts_unique_idx",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    source_item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_items.id", ondelete="CASCADE")
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    producer: Mapped[str] = mapped_column(String(128), nullable=False)
    producer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    storage_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    item: Mapped[SourceItem] = relationship(back_populates="derived_artifacts")


class ProcessedContext(Base):
    __tablename__ = "processed_contexts"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    context_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    entities: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    location: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    event_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    start_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_episode: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    source_item_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PGUUID(as_uuid=True)), nullable=False)
    merged_from_context_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    vector_text: Mapped[str] = mapped_column(Text, nullable=False)
    processor_versions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )


class MemoryNode(Base):
    __tablename__ = "memory_nodes"
    __table_args__ = (
        UniqueConstraint("user_id", "node_type", "name", name="memory_nodes_user_type_name_idx"),
        Index("memory_nodes_user_type_idx", "user_id", "node_type"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class MemoryEdge(Base):
    __tablename__ = "memory_edges"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_node_id",
            "target_node_id",
            "relation_type",
            name="memory_edges_unique_idx",
        ),
        Index("memory_edges_user_source_idx", "user_id", "source_node_id"),
        Index("memory_edges_user_target_idx", "user_id", "target_node_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    source_node_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memory_nodes.id", ondelete="CASCADE")
    )
    target_node_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memory_nodes.id", ondelete="CASCADE")
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    strength: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"))
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    last_connected: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    source_item_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True
    )
    source_context_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("processed_contexts.id", ondelete="SET NULL"), nullable=True
    )


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("user_id", "summary_date", name="daily_summaries_user_date_idx"),)

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    summary_date: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    user: Mapped[User] = relationship()


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (Index("chat_sessions_user_idx", "user_id", "last_message_at"),)

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','system')",
            name="chat_messages_role_check",
        ),
        Index("chat_messages_session_idx", "session_id", "created_at"),
        Index("chat_messages_user_idx", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")


class ChatFeedback(Base):
    __tablename__ = "chat_feedback"
    __table_args__ = (
        CheckConstraint("rating IN (-1, 1)", name="chat_feedback_rating_check"),
        UniqueConstraint("user_id", "message_id", name="chat_feedback_user_message_idx"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    message_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="CASCADE")
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )


class ChatAttachment(Base):
    __tablename__ = "chat_attachments"
    __table_args__ = (
        Index("chat_attachments_session_idx", "session_id", "created_at"),
        Index("chat_attachments_message_idx", "message_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    message_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
class AiUsageEvent(Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        Index("ai_usage_events_user_created_idx", "user_id", "created_at"),
        Index("ai_usage_events_item_created_idx", "item_id", "created_at"),
        Index("ai_usage_events_model_idx", "model"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    item_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    user: Mapped[User] = relationship()


__all__ = [
    "Base",
    "User",
    "DataConnection",
    "SourceItem",
    "ProcessedContent",
    "DerivedArtifact",
    "ProcessedContext",
    "DailySummary",
    "AiUsageEvent",
    "DEFAULT_TEST_USER_ID",
]
