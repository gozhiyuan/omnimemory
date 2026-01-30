"""Tests for pipeline utility functions."""

from datetime import datetime, timezone

import pytest

from app.pipeline.utils import (
    hash_bytes,
    hash_text,
    hash_parts,
    parse_iso_datetime,
    parse_exif_datetime,
    ensure_tz_aware,
    hamming_distance_hex,
    extract_keywords,
    build_vector_text,
)


# ---------------------------------------------------------------------------
# hash_bytes tests
# ---------------------------------------------------------------------------


def test_hash_bytes_basic():
    """Basic bytes hashing."""
    result = hash_bytes(b"hello world")
    assert isinstance(result, str)
    assert len(result) == 64  # SHA256 hex digest


def test_hash_bytes_consistency():
    """Same input produces same hash."""
    result1 = hash_bytes(b"test data")
    result2 = hash_bytes(b"test data")
    assert result1 == result2


def test_hash_bytes_different_inputs():
    """Different inputs produce different hashes."""
    result1 = hash_bytes(b"hello")
    result2 = hash_bytes(b"world")
    assert result1 != result2


def test_hash_bytes_empty():
    """Empty bytes should produce valid hash."""
    result = hash_bytes(b"")
    assert len(result) == 64


# ---------------------------------------------------------------------------
# hash_text tests
# ---------------------------------------------------------------------------


def test_hash_text_basic():
    """Basic text hashing."""
    result = hash_text("hello world")
    assert isinstance(result, str)
    assert len(result) == 64


def test_hash_text_utf8():
    """UTF-8 text is handled correctly."""
    result = hash_text("hello \u4e16\u754c")  # "hello 世界"
    assert len(result) == 64


def test_hash_text_consistency():
    """Same input produces same hash."""
    result1 = hash_text("test string")
    result2 = hash_text("test string")
    assert result1 == result2


