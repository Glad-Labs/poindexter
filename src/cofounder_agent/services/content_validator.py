"""
Content Validator — programmatic quality gate for AI-generated content.

Runs hard rules against generated content BEFORE it can be published.
No LLM judgment — deterministic pattern matching that catches:
- Fabricated people, quotes, and statistics
- False claims about the company
- Unverifiable citations
- Impossible timeframes and metrics

Usage:
    from services.content_validator import validate_content
    issues = validate_content(title, content, topic)
    if issues:
        # Reject — content has quality issues
"""

import re
import time as _time
from dataclasses import dataclass, field

from services.logger_config import get_logger
from services.site_config import site_config as _sc

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus counter — per-rule warning emission (GH-91)
# ---------------------------------------------------------------------------
#
# Matt, 2026-04-20: the validator was already emitting warnings but nothing
# downstream counted them. Without aggregate visibility in Grafana,
# patterns like "unlinked_citation spiking across the week" go unnoticed
# until a reader points at an embarrassing post. This counter fixes that.
#
# Wrapped in a try/except so tests that stub out prometheus_client (or
# envs that never pulled the package) don't explode. If the dependency
# is missing we fall back to a no-op shim — nothing else in the module
# depends on the counter actually recording.

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]

    CONTENT_VALIDATOR_WARNINGS_TOTAL = _Counter(
        "content_validator_warnings_total",
        "Total warnings emitted by content_validator, labeled by rule category",
        ["rule"],
    )
except Exception:  # pragma: no cover — exercised only when prometheus_client is absent
    class _NoopCounter:
        def labels(self, **_kwargs):  # noqa: D401 — trivial shim
            return self

        def inc(self, _amount: float = 1.0) -> None:
            return None

    CONTENT_VALIDATOR_WARNINGS_TOTAL = _NoopCounter()  # type: ignore[assignment]


# Keywords that, when present in an unlinked-citation match, promote the
# warning to critical (Matt's call: "named source without a URL" is the
# hallucinated-attribution pattern, worse than a generic weasel).
_NAMED_SOURCE_KEYWORDS = (
    "medium",
    "article",
    "blog post",
    "documentation",
    "paper",
    "study",
)

# ============================================================================
# COMPANY FACTS — ground truth for fact-checking (configurable)
# Override via environment variables for your own brand
# ============================================================================


def _get_company_facts() -> dict:
    """Load company facts from DB (site_config) with env fallback."""
    return {
        "company_name": _sc.get("company_name", "My Company"),
        "founded_date": _sc.get("company_founded_date", "2025-01-01"),
        "founded_year": _sc.get_int("company_founded_year", 2025),
        "age_months": _sc.get_int("company_age_months", 12),
        "team_size": _sc.get_int("company_team_size", 1),
        "founder_name": _sc.get("company_founder_name", "Founder"),
        "known_employees": set(),
        "real_products": set(_sc.get("company_products", "").split(",")) if _sc.get("company_products") else set(),
        "real_tech": {"fastapi", "next.js", "postgresql", "ollama", "vercel", "grafana"},
    }


# Loaded at module import time — uses DB values from site_config cache. Not refreshed on config changes without reimport.
GLAD_LABS_FACTS = _get_company_facts()
_COMPANY_NAME = GLAD_LABS_FACTS["company_name"]

# People names that should NEVER appear (fabricated by LLMs)
FAKE_NAME_PATTERNS = [
    r"\b(?:Sarah|John|Emily|David|Michael|Jennifer|James|Jessica|Robert|Lisa)\s+[A-Z][a-z]+(?:,\s*(?:CEO|CTO|VP|Director|Lead|Head|Chief|Manager|Founder|Co-founder))",
    r"\b(?:Dr\.|Prof\.)\s+[A-Z][a-z]+\s+[A-Z][a-z]+",
    rf"(?:CEO|CTO|VP|Director|Lead Architect|Head of|Chief)\s+(?:at|of)\s+(?:{re.escape(_COMPANY_NAME)})",
]

# Fake statistic patterns
FAKE_STAT_PATTERNS = [
    r"\b\d{1,3}%\s+(?:reduction|increase|improvement|decrease|growth|decline|boost|drop|surge|rise)",
    r"(?:according to|a\s+\d{4}\s+(?:study|report|survey))\s+(?:by|from|conducted)",
    r"\b(?:McKinsey|Gartner|Forrester|Deloitte|BCG|Bain|Accenture)\s+(?:report|study|survey|research|found|estimates)",
    r"(?:research|data|studies)\s+(?:shows?|suggests?|indicates?|reveals?|confirms?)\s+that\s+\d",
]

# Impossible claims about the company (uses configurable company name)
_CN = re.escape(_COMPANY_NAME)
GLAD_LABS_IMPOSSIBLE = [
    rf"(?:{_CN}|our|we)\s+(?:has|have)\s+(?:been|spent)\s+(?:\w+\s+)*(?:years?|decade)",
    rf"(?:{_CN}|our)\s+(?:team|staff|employees|engineers|developers)\s+of\s+\d{{2,}}",
    rf"(?:{_CN}|our)\s+(?:clients?|customers?|users?)\s+(?:include|such as|like)\s+[A-Z]",
    rf"(?:{_CN}|we)\s+(?:processed|handled|served|generated)\s+(?:\d+\s*(?:million|billion|thousand))",
    rf"(?:{_CN}|our)\s+(?:revenue|profit|valuation|funding)",
]

# Fabricated quote patterns
FAKE_QUOTE_PATTERNS = [
    r'["\u201c][^"\u201d]{10,200}["\u201d]\s*(?:says?|said|explains?|explained|recalls?|recalled|notes?|noted|adds?|added)\s+[A-Z][a-z]+',
    r'(?:says?|said|explains?|recalled)\s+[A-Z][a-z]+\s+[A-Z][a-z]+(?:,\s*(?:CEO|CTO|VP|founder|director))',
]

# Hallucinated internal links — phrases that claim internal content exists
HALLUCINATED_LINK_PATTERNS = [
    r"\b(?:our|my|the)\s+(?:guide|article|post|tutorial|deep.dive|report)\s+on\s+[a-z]",
    r"(?:as\s+(?:we|I)\s+(?:discussed|explored|covered|explained|wrote))\s+in\s+(?:our|a\s+previous)",
    r"(?:check\s+out|see|read)\s+our\s+(?:guide|post|article|tutorial)",
]

