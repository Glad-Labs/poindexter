# Research + Web Research

**Files:**

- `src/cofounder_agent/services/research_service.py`
- `src/cofounder_agent/services/web_research.py`

**Tested by:**

- `src/cofounder_agent/tests/unit/services/test_research_service.py`
- `src/cofounder_agent/tests/unit/services/test_web_research.py`

**Last reviewed:** 2026-04-30

> Documented as a pair because `ResearchService` calls `WebResearcher`
> and `MultiModelQA` calls `WebResearcher` directly for fact-check.
> CLAUDE.md groups them under one row in the load-bearing services table.

## What it does

`ResearchService.build_context(topic)` assembles a single
prompt-ready string that the writer prepends to the draft prompt so
generated content cites real sources instead of fabricated ones. It
fans out to three sources, in this priority order:

1. **Known reference database** — a curated `{keyword: [{title, url}, ...]}`
   map of official documentation links (FastAPI, PostgreSQL, Docker,
   etc.). Defaults are tech-oriented; operators in non-tech niches
   override the entire map via `app_settings.known_references_json`.
2. **Internal links** — published posts on the operator's own site
   that share keywords with the topic, so the writer can build internal
   linking.
3. **Web search** — DuckDuckGo via `WebResearcher.search_simple()`,
   free and key-less.

`WebResearcher` is the lower-level web layer. It exposes two modes:
`search()` does DuckDuckGo + concurrent HTTP fetch + BeautifulSoup
text extraction (used for "give me real content"); `search_simple()`
returns just titles/URLs/snippets (used by `ResearchService` and the
QA fact-check gate, which only need the URL list).

The pair replaces an older Serper-based ($0.001/search) implementation
with $0 DuckDuckGo. Intentionally graceful — if DDG rate-limits,
times out, or returns nothing, every entry-point logs a warning and
returns an empty list/string rather than crashing the pipeline.

## Public API

### `research_service.py`

- `ResearchService(pool=None, settings_service=None)` — constructor.
  `pool` is an asyncpg pool (used only for the internal-links lookup);
  `None` disables that source.
- `await rs.build_context(topic, category="technology") -> str` —
  formatted prompt block. Header sections include
  `VERIFIED REFERENCE LINKS`, `EXISTING POSTS ON OUR SITE`,
  `RECENT WEB SOURCES`, and a `CITATION GUIDANCE` footer telling the
  writer how to use the links. `category` is reserved (no behavior yet).
- `get_known_references() -> dict[str, list[dict[str, str]]]` —
  module function that returns the active reference map. Reads
  `known_references_json` from app_settings; falls back to
  `_DEFAULT_KNOWN_REFERENCES` if unset, malformed, or shape-invalid.
  Entries are normalized to lowercase keys with `{title, url}` shape.
- `KNOWN_REFERENCES` — backward-compat module alias to the DEFAULT
  map. New callers should use `get_known_references()`.
- `await research_topic(query, max_sources=None) -> str` — module-level
  shim used by the TWO_PASS writer mode (`writer_rag_modes/two_pass.py`)
  to fill `[EXTERNAL_NEEDED]` markers without needing a DB pool. Wraps
  `ResearchService(pool=None).build_context(query)`. Returns
  `"[research stub for: <query>]"` on failure so the writer keeps moving.

### `web_research.py`

- `WebResearcher()` — constructor (no args, no state).
- `await wr.search(query, num_results=5) -> list[dict]` — full pipeline:
  DuckDuckGo search, then concurrent HTTP fetch + content extraction
  for each result. Each dict has `title`, `url`, `snippet`, `content`.
- `await wr.search_simple(query, num_results=5) -> list[dict]` —
  search only (no content fetch). Faster; used when the caller only
  needs URLs and snippets.
- `wr.format_for_prompt(results, max_chars=3000) -> str` — render a
  result list as a markdown-formatted prompt block with
  `WEB RESEARCH (current sources — cite these, do not fabricate URLs):`
  header. Truncates at `max_chars` to control prompt budget.

## Configuration

All from `app_settings` via `site_config`.

### `research_service.py`

- `known_references_json` (default empty — falls back to built-in
  `_DEFAULT_KNOWN_REFERENCES`). JSON object mapping lowercase keyword
  to a list of `{title, url}` entries. Customize per niche (cooking,
  gardening, legal) so the writer cites authoritative sources for
  that field. Malformed JSON logs a warning and falls back to defaults.
