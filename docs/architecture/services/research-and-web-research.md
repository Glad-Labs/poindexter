# Research + Web Research

**Files:**

- `src/cofounder_agent/poindexter/services/research_service.py`
- `src/cofounder_agent/poindexter/services/research_quality_service.py`
- `src/cofounder_agent/poindexter/services/web_research.py`

**Tested by:**

- `src/cofounder_agent/tests/unit/services/test_research_service.py`
- `src/cofounder_agent/tests/unit/services/test_research_quality_service.py`
- `src/cofounder_agent/tests/unit/services/test_web_research.py`
- `src/cofounder_agent/tests/unit/services/migrations/test_drop_org_from_research_tier1_domains.py`

**Last reviewed:** 2026-09-28

> Documented together because `ResearchService` calls `WebResearcher` for its
> web tier and `ResearchQualityService` to filter and rank it. `MultiModelQA`
> also calls `WebResearcher` directly, for the web fact-check.

## What it does

`ResearchService.build_context(topic)` assembles the research corpus the
writer is grounded on, so generated content cites real sources instead of
fabricated ones. The same string, stored as `research_context`, is what the
`ragas` / `faithfulness` rails and `qa.numeric_fidelity` score the draft
against. It renders three sections, in this order:

1. **`VERIFIED REFERENCE LINKS`**: a curated `{keyword: [{title, url}, ...]}`
   map of official documentation (FastAPI, PostgreSQL, Docker, ...). Defaults
   are tech-oriented; other niches replace the whole map via
   `known_references_json`.
2. **`EXISTING POSTS ON OUR SITE`**: published posts whose title or slug
   shares a word with the topic, for internal linking.
3. **`RECENT WEB SOURCES (cite if relevant)`**: the web tier, built in three
   steps:
   1. **Search and read.** `WebResearcher.search()` asks DuckDuckGo for 5
      results and fetches and extracts each page's text. With
      `research_extract_web_content=false` it calls `search_simple()` instead,
      which returns snippets only.
   2. **Drop what we could not read.** A result whose page could not be
      fetched is not a source the writer may cite
      (`research_require_fetched_source_for_citation`; see
      [anti-hallucination.md](../anti-hallucination.md)).
   3. **Filter, dedup and rank.** `ResearchQualityService.filter_and_score`
      takes the readable results. It drops those whose snippet is too thin to
      be a source, collapses near-duplicates to their best copy, and orders the
      rest by a weighted score. Each source renders as
      `- [title](url): <first 100 chars of snippet>` plus a bounded
      `Source text:` excerpt of the page.

A `CITATION GUIDANCE:` footer follows whenever any section rendered.
`writer_core` records the string at
`pipeline_versions.stage_data -> 'task_metadata' ->> 'research_context'`.

Two production paths call it: the canonical_blog writer
(`modules/content/writer_core.py::_collect_research_context`, once per task)
and the two_pass writer's `[EXTERNAL_NEEDED]` lookups (`research_topic`, once
per marker, no DB pool).

### The quality step

`ResearchQualityService` had no production caller from
Glad-Labs/poindexter#367 until 2026-09-28. That PR deleted the content_agent
research agent, its only caller. When it was wired in, the 181 web tiers stored
on prod (2026-06-23 → 09-26) were replayed through it, after the fetch gate as
`build_context` runs it. That leaves 671 readable sources:

| Effect                      | Count                  | What it was                                                                                                                                                                  |
| --------------------------- | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Duplicate collapsed         | 13 sources in 10 tiers | arXiv html + abs + a Hugging Face papers page; a Springer PDF + article + RePEc; book blurbs on author, publisher and Amazon pages; posts syndicated to LinkedIn or Substack |
| Thin snippet dropped        | 8 sources              | a Telegram channel preview, a cookie banner, a YouTube footer, an aggregator's "Visit the post for more."                                                                    |
| First-listed source changed | 1 tier                 | a `.edu` page that sat right behind a Medium post                                                                                                                            |
| Order changed further down  | 6 more tiers           |                                                                                                                                                                              |
| Tier emptied by the filter  | 0                      | `research_web_sources_all_filtered` would never have fired                                                                                                                   |

