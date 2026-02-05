"""Tests for RAG entity normalization functions."""

import pytest

from app.rag import _normalize_entity_list


# ---------------------------------------------------------------------------
# _normalize_entity_list tests
# ---------------------------------------------------------------------------


def test_normalize_entity_list_basic():
    """Basic entity list normalization."""
    result = _normalize_entity_list(["Alice", "Bob", "Charlie"])
    assert result == ["Alice", "Bob", "Charlie"]


def test_normalize_entity_list_dedup():
    """Duplicate entities should be removed."""
    result = _normalize_entity_list(["Alice", "Bob", "Alice"])
    assert result == ["Alice", "Bob"]


def test_normalize_entity_list_filter_empty():
    """Empty strings should be filtered."""
    result = _normalize_entity_list(["Alice", "", "Bob", "   "])
    assert result == ["Alice", "Bob"]


def test_normalize_entity_list_filter_none():
    """None values should be filtered."""
    result = _normalize_entity_list(["Alice", None, "Bob"])
    assert result == ["Alice", "Bob"]


def test_normalize_entity_list_not_a_list():
    """Non-list input returns empty list."""
    assert _normalize_entity_list("not a list") == []
    assert _normalize_entity_list(42) == []
    assert _normalize_entity_list(None) == []


def test_normalize_entity_list_type_coercion():
    """Non-string values should be coerced to strings."""
    result = _normalize_entity_list(["Alice", 123, "Bob"])
    assert "123" in result


def test_normalize_entity_list_whitespace_trimming():
    """Whitespace should be trimmed from values."""
    result = _normalize_entity_list(["  Alice  ", "Bob\n"])
    assert result == ["Alice", "Bob"]