- `writer_rag_research_topic_max_sources` (default `2`) — advisory
  cap used by the `research_topic()` shim. Currently logged but not
  enforced; the underlying `build_context` caps internally
  (5 web results, 8 references). Plumbing a true cap requires
  refactoring `build_context` (out of scope for the migration that
  introduced it — see Task 14 in
  `docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md`).

### `web_research.py`

All read at call time through `_web_research_int(key, default)` so live
app_settings changes apply without a restart (#198):

- `web_research_max_content_chars` (default `2000`) — per-page text
  extraction cap.
- `web_research_fetch_timeout_seconds` (default `10`) — per-URL HTTP
  timeout.
- `web_research_max_concurrent` (default `3`) — fetch parallelism
  semaphore for `search()`.
- `web_research_search_timeout_seconds` (default `20`) — hard cap on
  the DuckDuckGo call itself; `asyncio.wait_for` enforces this so a
  hung DDG request can't stall the pipeline.

## Dependencies

- **Reads from:**
  - `posts` table (status = 'published') for internal-link candidates
    via the ILIKE-by-word-overlap query.
  - DuckDuckGo (via `ddgs` package; falls back to legacy
    `duckduckgo_search` import name if `ddgs` is missing).
  - Arbitrary HTTP origins via `httpx.AsyncClient` (User-Agent
    `Mozilla/5.0 (compatible; ContentResearcher/1.0)`).
  - `services.site_config` for the tunables above.
- **Writes to:** nothing. Both services are read-only / pure.
- **External APIs:** DuckDuckGo (no key) + outbound HTTP fetches.
- **Sister-service callers (non-exhaustive):**
  - `services.stages.generate_content` — primary writer pipeline.
  - `services.writer_rag_modes.two_pass` — TWO_PASS revise loop via
    the `research_topic()` shim.
  - `services.multi_model_qa` — the web fact-check gate constructs
    its own `WebResearcher()` directly.
  - `services.title_generation` — title generator searches for
    competing titles in the niche.
  - `services.topic_sources.web_search` — topic discovery tap.

## Failure modes

- **DuckDuckGo rate-limited / network down** —
  `WebResearcher._ddg_search` catches the exception, logs
  `[RESEARCH] DuckDuckGo search failed: ...` at warning level, returns
  `[]`. Callers then return an empty section; `build_context` still
  returns whatever other sections it could assemble.
- **DuckDuckGo hangs** — `asyncio.wait_for` aborts after
  `web_research_search_timeout_seconds`. Logged as
  `[RESEARCH] DuckDuckGo search timed out after Ns`.
- **`ddgs` package missing** — falls back to importing legacy
  `duckduckgo_search`. If both are missing the outer try/except
  catches the ImportError and returns `[]`.
- **HTTP fetch fails or returns non-200** — `_extract_content` returns
  empty string. The caller still keeps the title/URL/snippet from the
  search step.
- **Internal-links DB query fails** — caught, logged at debug,
  returns `[]`. Pool=None short-circuits to `[]` without touching
  the DB at all.
- **`research_topic()` shim — any exception** — returns
  `"[research stub for: <query>]"` so the TWO_PASS writer can keep
  going; the downstream validator will still flag a missing citation.
- **Malformed `known_references_json`** —
  `get_known_references()` logs a warning and falls back to the
  built-in defaults. No crash propagation.

## Common ops

- **Bring your own niche references** (replace tech defaults):
  ```sql
  UPDATE app_settings SET value = '{"sourdough":[{"title":"King Arthur Sourdough Guide","url":"https://www.kingarthurbaking.com/learn/guides/sourdough"}]}'
  WHERE key = 'known_references_json';
  ```
- **Tighten DDG timeout when it's hanging:**
  `poindexter set web_research_search_timeout_seconds 10`
- **Test research output for a topic without running the pipeline:**
  ```python
  python -c "import asyncio; from services.research_service import ResearchService; print(asyncio.run(ResearchService(pool=None).build_context('FastAPI streaming')))"
  ```
- **Spot-check what DDG returns** — same shape, faster:
  `WebResearcher().search_simple("topic", num_results=3)`.
- **Audit research-quality of recent generations** — research output
  isn't persisted; rerun `build_context()` against the post's topic
  to see what the writer was given. (Persisting research output is
  TBD — needs operator confirmation if this becomes a recurring need.)

## See also

- `docs/architecture/services/multi_model_qa.md` — how the web
  fact-check gate uses `WebResearcher` for adversarial QA.
- `docs/architecture/anti-hallucination.md` — research is the FIRST
  line of defense (real citations beat downstream link-verification).
- `docs/architecture/content-pipeline.md` — pipeline ordering.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_paid_apis.md`
  — why DuckDuckGo replaced Serper.
