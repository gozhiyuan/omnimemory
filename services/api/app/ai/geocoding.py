"""Geocoding helpers for location enrichment."""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from loguru import logger

from ..config import Settings


def _pick_component(components: list[dict], target: str) -> Optional[str]:
    for component in components:
        types = component.get("types") or []
        if target in types:
            return component.get("long_name") or component.get("short_name")
    return None


async def reverse_geocode(
    lat: float,
    lng: float,
    settings: Settings,
) -> Dict[str, Any]:
    provider = settings.maps_geocoding_provider
    if provider == "none":
        return {"status": "disabled", "reason": "provider_disabled"}
    if provider != "google_maps":
        return {"status": "disabled", "reason": f"unsupported_provider:{provider}"}

    api_key = settings.maps_google_api_key
    if not api_key:
        return {"status": "disabled", "reason": "missing_api_key"}

    params = {"latlng": f"{lat},{lng}", "key": api_key}
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    async with httpx.AsyncClient(timeout=settings.maps_timeout_seconds) as client:
        response = await client.get(url, params=params)
    if response.status_code >= 400:
        logger.warning("Geocoding failed status={} body={}", response.status_code, response.text)
        return {
            "status": "error",
            "error": f"maps_api_status_{response.status_code}",
        }
    payload = response.json()
    status = payload.get("status")
    if status != "OK":
        return {"status": "error", "error": status or "unknown_error"}
    result = (payload.get("results") or [{}])[0]
    components = result.get("address_components") or []
    return {
        "status": "ok",
        "lat": lat,
        "lng": lng,
        "formatted_address": result.get("formatted_address"),
        "place_id": result.get("place_id"),
        "types": result.get("types") or [],
        "components": {
            "street_number": _pick_component(components, "street_number"),
            "route": _pick_component(components, "route"),
            "locality": _pick_component(components, "locality"),
            "sublocality": _pick_component(components, "sublocality"),
            "administrative_area_level_1": _pick_component(components, "administrative_area_level_1"),
            "country": _pick_component(components, "country"),
            "postal_code": _pick_component(components, "postal_code"),
        },
    }
