"""
Unified Prompt Manager

Consolidates ALL LLM prompts from across the codebase into a single,
versioned, and documented system.

Replaces:
- agents/content_agent/prompts.json (blog generation prompts)
- services/prompt_templates.py (utility prompt builders)
- Hardcoded prompts in ai_content_generator.py, unified_metadata_service.py, etc.

Benefits:
- Single source of truth for all prompts
- Easy version control and A/B testing
- Consistent output format specifications
- Built-in documentation and examples
- Centralized temperature/parameter management
- Easy to add new prompts without scattered changes

Version History:
- v1.0 (2026-02-07): Initial consolidation from scattered prompts across codebase
- v1.1 (2026-03-25): Extracted prompt templates to YAML files in prompts/ directory
"""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from services.logger_config import get_logger

logger = get_logger(__name__)


class PromptVersion(str, Enum):
    """Prompt versions for A/B testing and rollouts"""

    V1_0 = "v1.0"  # Initial consolidated version
    V1_1 = "v1.1"  # Current production


class PromptCategory(str, Enum):
    """Prompt categories for organization"""

    BLOG_GENERATION = "blog_generation"
    CONTENT_QA = "content_qa"
    SEO_METADATA = "seo_metadata"
    SOCIAL_MEDIA = "social_media"
    RESEARCH = "research"
    FINANCIAL = "financial"
    MARKET_ANALYSIS = "market_analysis"
    IMAGE_GENERATION = "image_generation"
    UTILITY = "utility"


@dataclass
class PromptMetadata:
    """Metadata about a prompt for versioning and tracking"""

    category: PromptCategory
    version: PromptVersion
    created_date: str  # ISO format: YYYY-MM-DD
    last_modified: str  # ISO format: YYYY-MM-DD
    deprecated: bool = False
    replacement_prompt_key: str | None = None
    description: str = ""
    output_format: str = ""  # "json", "text", "markdown", etc.
    example_output: str | None = None
    notes: str = ""  # A/B test results, performance notes, etc.


