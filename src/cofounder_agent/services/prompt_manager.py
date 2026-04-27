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
        # DB prompt overrides loaded by ``load_from_db``. Kept in two
        # buckets so the Pro tier (gitea#225) can layer over defaults
        # without losing them — toggling ``premium_active`` flips the
        # pipeline cleanly.
        self._db_overrides: dict[str, str] = {}
        self._db_overrides_premium: dict[str, str] = {}
        self._site_config: Any = None
        self._premium_active_static: bool = False
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

    async def load_from_db(self, pool, site_config: Any = None) -> int:
        """Load prompt overrides from the ``prompt_templates`` database table.

        Call this once at app startup (after DB pool is ready). DB prompts
        take priority over YAML prompts so operators can tune at runtime
        without a redeploy.

        Two layers:
        - ``source='default'`` rows — free baseline, always loaded.
        - ``source='premium'`` rows — Pro-tier pack, also loaded but only
          consulted by ``get_prompt`` when ``premium_active='true'`` in
          ``app_settings`` (gitea#225). The premium pack overrides defaults
          for matching keys without deleting the defaults — toggling the
          flag flips the whole pipeline cleanly.

        ``site_config`` is optional but recommended. When provided,
        ``get_prompt`` reads ``premium_active`` through it on every lookup
        (fast in-memory cache hit) so license activation takes effect
        without restarting the worker. Without it, premium gating falls
        back to a one-shot DB read at load time and won't notice a later
        activation until the next ``load_from_db`` call.

        Returns the total number of rows loaded (default + premium).
        """
        if pool is None:
            return 0
        try:
            rows = await pool.fetch(
                "SELECT key, template, source "
                "FROM prompt_templates WHERE is_active = true"
            )
            self._db_overrides.clear()
            self._db_overrides_premium.clear()
            for row in rows:
                # Tolerate pre-migration rows (no source column) by treating
                # them as default. The migration backfills 'default', so
                # this only matters for unit tests against a freshly-built
                # in-memory schema.
                source = (row.get("source") or "default") if hasattr(row, "get") else (
                    row["source"] if "source" in row.keys() else "default"
                )
                if source == "premium":
                    self._db_overrides_premium[row["key"]] = row["template"]
                else:
                    self._db_overrides[row["key"]] = row["template"]

            # Site config gives us a live read of premium_active per call.
            # Fall back to a one-shot DB read so the loader still works
            # without DI plumbing (tests, REPL).
            self._site_config = site_config
            if site_config is None:
                try:
                    flag_row = await pool.fetchrow(
                        "SELECT value FROM app_settings "
                        "WHERE key = 'premium_active'"
                    )
                    self._premium_active_static = bool(
                        flag_row and str(flag_row["value"]).strip().lower()
                        in ("true", "1", "yes", "on")
                    )
                except Exception:
                    self._premium_active_static = False

            logger.info(
                "Loaded %d default + %d premium prompts (premium_active=%s)",
                len(self._db_overrides),
                len(self._db_overrides_premium),
                self._is_premium_active(),
            )
            return len(self._db_overrides) + len(self._db_overrides_premium)
        except Exception as e:
            logger.warning("Could not load prompts from DB (using YAML fallback): %s", e)
            return 0

    def _is_premium_active(self) -> bool:
        """Live-read ``premium_active`` if site_config was provided, else
        use the snapshot from ``load_from_db``.

        Kept off the public surface — callers should go through
        ``get_prompt`` and let it route. Exposed only for the load log line
        and tests."""
        if self._site_config is not None:
            try:
                return bool(self._site_config.get_bool("premium_active", False))
            except Exception as e:
                logger.warning(
                    "[prompt_manager] live premium_active read from "
                    "site_config failed; falling back to startup snapshot: %s",
                    e,
                )
        return getattr(self, "_premium_active_static", False)

    def get_prompt(self, key: str, **kwargs) -> str:
        """
        Get a prompt by key and format with provided kwargs.

        Priority: premium DB override (when ``premium_active``) > default
        DB override > YAML file > KeyError.

        Args:
            key: Prompt key (e.g., "blog_generation.initial_draft")
            **kwargs: Values to format into prompt template

        Returns:
            Formatted prompt ready for LLM

        Raises:
            KeyError: If prompt key not found
        """
        # Premium overrides win when the operator's license is active.
        # Otherwise (no license, expired, or pre-activation), we silently
        # use the same default the free tier sees. Toggling the flag
        # flips the pipeline immediately on the next prompt lookup —
        # no redeploy and no reload required.
        template = None
        if self._is_premium_active():
            template = self._db_overrides_premium.get(key)
        if template is None:
            template = self._db_overrides.get(key)

        # Fall back to YAML-loaded prompts
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
