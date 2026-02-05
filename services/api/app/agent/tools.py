"""ADK tool wrappers backed by Memory API functions."""

from __future__ import annotations

from datetime import date as Date
from typing import Optional
from uuid import UUID

from ..ai import summarize_text_with_gemini
from ..chat import build_query_plan_with_parsed
from ..config import Settings, get_settings


class AgentMemoryTrace:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.hits: list[dict] = []

    def add_hit(self, hit: dict, score: Optional[float] = None) -> None:
        context_id = str(hit.get("context_id") or "").strip()
        if not context_id or context_id in self._seen:
            return
        entry = dict(hit)
        entry["context_id"] = context_id
        if score is not None and entry.get("score") is None:
            entry["score"] = score
        self.hits.append(entry)
        self._seen.add(context_id)

    def add_context_ids(
        self,
        context_ids: list[str],
        *,
        score: Optional[float] = None,
        extra: Optional[dict] = None,
    ) -> None:
        for context_id in context_ids:
            hit = {"context_id": str(context_id)}
            if extra:
                hit.update(extra)
            self.add_hit(hit, score=score)


def _memory_routes():
    from ..routes import memory as memory_routes

    return memory_routes


def _parse_date(value: Optional[str]) -> Optional[Date]:
    if not value:
        return None
    return Date.fromisoformat(value)


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
    try:
        import json

        return json.loads(text)
    except Exception:
        return None


def build_memory_tools(
    *,
    user_id: UUID,
    session,
    tz_offset_minutes: Optional[int],
    settings: Optional[Settings] = None,
    trace: Optional[AgentMemoryTrace] = None,
):
    settings = settings or get_settings()

    async def search_memories(
        query: str,
        limit: int = 10,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        context_types: Optional[list[str]] = None,
    ) -> dict:
        """Search memories by natural language with optional date range."""
        memory_routes = _memory_routes()
        time_range = None
        if date_from:
            start = _parse_date(date_from)
            end = _parse_date(date_to) or start
            time_range = memory_routes.MemoryTimeRange(start=start, end=end)
        request = memory_routes.MemorySearchRequest(
            query=query,
            limit=limit,
            time_range=time_range,
            context_types=context_types,
            tz_offset_minutes=tz_offset_minutes,
            debug=False,
        )
        response = await memory_routes.search_memories(
            request=request,
            user_id=user_id,
            session=session,
        )
        payload = response.model_dump(mode="json")
        if trace:
            for hit in payload.get("hits", []) or []:
                if isinstance(hit, dict):
                    trace.add_hit(hit, score=hit.get("score"))
        return payload

    async def get_timeline(date: str) -> list[dict]:
        """Get timeline for a specific local date (YYYY-MM-DD)."""
        memory_routes = _memory_routes()
        timeline = await memory_routes.get_memory_timeline(
            date=date,
            user_id=user_id,
            session=session,
            tz_offset_minutes=tz_offset_minutes,
            limit=200,
        )
        payload = [day.model_dump(mode="json") for day in timeline]
        if trace:
            for day in payload:
                for episode in day.get("episodes", []) or []:
                    context_ids = episode.get("context_ids") or []
                    if isinstance(context_ids, list) and context_ids:
                        trace.add_context_ids(
                            [str(cid) for cid in context_ids if cid],
                            score=0.5,
                            extra={"context_type": episode.get("context_type")},
                        )
        return payload

    async def get_episode(episode_id: str) -> dict:
        """Get episode detail by episode ID."""
        memory_routes = _memory_routes()
        episode = await memory_routes.get_memory_episode(
            episode_id=episode_id,
            user_id=user_id,
            session=session,
        )
        return episode.model_dump(mode="json")

    async def get_context(context_id: str) -> dict:
        """Get a context detail by context ID."""
        memory_routes = _memory_routes()
        context = await memory_routes.get_memory_context(
            context_id=context_id,
            user_id=user_id,
            session=session,
        )
        payload = context.model_dump(mode="json")
        if trace:
            trace.add_hit(
                {"context_id": payload.get("context_id"), "context_type": payload.get("context_type")},
                score=0.7,
            )
        return payload

    async def parse_dates(query: str) -> dict:
        """Parse a query into a date range if possible."""
        plan, _parsed = await build_query_plan_with_parsed(
            query=query,
            history=[],
            tz_offset_minutes=tz_offset_minutes,
            settings=settings,
        )
        if not plan.time_range:
            return {"start": None, "end": None, "grain": None}
        return {
            "start": plan.time_range.start.date().isoformat(),
            "end": plan.time_range.end.date().isoformat(),
            "grain": plan.time_range.grain,
        }

    async def verify_relevance(answer: str, evidence: list[dict], query: str = "") -> dict:
        """Verify whether an answer is grounded in evidence snippets."""
        evidence_lines = []
        for entry in evidence[:8]:
            summary = str(entry.get("summary") or "")
            if len(summary) > 220:
                summary = summary[:220] + "..."
            evidence_lines.append(
                " | ".join(
                    part
                    for part in [
                        f"type={entry.get('context_type')}",
                        f"time={entry.get('event_time_utc')}",
                        f"title={entry.get('title')}",
                        f"summary={summary}",
                    ]
                    if part and part != "type=None"
                )
            )
        prompt = (
            "Check if the assistant answer is supported by the evidence below. "
            "Return JSON with fields: is_grounded (true/false), confidence (0-1), unsupported_claims (array).\n\n"
            f"Query: {query}\n\n"
            f"Answer: {answer}\n\n"
            "Evidence:\n"
            + "\n".join(evidence_lines)
        )
        llm = await summarize_text_with_gemini(
            prompt=prompt,
            settings=settings,
            model=settings.chat_model,
            temperature=0.0,
            max_output_tokens=256,
            timeout_seconds=settings.chat_timeout_seconds,
            step_name="agent_verify",
        )
        parsed = llm.get("parsed")
        if not isinstance(parsed, dict):
            parsed = _extract_json(llm.get("raw_text", "")) or {}
        return parsed

    return [
        search_memories,
        get_timeline,
        get_episode,
        get_context,
        parse_dates,
        verify_relevance,
    ]