class UnifiedPromptManager:
    """
    Central manager for all LLM prompts.

    Usage:
        pm = UnifiedPromptManager()
        prompt = pm.get_prompt("blog_generation.initial_draft", topic="AI Trends")
        metadata = pm.get_metadata("blog_generation.initial_draft")
    """

    # Mapping from YAML category strings to PromptCategory enum values
    _CATEGORY_MAP: dict[str, PromptCategory] = {
        "blog_generation": PromptCategory.BLOG_GENERATION,
        "content_qa": PromptCategory.CONTENT_QA,
        "seo_metadata": PromptCategory.SEO_METADATA,
        "social_media": PromptCategory.SOCIAL_MEDIA,
        "research": PromptCategory.RESEARCH,
        "financial": PromptCategory.FINANCIAL,
        "market_analysis": PromptCategory.MARKET_ANALYSIS,
        "image_generation": PromptCategory.IMAGE_GENERATION,
        "utility": PromptCategory.UTILITY,
    }

    def __init__(self):
        self.prompts: dict[str, dict[str, Any]] = {}
        self.metadata: dict[str, PromptMetadata] = {}
        self._db_overrides: dict[str, str] = {}  # DB prompt overrides (loaded async)
        # Langfuse client — lazy-initialized on first get_prompt call so
        # apps that don't use Langfuse don't pay the connect cost. None
        # when langfuse SDK isn't installed OR app_settings lacks the
        # connection config OR the connection failed. Per
        # feedback_prompts_must_be_db_configurable, Langfuse is the
        # editing/observability surface above the DB+YAML stack.
        self._langfuse_client: Any = None
        self._langfuse_enabled: bool | None = None  # None = unevaluated
        self._initialize_prompts()

    def _initialize_prompts(self):
        """Load all prompts from YAML files in the prompts/ directory."""
        prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        if not prompts_dir.is_dir():
            logger.warning("Prompts directory not found: %s", prompts_dir)
            return

        for yaml_path in sorted(prompts_dir.glob("*.yaml")):
            try:
                with open(yaml_path, encoding="utf-8") as f:
                    entries: list[dict[str, Any]] = yaml.safe_load(f) or []
            except Exception:
                logger.error("Failed to load prompt file: %s", yaml_path, exc_info=True)
                continue

            for entry in entries:
                category_str = entry.get("category", "")
                category = self._CATEGORY_MAP.get(category_str)
                if category is None:
                    logger.warning(
                        f"Unknown category '{category_str}' in {yaml_path.name}, "
                        f"skipping prompt '{entry.get('key', '?')}'"
                    )
                    continue

                self._register_prompt(
                    key=entry["key"],
                    category=category,
                    template=entry.get("template", ""),
                    description=entry.get("description", ""),
                    output_format=entry.get("output_format", "text"),
                    example_output=entry.get("example_output"),
                    notes=entry.get("notes", ""),
                )

    def _register_prompt(
        self,
        key: str,
        category: PromptCategory,
        template: str,
        description: str = "",
        output_format: str = "text",
        example_output: str | None = None,
        notes: str = "",
        version: PromptVersion = PromptVersion.V1_1,
        created_date: str = "2026-02-07",
        last_modified: str = "2026-02-07",
    ):
        """Register a prompt with metadata"""
        self.prompts[key] = {
            "template": template,
            "version": version.value,
        }
        self.metadata[key] = PromptMetadata(
            category=category,
            version=version,
            created_date=created_date,
            last_modified=last_modified,
            description=description,
            output_format=output_format,
            example_output=example_output,
            notes=notes,
        )
        logger.debug("Registered prompt: %s (%s)", key, category.value)

    async def load_from_db(self, pool, *, site_config=None) -> int:
        """Load prompt overrides from the prompt_templates database table.

        Call this once at app startup (after DB pool is ready).
        DB prompts take priority over YAML prompts — enabling runtime editing.

        Premium gating (migration 0092 added the ``source`` column;
        0141 wired the loader): when ``app_settings.premium_active`` is
        truthy, rows with ``source='premium'`` AND ``source='default'``
        load, with ``premium`` overriding ``default`` for matching keys
        (``ORDER BY source`` puts ``default`` first, ``premium`` later;
        last-write-wins on the dict so ``premium`` wins). When the flag
        is absent or false (the OSS default), only ``'default'`` rows
        load — premium prompts ship as a separate file drop and arrive
        as DB rows that are inert until the operator flips
        ``premium_active=true``.

        ``site_config`` is read for the ``premium_active`` lookup. When
        ``None`` (test paths, early bootstrap), the loader behaves as if
        ``premium_active=false``.

        Returns number of prompts loaded from DB.
        """
        self._site_config = site_config
        if pool is None:
            return 0

        premium_active = False
        if site_config is not None:
            try:
                premium_active = bool(site_config.get_bool("premium_active", False))
            except AttributeError:
                premium_active = (
                    str(site_config.get("premium_active", "false")).lower() == "true"
                )

        sources = ("default", "premium") if premium_active else ("default",)
        try:
            rows = await pool.fetch(
                # ORDER BY source: 'default' < 'premium' alphabetically, so
                # default rows assign first and premium rows overwrite.
                "SELECT key, template, source FROM prompt_templates "
                "WHERE is_active = true AND source = ANY($1::text[]) "
                "ORDER BY source",
                list(sources),
            )
            for row in rows:
                self._db_overrides[row["key"]] = row["template"]
            logger.info(
                "Loaded %d prompt templates from database (premium_active=%s, sources=%s)",
                len(rows), premium_active, sources,
            )
            return len(rows)
        except Exception as e:
            logger.warning("Could not load prompts from DB (using YAML fallback): %s", e)
            return 0

    def get_prompt(self, key: str, **kwargs) -> str:
        """
        Get a prompt by key and format with provided kwargs.

        Priority: Langfuse production label > DB override > YAML file > KeyError.
        Langfuse comes first because it's the operator's edit surface;
        when an operator updates a prompt in the Langfuse UI it should
        take effect on the next get_prompt call without a worker
        restart. The Langfuse SDK caches in-process so the lookup is
        cheap after the first call.

        When Langfuse isn't configured (no host + key in app_settings)
        or the lookup fails (network/auth/missing prompt), the call
        falls through to the existing DB-override → YAML → KeyError
        chain. This keeps the OSS distribution working without a
        Langfuse account.

        Args:
            key: Prompt key (e.g., "blog_generation.initial_draft")
            **kwargs: Values to format into prompt template

        Returns:
            Formatted prompt ready for LLM

        Raises:
            KeyError: If prompt key not found in any source
        """
        # Langfuse first — operator's preferred edit surface. The SDK
        # caches in-process; first call hits the API, subsequent calls
        # for the same key return the cached version until the cache
        # window expires (default 60s in langfuse SDK).
        template = self._fetch_from_langfuse(key)

        # DB overrides take second priority (editable at runtime via
        # SQL or the prompt_templates table).
        if template is None:
            template = self._db_overrides.get(key)

        # Fall back to YAML-loaded prompts.
        if template is None:
            if key not in self.prompts:
                available = ", ".join(self.prompts.keys())
                raise KeyError(f"Prompt '{key}' not found. Available: {available}")
            template = self.prompts[key]["template"]

        try:
            return template.format(**kwargs)
        except KeyError as e:
            missing_var = e.args[0]
            raise KeyError(
                f"Prompt '{key}' missing required variable: {missing_var}. "
                f"Please provide: {missing_var}=..."
            ) from e

    def _fetch_from_langfuse(self, key: str) -> str | None:
        """Look up the production prompt from Langfuse, or return None.

        Lazy-initializes the Langfuse client on first call. Caches the
        client + the per-prompt response (Langfuse SDK does its own
        caching; we just don't fight it). All errors are swallowed —
        the caller falls through to DB+YAML on any Langfuse failure.

        Returns the prompt template string when Langfuse has a
        ``production``-labeled version of the prompt; ``None`` otherwise.
        """
        # Has Langfuse been wired up at all?
        if self._langfuse_enabled is False:
            return None

        client = self._init_langfuse_client()
        if client is None:
            self._langfuse_enabled = False
            return None

        try:
            prompt = client.get_prompt(name=key, label="production")
            # Langfuse Prompt objects expose .prompt for the template
            # body. The SDK normalizes string + chat prompts; for our
            # use we want the string form.
            template = getattr(prompt, "prompt", None)
            if isinstance(template, list):
                # chat-prompt shape: render as a flattened single string
                # for compatibility with the existing get_prompt API.
                # Rare path; chat prompts are rare in our system.
                template = "\n\n".join(
                    f"{m.get('role', 'user')}: {m.get('content', '')}"
                    for m in template
                    if isinstance(m, dict)
                )
            if isinstance(template, str) and template.strip():
                return template
        except Exception as exc:  # noqa: BLE001
            # Either the prompt doesn't exist in Langfuse yet (expected
            # before the import script runs) or the API is unreachable.
            # Demote to debug — this fires on every get_prompt call when
            # Langfuse is configured but the specific prompt isn't yet
            # synced. Caller falls through to DB+YAML.
            logger.debug("[prompt_manager] Langfuse lookup for %r failed: %s", key, exc)
        return None

    def _init_langfuse_client(self):
        """Build the Langfuse client lazily on first use.

        Reads connection config from ``app_settings`` via the global
        site_config (no env-var dependency per Matt's preference). When
        any required setting is missing, returns None — the caller
        gracefully falls through to the DB+YAML stack and Langfuse
        becomes a no-op until the operator provisions credentials.
        """
        if self._langfuse_client is not None:
            return self._langfuse_client
        try:
            from langfuse import Langfuse
        except ImportError:
            return None

        try:
            from services.site_config import site_config
            host = site_config.get("langfuse_host", "")
            public_key = site_config.get("langfuse_public_key", "")
            # Secret key is a SECRET, so it routes through get_secret
            # which hits the encrypted column synchronously via cache.
            # Falls back to plain get when get_secret isn't available.
            try:
                # site_config.get_secret is async — use the sync
                # accessor that reads from the secret cache.
                secret_key = (
                    getattr(site_config, "_secrets", {}).get("langfuse_secret_key")
                    or site_config.get("langfuse_secret_key", "")
                )
            except Exception:
                secret_key = ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("[prompt_manager] site_config unavailable: %s", exc)
            return None

        host = (host or "").strip()
        public_key = (public_key or "").strip()
        secret_key = (secret_key or "").strip()
        if not (host and public_key and secret_key):
            logger.info(
                "[prompt_manager] Langfuse not configured "
                "(host=%s public_key=%s secret_key=%s) — using DB+YAML fallback. "
                "Set langfuse_host / langfuse_public_key / langfuse_secret_key "
                "in app_settings to enable.",
                bool(host), bool(public_key), bool(secret_key),
            )
            return None

        try:
            self._langfuse_client = Langfuse(
                host=host,
                public_key=public_key,
                secret_key=secret_key,
            )
            self._langfuse_enabled = True
            logger.info(
                "[prompt_manager] Langfuse prompt management active (host=%s)",
                host,
            )
            return self._langfuse_client
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[prompt_manager] Langfuse client init failed: %s — "
                "using DB+YAML fallback",
                exc,
            )
            self._langfuse_enabled = False
            return None

    def get_metadata(self, key: str) -> PromptMetadata:
        """Get metadata for a prompt"""
        if key not in self.metadata:
            raise KeyError(f"Prompt '{key}' not found")
        return self.metadata[key]

    def list_prompts(self, category: PromptCategory | None = None) -> dict[str, dict[str, Any]]:
        """List all prompts, optionally filtered by category"""
        result = {}
        for key, metadata in self.metadata.items():
            if category is None or metadata.category == category:
                result[key] = {
                    "category": metadata.category.value,
                    "description": metadata.description,
                    "output_format": metadata.output_format,
                    "version": metadata.version.value,
                    "example": metadata.example_output,
                }
        return result

    def export_prompts_as_json(self) -> str:
        """Export all prompts as JSON for documentation/migration"""
        export_data = {}
        for key, prompt_data in self.prompts.items():
            meta = self.metadata[key]
            export_data[key] = {
                "template": prompt_data["template"],
                "category": meta.category.value,
                "description": meta.description,
                "output_format": meta.output_format,
                "example_output": meta.example_output,
                "version": meta.version.value,
                "created": meta.created_date,
                "modified": meta.last_modified,
                "notes": meta.notes,
            }
        return json.dumps(export_data, indent=2)


# Global singleton instance
_prompt_manager: UnifiedPromptManager | None = None


def get_prompt_manager() -> UnifiedPromptManager:
    """Get or create the global prompt manager instance"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = UnifiedPromptManager()
    return _prompt_manager
