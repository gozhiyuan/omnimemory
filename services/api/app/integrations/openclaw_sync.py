"""Sync OmniMemory daily summaries to OpenClaw memory files.

This module syncs daily summaries and episodes to OpenClaw's memory directory
(~/.openclaw/memory/*.md) so OpenClaw can reference them naturally without
making API calls.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class OpenClawMemorySync:
    """Syncs OmniMemory content to OpenClaw's memory files.

    Appends to OpenClaw's workspace memory files (not the vector DB).
    Path: ~/.openclaw/workspace/memory/YYYY-MM-DD.md

    OpenClaw reads these daily files automatically (today + yesterday).
    We append with a separator to avoid overwriting OpenClaw's own notes.
    """

    # Separator to distinguish OmniMemory content
    SEPARATOR = "\n---\n\n"
    MARKER = "<!-- omnimemory-sync -->"
    ENTRY_START_PREFIX = "<!-- omnimemory-entry:"
    ENTRY_END = "<!-- /omnimemory-entry -->"

    def __init__(
        self,
        openclaw_workspace: str = "~/.openclaw",
        enabled: bool = False,
    ):
        self.workspace = Path(openclaw_workspace).expanduser()
        # Use workspace/memory/ where OpenClaw reads daily notes
        self.memory_dir = self.workspace / "workspace" / "memory"
        self.enabled = enabled

    def sync_daily_summary(
        self,
        user_id: str,
        summary_date: date,
        summary: str,
        episodes: list[dict[str, Any]],
        highlights: list[str] | None = None,
    ) -> bool:
        """Sync a day's summary to OpenClaw's memory file.

        Appends to existing file (or creates new) with a marker to identify
        OmniMemory content. If OmniMemory section exists, it's replaced.

        Args:
            user_id: The user's ID (for logging/multi-user support)
            summary_date: The date of the summary
            summary: The summary text
            episodes: List of episode dicts with title, summary, start_time, end_time
            highlights: Optional list of highlight strings

        Returns:
            True if sync succeeded, False otherwise
        """
        if not self.enabled:
            return False

        try:
            # Ensure memory directory exists
            self.memory_dir.mkdir(parents=True, exist_ok=True)

            # Format for OpenClaw's memory file
            memory_path = self.memory_dir / f"{summary_date.isoformat()}.md"

            omnimemory_content = self._format_daily_memory(
                summary_date=summary_date,
                summary=summary,
                episodes=episodes,
                highlights=highlights or [],
            )

            # Read existing content if file exists
            existing_content = ""
            if memory_path.exists():
                existing_content = memory_path.read_text(encoding="utf-8")

            # Check if OmniMemory section already exists
            if self.MARKER in existing_content:
                # Replace existing OmniMemory section
                parts = existing_content.split(self.MARKER)
                if len(parts) >= 2:
                    # Keep content before the marker, removing any trailing separator
                    before_marker = parts[0].rstrip()
                    # Remove trailing --- that preceded the marker
                    while before_marker.endswith("---"):
                        before_marker = before_marker[:-3].rstrip()

                    # Find the end of OmniMemory section (next --- or end of file)
                    after_omnimemory = ""
                    remaining = self.MARKER.join(parts[1:])
                    if "\n---\n" in remaining:
                        # There's content after OmniMemory section
                        _, after_omnimemory = remaining.split("\n---\n", 1)
                        after_omnimemory = "\n---\n" + after_omnimemory

                    final_content = before_marker + self.SEPARATOR + self.MARKER + "\n" + omnimemory_content
                    if after_omnimemory:
                        final_content += after_omnimemory
                else:
                    # Marker exists but malformed, just append
                    final_content = existing_content.rstrip() + self.SEPARATOR + self.MARKER + "\n" + omnimemory_content
            elif existing_content.strip():
                # Append to existing content
                final_content = existing_content.rstrip() + self.SEPARATOR + self.MARKER + "\n" + omnimemory_content
            else:
                # New file
                final_content = self.MARKER + "\n" + omnimemory_content

            # Write atomically (write to temp, then rename)
            tmp_path = memory_path.with_suffix(".md.tmp")
            tmp_path.write_text(final_content, encoding="utf-8")
            tmp_path.rename(memory_path)

            logger.info(f"Synced daily summary for {summary_date} to {memory_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to sync to OpenClaw memory: {e}")
            return False

    def _format_daily_memory(
        self,
        summary_date: date,
        summary: str,
        episodes: list[dict[str, Any]],
        highlights: list[str],
    ) -> str:
        """Format content for OpenClaw's memory file."""
        lines = [
            f"## OmniMemory Daily Summary - {summary_date.strftime('%A, %B %d, %Y')}",
            "",
            summary,
            "",
        ]

        if episodes:
            lines.append("### Episodes")
            lines.append("")
            for ep in episodes:
                time_range = self._format_time_range(
                    ep.get("start_time"), ep.get("end_time")
                )
                title = ep.get("title", "Untitled")
                if time_range:
                    lines.append(f"- **{title}** ({time_range})")
                else:
                    lines.append(f"- **{title}**")

                if ep.get("summary"):
                    # Indent summary under the title
                    lines.append(f"  {ep['summary']}")
            lines.append("")

        if highlights:
            lines.append("### Highlights")
            lines.append("")
            for h in highlights:
                lines.append(f"- {h}")
            lines.append("")

        lines.append(
            f"*Source: OmniMemory, synced at {datetime.now(timezone.utc).isoformat()}*"
        )

        return "\n".join(lines)

    def _format_time_range(
        self, start_time: str | datetime | None, end_time: str | datetime | None
    ) -> str:
        """Format start/end times as HH:MM - HH:MM."""
        if not start_time:
            return ""

        try:
            if isinstance(start_time, str):
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            else:
                start_dt = start_time

            start_str = start_dt.strftime("%H:%M")

            if end_time:
                if isinstance(end_time, str):
                    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                else:
                    end_dt = end_time
                end_str = end_dt.strftime("%H:%M")
                return f"{start_str} - {end_str}"
            else:
                return start_str
        except Exception:
            return ""

    def sync_memory_entry(
        self,
        user_id: str,
        context: dict[str, Any],
        entry_date: date | None = None,
    ) -> bool:
        """Sync a single memory/context entry to OpenClaw (optional, can be noisy).

        Args:
            user_id: The user's ID
            context: The context dict with title, summary, event_time_utc, etc.
            entry_date: Optional date override

        Returns:
            True if sync succeeded, False otherwise
        """
        if not self.enabled:
            return False

        try:
            event_time = context.get("event_time_utc")
            if entry_date:
                target_date = entry_date
            elif event_time:
                if isinstance(event_time, str):
                    dt = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                else:
                    dt = event_time
                target_date = dt.date()
            else:
                target_date = date.today()

            memory_path = self.memory_dir / f"{target_date.isoformat()}.md"

            entry = self._format_memory_entry(context)
            context_id = str(context.get("id") or "").strip()

            # Append to existing file or create new
            self.memory_dir.mkdir(parents=True, exist_ok=True)

            existing = ""
            if memory_path.exists():
                existing = memory_path.read_text(encoding="utf-8")

            if context_id:
                start_marker = f"{self.ENTRY_START_PREFIX}{context_id} -->"
                replacement = f"{start_marker}\n{entry}\n{self.ENTRY_END}"
                if start_marker in existing:
                    start_idx = existing.find(start_marker)
                    end_idx = existing.find(self.ENTRY_END, start_idx)
                    if start_idx >= 0 and end_idx >= 0:
                        end_idx += len(self.ENTRY_END)
                        content = existing[:start_idx].rstrip()
                        if content:
                            content += "\n\n"
                        content += replacement
                        trailing = existing[end_idx:].strip()
                        if trailing:
                            content += "\n\n" + trailing
                    else:
                        logger.warning(
                            "Malformed OpenClaw entry for context {} in {}; missing end marker. Appending replacement block.",
                            context_id,
                            memory_path,
                        )
                        content = (existing.rstrip() + "\n\n" if existing.strip() else "") + replacement
                else:
                    content = (existing.rstrip() + "\n\n" if existing.strip() else "") + replacement
            else:
                content = (existing.rstrip() + "\n\n" if existing.strip() else "") + entry

            tmp_path = memory_path.with_suffix(".md.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(memory_path)
            return True

        except Exception as e:
            logger.error(f"Failed to sync memory entry: {e}")
            return False

    def _format_memory_entry(self, context: dict[str, Any]) -> str:
        """Format a single context as a memory entry."""
        timestamp = context.get("event_time_utc", "")
        time_str = ""
        if timestamp:
            try:
                if isinstance(timestamp, str):
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                else:
                    dt = timestamp
                time_str = dt.strftime("%H:%M")
            except Exception:
                pass

        title = context.get("title", "Memory")
        summary = context.get("summary", "")
        keywords = context.get("keywords", [])
        context_type = context.get("context_type", "")

        lines = []
        if time_str:
            lines.append(f"### {time_str} - {title}")
        else:
            lines.append(f"### {title}")

        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")

        if keywords:
            lines.append(f"Keywords: {', '.join(keywords)}")

        if context_type:
            lines.append(f"Type: {context_type}")

        lines.append(f"Source: omnimemory:{context.get('id', 'unknown')}")

        return "\n".join(lines)

    def delete_daily_summary(self, summary_date: date) -> bool:
        """Remove OmniMemory section from a daily summary file.

        Only removes the OmniMemory section, preserving OpenClaw's own notes.
        If only OmniMemory content exists, deletes the file.

        Args:
            summary_date: The date to delete

        Returns:
            True if deleted or didn't exist, False on error
        """
        if not self.enabled:
            return False

        try:
            memory_path = self.memory_dir / f"{summary_date.isoformat()}.md"
            if not memory_path.exists():
                return True

            content = memory_path.read_text(encoding="utf-8")

            if self.MARKER not in content:
                # No OmniMemory section to remove
                return True

            # Remove OmniMemory section
            parts = content.split(self.MARKER)
            if len(parts) >= 2:
                before_marker = parts[0].rstrip()
                # Find the end of OmniMemory section
                remaining = self.MARKER.join(parts[1:])
                after_omnimemory = ""
                if "\n---\n" in remaining:
                    _, after_omnimemory = remaining.split("\n---\n", 1)

                # Reconstruct without OmniMemory section
                if before_marker.strip() or after_omnimemory.strip():
                    # There's other content, keep it
                    new_content = before_marker
                    if after_omnimemory.strip():
                        if new_content.strip():
                            new_content += "\n---\n" + after_omnimemory
                        else:
                            new_content = after_omnimemory
                    new_content = new_content.strip() + "\n"
                    memory_path.write_text(new_content, encoding="utf-8")
                else:
                    # Only OmniMemory content existed, delete the file
                    memory_path.unlink()

                logger.info(f"Removed OmniMemory section from {summary_date}")

            return True
        except Exception as e:
            logger.error(f"Failed to remove OmniMemory section: {e}")
            return False


def get_openclaw_sync(user_settings: dict[str, Any]) -> OpenClawMemorySync:
    """Create an OpenClawMemorySync instance from user settings.

    Checks user settings, app defaults, and environment variables.
    Precedence is: env var OPENCLAW_SYNC_MEMORY > user setting openclaw.syncMemory
    > app default settings.openclaw_sync_memory.

    Args:
        user_settings: User settings dict containing openclaw config

    Returns:
        Configured OpenClawMemorySync instance
    """
    from ..config import get_settings

    openclaw_config = user_settings.get("openclaw", {})
    settings = get_settings()

    # Precedence: explicit env var -> explicit user setting -> app default.
    if isinstance(openclaw_config, dict) and "syncMemory" in openclaw_config:
        sync_enabled = bool(openclaw_config.get("syncMemory"))
    else:
        sync_enabled = bool(settings.openclaw_sync_memory)

    env_override = os.getenv("OPENCLAW_SYNC_MEMORY")
    if env_override is not None and env_override.strip():
        sync_enabled = env_override.strip().lower() == "true"

    workspace = openclaw_config.get("workspace") if isinstance(openclaw_config, dict) else None
    return OpenClawMemorySync(
        openclaw_workspace=workspace or settings.openclaw_workspace,
        enabled=sync_enabled,
    )
