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
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from services.logger_config import get_logger
from services.site_config import SiteConfig

# Process-wide empty-SiteConfig fallback (#272 capstone). Used by the
# ctor / Langfuse-init path when no SiteConfig is injected AND no
# AppContainer has been registered (import time, CLI early paths, tests
# that never bootstrap). Behaves exactly like the old per-module
# ``site_config`` global did before its lifespan setter fired.
_FALLBACK_SITE_CONFIG = SiteConfig()


def _sc() -> SiteConfig:
    """Return the active container's SiteConfig, or the empty fallback.

    #272 capstone: sources SiteConfig from the process-wide
    ``AppContainer`` registered by ``bootstrap.build_container`` instead
    of a module-level global wired via the retired ``set_site_config``.
    The ``get_prompt_manager()`` singleton factory builds
    ``UnifiedPromptManager()`` with no ctor arg, so the instance pulls
    its SiteConfig from here. Crash-safe — returns ``_FALLBACK_SITE_CONFIG``
    when no container is registered yet.
    """
    from services.container_registry import get_container

    container = get_container()
    return container.site_config if container is not None else _FALLBACK_SITE_CONFIG


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
    VIDEO = "video"
    PODCAST = "podcast"


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


@dataclass(frozen=True)
class PromptResolution:
    """A resolved prompt — the rendered text plus the provenance metadata
    callers need to stamp on outcome rows.

    Returned by :meth:`UnifiedPromptManager.get_prompt_resolution`. The
    plain :meth:`UnifiedPromptManager.get_prompt` API still returns a
    bare string so the dozen existing call sites don't churn.

    ``source`` is one of ``"langfuse"`` / ``"yaml"`` / ``"fallback"`` —
    a coarse provenance signal for the lab. When Langfuse is the
    active edit surface, ``version`` is the Langfuse prompt version
    integer (when the SDK reports one); on the YAML path, it's the
    YAML-registered prompt version cast to an int when possible, else
    None.

    The dataclass is frozen so callers can stash it in state dicts
    without worrying about accidental mutation downstream.
    """

    text: str
    key: str
    version: int | None = None
    source: str = "yaml"


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
        "video": PromptCategory.VIDEO,
        "podcast": PromptCategory.PODCAST,
    }

    def __init__(self, site_config: "SiteConfig | None" = None):
        self.prompts: dict[str, dict[str, Any]] = {}
        self.metadata: dict[str, PromptMetadata] = {}
        self._db_overrides: dict[str, str] = {}  # DB prompt overrides (loaded async)
        # Site config injected via ctor (DI shim, #272) or by
        # load_from_db — used by the Langfuse init path. The container's
        # SiteConfig may not be .load()'d at construction time, so we
        # capture the loaded one at the same DI seam as the DB pool.
        #
        # #272 capstone: when no instance is injected, source it from the
        # process-wide ``AppContainer`` via ``_sc()`` (empty fallback when
        # no container is registered). ``load_from_db`` may overwrite
        # ``self._site_config`` later with the DI seam's instance — that
        # path is unchanged.
        self._site_config: Any = (
            site_config if site_config is not None else _sc()
        )
        # Langfuse secret pre-fetched at load_from_db time. The Langfuse
        # client init runs in a sync code path (get_prompt is sync), so
        # the async-only get_secret call has to happen earlier and the
        # value gets cached here.
        self._langfuse_secret_key: str = ""
        # Langfuse client — lazy-initialized on first get_prompt call so
        # apps that don't use Langfuse don't pay the connect cost. None
        # when langfuse SDK isn't installed OR app_settings lacks the
        # connection config OR the connection failed. Per
        # feedback_prompts_must_be_db_configurable, Langfuse is the
        # editing/observability surface above the DB+YAML stack.
        self._langfuse_client: Any = None
        self._langfuse_enabled: bool | None = None  # None = unevaluated
        self._initialize_prompts()
        # Skills load AFTER YAML so a migrated SKILL.md transparently takes
        # precedence over any leftover YAML entry for the same key — lets us
        # migrate one prompt file at a time without a flag day. See
        # docs/architecture/business-os-endgame.md.
        self._initialize_skills()

    def _initialize_skills(self):
        """Load pipeline prompts from agentskills.io SKILL.md packs.

        Skills live in the repo-root ``skills/`` tree, namespaced by pack:
        ``skills/<pack>/<skill>/SKILL.md`` (industry-standard layout, uniform
        with the existing ``skills/poindexter/`` operator pack). A
        prompt-bearing skill's frontmatter declares ``metadata.category`` +
        ``metadata.prompts`` (the keys it provides); the body holds one
        ``## <key>`` section per prompt with the template in a fenced block.

        Skills WITHOUT a ``metadata.prompts`` block (e.g. the operator action
        skills under ``skills/poindexter/`` that wrap the CLI/MCP) are silently
        skipped — they're a different layer (agent-runtime tools), not pipeline
        prompt text, and share only the file format.

        Each key registers exactly like :meth:`_initialize_prompts` does for
        YAML, so the resolution chain (Langfuse override -> in-memory default)
        is unchanged. See docs/architecture/business-os-endgame.md.
        """
        # Repo root holds the top-level skills/ tree, a sibling of src/.
        # __file__ = src/cofounder_agent/services/prompt_manager.py -> parents[3].
        skills_dir = Path(__file__).resolve().parents[3] / "skills"
        if not skills_dir.is_dir():
            return

        for skill_md in sorted(skills_dir.glob("*/*/SKILL.md")):
            try:
                # Frontmatter is delimited by the first two '---' lines.
                _, frontmatter_raw, body = skill_md.read_text(
                    encoding="utf-8",
                ).split("---", 2)
                frontmatter: dict[str, Any] = yaml.safe_load(frontmatter_raw) or {}
            except Exception:
                # ValueError = missing frontmatter delimiters; yaml errors etc.
                logger.error("Failed to load skill: %s", skill_md, exc_info=True)
                continue

            meta = frontmatter.get("metadata", {}) or {}
            prompts = meta.get("prompts") or []
            if not prompts:
                # Not a prompt-bearing skill (operator action skill, etc.) —
                # different layer, silently ignore.
                continue

            category = self._CATEGORY_MAP.get(meta.get("category", ""))
            if category is None:
                logger.warning(
                    "Skill %s declares prompts but has unknown/absent "
                    "metadata.category — skipping", skill_md,
                )
                continue

            for prompt in prompts:
                key = prompt.get("key")
                if not key:
                    continue
                template = self._extract_skill_section(body, key)
                if not template:
                    logger.warning(
                        "Skill %s declares key %r with no '## %s' section",
                        skill_md.parent.name, key, key,
                    )
                    continue
                self._register_prompt(
                    key=key,
                    category=category,
                    template=template,
                    description=prompt.get(
                        "description", frontmatter.get("description", ""),
                    ),
                    output_format=prompt.get("output_format", "text"),
                )

    @staticmethod
    def _extract_skill_section(body: str, key: str) -> str:
        """Return the fenced template under a ``## <key>`` heading, or ''.

        Normalizes to YAML ``|`` (literal block, clip-chomp) semantics — a
        single trailing newline — so a migrated SKILL.md template is
        byte-identical to the YAML ``template: |`` it replaced. Downstream
        snapshot tests pin that trailing ``\\n`` (e.g.
        test_topic_ranking_prompt.py) and rendered prompts assume it.
        """
        match = re.search(
            rf"^##\s+{re.escape(key)}\s*$\n+```[^\n]*\n(.*?)\n```",
            body,
            re.MULTILINE | re.DOTALL,
        )
        if not match:
            return ""
        return match.group(1).rstrip("\n") + "\n"

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
        """Worker-startup hook: capture site_config + pre-fetch Langfuse secret.

        This used to ALSO load prompt overrides from the
        ``prompt_templates`` table (migrations 0092 + 0141 wired premium
        gating via the ``source`` column). Phase 2 of poindexter#47
        retired the table — Langfuse is the live edit surface, YAML is
        the OSS distribution default, and the parallel DB store added
        no value over those two layers. The ``pool`` argument is kept
        on the signature so the dozen callers don't churn.

        ``site_config`` is captured on ``self._site_config`` for the
        Langfuse client init path (sync code can't await get_secret).
        Pre-fetching the Langfuse secret here is best-effort; an empty
        cache trips the "Langfuse not configured" log + falls through
        to YAML, which is the documented OSS path.

        Returns 0 (no rows loaded — the DB layer is gone). The return
        type stays `int` so callers that ignore-or-log the count don't
        churn either.
        """
        del pool  # parameter kept for source-compat; see docstring
        # Only rebind when an instance is actually supplied — a None
        # arg here must not clobber the ctor-injected / module-global
        # fallback captured in __init__ (Phase-1 DI shim, #272).
        if site_config is not None:
            self._site_config = site_config

        if site_config is not None:
            try:
                self._langfuse_secret_key = (
                    await site_config.get_secret("langfuse_secret_key", "") or ""
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[prompt_manager] langfuse secret pre-fetch failed: %s", exc,
                )

        return 0

    def get_prompt(self, key: str, **kwargs) -> str:
        """
        Get a prompt by key and format with provided kwargs.

        Priority: Langfuse production label > YAML file > KeyError.
        Langfuse is the operator's edit surface; edits in the Langfuse
        UI take effect on the next get_prompt call without a worker
        restart (the SDK caches in-process for ~60s). When Langfuse
        isn't configured (OSS distribution without a Langfuse host /
        key) or the lookup fails, the call falls through to the
        baked-in YAML defaults — the open-source path.

        Phase 2 of poindexter#47 dropped the prompt_templates DB
        override layer; the parallel store added no value over Langfuse
        + YAML and made it harder to know "which prompt is live."

        Args:
            key: Prompt key (e.g., "blog_generation.initial_draft")
            **kwargs: Values to format into prompt template

        Returns:
            Formatted prompt ready for LLM

        Raises:
            KeyError: If prompt key not found in any source
        """
        return self.get_prompt_resolution(key, **kwargs).text

    def get_prompt_resolution(self, key: str, **kwargs) -> "PromptResolution":
        """Resolve a prompt and return both the rendered text and its
        provenance (key + version + source).

        This is the lab-instrumentation seam. Atoms that want to stamp
        ``prompt_template_key`` + ``prompt_template_version`` onto a
        capability_outcomes row call this instead of the plain
        :meth:`get_prompt` and then stash ``.key`` + ``.version`` on
        the return dict. Existing call sites that only want the text
        keep using :meth:`get_prompt` — backwards-compatible.

        Priority is the same as :meth:`get_prompt`: Langfuse production
        label > YAML defaults > KeyError. The Langfuse SDK exposes the
        ``version`` attribute on its Prompt object when present; we
        use it when integer-castable, else None.
        """
        template, source, version = self._resolve_template_with_meta(key)
        try:
            rendered = template.format(**kwargs)
        except KeyError as e:
            missing_var = e.args[0]
            raise KeyError(
                f"Prompt '{key}' missing required variable: {missing_var}. "
                f"Please provide: {missing_var}=..."
            ) from e
        return PromptResolution(
            text=rendered, key=key, version=version, source=source,
        )

    def _resolve_template_with_meta(
        self, key: str,
    ) -> tuple[str, str, int | None]:
        """Return (template_str, source, version) for a key.

        ``source`` is one of ``"langfuse"`` / ``"yaml"``. Raises
        ``KeyError`` when the key isn't registered in either source.
        """
        # Langfuse first — operator's preferred edit surface.
        lf = self._fetch_from_langfuse_with_meta(key)
        if lf is not None:
            template, version = lf
            return template, "langfuse", version

        # Fall back to YAML-loaded prompts (the OSS default).
        if key not in self.prompts:
            available = ", ".join(self.prompts.keys())
            raise KeyError(f"Prompt '{key}' not found. Available: {available}")
        entry = self.prompts[key]
        template = entry["template"]
        # YAML "version" is a string like "v1.1" — try to extract the
        # major-int for the outcome row. Non-numeric versions land as
        # None; consumers expect that.
        version: int | None = None
        raw_version = entry.get("version") or ""
        if isinstance(raw_version, str) and raw_version.startswith("v"):
            head = raw_version[1:].split(".", 1)[0]
            if head.isdigit():
                version = int(head)
        return template, "yaml", version

    def _fetch_from_langfuse(self, key: str) -> str | None:
        """Look up the production prompt from Langfuse, or return None.

        Thin wrapper over :meth:`_fetch_from_langfuse_with_meta` that
        keeps the historical "just give me the text" call sites green.
        Behavior is identical — same lazy client init, same error
        swallowing — but discards the version metadata.
        """
        result = self._fetch_from_langfuse_with_meta(key)
        return result[0] if result is not None else None

    def _fetch_from_langfuse_with_meta(
        self, key: str,
    ) -> tuple[str, int | None] | None:
        """Look up the production prompt from Langfuse with version.

        Lazy-initializes the Langfuse client on first call. Caches the
        client + the per-prompt response (Langfuse SDK does its own
        caching; we just don't fight it). All errors are swallowed —
        the caller falls through to DB+YAML on any Langfuse failure.

        Returns ``(template_string, version)`` when Langfuse has a
        ``production``-labeled version of the prompt; ``None``
        otherwise. ``version`` is the Langfuse prompt version integer
        when the SDK reports one (it does for normal prompts), else
        ``None``.
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
                # version is an int on the Langfuse Prompt object when
                # the SDK reports one. Coerce defensively.
                raw_version = getattr(prompt, "version", None)
                version: int | None = None
                if isinstance(raw_version, int):
                    version = raw_version
                elif isinstance(raw_version, str) and raw_version.isdigit():
                    version = int(raw_version)
                return template, version
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

        Reads connection config from ``app_settings`` via the SiteConfig
        the worker handed us at ``load_from_db`` time (no env-var
        dependency per Matt's preference). When any required setting is
        missing, returns None — the caller gracefully falls through to
        the DB+YAML stack and Langfuse becomes a no-op until the
        operator provisions credentials.

        Per CLAUDE.md §Configuration: the per-module ``site_config``
        global + ``set_site_config`` setter were retired in the #272
        capstone. The DI seam is the one passed to ``load_from_db``;
        falling back to the process-wide container's SiteConfig (via
        ``_sc()``) keeps the no-DB unit-test paths green when
        ``load_from_db`` was never called.
        """
        if self._langfuse_client is not None:
            return self._langfuse_client
        try:
            from langfuse import Langfuse
        except ImportError:
            return None

        # __init__ now guarantees self._site_config is non-None (ctor
        # injection or the container/empty-fallback via ``_sc()``). The
        # None-guard stays for defensiveness; #272 capstone sources the
        # fallback from the process-wide AppContainer.
        site_config = self._site_config
        if site_config is None:
            try:
                site_config = _sc()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[prompt_manager] site_config unavailable: %s", exc)
                return None

        try:
            host = site_config.get("langfuse_host", "")
            public_key = site_config.get("langfuse_public_key", "")
            # Secret pre-fetched at load_from_db time (async path);
            # this sync init can't await get_secret itself.
            secret_key = self._langfuse_secret_key
        except Exception as exc:  # noqa: BLE001
            logger.debug("[prompt_manager] site_config read failed: %s", exc)
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
