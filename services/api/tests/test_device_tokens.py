"""Tests for device token functions."""

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.routes.devices import (
    _hash_token,
    _derive_device_token,
    _split_upload_url,
)


# ---------------------------------------------------------------------------
# _hash_token tests
# ---------------------------------------------------------------------------


def test_hash_token_basic():
    """Basic token hashing."""
    result = _hash_token("my-token", "my-secret")
    assert isinstance(result, str)
    assert len(result) == 64  # SHA256 hex digest


def test_hash_token_consistency():
    """Same inputs produce same hash."""
    result1 = _hash_token("token", "secret")
    result2 = _hash_token("token", "secret")
    assert result1 == result2


def test_hash_token_different_tokens():
    """Different tokens produce different hashes."""
    result1 = _hash_token("token1", "secret")
    result2 = _hash_token("token2", "secret")
    assert result1 != result2


def test_hash_token_different_secrets():
    """Different secrets produce different hashes."""
    result1 = _hash_token("token", "secret1")
    result2 = _hash_token("token", "secret2")
    assert result1 != result2


def test_hash_token_empty_inputs():
    """Empty inputs still produce valid hash."""
    result = _hash_token("", "")
    assert len(result) == 64


# ---------------------------------------------------------------------------
# _derive_device_token tests
# ---------------------------------------------------------------------------


def test_derive_device_token_basic():
    """Basic device token derivation."""
    device_id = UUID("12345678-1234-5678-1234-567812345678")
    result = _derive_device_token(device_id, "salt123", "secret")
    assert isinstance(result, str)
    # Base64 URL-safe encoded
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in result)


def test_derive_device_token_deterministic():
    """Same inputs produce same token."""
    device_id = UUID("12345678-1234-5678-1234-567812345678")
    result1 = _derive_device_token(device_id, "salt", "secret")
    result2 = _derive_device_token(device_id, "salt", "secret")
    assert result1 == result2


def test_derive_device_token_different_ids():
    """Different device IDs produce different tokens."""
    id1 = UUID("12345678-1234-5678-1234-567812345678")
    id2 = UUID("87654321-4321-8765-4321-876543218765")
    result1 = _derive_device_token(id1, "salt", "secret")
    result2 = _derive_device_token(id2, "salt", "secret")
    assert result1 != result2


def test_derive_device_token_different_salts():
    """Different salts produce different tokens."""
    device_id = UUID("12345678-1234-5678-1234-567812345678")
    result1 = _derive_device_token(device_id, "salt1", "secret")
    result2 = _derive_device_token(device_id, "salt2", "secret")
    assert result1 != result2


def test_derive_device_token_format():
    """Token has expected format (no padding)."""
    device_id = uuid4()
    result = _derive_device_token(device_id, "salt", "secret")
    # Should not have = padding
    assert "=" not in result


# ---------------------------------------------------------------------------
# _split_upload_url tests
# ---------------------------------------------------------------------------


def test_split_upload_url_https():
    """Parse HTTPS URL."""
    url = "https://storage.example.com/bucket/key?signature=abc123"
    host, port, path = _split_upload_url(url)
    assert host == "storage.example.com"
    assert port == 443
    assert path == "/bucket/key?signature=abc123"


def test_split_upload_url_http():
    """Parse HTTP URL."""
    url = "http://localhost/upload/path"
    host, port, path = _split_upload_url(url)
    assert host == "localhost"
    assert port == 80
    assert path == "/upload/path"


def test_split_upload_url_with_port():
    """Parse URL with explicit port."""
    url = "https://storage.example.com:9000/bucket/key"
    host, port, path = _split_upload_url(url)
    assert host == "storage.example.com"
    assert port == 9000
    assert path == "/bucket/key"


def test_split_upload_url_with_query_params():
    """Query params are preserved in path."""
    url = "https://s3.amazonaws.com/bucket/key?X-Amz-Signature=abc&X-Amz-Date=123"
    host, port, path = _split_upload_url(url)
    assert "X-Amz-Signature=abc" in path
    assert "X-Amz-Date=123" in path


def test_split_upload_url_missing_path():
    """URL without path defaults to '/'."""
    url = "https://storage.example.com"
    host, port, path = _split_upload_url(url)
    assert path == "/"


def test_split_upload_url_missing_host():
    """URL without host raises HTTPException."""
    url = "/just/a/path"
    with pytest.raises(HTTPException) as exc_info:
        _split_upload_url(url)
    assert exc_info.value.status_code == 500
    assert "missing host" in exc_info.value.detail.lower()