def test_hash_text_matches_bytes():
    """hash_text and hash_bytes produce same result for UTF-8."""
    text = "hello"
    assert hash_text(text) == hash_bytes(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# hash_parts tests
# ---------------------------------------------------------------------------


def test_hash_parts_basic():
    """Basic parts hashing."""
    result = hash_parts(["part1", "part2", "part3"])
    assert len(result) == 64


def test_hash_parts_filters_none():
    """None values should be filtered out."""
    result1 = hash_parts(["a", "b", "c"])
    result2 = hash_parts(["a", None, "b", None, "c"])
    assert result1 == result2


def test_hash_parts_order_matters():
    """Different order produces different hash."""
    result1 = hash_parts(["a", "b", "c"])
    result2 = hash_parts(["c", "b", "a"])
    assert result1 != result2


def test_hash_parts_empty():
    """Empty parts list produces valid hash."""
    result = hash_parts([])
    assert len(result) == 64


def test_hash_parts_all_none():
    """All None values produces valid hash."""
    result = hash_parts([None, None, None])
    assert len(result) == 64
    # Should be same as empty list
    assert result == hash_parts([])


# ---------------------------------------------------------------------------
# parse_iso_datetime tests
# ---------------------------------------------------------------------------


def test_parse_iso_datetime_with_z():
    """Parse ISO datetime with Z suffix."""
    result = parse_iso_datetime("2025-06-15T10:30:00Z")
    assert result is not None
    assert result.year == 2025
    assert result.month == 6
    assert result.day == 15
    assert result.hour == 10
    assert result.minute == 30
    assert result.tzinfo == timezone.utc


def test_parse_iso_datetime_with_offset():
    """Parse ISO datetime with +00:00 offset."""
    result = parse_iso_datetime("2025-06-15T10:30:00+00:00")
    assert result is not None
    assert result.tzinfo is not None


def test_parse_iso_datetime_with_positive_offset():
    """Parse ISO datetime with positive offset."""
    result = parse_iso_datetime("2025-06-15T10:30:00+05:30")
    assert result is not None
    assert result.tzinfo is not None


def test_parse_iso_datetime_naive():
    """Parse naive ISO datetime (adds UTC)."""
    result = parse_iso_datetime("2025-06-15T10:30:00")
    assert result is not None
    assert result.tzinfo == timezone.utc


def test_parse_iso_datetime_invalid():
    """Invalid datetime returns None."""
    assert parse_iso_datetime("not-a-datetime") is None
    assert parse_iso_datetime("2025-13-45T99:99:99") is None


def test_parse_iso_datetime_empty():
    """Empty string returns None."""
    assert parse_iso_datetime("") is None
    assert parse_iso_datetime(None) is None


# ---------------------------------------------------------------------------
# parse_exif_datetime tests
# ---------------------------------------------------------------------------


def test_parse_exif_datetime_valid():
    """Parse valid EXIF datetime format."""
    result = parse_exif_datetime("2025:06:15 10:30:45")
    assert result is not None
    assert result.year == 2025
    assert result.month == 6
    assert result.day == 15
    assert result.hour == 10
    assert result.minute == 30
    assert result.second == 45


def test_parse_exif_datetime_invalid():
    """Invalid EXIF datetime returns None."""
    assert parse_exif_datetime("2025-06-15 10:30:45") is None
    assert parse_exif_datetime("not-a-datetime") is None


def test_parse_exif_datetime_empty():
    """Empty string returns None."""
    assert parse_exif_datetime("") is None
    assert parse_exif_datetime(None) is None


# ---------------------------------------------------------------------------
# ensure_tz_aware tests
# ---------------------------------------------------------------------------


def test_ensure_tz_aware_naive_datetime():
    """Naive datetime gets UTC timezone."""
    dt = datetime(2025, 6, 15, 10, 30, 0)
    result = ensure_tz_aware(dt)
    assert result.tzinfo == timezone.utc
    assert result.year == 2025
    assert result.month == 6


def test_ensure_tz_aware_already_aware():
    """Aware datetime is unchanged."""
    dt = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
    result = ensure_tz_aware(dt)
    assert result is dt  # Same object


def test_ensure_tz_aware_preserves_values():
    """Ensure all datetime values are preserved."""
    dt = datetime(2025, 6, 15, 10, 30, 45, 123456)
    result = ensure_tz_aware(dt)
    assert result.year == dt.year
    assert result.month == dt.month
    assert result.day == dt.day
    assert result.hour == dt.hour
    assert result.minute == dt.minute
    assert result.second == dt.second
    assert result.microsecond == dt.microsecond


# ---------------------------------------------------------------------------
# hamming_distance_hex tests
# ---------------------------------------------------------------------------


def test_hamming_distance_zero():
    """Identical hashes have zero distance."""
    assert hamming_distance_hex("abcd1234", "abcd1234") == 0


def test_hamming_distance_max():
    """Completely different hashes have max distance."""
    # All 0s vs all 1s
    assert hamming_distance_hex("0000", "ffff") == 16


def test_hamming_distance_one_bit():
    """Single bit difference."""
    # 0x0 = 0000, 0x1 = 0001
    assert hamming_distance_hex("0", "1") == 1


def test_hamming_distance_invalid():
    """Invalid hex returns None."""
    assert hamming_distance_hex("not-hex", "1234") is None
    assert hamming_distance_hex("1234", "not-hex") is None


def test_hamming_distance_symmetric():
    """Distance is symmetric."""
    dist1 = hamming_distance_hex("abcd", "1234")
    dist2 = hamming_distance_hex("1234", "abcd")
    assert dist1 == dist2


# ---------------------------------------------------------------------------
# extract_keywords tests
# ---------------------------------------------------------------------------


def test_extract_keywords_basic():
    """Extract keywords from text."""
    result = extract_keywords("The quick brown fox jumps over the lazy dog")
    assert "quick" in result
    assert "brown" in result
    assert "fox" in result


def test_extract_keywords_filters_short():
    """Words <= 2 chars are filtered."""
    result = extract_keywords("A big cat is on the mat")
    assert "a" not in result
    assert "is" not in result
    assert "on" not in result
    # "the" has 3 chars so it passes the filter
    assert "big" in result
    assert "cat" in result
    assert "mat" in result


def test_extract_keywords_limit():
    """Respects limit parameter."""
    result = extract_keywords("one two three four five six seven eight nine ten", limit=5)
    assert len(result) <= 5


def test_extract_keywords_dedup():
    """Duplicate keywords are removed."""
    result = extract_keywords("cat cat cat dog dog cat")
    assert result.count("cat") == 1
    assert result.count("dog") == 1


def test_extract_keywords_lowercase():
    """Keywords are lowercased."""
    result = extract_keywords("Hello WORLD HeLLo")
    assert "hello" in result
    assert "world" in result
    assert "Hello" not in result


def test_extract_keywords_empty():
    """Empty text returns empty list."""
    assert extract_keywords("") == []


def test_extract_keywords_special_chars():
    """Special characters are handled."""
    result = extract_keywords("hello-world, foo.bar! baz?qux")
    assert "hello" in result
    assert "world" in result


# ---------------------------------------------------------------------------
# build_vector_text tests
# ---------------------------------------------------------------------------


def test_build_vector_text_full():
    """Build text with all components."""
    result = build_vector_text(
        title="My Photo",
        summary="A beautiful sunset over the ocean",
        keywords=["sunset", "ocean", "beach"]
    )
    assert "My Photo" in result
    assert "A beautiful sunset over the ocean" in result
    assert "Keywords:" in result
    assert "sunset" in result


def test_build_vector_text_no_keywords():
    """Build text without keywords."""
    result = build_vector_text(
        title="My Photo",
        summary="A sunset photo",
        keywords=[]
    )
    assert "My Photo" in result
    assert "A sunset photo" in result
    assert "Keywords:" not in result


def test_build_vector_text_empty_parts():
    """Empty title or summary are handled."""
    result = build_vector_text(title="", summary="Just a summary", keywords=[])
    assert "Just a summary" in result


def test_build_vector_text_whitespace():
    """Whitespace is stripped from parts."""
    result = build_vector_text(
        title="  My Title  ",
        summary="  My Summary  ",
        keywords=["tag1", "tag2"]
    )
    assert "My Title" in result
    assert "My Summary" in result