# Unlinked citations — references to papers/studies/research by name without a URL.
# These are almost always hallucinated by the LLM. Real citations need real links.
# Matches patterns like "introduced in Paper Title", "according to Study Name".
# Only flags if the citation-like text is NOT inside a markdown link [text](url).
UNLINKED_CITATION_PATTERNS = [
    # "introduced in <Paper Title>" / "proposed in <Title>" — catches ALL-CAPS
    # acronyms (I-DLM), acronym-colon-title (I-DLM: Introspective...), and
    # plain title-case paper names.
    r"(?:introduced|proposed|described|presented|outlined|documented|published)\s+in\s+(?!\[)(?:[A-Z][A-Za-z0-9\-]*(?::\s+)?[A-Za-z]+(?:\s+[A-Za-z]+){1,10})",
    # "described in 'Paper Title'" — quoted paper references (real papers are linked)
    r"(?:described|referenced|cited|mentioned)\s+in\s+['\"\u2018\u201c][A-Z][^'\"\u2019\u201d]{15,100}['\"\u2019\u201d]",
    # "according to Title Case Source" (not followed by a link)
    r"(?:according\s+to|as\s+(?:highlighted|noted|reported|described|shown)\s+(?:in|by))\s+(?!\[)(?:[A-Z][A-Za-z0-9\-]*(?:\s+[A-Za-z]+){1,6})",
    # Bare paper-style titles with colon: "Word Word: Subtitle With Title Case"
    r"(?<!\[)(?:[A-Z][A-Za-z0-9\-]*(?:\s+[A-Z][a-z]+){1,}:\s+[A-Z][a-z]+(?:\s+[A-Za-z]+){2,})(?!\])",
    # "et al." references — almost certainly fabricated
    r"\b[A-Z][a-z]+\s+et\s+al\.?\s*(?:\(\d{4}\)|\[\d+\])?",
    # arXiv IDs without accompanying URL: "arXiv:2401.12345"
    r"\barXiv:\s*\d{4}\.\d{4,5}(?!\s*[\]\)])",
    # DOI without link: "doi:10.xxxx/..."
    r"\bdoi:\s*10\.\d{4,}/[^\s\]\)]+",
]

# Brand contradiction — we are Ollama-only, never promote paid cloud APIs
BRAND_CONTRADICTION_PATTERNS = [
    r"(?:pay(?:ing)?\s+(?:for|per)\s+(?:token|API|inference))\s+(?:to|with|from)\s+(?:OpenAI|Anthropic|Google)",
    r"(?:OpenAI|Anthropic)\s+(?:API|pricing|bill|invoice|subscription|cost)",
    r"(?:bill|invoice|cost)\s+from\s+(?:OpenAI|Anthropic|Google\s+Cloud)",
]

# Fabricated personal experience patterns — AI pretending to be a person
FABRICATED_EXPERIENCE_PATTERNS = [
    # "I was on a call with...", "I sat down with a client..."
    r"\bI\s+(?:was|sat|had|got)\s+(?:on\s+a\s+call|in\s+a\s+meeting|talking|chatting)\s+with\s+(?:a\s+)?(?:startup|client|founder|engineer|developer|CTO|CEO|team)",
    # "at my company", "at my current company", "at our company"
    r"\b(?:at|for)\s+(?:my|our)\s+(?:current\s+)?(?:company|startup|org|organization|employer|firm|agency)",
    # "a client of mine", "one of my clients", "a founder I know"
    r"\b(?:a\s+(?:client|customer|founder|friend|colleague)\s+(?:of\s+mine|I\s+(?:know|work|met)))",
    # "last week I...", "last month we...", "recently I..."
    r"\b(?:last\s+(?:week|month|year|quarter)|recently|the\s+other\s+day)\s+(?:I|we)\s+(?:was|were|had|got|built|deployed|switched|migrated)",
    # Fabricated dollar amounts in anecdotes: "$1,200/month", "saved us $X"
    r"\b(?:cost(?:ing)?|saved?|spent|paying|bill(?:ed)?)\s+(?:us\s+)?\$[\d,]+(?:/(?:month|year|mo|yr))?",
    # "he said", "she told me" — fabricated dialogue
    r'["\u201c][^"\u201d]{10,150}["\u201d]\s*(?:he|she|they)\s+(?:said|told|explained|replied|asked)',
]

# Leaked image generation prompts — italic descriptions after images
LEAKED_IMAGE_PROMPT_PATTERNS = [
    r"(?:^|\n)\s*:\s*\*[A-Z][^*]{30,}\*",  # `: *A split-screen comparison...*`
    r"(?:^|\n)\s*\*(?:A |An |Imagine |Visual |Split|Close)[^*]{40,}\*",  # standalone `*A description...*`
]

# Removed 2026-05-01: FIRST_PERSON_TITLE_PATTERNS — Matt killed the title
# pronoun gate after it became the dominant rejection reason (65 of 91
# programmatic-validator vetoes in a 24h window were "Title contains
# first-person pronoun"). The body-side voice rules in quality_scorers
# (`first_person_claims` for "I/we built/created/etc") still enforce
# newsroom voice in the post body — only the *title* check is gone.
# Original rule + tests preserved in git history if revival is needed.

# Known-wrong facts are now loaded from the `fact_overrides` DB table.
# Manage via pgAdmin or API — no redeployment needed.
# Cached in-memory with a 5-minute TTL.
_fact_overrides_cache: list[tuple[str, str, str]] = []
_fact_overrides_ts: float = 0.0
_FACT_OVERRIDES_TTL = 300  # seconds


def _load_fact_overrides_sync() -> list[tuple[str, str, str]]:
    """Load active fact overrides from DB (sync, cached).

    Returns list of (pattern, correct_fact, severity) tuples.
    Falls back to empty list if DB unavailable.
    """
    global _fact_overrides_cache, _fact_overrides_ts
    now = _time.time()
    if _fact_overrides_cache and (now - _fact_overrides_ts) < _FACT_OVERRIDES_TTL:
        return _fact_overrides_cache

    try:
        import asyncio
        import sys
        from pathlib import Path

        # brain.bootstrap.resolve_database_url() is the canonical DSN
        # resolver — no os.getenv in services (project-wide rule).
        try:
            _proj = Path(__file__).resolve()
            for _p in _proj.parents:
                if (_p / "brain" / "bootstrap.py").is_file():
                    if str(_p) not in sys.path:
                        sys.path.insert(0, str(_p))
                    break
            from brain.bootstrap import resolve_database_url
            db_url = resolve_database_url() or ""
        except Exception:
            db_url = ""
        if not db_url:
            return _fact_overrides_cache

        async def _fetch():
            import asyncpg
            conn = await asyncpg.connect(db_url, timeout=5)
            try:
                rows = await conn.fetch(
                    "SELECT pattern, correct_fact, severity FROM fact_overrides WHERE active = true"
                )
                return [(r["pattern"], r["correct_fact"], r["severity"]) for r in rows]
            finally:
                await conn.close()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, _fetch())
                result = future.result(timeout=5)
        else:
            result = asyncio.run(_fetch())

        _fact_overrides_cache = result
        _fact_overrides_ts = now
        logger.debug("[VALIDATOR] Loaded %d fact overrides from DB", len(result))
    except Exception as e:
        logger.warning("[VALIDATOR] fact_overrides DB load failed (using cache): %s", e)

    return _fact_overrides_cache


# Legacy alias — kept so nothing breaks if referenced externally
KNOWN_WRONG_HARDWARE_PATTERNS: list[tuple[str, str]] = []


