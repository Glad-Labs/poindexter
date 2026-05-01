# Content Validator

**File:** `src/cofounder_agent/services/content_validator.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_content_validator.py`
**Last reviewed:** 2026-04-30

## What it does

`validate_content(title, content, topic, tags)` runs deterministic
regex rules against generated content and returns a
`ValidationResult` with a list of `ValidationIssue` records, a
`passed: bool`, and a `score_penalty: int`. No LLM calls — this is the
fast hard-rules layer that catches the patterns LLMs reliably get
wrong: fabricated people, made-up statistics, hallucinated citations,
fake personal anecdotes, leaked image-prompt artifacts, contradictions
of brand-known facts.

A `ValidationResult.passed` is `False` if ANY issue is `severity =
"critical"`. Warnings drop the score (`-3 each`), criticals drop it
harder (`-10 each`) and block publish.

The validator is one of three QA layers:

- This file — programmatic regex (no LLM).
- `quality_service.py` — heuristic + LLM-based scoring.
- `multi_model_qa.py` — adversarial multi-reviewer aggregator that
  consumes this validator's `ValidationResult` as one of its inputs.

A separate async helper `verify_content_urls(content)` does HTTP HEAD
against every URL in the content (plus a no-citations check) and
returns its own `list[ValidationIssue]` — kept out of `validate_content`
so the sync path stays sync.

## Public API

- `validate_content(title, content, topic="", tags=None) -> ValidationResult` —
  the single sync entry point. Runs ALL rules in order, then applies
  severity-promotion (see below), then returns.
- `await verify_content_urls(content) -> list[ValidationIssue]` —
  async URL liveness check. Skips internal links (domains listed in
  `app_settings.site_domains`) and emits `dead_link` (HTTP 4xx/5xx),
  `slow_link` (timeout), `unresolvable_link` (other exceptions), and
  a single `no_citations` warning if the content has zero external
  URLs.
- `ValidationResult(passed, issues, score_penalty)` — dataclass.
  Properties: `critical_count`, `warning_count`.
- `ValidationIssue(severity, category, description, matched_text, line_number=0)` —
  dataclass. `severity` is `"critical"` or `"warning"`. `category`
  values are documented per-rule below.
- `CONTENT_VALIDATOR_WARNINGS_TOTAL` — Prometheus counter
  (`content_validator_warnings_total{rule=...}`), incremented per
  warning category emitted (GH-91). Falls back to a no-op shim if
  `prometheus_client` isn't installed.
- Module-level pattern lists (exposed but generally not callable from
  outside): `FAKE_NAME_PATTERNS`, `FAKE_STAT_PATTERNS`,
  `GLAD_LABS_IMPOSSIBLE`, `FAKE_QUOTE_PATTERNS`,
  `FABRICATED_EXPERIENCE_PATTERNS`, `HALLUCINATED_LINK_PATTERNS`,
  `UNLINKED_CITATION_PATTERNS`, `BRAND_CONTRADICTION_PATTERNS`,
  `LEAKED_IMAGE_PROMPT_PATTERNS`, `IMAGE_PLACEHOLDER_PATTERNS`,
  `FILLER_PHRASE_PATTERNS`, `FIRST_PERSON_TITLE_PATTERNS`.

### Rule categories

| Category                 | Severity  | Notes                                                  |
| ------------------------ | --------- | ------------------------------------------------------ |
| `fake_person`            | critical  | Sarah/John-style names + role suffix                   |
| `fake_stat`              | critical  | "%-reduction", McKinsey-style citations                |
| `glad_labs_claim`        | critical  | Impossible claims about company size/age               |
| `fake_quote`             | critical  | Quoted dialogue + attribution to invented person       |
| `fabricated_experience`  | critical  | "I sat down with...", "at my company"                  |
| `hallucinated_link`      | critical  | "our guide on X", "see our post"                       |
| `image_placeholder`      | critical  | `[IMAGE-1: ...]`, `[FIGURE: ...]`                      |
| `truncated_content`      | critical  | Doesn't end with sentence punctuation                  |
| `first_person_title`     | critical  | "we"/"our"/"I" in the title (after quote-strip)        |
| `unlinked_citation`      | warning\* | Promoted to critical when source is named without URL  |
| `hallucinated_reference` | warning\* | Library/API reference not in stdlib/PyPI/Ollama lists  |
| `code_block_density`     | warning   | Tech-tagged post with insufficient code                |
| `brand_contradiction`    | warning   | "OpenAI API pricing", "AWS bill"                       |
| `leaked_image_prompt`    | warning   | `*A split-screen comparison...*` italics               |
| `known_wrong_fact`       | DB-driven | Severity per `fact_overrides.severity`                 |
| `filler_phrase`          | warning   | "many organizations have found", etc.                  |
| `banned_header`          | warning   | "## Introduction", "## Conclusion", etc.               |
| `filler_intro`           | warning   | "In this post,...", "In today's fast-paced..."         |
| `late_acronym_expansion` | warning   | "CRM (Customer Relationship Management)" after 2+ uses |
| `title_diversity`        | warning   | "Beyond the...", "Mastering...", "Unlocking..."        |
| `dead_link`              | critical  | HTTP 4xx/5xx (from `verify_content_urls`)              |
| `slow_link`              | warning   | Timed out (from `verify_content_urls`)                 |
| `unresolvable_link`      | warning   | Network/DNS failure (from `verify_content_urls`)       |
| `no_citations`           | warning   | Zero external URLs (from `verify_content_urls`)        |

