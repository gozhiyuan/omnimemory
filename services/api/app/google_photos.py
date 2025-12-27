"""Google Photos OAuth helpers and API integration utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from loguru import logger

from .config import Settings


AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
MEDIA_ITEMS_ENDPOINT = "https://photoslibrary.googleapis.com/v1/mediaItems"
PHOTO_SCOPE = "https://www.googleapis.com/auth/photoslibrary.readonly"


def _require_google_settings(settings: Settings) -> None:
    if not settings.google_photos_client_id or not settings.google_photos_client_secret:
        raise RuntimeError("Google Photos client credentials are not configured")
    if not settings.google_photos_redirect_uri:
        raise RuntimeError("Google Photos redirect URI is not configured")


def build_google_photos_auth_url(settings: Settings, state: Optional[str] = None) -> str:
    _require_google_settings(settings)
    params = {
        "client_id": settings.google_photos_client_id,
        "redirect_uri": settings.google_photos_redirect_uri,
        "response_type": "code",
        "scope": PHOTO_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def _expires_at_from_now(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def exchange_google_photos_code(settings: Settings, code: str) -> Dict[str, Any]:
    _require_google_settings(settings)
    payload = {
        "client_id": settings.google_photos_client_id,
        "client_secret": settings.google_photos_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.google_photos_redirect_uri,
    }
    response = httpx.post(TOKEN_ENDPOINT, data=payload, timeout=15)
    response.raise_for_status()
    data = response.json()
    expires_in = int(data.get("expires_in", 0))
    if expires_in:
        data["expires_at"] = _expires_at_from_now(expires_in)
    return data


def refresh_google_photos_token(settings: Settings, refresh_token: str) -> Dict[str, Any]:
    _require_google_settings(settings)
    payload = {
        "client_id": settings.google_photos_client_id,
        "client_secret": settings.google_photos_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    response = httpx.post(TOKEN_ENDPOINT, data=payload, timeout=15)
    response.raise_for_status()
    data = response.json()
    expires_in = int(data.get("expires_in", 0))
    if expires_in:
        data["expires_at"] = _expires_at_from_now(expires_in)
    return data


def token_is_expired(oauth_payload: Dict[str, Any]) -> bool:
    expires_at = _parse_datetime(oauth_payload.get("expires_at"))
    if not expires_at:
        return True
    now = datetime.now(timezone.utc)
    return now + timedelta(seconds=60) >= expires_at


def update_oauth_payload(existing: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**existing, **update}
    if "refresh_token" not in merged and existing.get("refresh_token"):
        merged["refresh_token"] = existing["refresh_token"]
    return merged


async def ensure_valid_access_token(settings: Settings, oauth_payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_google_settings(settings)
    if oauth_payload.get("access_token") and not token_is_expired(oauth_payload):
        return oauth_payload

    refresh_token = oauth_payload.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Google Photos refresh token missing for connection")

    logger.info("Refreshing Google Photos access token")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": settings.google_photos_client_id,
                "client_secret": settings.google_photos_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    response.raise_for_status()
    payload = response.json()
    expires_in = int(payload.get("expires_in", 0))
    if expires_in:
        payload["expires_at"] = _expires_at_from_now(expires_in)
    return update_oauth_payload(oauth_payload, payload)


__all__ = [
    "AUTH_ENDPOINT",
    "MEDIA_ITEMS_ENDPOINT",
    "PHOTO_SCOPE",
    "build_google_photos_auth_url",
    "exchange_google_photos_code",
    "refresh_google_photos_token",
    "token_is_expired",
    "update_oauth_payload",
    "ensure_valid_access_token",
]