# Filler phrases the writer falls back on when it has nothing specific to
# say. Every post I audited on 2026-04-11 had at least one of these. They
# add nothing and signal "AI-generated" to any attentive reader. Warning
# level — they hurt quality but don't rise to the fabrication bar.
FILLER_PHRASE_PATTERNS = [
    r"\bmany organizations have found\b",
    r"\bmany companies have found\b",
    r"\bmany (?:teams|developers|users) have discovered\b",
    r"\bthe landscape (?:of .+? )?is constantly evolving\b",
    r"\bin today'?s (?:fast-paced|rapidly evolving|digital|modern)\b",
    r"\bthe (?:journey|possibilities) (?:is|are) (?:rewarding|endless)\b",
    r"\bthe future of .+? is (?:here|local|bright|within reach)\b",
    r"\bunlock the (?:full )?potential of\b",
]

# LLM image placeholder artifacts — [IMAGE-1: description], [IMAGE: ...], etc.
IMAGE_PLACEHOLDER_PATTERNS = [
    r"\[IMAGE(?:-\d+)?:\s*[^\]]+\]",  # [IMAGE-1: description] or [IMAGE: description]
    r"\[FIGURE(?:-\d+)?:\s*[^\]]+\]",  # [FIGURE-1: description]
    r"\[DIAGRAM(?:-\d+)?:\s*[^\]]+\]",  # [DIAGRAM: description]
    r"\[CHART(?:-\d+)?:\s*[^\]]+\]",  # [CHART: description]
    r"\[SCREENSHOT(?:-\d+)?:\s*[^\]]+\]",  # [SCREENSHOT: description]
]


@dataclass
class ValidationIssue:
    """A single quality issue found in the content."""
    severity: str  # "critical", "warning"
    category: str  # "fake_person", "fake_stat", "glad_labs_claim", "fake_quote"
    description: str
    matched_text: str
    line_number: int = 0


@dataclass
class ValidationResult:
    """Result of content validation."""
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    score_penalty: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


def _strip_html(text: str) -> str:
    """Remove HTML tags for pattern matching."""
    return re.sub(r"<[^>]+>", "", text)


def _check_patterns(
    text: str,
    patterns: list,
    severity: str,
    category: str,
    description_template: str,
) -> list[ValidationIssue]:
    """Run regex patterns against text and return issues."""
    issues = []
    clean_text = _strip_html(text)
    lines = clean_text.split("\n")

    for pattern in patterns:
        for i, line in enumerate(lines, 1):
            for match in re.finditer(pattern, line, re.IGNORECASE):
                matched = match.group(0)[:100]
                issues.append(ValidationIssue(
                    severity=severity,
                    category=category,
                    description=description_template.format(matched=matched),
                    matched_text=matched,
                    line_number=i,
                ))
    return issues


# ---------------------------------------------------------------------------
# Hallucinated library / API reference detection (GH-83 part b)
# ---------------------------------------------------------------------------
#
# Real cases caught manually on 2026-04-21:
#   * `schedule_callback(event)` described as a "central asyncio function" —
#     not a real asyncio API (real: loop.call_soon / call_later / call_at).
#   * "explore CadQuery to see how asyncio is used" in an ai-ml post —
#     CadQuery is a 3D CAD library, topically orthogonal to asyncio.
#
# Strategy (low false positives by design):
#   1. Extract only identifiers that LOOK like library/API references:
#      backtick-quoted (`foo.bar`, `baz(args)`) or dotted CamelCase calls.
#   2. Compare against a known-good list: Python 3.12 stdlib + top-500
#      PyPI packages + common Ollama models. All three live as data files
#      under brain/hallucination-check/ — update in one place, no redeploy.
#   3. For library names in the post that ARE recognized, optionally check
#      topic coherence against brain/hallucination-check/library-topics.json.
#
# Files are loaded lazily + cached in-module; the caches are ephemeral
# (no TTL) because the lists are static data, not DB state.
from pathlib import Path as _Path


def _find_hc_dir() -> _Path:
    """Locate ``brain/hallucination-check/`` regardless of host vs container layout.

    On the host the directory lives at ``<repo-root>/brain/hallucination-check``.
    In the worker container ``brain/`` is bind-mounted at
    ``/opt/poindexter/brain`` (see docker-compose.local.yml), not as a
    descendant of this file's path — so ``parents[3]`` from /app/services
    overshoots the filesystem root and raises IndexError.

    Walk every ancestor of ``__file__`` looking for the directory, then
    fall back to the container mount path. If neither exists the lazy
    file loaders below will surface a clearer error at first use.
    """
    here = _Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "brain" / "hallucination-check"
        if candidate.is_dir():
            return candidate
    container_hc = _Path("/opt/poindexter/brain/hallucination-check")
    if container_hc.is_dir():
        return container_hc
    # Best-effort guess for diagnostic output; file reads will fail
    # explicitly if the directory really is missing.
    return here.parent.parent.parent.parent / "brain" / "hallucination-check"


_HC_DIR = _find_hc_dir()

_stdlib_names_cache: set[str] | None = None
_pypi_names_cache: set[str] | None = None
_ollama_names_cache: set[str] | None = None
_library_topics_cache: dict[str, list[str]] | None = None


def _normalize_pkg(name: str) -> str:
    """Normalize PyPI-style names: lowercase, dashes == underscores."""
    return name.strip().lower().replace("_", "-")


def _load_known_list(filename: str) -> set[str]:
    """Load a simple newline-delimited list file from brain/hallucination-check.

    Ignores blank lines and `#` comments. Normalizes each entry.
    Returns an empty set if the file is missing — missing data should
    degrade to "don't flag anything" rather than crash the pipeline.
    """
    path = _HC_DIR / filename
    if not path.is_file():
        logger.warning("[VALIDATOR] hallucination-check list missing: %s", path)
        return set()
    names: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                names.add(_normalize_pkg(line))
    except Exception as exc:  # pragma: no cover — best-effort file read
        logger.warning("[VALIDATOR] failed to load %s: %s", path, exc)
    return names


def _get_stdlib_names() -> set[str]:
    global _stdlib_names_cache
    if _stdlib_names_cache is None:
        _stdlib_names_cache = _load_known_list("stdlib-python-312.txt")
    return _stdlib_names_cache


def _get_pypi_names() -> set[str]:
    global _pypi_names_cache
    if _pypi_names_cache is None:
        _pypi_names_cache = _load_known_list("pypi-top-500.txt")
    return _pypi_names_cache


def _get_ollama_names() -> set[str]:
    global _ollama_names_cache
    if _ollama_names_cache is None:
        _ollama_names_cache = _load_known_list("ollama-models.txt")
    return _ollama_names_cache


