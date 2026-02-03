"""Prompt manager for loading, caching, and rendering prompt templates.

This module provides:
- Per-user prompt loading with fallback chain
- YAML frontmatter parsing
- Jinja2 rendering with StrictUndefined
- sha256 hashing for optimistic concurrency
- TTL caching with optional hot reload
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from jinja2 import Environment, StrictUndefined, TemplateSyntaxError

from .prompt_manifest import (
    DEFAULT_MAX_SIZE_BYTES,
    get_max_size,
    get_prompt_spec,
    get_required_vars,
    is_valid_prompt_name,
    validate_prompt_vars,
)

logger = logging.getLogger(__name__)


@dataclass
class PromptTemplate:
    """A loaded prompt template with metadata."""

    name: str
    content: str
    metadata: dict[str, Any]
    source: str  # "user" | "bundled" | "inline"
    source_path: Optional[Path] = None
    sha256: str = ""
    updated_at: Optional[datetime] = None
    version: str = "1.0"

    def __post_init__(self):
        """Compute sha256 hash after initialization."""
        if not self.sha256:
            self.sha256 = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.metadata:
            self.version = self.metadata.get("version", "1.0")


@dataclass
class CacheEntry:
    """Cache entry with TTL tracking."""

    template: PromptTemplate
    cached_at: float = field(default_factory=time.monotonic)


class PromptManager:
    """Manages loading, caching, and rendering of prompt templates.

    Prompt loading follows this precedence (highest to lowest):
    1. User override: {base_path}/users/{user_id}/prompts/{name}.md
    2. Bundled default: {bundled_path}/{name}.md
    3. Inline fallback: INLINE_DEFAULTS from prompt_templates.py

    Attributes:
        base_path: Base directory for prompts (from config)
        bundled_path: Path to bundled default prompts
        hot_reload: Whether to watch for file changes
        cache_ttl: TTL in seconds for cached prompts (when hot_reload=False)
        global_max_size: Global max size limit for prompts
    """

    def __init__(
        self,
        base_path: Optional[str] = None,
        bundled_path: Optional[str] = None,
        hot_reload: bool = False,
        cache_ttl: int = 300,
        global_max_size: int = DEFAULT_MAX_SIZE_BYTES,
    ):
        """Initialize PromptManager.

        Args:
            base_path: Base path for user prompts (default: ~/.omnimemory)
            bundled_path: Path to bundled defaults (default: services/api/data/prompts)
            hot_reload: Enable file watching for hot reload
            cache_ttl: Cache TTL in seconds (used when hot_reload=False)
            global_max_size: Global max size limit for prompts
        """
        self.base_path = Path(
            base_path or os.environ.get("OMNIMEMORY_PROMPTS_DIR", "~/.omnimemory")
        ).expanduser()
        self.bundled_path = Path(
            bundled_path or Path(__file__).parent.parent.parent / "data" / "prompts"
        )
        self.hot_reload = hot_reload
        self.cache_ttl = cache_ttl
        self.global_max_size = global_max_size

        # Per-user cache: {user_id: {prompt_name: CacheEntry}}
        self._user_cache: dict[str, dict[str, CacheEntry]] = {}
        # Bundled cache: {prompt_name: CacheEntry}
        self._bundled_cache: dict[str, CacheEntry] = {}
        # Inline cache: {prompt_name: PromptTemplate}
        self._inline_cache: dict[str, PromptTemplate] = {}

        self._lock = threading.RLock()
        self._jinja_env = Environment(undefined=StrictUndefined)
        self._observer: Any = None

        # Load inline fallbacks
        self._load_inline_defaults()

        # Setup hot reload if enabled
        if hot_reload:
            self._setup_hot_reload()

    def _load_inline_defaults(self) -> None:
        """Load inline fallback prompts from prompt_templates.py."""
        try:
            from .prompt_templates import INLINE_DEFAULTS

            for name, content in INLINE_DEFAULTS.items():
                self._inline_cache[name] = PromptTemplate(
                    name=name,
                    content=content,
                    metadata={"version": "inline", "source": "inline"},
                    source="inline",
                    sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    updated_at=None,
                    version="inline",
                )
        except ImportError:
            logger.warning("prompt_templates.py not found, no inline fallbacks available")

    def _setup_hot_reload(self) -> None:
        """Setup file watching for hot reload."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            class PromptReloadHandler(FileSystemEventHandler):
                def __init__(self, manager: PromptManager):
                    self.manager = manager

                def on_modified(self, event):
                    if event.src_path.endswith(".md"):
                        path = Path(event.src_path)
                        name = path.stem
                        logger.info(f"Hot-reloading prompt: {name} from {path}")
                        # Invalidate caches for this prompt
                        with self.manager._lock:
                            # Clear bundled cache if bundled file changed
                            if self.manager.bundled_path in path.parents:
                                self.manager._bundled_cache.pop(name, None)
                            # Clear all user caches for this prompt
                            for user_cache in self.manager._user_cache.values():
                                user_cache.pop(name, None)

            self._observer = Observer()

            # Watch bundled path
            if self.bundled_path.exists():
                self._observer.schedule(
                    PromptReloadHandler(self),
                    str(self.bundled_path),
                    recursive=False,
                )

            # Watch base path for user prompts
            if self.base_path.exists():
                self._observer.schedule(
                    PromptReloadHandler(self),
                    str(self.base_path),
                    recursive=True,
                )

            self._observer.start()
            logger.info("Prompt hot-reload enabled")
        except ImportError:
            logger.warning("watchdog not installed, hot-reload disabled")

    def _get_user_prompt_path(self, user_id: str, name: str) -> Path:
        """Get path to user's custom prompt file."""
        return self.base_path / "users" / user_id / "prompts" / f"{name}.md"

    def _get_bundled_prompt_path(self, name: str) -> Path:
        """Get path to bundled prompt file."""
        return self.bundled_path / f"{name}.md"

    def _parse_prompt_file(self, path: Path) -> Optional[PromptTemplate]:
        """Parse a prompt file with YAML frontmatter.

        Args:
            path: Path to the prompt file

        Returns:
            PromptTemplate if successful, None otherwise
        """
        try:
            content = path.read_text(encoding="utf-8")
            file_size = len(content.encode("utf-8"))

            # Check size limit
            name = path.stem
            max_size = get_max_size(name, self.global_max_size)
            if file_size > max_size:
                logger.error(f"Prompt {name} exceeds size limit: {file_size} > {max_size}")
                return None

            # Extract YAML frontmatter
            metadata: dict[str, Any] = {}
            markdown_content = content

            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    yaml_str = parts[1].strip()
                    markdown_content = parts[2].strip()
                    try:
                        metadata = yaml.safe_load(yaml_str) or {}
                    except yaml.YAMLError as e:
                        logger.error(f"Invalid YAML frontmatter in {path}: {e}")
                        return None

            # Get file mtime for updated_at
            stat = path.stat()
            updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

            # Determine source from path
            source = "bundled"
            if "users" in path.parts:
                source = "user"

            return PromptTemplate(
                name=metadata.get("name", path.stem),
                content=markdown_content,
                metadata=metadata,
                source=source,
                source_path=path,
                updated_at=updated_at,
                version=metadata.get("version", "1.0"),
            )
        except Exception as e:
            logger.error(f"Failed to parse prompt file {path}: {e}")
            return None

    def _is_cache_valid(self, entry: CacheEntry) -> bool:
        """Check if cache entry is still valid."""
        if self.hot_reload:
            # With hot reload, cache is always valid (watchdog invalidates)
            return True
        # Without hot reload, check TTL
        age = time.monotonic() - entry.cached_at
        return age < self.cache_ttl

    def get(
        self,
        name: str,
        user_id: Optional[str] = None,
    ) -> Optional[PromptTemplate]:
        """Get a prompt template by name.

        Follows fallback chain: user → bundled → inline

        Args:
            name: The prompt name
            user_id: Optional user ID for per-user prompts

        Returns:
            PromptTemplate if found, None otherwise
        """
        if not is_valid_prompt_name(name):
            logger.warning(f"Unknown prompt name: {name}")
            return None

        with self._lock:
            # Try user cache first
            if user_id:
                user_cache = self._user_cache.get(user_id, {})
                if name in user_cache and self._is_cache_valid(user_cache[name]):
                    return user_cache[name].template

                # Try loading from user path
                user_path = self._get_user_prompt_path(user_id, name)
                if user_path.exists():
                    template = self._parse_prompt_file(user_path)
                    if template:
                        if user_id not in self._user_cache:
                            self._user_cache[user_id] = {}
                        self._user_cache[user_id][name] = CacheEntry(template=template)
                        logger.debug(f"Loaded user prompt: {name} for user {user_id}")
                        return template

            # Try bundled cache
            if name in self._bundled_cache and self._is_cache_valid(self._bundled_cache[name]):
                return self._bundled_cache[name].template

            # Try loading from bundled path
            bundled_path = self._get_bundled_prompt_path(name)
            if bundled_path.exists():
                template = self._parse_prompt_file(bundled_path)
                if template:
                    self._bundled_cache[name] = CacheEntry(template=template)
                    logger.debug(f"Loaded bundled prompt: {name}")
                    return template

            # Fall back to inline
            if name in self._inline_cache:
                logger.debug(f"Using inline fallback for prompt: {name}")
                return self._inline_cache[name]

            return None

    def render(
        self,
        name: str,
        user_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Render a prompt template with provided variables.

        Args:
            name: The prompt name
            user_id: Optional user ID for per-user prompts
            **kwargs: Variables to pass to the template

        Returns:
            Rendered prompt string

        Raises:
            ValueError: If prompt not found or missing required variables
        """
        template = self.get(name, user_id=user_id)
        if not template:
            raise ValueError(f"Prompt not found: {name}")

        # Validate required variables
        provided_vars = set(kwargs.keys())
        required_vars = set(get_required_vars(name))
        missing = required_vars - provided_vars

        if missing:
            raise ValueError(f"Missing required variables for {name}: {missing}")

        try:
            jinja_template = self._jinja_env.from_string(template.content)
            return jinja_template.render(**kwargs)
        except TemplateSyntaxError as e:
            logger.error(f"Template syntax error in {name}: {e}")
            raise ValueError(f"Template syntax error in {name}: {e}")

    def list_prompts(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        """List all available prompts with metadata.

        Args:
            user_id: Optional user ID to include user overrides

        Returns:
            List of prompt info dicts with name, source, sha256, updated_at, version
        """
        from .prompt_manifest import get_prompt_names

        result = []
        for name in get_prompt_names():
            template = self.get(name, user_id=user_id)
            if template:
                spec = get_prompt_spec(name)
                result.append({
                    "name": name,
                    "source": template.source,
                    "sha256": template.sha256,
                    "updated_at": template.updated_at.isoformat() if template.updated_at else None,
                    "version": template.version,
                    "description": spec.get("description", "") if spec else "",
                    "updatable_via_api": spec.get("updatable_via_api", False) if spec else False,
                })
        return result

    def update_prompt(
        self,
        name: str,
        user_id: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
        expected_sha256: Optional[str] = None,
    ) -> tuple[bool, str, Optional[PromptTemplate]]:
        """Update a user's prompt override.

        Args:
            name: The prompt name
            user_id: User ID
            content: New prompt content
            metadata: Optional YAML frontmatter metadata
            expected_sha256: Expected sha256 for optimistic concurrency

        Returns:
            Tuple of (success, message, updated_template)
        """
        if not is_valid_prompt_name(name):
            return False, f"Unknown prompt name: {name}", None

        spec = get_prompt_spec(name)
        if not spec or not spec.get("updatable_via_api", False):
            return False, f"Prompt '{name}' is not updatable via API", None

        # Check size limit
        content_bytes = content.encode("utf-8")
        max_size = get_max_size(name, self.global_max_size)
        if len(content_bytes) > max_size:
            return False, f"Content exceeds size limit: {len(content_bytes)} > {max_size}", None

        with self._lock:
            # Check optimistic concurrency
            if expected_sha256:
                existing = self.get(name, user_id=user_id)
                if existing and existing.sha256 != expected_sha256:
                    return False, "Concurrency conflict: sha256 mismatch", None

            # Create user prompt directory
            user_prompts_dir = self.base_path / "users" / user_id / "prompts"
            user_prompts_dir.mkdir(parents=True, exist_ok=True)

            # Build file content with frontmatter
            meta = metadata or {}
            meta.setdefault("name", name)
            meta.setdefault("version", "user-1.0")
            yaml_content = yaml.dump(meta, default_flow_style=False, allow_unicode=True)
            file_content = f"---\n{yaml_content}---\n\n{content}"

            # Write file
            prompt_path = user_prompts_dir / f"{name}.md"
            prompt_path.write_text(file_content, encoding="utf-8")

            # Invalidate cache and reload
            if user_id in self._user_cache:
                self._user_cache[user_id].pop(name, None)

            template = self.get(name, user_id=user_id)
            logger.info(f"Updated prompt: {name} for user {user_id}")

            return True, "Prompt updated successfully", template

    def delete_prompt(
        self,
        name: str,
        user_id: str,
    ) -> tuple[bool, str]:
        """Delete a user's prompt override (reverts to bundled/inline).

        Args:
            name: The prompt name
            user_id: User ID

        Returns:
            Tuple of (success, message)
        """
        if not is_valid_prompt_name(name):
            return False, f"Unknown prompt name: {name}"

        with self._lock:
            prompt_path = self._get_user_prompt_path(user_id, name)
            if not prompt_path.exists():
                return False, f"No user override exists for prompt: {name}"

            prompt_path.unlink()

            # Invalidate cache
            if user_id in self._user_cache:
                self._user_cache[user_id].pop(name, None)

            logger.info(f"Deleted user prompt override: {name} for user {user_id}")
            return True, "Prompt override deleted"

    def shutdown(self) -> None:
        """Shutdown the prompt manager (stops file watcher)."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None


# Singleton instance
_manager: Optional[PromptManager] = None
_manager_lock = threading.Lock()


def get_prompt_manager() -> PromptManager:
    """Get the singleton PromptManager instance."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                # Load config from settings
                try:
                    from ..config import get_settings

                    settings = get_settings()
                    base_path = getattr(settings, "omnimemory_prompts_dir", None)
                    hot_reload = getattr(settings, "prompt_hot_reload", False)
                    cache_ttl = getattr(settings, "prompt_cache_ttl_seconds", 300)
                    max_size = getattr(settings, "prompt_max_size_bytes", DEFAULT_MAX_SIZE_BYTES)
                except Exception:
                    base_path = None
                    hot_reload = False
                    cache_ttl = 300
                    max_size = DEFAULT_MAX_SIZE_BYTES

                _manager = PromptManager(
                    base_path=base_path,
                    hot_reload=hot_reload,
                    cache_ttl=cache_ttl,
                    global_max_size=max_size,
                )
    return _manager


def reset_prompt_manager() -> None:
    """Reset the singleton (useful for testing)."""
    global _manager
    with _manager_lock:
        if _manager:
            _manager.shutdown()
            _manager = None
