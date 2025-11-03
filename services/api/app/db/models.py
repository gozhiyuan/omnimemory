"""SQLAlchemy models for the core database tables."""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
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
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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
    processed_content: Mapped[list["ProcessedContent"]] = relationship(back_populates="item")


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


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("user_id", "summary_date", name="daily_summaries_user_date_idx"),)

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    summary_date: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
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
    "DailySummary",
    "DEFAULT_TEST_USER_ID",
]