def _get_library_topics() -> dict[str, list[str]]:
    """Load library -> expected-topics map. Empty dict on failure."""
    global _library_topics_cache
    if _library_topics_cache is not None:
        return _library_topics_cache
    import json
    path = _HC_DIR / "library-topics.json"
    result: dict[str, list[str]] = {}
    if not path.is_file():
        logger.warning("[VALIDATOR] library-topics.json missing: %s", path)
        _library_topics_cache = result
        return result
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        for k, v in data.items():
            if k.startswith("_") or not isinstance(v, list):
                continue
            result[_normalize_pkg(k)] = [str(t).strip().lower() for t in v if str(t).strip()]
    except Exception as exc:  # pragma: no cover
        logger.warning("[VALIDATOR] failed to parse %s: %s", path, exc)
    _library_topics_cache = result
    return result


# Reference-shaped identifiers. Only flag tokens that LOOK like an API call
# or a library reference; bare prose capitalized words are out of scope.
#   (a) Backtick-quoted identifier with a call or attribute access:
#       `foo()`, `foo.bar`, `Foo.bar()`, `foo.bar.baz(args)`
#   (b) Backtick-quoted module/library-looking bare token (no special chars):
#       `asyncio`, `cadquery` — included because real cases use plain
#       backticks around the lib name.
#   (c) Un-backticked "Package.method(...)" in narrative prose — e.g.
#       "explore CadQuery to see..." (bare capitalized product name
#       followed by one of "module" / "library" / "package" / "framework").
# Group 1 of each regex is the raw identifier (may be dotted).
_HALLUCINATED_REF_PATTERNS = [
    # Backtick-wrapped dotted call or attribute: `foo.bar`, `foo.bar()`
    re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)(?:\(\s*[^`]*\))?`"),
    # Backtick-wrapped bare function call: `schedule_callback(event)`
    re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})\(\s*[^`]*\)`"),
    # Backtick-wrapped bare module name (lowercase, includes - or _): `asyncio`
    re.compile(r"`([a-z][a-z0-9_\-]{2,})`"),
    # Narrative prose: "explore CadQuery to see..." / "the Foo library..."
    # Requires the word to be capitalized AND be followed by an English
    # hint that it's a code artifact. This narrows false positives from
    # prose proper nouns (which rarely carry those hints).
    re.compile(
        r"\b([A-Z][A-Za-z0-9]{2,}(?:\.[A-Za-z][A-Za-z0-9]*)?)\s+"
        r"(?:library|package|module|framework|SDK|API)\b"
    ),
    re.compile(
        r"(?:explor(?:e|ing|ed)|tr(?:y|ying|ied)|us(?:e|ing|ed)"
        r"|consider(?:ing)?|adopt(?:ing|ed)?|install(?:ing|ed)?"
        r"|import(?:ing|ed)?|leverag(?:e|ing|ed)|check(?:ing)?\s+out)"
        r"\s+`?([A-Z][A-Za-z0-9]{2,})`?"
    ),
]

# Identifiers we NEVER flag (pipeline internals, super-common Python builtins,
# common variable names that happen to look dotted, HTTP verbs, test keywords).
# Normalized to lowercase + dashes for matching against _normalize_pkg().
_HALLUCINATION_WHITELIST = {
    # Ambient project-local / generic names we don't want pattern-matching on
    "glad-labs", "poindexter", "ollama",
    # Python generic builtins and dunders that may show up in backticks
    "true", "false", "none", "self", "cls", "args", "kwargs",
    "print", "len", "range", "list", "dict", "set", "tuple", "str", "int",
    "float", "bool", "bytes", "type", "object", "super", "all", "any",
    "min", "max", "sum", "abs", "open", "map", "filter", "zip", "enumerate",
    "sorted", "reversed", "iter", "next", "input", "hash", "id", "repr",
    # HTTP verbs often shown in backticks in API writeups
    "get", "post", "put", "delete", "patch", "head", "options",
    # Test-framework keywords and common method stubs
    "assert", "yield", "async", "await", "lambda", "return",
    # Extremely common instance/variable names — "loop.call_soon" has root
    # "loop", which is an asyncio event loop instance, not a library. The
    # method name downstream (call_soon) is what matters; flagging "loop"
    # as a library generates high-noise false positives on any asyncio post.
    "loop", "app", "db", "client", "session", "config", "ctx", "context",
    "response", "request", "req", "res", "conn", "connection", "cursor",
    "user", "users", "data", "item", "items", "result", "results",
    "obj", "value", "values", "key", "keys", "event", "events", "error",
    "logger", "log", "logs", "state", "store", "router", "handler",
    "service", "services", "manager", "factory", "builder", "engine",
    "queue", "cache", "pool", "worker", "task", "tasks", "job", "jobs",
    "message", "messages", "payload", "header", "headers", "body", "field",
    "model", "models", "schema", "schemas", "view", "template",
    # Python style shorthands often used in examples
    "np", "pd", "plt", "tf", "torch",
}


def _extract_library_candidates(text: str) -> list[tuple[str, str]]:
    """Pull potential library/API references from the text.

    Returns list of (matched_raw, normalized_root) tuples. The "root" is
    the leading segment of a dotted name (e.g. 'asyncio.run' -> 'asyncio')
    because that is what we compare against the stdlib / PyPI lists.

    Deduplicated by the pair to avoid spamming issues for the same token.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    clean = _strip_html(text or "")
    for pattern in _HALLUCINATED_REF_PATTERNS:
        for m in pattern.finditer(clean):
            raw = m.group(1)
            if not raw:
                continue
            root = raw.split(".", 1)[0]
            norm = _normalize_pkg(root)
            if not norm or norm in _HALLUCINATION_WHITELIST:
                continue
            # Skip tokens that are just a single short letter (likely noise)
            if len(norm) < 3:
                continue
            key = (raw, norm)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _is_known_reference(norm_name: str) -> bool:
    """True if `norm_name` matches stdlib, top-500 PyPI, or Ollama models."""
    if norm_name in _get_stdlib_names():
        return True
    if norm_name in _get_pypi_names():
        return True
    if norm_name in _get_ollama_names():
        return True
    # Strip common Ollama suffixes (":7b", ":instruct", etc.) before final check
    base = norm_name.split(":", 1)[0]
    if base != norm_name and base in _get_ollama_names():
        return True
    return False


def _detect_hallucinated_references(
    title: str,
    content: str,
    topic: str,
    tags: list[str] | None = None,
) -> list[ValidationIssue]:
    """Flag library/API references that don't match any known source list.

    Also flags topic mismatches for libraries that ARE known (e.g. CadQuery
    in an ai-ml post). Both are emitted as `hallucinated_reference` warnings;
    the downstream severity-promotion step in validate_content() decides
    whether to upgrade any of them to critical.
    """
    issues: list[ValidationIssue] = []
    candidates = _extract_library_candidates(f"{title or ''}\n{content or ''}")
    if not candidates:
        return issues

    lib_topics = _get_library_topics()
    post_context_tokens: set[str] = set()
    for tag in tags or []:
        if tag:
            post_context_tokens.add(str(tag).strip().lower())
    # Fall back to topic + title words when tags aren't provided so we
    # still have *some* signal for topic coherence.
    for word in re.split(r"[\s,\-_/]+", (topic or "").lower()):
        word = word.strip()
        if word and len(word) > 2:
            post_context_tokens.add(word)
    for word in re.split(r"[\s,\-_/]+", (title or "").lower()):
        word = word.strip()
        if word and len(word) > 2:
            post_context_tokens.add(word)

    for raw, norm in candidates:
        if not _is_known_reference(norm):
            # Hallucinated reference — name doesn't match any known source.
            issues.append(ValidationIssue(
                severity="warning",
                category="hallucinated_reference",
                description=(
                    f"Likely hallucinated library/API reference: '{raw}'. "
                    f"Not found in Python stdlib, top-500 PyPI packages, "
                    f"or known Ollama models."
                ),
                matched_text=raw[:100],
            ))
            continue
        # Known reference — check topic coherence when we have a mapping.
        expected = lib_topics.get(norm)
        if not expected or not post_context_tokens:
            continue
        # A mismatch when: the library's expected topics share NO token
        # with the post's context (tags + topic + title words).
        overlap = False
        for topic_tag in expected:
            topic_tokens = {t for t in re.split(r"[\s\-_]+", topic_tag.lower()) if t}
            if topic_tokens & post_context_tokens or topic_tag in post_context_tokens:
                overlap = True
                break
        if overlap:
            continue
        issues.append(ValidationIssue(
            severity="warning",
            category="hallucinated_reference",
            description=(
                f"Library '{raw}' is off-topic for this post "
                f"(expected topics: {', '.join(expected)}). "
                "Possible semantic-embedding drift during research."
            ),
            matched_text=raw[:100],
        ))
    return issues


# ---------------------------------------------------------------------------
# Code-block density check (GH-234)
# ---------------------------------------------------------------------------
#
# Tech blogs without runnable code feel like surface summaries — "talks
# about Docker" rather than "shows Docker working." Pure-prose tech posts
# score worse on EEAT signals because they don't demonstrate first-hand
# expertise.
#
# This rule runs only when the post carries one of the configured tech
# tags (default: technical, ai, programming, ml, python, javascript,
# rust, go). It compares the number of fenced code blocks against the
# post's word count and emits a WARNING — never critical — when either
# threshold is missed:
#
#   (a) at least 1 fenced code block per N words (default N=700), AND
#   (b) at least P% of non-empty content lines live inside a fenced
#       code block (default P=20%, only applied to posts > 300 words).
#
# Intentionally a soft signal: operators may genuinely write a non-code
# tech post (architecture overview, postmortem). The warning surfaces in
# the multi_model_qa critique via the existing programmatic_validator
# review, which lets the human approver decide whether to override.

# Match an opening fenced code block (``` or ~~~), captured as group 1
# so we can find the matching closer with the same fence character.
_FENCE_RE = re.compile(r"^(\s*)(```|~~~)([^\n`~]*)$", re.MULTILINE)


def _count_code_blocks_and_lines(content: str) -> tuple[int, int, int]:
    """Walk ``content`` line-by-line and tally fenced code blocks.

    Returns ``(block_count, code_line_count, total_non_empty_lines)``.

    A "block" is any well-formed ``` ... ``` (or ~~~ ... ~~~) pair. An
    unterminated fence at EOF still counts as one block because the
    writer's intent was clear; we'd rather count it than reject the
    post for malformed markdown alone.

    "Code lines" are non-fence lines that sit between an opening and
    closing fence — used for the line-ratio sub-check.
    """
    if not content:
        return (0, 0, 0)
    block_count = 0
    code_lines = 0
    total_non_empty = 0
    in_block = False
    fence_char: str | None = None
    for raw in content.split("\n"):
        stripped = raw.strip()
        if not stripped:
            # Blank lines are excluded from the denominator so a heavily
            # paragraph-padded post doesn't dilute the ratio artificially.
            continue
        # Fence detection — must start the line (after optional whitespace)
        # and use only ``` or ~~~ for the fence itself.
        is_fence = False
        if stripped.startswith("```") and set(stripped[:3]) == {"`"}:
            is_fence = True
            this_fence = "```"
        elif stripped.startswith("~~~") and set(stripped[:3]) == {"~"}:
            is_fence = True
            this_fence = "~~~"
        if is_fence:
            if not in_block:
                in_block = True
                fence_char = this_fence
                block_count += 1
            elif fence_char == this_fence:
                in_block = False
                fence_char = None
            # Fence lines themselves don't count as code or prose for the
            # ratio; they're structural punctuation.
            continue
        total_non_empty += 1
        if in_block:
            code_lines += 1
    return (block_count, code_lines, total_non_empty)


def _strip_code_blocks_for_word_count(content: str) -> str:
    """Return ``content`` with the inside of every fenced block removed.

    Word count for the density ratio is *prose words only* — counting
    the words inside a code sample would let a single 200-line code
    block satisfy the per-700-words threshold trivially.
    """
    if not content:
        return ""
    out: list[str] = []
    in_block = False
    fence_char: str | None = None
    for raw in content.split("\n"):
        stripped = raw.strip()
        is_fence = False
        if stripped.startswith("```") and set(stripped[:3]) == {"`"}:
            is_fence = True
            this_fence = "```"
        elif stripped.startswith("~~~") and set(stripped[:3]) == {"~"}:
            is_fence = True
            this_fence = "~~~"
        if is_fence:
            if not in_block:
                in_block = True
                fence_char = this_fence
            elif fence_char == this_fence:
                in_block = False
                fence_char = None
            continue
        if not in_block:
            out.append(raw)
    return "\n".join(out)


def _is_tech_post(tags: list[str], topic: str, tech_tags: set[str]) -> bool:
    """Return True if any tag/topic token matches the configured tech-tag set.

    Matching is lowercase + whitespace/dash/underscore-tolerant so the
    operator can list ``"ai"`` and still catch tags like ``"AI/ML"``,
    ``"Artificial-Intelligence"`` (when "artificial intelligence" is on
    the list), etc. Empty inputs return False.
    """
    if not tech_tags:
        return False
    haystack: set[str] = set()
    for source in (*(tags or ()), topic or ""):
        if not source:
            continue
        token = str(source).strip().lower()
        if not token:
            continue
        haystack.add(token)
        for piece in re.split(r"[\s,/\-_]+", token):
            piece = piece.strip()
            if piece:
                haystack.add(piece)
    return any(t in haystack for t in tech_tags)


def _check_code_block_density(
    content: str,
    topic: str,
    tags: list[str],
) -> list[ValidationIssue]:
    """GH-234: warn when tech-tagged posts ship without enough code.

    All thresholds + the tag list are read from ``app_settings`` via
    ``site_config`` so operators can tune per niche without redeploys.
    Returns warnings only — never critical.
    """
    if not _sc.get_bool("code_density_check_enabled", True):
        return []
    tech_tags = {
        t.strip().lower()
        for t in _sc.get_list(
            "code_density_tag_filter",
            "technical,ai,programming,ml,python,javascript,rust,go",
        )
        if t and t.strip()
    }
    if not _is_tech_post(tags, topic, tech_tags):
        return []

    min_blocks_per_700w = _sc.get_int("code_density_min_blocks_per_700w", 1)
    min_line_ratio_pct = _sc.get_int("code_density_min_line_ratio_pct", 20)
    long_post_floor_words = _sc.get_int("code_density_long_post_floor_words", 300)

    block_count, code_lines, total_non_empty = _count_code_blocks_and_lines(content)
    prose_text = _strip_code_blocks_for_word_count(content)
    word_count = len(re.findall(r"\b[\w'-]+\b", prose_text))

    issues: list[ValidationIssue] = []

    # Sub-check (a): blocks-per-N-words floor. Only meaningful when the
    # post is long enough that "zero code blocks" is genuinely a signal —
    # a 200-word note doesn't need a snippet.
    if (
        min_blocks_per_700w > 0
        and word_count >= 200
    ):
        # Round up so a 701-word post still requires the floor count.
        expected_blocks = max(
            min_blocks_per_700w,
            -(-word_count * min_blocks_per_700w // 700),
        )
        if block_count < expected_blocks:
            issues.append(ValidationIssue(
                severity="warning",
                category="code_block_density",
                description=(
                    f"Tech post has {block_count} fenced code block(s) for "
                    f"{word_count} prose words; expected at least "
                    f"{expected_blocks} (threshold: "
                    f"{min_blocks_per_700w} per 700 words). Consider adding "
                    "a runnable example — pure-prose tech posts hurt EEAT signals."
                ),
                matched_text=f"blocks={block_count}, prose_words={word_count}",
            ))

    # Sub-check (b): code-line ratio floor for longer posts. Independent
    # of (a) — a post can have one giant block but still flunk this if
    # the code share is buried under prose.
    if (
        min_line_ratio_pct > 0
        and total_non_empty > 0
        and word_count >= long_post_floor_words
    ):
        ratio_pct = (code_lines * 100) // total_non_empty
        if ratio_pct < min_line_ratio_pct:
            issues.append(ValidationIssue(
                severity="warning",
                category="code_block_density",
                description=(
                    f"Tech post code-line ratio is {ratio_pct}% "
                    f"({code_lines}/{total_non_empty} non-empty lines); "
                    f"threshold is {min_line_ratio_pct}%. Add or expand "
                    "code samples so the post demonstrates the technique, "
                    "not just describes it."
                ),
                matched_text=f"code_lines={code_lines}, total_lines={total_non_empty}",
            ))

    return issues


def validate_content(
    title: str,
    content: str,
    topic: str = "",
    tags: list[str] | None = None,
) -> ValidationResult:
    """
    Validate content against hard quality rules.

    Returns ValidationResult with pass/fail and list of issues.
    Content fails if ANY critical issue is found.

    tags (GH-83 part b): optional list of the post's topic tags. When
    provided, the hallucinated-reference rule uses them to detect
    topic-mismatched library mentions (e.g. recommending CadQuery from
    an ai-ml post). Omitting tags keeps the rule working but with a
    weaker fallback based on the topic/title text.
    """
    issues: list[ValidationIssue] = []
    title = title or ""
    content = content or ""
    topic = topic or ""
    tags = list(tags) if tags else []
    full_text = f"{title}\n{content}"

    # 1. Check for fabricated people
    issues.extend(_check_patterns(
        full_text, FAKE_NAME_PATTERNS, "critical", "fake_person",
        "Fabricated person detected: '{matched}'"
    ))

    # 2. Check for fabricated statistics.
    # Matt 2026-04-11: "A fabrication is a fail, I can't be lying to the
    # audience. That kills brand credibility." Every fabrication
    # category is CRITICAL now — any match blocks approval. There is no
    # "probably fake" middle ground when the consequence is publishing
    # a lie under your byline.
    issues.extend(_check_patterns(
        full_text, FAKE_STAT_PATTERNS, "critical", "fake_stat",
        "Fabricated statistic: '{matched}'"
    ))

    # 3. Check for impossible company claims
    issues.extend(_check_patterns(
        full_text, GLAD_LABS_IMPOSSIBLE, "critical", "glad_labs_claim",
        f"Impossible claim about {_COMPANY_NAME}: " + "'{matched}'"
    ))

    # 4. Check for fabricated quotes
    issues.extend(_check_patterns(
        full_text, FAKE_QUOTE_PATTERNS, "critical", "fake_quote",
        "Fabricated quote detected: '{matched}'"
    ))

    # 4b. Check for fabricated personal experiences (AI pretending to be
    # human). Promoted to critical — a fake anecdote is the same class
    # of lie as a fake stat.
    issues.extend(_check_patterns(
        full_text, FABRICATED_EXPERIENCE_PATTERNS, "critical", "fabricated_experience",
        "Fabricated personal experience: '{matched}'"
    ))

    # 5. Check for hallucinated internal links. Promoted to critical —
    # a link that looks valid but leads nowhere is functionally a lie
    # to the reader.
    issues.extend(_check_patterns(
        full_text, HALLUCINATED_LINK_PATTERNS, "critical", "hallucinated_link",
        "Hallucinated internal link: '{matched}'"
    ))

    # 5b. Check for unlinked citations (hallucinated paper/study references)
    issues.extend(_check_patterns(
        full_text, UNLINKED_CITATION_PATTERNS, "warning", "unlinked_citation",
        "Unlinked citation — possible hallucinated reference: '{matched}'"
    ))

    # 5c. Hallucinated library/API reference detection (GH-83 part b).
    # Catches `schedule_callback(event)`-style fake asyncio functions and
    # topic-orthogonal library mentions ("explore CadQuery..." in ai-ml).
    # Emitted as warnings; the per-rule threshold promotion below escalates
    # to critical if the same category fires > N times (same plumbing as
    # #91's unlinked_citation path).
    issues.extend(_detect_hallucinated_references(title, content, topic, tags))

    # 5d. Code-block density (GH-234). Soft signal — tech-tagged posts
    # with too little runnable code get a warning (never critical) so
    # the human approver in multi_model_qa can decide whether the post
    # legitimately doesn't need code (architecture overview, postmortem)
    # or whether the writer surface-summarized a topic that needed
    # demonstration. Tag list + thresholds are DB-tunable.
    issues.extend(_check_code_block_density(content, topic, tags))

    # 6. Check for brand contradictions (promoting paid cloud APIs)
    issues.extend(_check_patterns(
        full_text, BRAND_CONTRADICTION_PATTERNS, "warning", "brand_contradiction",
        "Brand contradiction — references paid cloud API: '{matched}'"
    ))

    # 7. Check for leaked image generation prompts
    issues.extend(_check_patterns(
        full_text, LEAKED_IMAGE_PROMPT_PATTERNS, "warning", "leaked_image_prompt",
        "Leaked image generation prompt in content: '{matched}'"
    ))

    # 7b. Check for LLM image placeholder artifacts ([IMAGE-1: ...], [FIGURE: ...], etc.)
    issues.extend(_check_patterns(
        full_text, IMAGE_PLACEHOLDER_PATTERNS, "critical", "image_placeholder",
        "LLM image placeholder left in content: '{matched}'"
    ))

    # 7c. Known-wrong facts — loaded from DB (fact_overrides table).
    # Each row has its own explanation so the rewrite prompt carries the
    # correction, not just "you lied". Manageable via pgAdmin, no redeploy.
    _fact_overrides = _load_fact_overrides_sync()
    clean_full = _strip_html(full_text)
    for _hw_pat, _hw_reason, _hw_sev in _fact_overrides:
        for _hw_line_idx, _hw_line in enumerate(clean_full.split("\n"), 1):
            for _hw_match in re.finditer(_hw_pat, _hw_line, re.IGNORECASE):
                issues.append(ValidationIssue(
                    severity=_hw_sev,
                    category="known_wrong_fact",
                    description=f"{_hw_reason} Matched: '{_hw_match.group(0)[:80]}'",
                    matched_text=_hw_match.group(0)[:100],
                    line_number=_hw_line_idx,
                ))

    # 7c-bis. Removed 2026-05-01 — first-person title gate killed (see
    # FIRST_PERSON_TITLE_PATTERNS removal note at module top for context).

    # 7d. Filler phrases — "many organizations have found...", "the journey
    # is rewarding", etc. Warning level, score penalty only.
    issues.extend(_check_patterns(
        full_text, FILLER_PHRASE_PATTERNS, "warning", "filler_phrase",
        "Filler phrase: '{matched}' — replace with a specific, concrete claim"
    ))

    # 8. Check title for impossible claims (numeric and written-out years)
    WRITTEN_YEARS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    for word, num in WRITTEN_YEARS.items():
        if re.search(rf"\b{word}\s+years?\b", title, re.IGNORECASE) and num > 1:
            issues.append(ValidationIssue(
                severity="critical", category="glad_labs_claim",
                description=f"Title claims {word} years — {_COMPANY_NAME} is {GLAD_LABS_FACTS['age_months']} months old",
                matched_text=title,
            ))
    if re.search(r"\d+\s*years?", title, re.IGNORECASE):
        match = re.search(r"(\d+)\s*years?", title, re.IGNORECASE)
        years = int(match.group(1)) if match else 0
        if years > 1:
            issues.append(ValidationIssue(
                severity="critical",
                category="glad_labs_claim",
                description=f"Title claims {years} years — {_COMPANY_NAME} is {GLAD_LABS_FACTS['age_months']} months old",
                matched_text=title,
            ))

    # 8b. Structural banned headers — the prompts already tell the LLM not to
    # use generic section titles like "## Introduction" / "## Conclusion", but
    # some models ignore the rule. This is a warning (not critical): the post
    # is readable, but the score drops so the model learns the pattern over
    # time and we prefer regenerating when it happens.
    BANNED_HEADER_WORDS = {
        "introduction",
        "conclusion",
        "summary",
        "background",
        "overview",
        "final thoughts",
        "wrap-up",
        "wrap up",
        "the end",
    }
    for m in re.finditer(r"^#{2,3}\s+(.+?)\s*$", content, re.MULTILINE):
        heading = m.group(1).strip().lower().rstrip(":")
        if heading in BANNED_HEADER_WORDS:
            issues.append(ValidationIssue(
                severity="warning",
                category="banned_header",
                description=f"Generic section title: '{m.group(1).strip()}' — use a creative, benefit-focused heading instead",
                matched_text=m.group(0)[:80],
            ))

    # 8c. "In this post/article/guide" intros — a common LLM crutch the
    # prompts already ban. Warning-level; penalizes the score without
    # killing the post outright.
    first_500 = content[:500]
    for pat in (
        r"\bIn this (?:post|article|guide|blog post|tutorial)[,\s]",
        r"\bIn today'?s (?:fast-paced|digital|modern|competitive)",
    ):
        m = re.search(pat, first_500, re.IGNORECASE)
        if m:
            issues.append(ValidationIssue(
                severity="warning",
                category="filler_intro",
                description=f"Filler intro phrase: '{m.group(0).strip()}' — start with a concrete hook instead",
                matched_text=m.group(0)[:80],
            ))
            break

    # 9. Check for late acronym expansions — e.g. "CRM (Customer Relationship Management)"
    #    when the acronym was already used earlier without expansion
    for m in re.finditer(r'\b([A-Z]{2,6})\s*\(([A-Z][a-z][\w\s]{5,50})\)', content):
        acronym = m.group(1)
        # Check if the acronym appears earlier in the content (before this match)
        prior_text = content[:m.start()]
        prior_uses = len(re.findall(rf'\b{re.escape(acronym)}\b', prior_text))
        if prior_uses >= 2:
            issues.append(ValidationIssue(
                severity="warning", category="late_acronym_expansion",
                description=f"Acronym '{acronym}' expanded after {prior_uses} prior uses — expand on first use or not at all",
                matched_text=m.group(0)[:80],
            ))

    # 10. Truncation detection — content that ends mid-sentence indicates
    # the LLM hit its token limit. This is critical because it means the
    # reader gets an incomplete article.
    stripped_content = content.rstrip()
    if stripped_content and len(stripped_content) > 200:
        # Check if content ends with a sentence-ending character
        last_char = stripped_content[-1]
        if last_char not in '.!?"\u201d)\u2019':
            # Check it's not a code block or list that legitimately ends without punctuation
            last_line = stripped_content.split('\n')[-1].strip()
            _in_code = last_line.startswith('```') or last_line.startswith('    ')
            _is_heading = last_line.startswith('#')
            _is_list_item = re.match(r'^[-*\d]+[.)]\s', last_line) or re.match(r'^[-*]\s', last_line)
            if not (_in_code or _is_heading or _is_list_item):
                issues.append(ValidationIssue(
                    severity="critical",
                    category="truncated_content",
                    description=(
                        f"Content appears truncated — ends with '{last_line[-60:]}' "
                        f"which is not a complete sentence. The LLM likely hit its token limit."
                    ),
                    matched_text=stripped_content[-100:],
                ))

    # 11. Title diversity — detect repetitive opener patterns
    _BANNED_OPENERS = [
        "beyond the", "beyond", "building", "unlocking", "the ultimate",
        "the hidden", "the silent", "the invisible", "the secret",
        "mastering", "revolutionizing", "the complete", "the definitive",
        "how to build", "scale your", "why you need",
    ]
    if title:
        title_lower = title.lower().strip()
        for opener in _BANNED_OPENERS:
            if title_lower.startswith(opener):
                issues.append(ValidationIssue(
                    severity="warning",
                    category="title_diversity",
                    description=(
                        f"Title starts with overused opener '{opener}'. "
                        "Rotate title structure for better variety."
                    ),
                    matched_text=title[:60],
                ))
                break

    # ------------------------------------------------------------------
    # Severity promotion (GH-91)
    # ------------------------------------------------------------------
    # Two-stage promotion wired in 2026-04-20 so validator warnings
    # stop being silent. Before this, a post with 9 `unlinked_citation`
    # warnings reached QA with 0 critical and still passed Q80. Now:
    #
    #   (a) Per-rule threshold: if any single warning category exceeds
    #       `content_validator_warning_reject_threshold` (default 3),
    #       promote every warning in that category to critical. This
    #       catches "writer hallucinated 9 Medium articles" patterns
    #       that were not surfacing as rejects.
    #
    #   (b) Named-source-without-URL: specifically for
    #       `unlinked_citation`, if the matched text names a source
    #       type ("Medium", "article", "blog post", "documentation",
    #       "paper", "study") and has no URL within ~100 chars of the
    #       match, upgrade to critical individually. This is the
    #       hallucinated-attribution pattern — worse than a generic
    #       weasel because the writer literally named a source it
    #       failed to cite.
    #
    # Both promotions preserve the original `category` so downstream
    # rewrite prompts still see *which* rule fired, and Prometheus
    # counts are taken from the original warnings (not post-promotion)
    # so Grafana panels keep showing the raw warning volume.
    warning_counts_by_category: dict[str, int] = {}
    for _i in issues:
        if _i.severity == "warning":
            warning_counts_by_category[_i.category] = (
                warning_counts_by_category.get(_i.category, 0) + 1
            )

    # Emit Prometheus counter BEFORE promotion so Grafana sees the raw
    # warning volume regardless of whether this post was auto-rejected.
    for _cat, _count in warning_counts_by_category.items():
        try:
            CONTENT_VALIDATOR_WARNINGS_TOTAL.labels(rule=_cat).inc(_count)
        except Exception as _exc:  # pragma: no cover — best-effort metric
            logger.debug("[VALIDATOR] prometheus counter emit failed: %s", _exc)

    # (b) Named-source-without-URL promotion. Runs per-issue so we can
    # look at each match in isolation; a post with one fabricated
    # "as noted in this Medium article" should reject even if the
    # category count is only 1.
    _clean_full_text = _strip_html(full_text)
    for _i in issues:
        if _i.severity != "warning" or _i.category != "unlinked_citation":
            continue
        _match_lower = _i.matched_text.lower()
        if not any(kw in _match_lower for kw in _NAMED_SOURCE_KEYWORDS):
            continue
        # Look for a URL within ~100 chars after the match; if missing,
        # treat as a hallucinated attribution and promote to critical.
        _idx = _clean_full_text.find(_i.matched_text)
        _context_window = ""
        if _idx != -1:
            _context_window = _clean_full_text[
                max(0, _idx - 20): _idx + len(_i.matched_text) + 100
            ]
        else:
            _context_window = _i.matched_text
        _has_url = bool(re.search(r"https?://\S+", _context_window))
        if not _has_url:
            _i.severity = "critical"
            _i.description = (
                "Named source without accompanying URL (hallucinated attribution): "
                f"{_i.matched_text!r}"
            )

    # (a) Per-rule threshold promotion. Read the threshold from
    # site_config (DB-first) with a hardcoded floor of 3 so the guard
    # still fires on a cold-boot environment with no settings loaded.
    _warning_threshold = _sc.get_int(
        "content_validator_warning_reject_threshold", 3,
    )
    if _warning_threshold > 0:
        for _i in issues:
            if (
                _i.severity == "warning"
                and warning_counts_by_category.get(_i.category, 0) > _warning_threshold
            ):
                _i.severity = "critical"
                _i.description = (
                    f"{_i.description} "
                    f"(promoted: {warning_counts_by_category[_i.category]} "
                    f"{_i.category} warnings exceeds reject threshold of "
                    f"{_warning_threshold})"
                )

    # Calculate score penalty
    score_penalty = sum(10 for i in issues if i.severity == "critical")
    score_penalty += sum(3 for i in issues if i.severity == "warning")

    passed = all(i.severity != "critical" for i in issues)

    result = ValidationResult(passed=passed, issues=issues, score_penalty=score_penalty)

    if issues:
        logger.warning(
            "[VALIDATOR] Content '%s': %d critical, %d warnings",
            title[:50], result.critical_count, result.warning_count,
        )
        for issue in issues[:5]:  # Log first 5
            logger.warning(
                "[VALIDATOR]   [%s] %s: %s",
                issue.severity, issue.category, issue.description,
            )

    return result


# ============================================================================
# ASYNC URL VERIFICATION — checks that cited URLs actually resolve (#214)
# Call separately from the async pipeline (validate_content is sync)
# ============================================================================

async def verify_content_urls(content: str) -> list[ValidationIssue]:
    """Extract all URLs from content and verify they resolve.

    Returns a list of ValidationIssues for dead/broken links.
    This is async because it makes HTTP requests.
    """
    import httpx

    issues: list[ValidationIssue] = []
    # Extract markdown links: [text](url) and bare https:// URLs
    url_pattern = re.compile(
        r'(?:\[([^\]]*)\]\((https?://[^)]+)\))'  # [text](url)
        r'|(?<![(\[])(https?://[^\s)\]<>"]+)'      # bare url
    )

    urls_found: list[tuple[str, str]] = []  # (display_text, url)
    for match in url_pattern.finditer(content):
        if match.group(2):  # markdown link
            urls_found.append((match.group(1) or "", match.group(2)))
        elif match.group(3):  # bare url
            urls_found.append(("", match.group(3)))

    if not urls_found:
        return issues

    # Skip internal links (our own site) and known-good domains.
    # Domain list comes from site_config (site_domains = comma-separated),
    # not hardcoded — lets operators bring their own brand (#198).
    _raw = _sc.get("site_domains", "")
    skip_domains = {d.strip().lower() for d in _raw.split(",") if d.strip()}
    skip_domains.add("localhost")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Poindexter-LinkChecker/1.0)"},
    ) as client:
        for display_text, url in urls_found:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.lower()
                if domain in skip_domains or domain.endswith(".localhost"):
                    continue

                resp = await client.head(url, timeout=10)
                # Accept 2xx and 3xx as valid
                if resp.status_code >= 400:
                    issues.append(ValidationIssue(
                        severity="critical",
                        category="dead_link",
                        description=f"Dead link (HTTP {resp.status_code}): {url[:80]}",
                        matched_text=f"[{display_text}]({url})" if display_text else url,
                    ))
            except httpx.TimeoutException:
                issues.append(ValidationIssue(
                    severity="warning",
                    category="slow_link",
                    description=f"URL timed out (10s): {url[:80]}",
                    matched_text=url[:100],
                ))
            except Exception as e:
                issues.append(ValidationIssue(
                    severity="warning",
                    category="unresolvable_link",
                    description=f"Cannot resolve URL: {url[:60]} ({type(e).__name__})",
                    matched_text=url[:100],
                ))

    # Citation count check
    external_citations = [
        (t, u) for t, u in urls_found
        if urlparse(u).netloc.lower() not in skip_domains
    ]
    if len(external_citations) == 0:
        issues.append(ValidationIssue(
            severity="warning",
            category="no_citations",
            description="No external citations found. Content should reference real sources.",
            matched_text="",
        ))

    return issues