\* Subject to severity promotion — see Failure modes.

## Configuration

Most rules are pattern-driven and not individually tunable, but the
following live in `app_settings` via `services.site_config`:

Company facts (loaded once at module import time — restart required
to pick up changes):

- `company_name` (default `"My Company"`)
- `company_founded_date` (default `"2025-01-01"`)
- `company_founded_year` (default `2025`)
- `company_age_months` (default `12`) — used by title-year-claim rule
- `company_team_size` (default `1`)
- `company_founder_name` (default `"Founder"`)
- `company_products` (default empty, comma-separated)

Severity promotion (GH-91):

- `content_validator_warning_reject_threshold` (default `3`) — when
  ANY single warning category exceeds this count, every warning in
  that category is promoted to critical. Set to `0` to disable.

Code-block density (GH-234):

- `code_density_check_enabled` (default `True`)
- `code_density_tag_filter` (default
  `"technical,ai,programming,ml,python,javascript,rust,go"`)
- `code_density_min_blocks_per_700w` (default `1`)
- `code_density_min_line_ratio_pct` (default `20`)
- `code_density_long_post_floor_words` (default `300`) — line-ratio
  rule only fires above this word count.

URL verification (`verify_content_urls`):

- `site_domains` (comma-separated, no default — operators bring their
  own brand) — domains to skip in liveness checks, plus the
  no-citations counting uses this to identify external vs internal
  URLs. `localhost` is always added.

DB-backed (no app_settings keys, not refreshable without restart):

- `fact_overrides` table — `(pattern, correct_fact, severity, active)`.
  Cached in-process for `_FACT_OVERRIDES_TTL = 300` seconds. Manage
  with pgAdmin or the API; no redeploy needed for new entries to take
  effect.

Disk-backed lists (under `brain/hallucination-check/`, located via
ancestor walk + `/opt/poindexter/brain/hallucination-check` fallback):

- `stdlib-python-312.txt` — Python 3.12 stdlib module names
- `pypi-top-500.txt` — top 500 PyPI packages
- `ollama-models.txt` — known Ollama model names
- `library-topics.json` — `{library: [topic, ...]}` for topic-coherence
  cross-check

Lists are loaded lazily and cached for the process lifetime.

## Dependencies

- **Reads from:**
  - `services.site_config` — company facts + tunables.
  - `fact_overrides` table — DB-driven known-wrong-fact list.
  - `brain.bootstrap.resolve_database_url()` — DSN resolver for the
    fact_overrides loader (no `os.getenv` in services).
  - Disk lists under `brain/hallucination-check/`.
  - `prometheus_client` (optional) for the warnings counter.
- **Writes to:**
  - `CONTENT_VALIDATOR_WARNINGS_TOTAL` Prometheus counter (incremented
    BEFORE severity promotion, so Grafana sees raw warning volume).
- **External APIs:**
  - `verify_content_urls` does outbound HTTP HEAD (User-Agent
    `Mozilla/5.0 (compatible; Poindexter-LinkChecker/1.0)`,
    10s timeout, follows redirects).
- **Sister-service callers:**
  - `services.multi_model_qa` — wraps `validate_content` as the
    `programmatic_validator` reviewer; calls `verify_content_urls`
    when `qa_citation_verify_enabled` is set.
  - `services.ai_content_generator` — uses validation in its
    refinement loop (legacy generation path).