The replay reads the render's 100-character snippet teasers. Live DuckDuckGo
snippets run 27-57 words, which matters most for the thin-snippet filter (fewer
drops) and snippet-length scoring. A live check of 19 real topics on
2026-09-28 agreed with the replay: 1 thin page, and 1 changed first slot
(github.com, a fresher page, over the daily.dev aggregator above it).

**Why the search order is in the score.** Ranked on its other four components
alone, the service replaced DuckDuckGo's first result in 102 of the 181
replayed tiers and 13 of the 19 live ones. In the replay only a fifth of
those changes were credibility-driven; the rest were snippet length, recency
and uniqueness, half by a margin under 0.03. One live case was anthropic.com,
the primary source for its own post, dropping below an aggregator that sat at
#5. The search engine's order is the strongest relevance signal available, so
it is the fifth component. At `research_search_rank_weight=0.4` a tier-1
(`.edu` / `.gov` / `.ac.uk`) host right behind a result can pass it, and so can
several signals together, but a tier-2 host alone cannot. Tier 2's promotions
were a coin flip in the evidence: two good (github.com over aggregators), two
bad (dev.to over bun.com's own site; a GitHub page mirroring Hacker News over
the Authors Guild's post about its own lawsuit). At 0.3 both bad ones happened;
at 0.4 neither does.

Scoring, each component 0.0-1.0, combined as a weighted **average** (so the
weights are relative and need not sum to 1):

- **Domain credibility**: 0.95 for a host under a `research_tier1_domains`
  suffix (`edu`, `gov`, `ac.uk`), 0.85 for a `research_tier2_domains` host or
  any subdomain of one, 0.65 for every other host. A top-level domain is not
  treated as evidence: `org` left tier 1 and the 0.5 floor for unfamiliar TLDs
  was removed when the replay showed both reordering on noise (the docstring
  of `_score_domain_credibility` has the numbers). To lift a publication, add
  it to tier 2.
- **Snippet quality**: 0.5, plus up to 0.3 for length, plus up to 0.2 for the
  share of the topic's significant (4+ letter) words the snippet contains as
  whole words, minus 0.3 for ad or error-page phrasing.
- **Recency**, from the date DuckDuckGo leads the snippet with ("3 days ago -",
  "Mar 15, 2024 ·"): 0.9 up to `research_recency_fresh_days` old, 0.8 up to
  `research_recency_recent_days`, 0.6 beyond, and 0.7 when there is no date.
- **Uniqueness**: 1 minus the snippet's highest similarity to any other
  surviving source.
- **Search rank**: 1.0 for the first result searched, falling evenly to 0.0
  for the last.

Dedup compares snippets word by word. Sources at or above
`research_dedup_similarity_threshold` are one source. The copy with the best
credibility, snippet and recency survives. Search rank and uniqueness sit out
that choice, because otherwise a top-ranked Amazon listing would beat the
publisher's own page for the same book. The survivor then takes the best
search position any copy had, since the search engine ranked the work, not the
host. Dedup runs after the fetch filter, so a cluster never keeps an
unreadable copy over the readable one.

## Public API

### `research_service.py`

- `ResearchService(pool=None, settings_service=None, *, site_config, research_quality=None)`.
  `pool` is used only for the internal-links lookup (`None` disables it).
  `research_quality` defaults to a `ResearchQualityService` built from
  `site_config`.
- `await rs.build_context(topic, category="technology") -> str`: the corpus
  described above. `category` is reserved (no behavior yet).
- `get_known_references(*, site_config) -> dict[str, list[dict[str, str]]]`:
  the active reference map. Reads `known_references_json` and falls back to
  `_DEFAULT_KNOWN_REFERENCES` when it is unset, malformed or shape-invalid.
- `KNOWN_REFERENCES`: backward-compat alias to the DEFAULT map.
- `await research_topic(query, max_sources=None, *, site_config) -> str`: the
  two_pass writer's `[EXTERNAL_NEEDED]` shim. Wraps
  `ResearchService(pool=None).build_context(query)`, and returns
  `"[research stub for: <query>]"` on failure so the writer keeps moving.
- `RESEARCH_RENDER_SENTINEL` (`"CITATION GUIDANCE:"`): lets `writer_core`
  recognise a stored render on a task re-run and skip rebuilding it.

### `research_quality_service.py`

- `ResearchQualityService(*, site_config)`. Every tunable is read from
  `app_settings` on each call, so the `AppContainer.research_quality_service`
  cached instance never goes stale.
- `svc.filter_and_score(results, query=None) -> list[ScoredSource]`. Takes
  `WebResearcher` result dicts (`title`, `url`, `snippet`, `content`) and
  returns survivors, highest `overall_score` first. Logs
  `ResearchQualityService: Processed N results, kept M (T thin snippet, D duplicate)`.
- `ScoredSource`: `title`, `url`, `snippet`, `content`, `domain`, the four
  component scores and `overall_score`.

### `web_research.py`

- `WebResearcher(*, site_config)`.
- `await wr.search(query, num_results=5) -> list[dict]`: DuckDuckGo search,
  then concurrent fetch + extraction through the SSRF guard. Each dict has
  `title`, `url`, `snippet`, `content` (`""` when the fetch failed).
- `await wr.search_simple(query, num_results=5) -> list[dict]`: search only.
- `wr.format_for_prompt(results, max_chars=3000) -> str`: render a result list
  as a `WEB RESEARCH` prompt block.

## Configuration

All from `app_settings` via `site_config`.

### `research_service.py`

- `known_references_json` (default empty → built-in map).
- `research_extract_web_content` (default `true`): `search()` with page text,
  or `search_simple()` snippets only.
- `research_require_fetched_source_for_citation` (default `true`): drop web
  results whose page could not be fetched.
- `research_web_content_chars_per_source` (default `600`): the `Source text:`
  excerpt per source.
- `writer_rag_research_topic_max_sources` (default `2`): advisory cap for
  `research_topic()`. Logged, not enforced; `build_context` caps internally at
  5 web results and 8 references.

### `research_quality_service.py`

- `research_min_snippet_length` (`50`) / `research_min_snippet_words` (`10`):
  a snippet under either is too thin to be a source.
- `research_dedup_similarity_threshold` (`0.7`): word-level similarity at which
  two snippets are one source.
- `research_tier1_domains` (`edu,gov,ac.uk`) / `research_tier2_domains`
  (`medium.com,dev.to,github.com,stackoverflow.com,wikipedia.org,arxiv.org,…`):
  comma-separated; an entry matches that host and its subdomains; a non-empty
  value replaces the defaults.
- `research_credibility_weight` (`0.4`), `research_snippet_quality_weight`
  (`0.3`), `research_recency_weight` (`0.2`), `research_uniqueness_weight`
  (`0.1`), `research_search_rank_weight` (`0.4`): relative weights of a
  weighted average. They are read through `_weight()`'s f-string, so the
  phantom-read lint cannot see them; `TestScoringWeightSettings` pins each
  seeded key to its read.
- `research_recency_fresh_days` (`7`) / `research_recency_recent_days` (`365`).

### `web_research.py`

All read at call time through `_web_research_int(key, default)`:

- `web_research_max_content_chars` (`2000`): per-page extraction cap.
- `web_research_fetch_timeout_seconds` (`10`): per-URL fetch timeout.
- `web_research_max_concurrent` (`3`): fetch parallelism for `search()`.
- `web_research_search_timeout_seconds` (`20`): hard cap on each DuckDuckGo
  attempt.
- `web_research_ddg_retry_attempts` (`3`) / `web_research_ddg_retry_base_delay_ms`
  (`500`): bounded exponential backoff with jitter when DuckDuckGo throttles.

## Dependencies

- **Reads from:** `posts` (`status = 'published'`) for internal links;
  DuckDuckGo via the `ddgs` package; arbitrary HTTP origins through
  `url_scraper._safe_get` (SSRF-guarded, User-Agent from
  `build_crawler_ua(..., product="PoindexterContentResearcher")`);
  `SiteConfig` for the tunables above.
- **Writes to:** nothing directly. Findings go to `audit_log` through
  `emit_finding`.
- **External APIs:** DuckDuckGo (no key) and outbound page fetches.
- **Callers:**
  - `modules.content.writer_core` (the `content.generate_draft` atom): builds
    the canonical_blog `research_context`.
  - `modules.content.atoms.two_pass_writer`: `[EXTERNAL_NEEDED]` lookups via
    `research_topic()`.
  - `modules.content.multi_model_qa`: the web fact-check calls
    `WebResearcher.search()` directly.
  - `services.title_generation`: `search_simple()` for competing titles.
  - `services.topic_sources.web_search`: `search_simple()` for topic discovery.

## Failure modes

- **DuckDuckGo throttled / network down**: `_ddg_search` retries with backoff,
  then logs a warning and returns `[]`; `build_context` renders whatever other
  sections it has.
- **DuckDuckGo hangs**: `asyncio.wait_for` aborts after
  `web_research_search_timeout_seconds`.
- **`ddgs` missing**: `_ddg_search` logs a warning and returns `[]`.
- **A page fetch fails**: `WebResearcher.search` keeps the result with
  `content=""` and emits `web_research_extract_failed` (keyed by failing host).
  `build_context` drops it from the citable corpus and logs a warning.
- **Every web result unfetchable**: no web section, and a
  `research_web_sources_all_unfetchable` finding. The writer falls back on
  model knowledge.
- **Every readable result rejected by the quality filter**: no web section,
  and a `research_web_sources_all_filtered` finding (`warn`, Discord, daily
  per-kind cooldown). Dedup always keeps one copy, so this means every snippet
  was under the length / word minimums; if it recurs across topics, those two
  settings are too strict.
- **Internal-links query fails**: `internal_link_search_failed` finding, `[]`.
- **`research_topic()` raises**: returns the stub string.
- **Malformed `known_references_json`**: warning, built-in defaults.

## Common ops

- **See what the writer was given** for a recent task:
  ```sql
  SELECT task_id, stage_data -> 'task_metadata' ->> 'research_context'
  FROM pipeline_versions
  WHERE task_id = '<task_id>'
  ORDER BY version DESC LIMIT 1;
  ```
- **Build a corpus without running the pipeline** (makes live DuckDuckGo and
  page fetches):
  ```python
  import asyncio
  from poindexter.services.research_service import research_topic
  from poindexter.services.site_config import SiteConfig
  print(asyncio.run(research_topic("FastAPI streaming", site_config=SiteConfig())))
  ```
- **Keep DuckDuckGo's order exactly** (filter and dedup only): set all five
  `research_*_weight` settings to `0`. Every score ties, the sort is stable,
  and a duplicate cluster keeps its first-listed copy.
- **Rank on quality alone**, ignoring the search order: set
  `research_search_rank_weight` to `0`. The replay above shows why that is not
  the default.
- **Lift a publication**: add its host to `research_tier2_domains`
  (comma-separated; subdomains follow).
- **Bring your own niche references**:
  ```sql
  UPDATE app_settings SET value = '{"sourdough":[{"title":"King Arthur Sourdough Guide","url":"https://www.kingarthurbaking.com/learn/guides/sourdough"}]}'
  WHERE key = 'known_references_json';
  ```
- **Tighten the DuckDuckGo timeout** when it hangs:
  `poindexter settings set web_research_search_timeout_seconds 10`

## Known limits

- **Authoritative `.org` hosts now score neutral**, python.org, owasp.org,
  jstor.org and imf.org among them. List the ones a niche relies on in
  `research_tier2_domains`. Official documentation for known tools already
  reaches the writer through `VERIFIED REFERENCE LINKS`, independent of
  ranking.
- **Tier 2's defaults include user-generated platforms** (github.com,
  medium.com, dev.to), which score like established publishers. At the default
  weight that alone does not reorder adjacent results, but it still decides
  which copy of a duplicate survives and adds up with other signals. Edit the
  list per niche.
- **Relevance beyond the search order is word overlap with the topic.** It
  matches whole significant words, so "compiler" does not match "compilers".

## See also

- [anti-hallucination.md](../anti-hallucination.md): research is the first
  line of defense; the fetch gate and the quality step are documented there too.
- [site_config.md](site_config.md): why `_weight()`'s dynamic keys need a
  hand-seeded row and a pinning test.
- [multi_model_qa.md](multi_model_qa.md): how the web fact-check uses
  `WebResearcher`.
- `feedback_no_paid_apis` (operator design note): why DuckDuckGo replaced
  Serper.
