"""Response grounding verifier for chat."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Optional

from ..ai import summarize_text_with_gemini
from ..config import Settings, get_settings
from ..db.models import ProcessedContext
from ..pipeline.utils import ensure_tz_aware


@dataclass
class VerificationResult:
    is_grounded: bool
    confidence: float
    unsupported_claims: list[str]
    suggested_followup: Optional[str] = None


def _extract_json(text: str) -> Optional[dict]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _format_evidence(entries: list[tuple[ProcessedContext, dict]], limit: int = 8) -> str:
    lines: list[str] = []
    for context, hit in entries[:limit]:
        payload = hit.get("payload") or {}
        context_type = payload.get("context_type") or context.context_type or "unknown"
        title = (context.title or "").strip()
        summary = (context.summary or "").strip()
        if len(summary) > 220:
            summary = summary[:220].rstrip() + "..."
        event_time = context.event_time_utc or context.start_time_utc or context.created_at
        if event_time:
            event_time = ensure_tz_aware(event_time).isoformat()
        line_parts = [f"[{context.id}]", f"type={context_type}"]
        if event_time:
            line_parts.append(f"time={event_time}")
        if title:
            line_parts.append(f"title={title}")
        if summary:
            line_parts.append(f"summary={summary}")
        lines.append(" | ".join(line_parts))
    return "\n".join(lines)


def _fallback_result() -> VerificationResult:
    return VerificationResult(
        is_grounded=True,
        confidence=0.5,
        unsupported_claims=[],
        suggested_followup=None,
    )


def _default_followup() -> str:
    return "I could not find enough evidence for that. Can you clarify the time period or event?"


async def verify_response(
    response: str,
    evidence_entries: list[tuple[ProcessedContext, dict]],
    query: str,
    settings: Optional[Settings] = None,
) -> VerificationResult:
    settings = settings or get_settings()
    if not settings.chat_verification_enabled:
        return _fallback_result()
    if settings.chat_provider != "gemini" or not settings.gemini_api_key:
        return _fallback_result()
    if not evidence_entries:
        return VerificationResult(
            is_grounded=False,
            confidence=0.0,
            unsupported_claims=["No evidence available"],
            suggested_followup=_default_followup(),
        )

    evidence_text = _format_evidence(evidence_entries)
    prompt = (
        "You are a grounding verifier. Determine whether the assistant response is fully supported "
        "by the evidence below. Be strict.\n\n"
        "User question:\n"
        f"{query}\n\n"
        "Assistant response:\n"
        f"{response}\n\n"
        "Evidence:\n"
        f"{evidence_text}\n\n"
        "Return JSON ONLY with fields: is_grounded (true/false), confidence (0-1), "
        "unsupported_claims (array of strings), suggested_followup (string or null)."
    )

    llm = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.chat_model,
        temperature=0.0,
        max_output_tokens=256,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="chat_verify",
    )
    parsed = llm.get("parsed")
    if not isinstance(parsed, dict):
        parsed = _extract_json(llm.get("raw_text", ""))
    if not isinstance(parsed, dict):
        return _fallback_result()

    is_grounded = bool(parsed.get("is_grounded", True))
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    unsupported = parsed.get("unsupported_claims", [])
    if not isinstance(unsupported, list):
        unsupported = []
    suggested = parsed.get("suggested_followup")
    if suggested is not None and not isinstance(suggested, str):
        suggested = None

    if not is_grounded and not suggested:
        suggested = _default_followup()

    return VerificationResult(
        is_grounded=is_grounded,
        confidence=confidence,
        unsupported_claims=[str(item) for item in unsupported],
        suggested_followup=suggested,
    )