## Failure modes

- **Severity promotion (a) — per-category threshold (GH-91):** if any
  warning category fires more than
  `content_validator_warning_reject_threshold` times in one post,
  every warning in that category is promoted to critical. Description
  is suffixed with `(promoted: N <category> warnings exceeds reject
threshold of T)`. The original warning categories are still emitted
  to Prometheus before promotion.
- **Severity promotion (b) — named-source-without-URL (GH-91):** any
  individual `unlinked_citation` warning whose matched text contains
  one of `medium`, `article`, `blog post`, `documentation`, `paper`,
  `study`, AND has no `https?://` URL within ~100 chars after the
  match, is promoted to critical with description "Named source
  without accompanying URL (hallucinated attribution): ...". Fires
  per-issue regardless of category count.
- **`fact_overrides` DB lookup fails** — caught, logged at warning
  with `[VALIDATOR] fact_overrides DB load failed (using cache): ...`.
  Falls back to whatever's in `_fact_overrides_cache` (may be empty
  on a cold start). Validation continues without the DB-driven rules.
- **Prometheus counter unavailable** — module-level shim
  (`_NoopCounter`) used at import time if `prometheus_client` is
  missing. Counter calls become no-ops; nothing else breaks.
- **`brain/hallucination-check/*.txt` missing** —
  `_load_known_list` logs warning, returns empty set.
  `_is_known_reference` then returns False for everything, which
  would normally flag every backticked identifier as hallucinated —
  but the `_HALLUCINATION_WHITELIST` and the strict reference-shape
  patterns keep noise manageable. Still, missing data files degrade
  the quality of this rule significantly. Watch the import-time log.
- **`library-topics.json` missing or unparseable** — logged, returns
  `{}`. Topic-coherence cross-check is then skipped (libraries that
  ARE recognized always pass).
- **Truncation false-positive on legit list-ending posts** — the
  detector ignores lines starting with code fences, headings, or
  list markers, so most cases are handled. Posts that legitimately
  end on a quote or parenthesis also pass (the closing-bracket set
  includes `"`, `'`, `)`, `”`, `’`).
- **Title quote-stripping** — first-person title detection strips
  quoted substrings first so idioms like "It Works on My Machine"
  don't match. Both straight and curly quotes (single + double).
- **Company name with regex metacharacters** — the rules use
  `re.escape(_COMPANY_NAME)` so brand names with `.` / `+` / `*` etc.
  don't blow up the patterns.

## Common ops

- **Add a new known-wrong fact** (no redeploy):
  ```sql
  INSERT INTO fact_overrides (pattern, correct_fact, severity, active)
  VALUES ('the RTX 5090 has 32GB VRAM',
          'The RTX 5090 has 32GB GDDR7 VRAM (announced Jan 2025).',
          'warning', true);
  ```
  Picks up within `_FACT_OVERRIDES_TTL` (300s) on the next call.
- **Tune the per-category warning threshold** (more lenient):
  `poindexter set content_validator_warning_reject_threshold 5`
- **Disable the code-density rule for a specific niche** — drop the
  niche's tags from `code_density_tag_filter`, or
  `poindexter set code_density_check_enabled false`.
- **Inspect which rules are firing in production** —
  `sum by (rule)(rate(content_validator_warnings_total[1h]))` in
  Grafana. (The counter is emitted pre-promotion so it shows raw
  signal, not blocking decisions.)
- **Run validation against a draft locally:**
  ```python
  from services.content_validator import validate_content
  result = validate_content("My Title", "Body...", topic="fastapi", tags=["technical"])
  for issue in result.issues:
      print(issue.severity, issue.category, issue.description)
  ```
- **Refresh hallucination-check lists** — edit the files under
  `brain/hallucination-check/`, then restart the worker (the
  in-process cache is module-level and never expires).

## See also

- `docs/architecture/anti-hallucination.md` — three-layer defense
  (prompts, programmatic validator, multi-model QA).
- `docs/architecture/services/multi_model_qa.md` — how this validator
  feeds the adversarial aggregator.
- `docs/architecture/services/quality_service.md` — companion scoring
  layer (heuristic + LLM, vs this validator's pure-regex hard rules).
- `brain/hallucination-check/README.md` (if present) — manage the
  stdlib/PyPI/Ollama and library-topics data files.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_silent_defaults.md`
  — why criticals must hard-block.
