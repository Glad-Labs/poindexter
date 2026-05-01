# Internal Link Coherence

**File:** `src/cofounder_agent/services/internal_link_coherence.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_internal_link_coherence.py`
**Last reviewed:** 2026-04-30

## What it does

`InternalLinkCoherenceFilter` is the gatekeeper between the
candidate-collection step of the internal-link recommender and the
point where candidates are rendered into the writer prompt. It exists
to fix two specific failure modes (GH-88) that pre-filter recommenders
hit:

1. **Off-topic neighbours.** Embedding similarity put the CadQuery
   post in the "related" list for any "engineering fundamentals"
   topic, so the writer pinned `Consider exploring CadQuery` calls-to-
   action on asyncio and AI-engineering posts.
2. **Single-target spam.** With no cap, the same target slug could be
   suggested as "related" on an unbounded number of posts.

The filter applies two guards per candidate, in order: (1) **tag
coherence** — source and target must share at least one normalized
topic tag; (2) **single-target cap** — no candidate may already have
N+ inbound internal links from published posts. Rejections are
recorded on the original `LinkCandidate.rejection_reason` so audit
logging and debug dumps can explain why a candidate was filtered.

The filter is best-effort: when the DB is unreachable or
queries fail, it logs and lets candidates through unfiltered, on the
"a half-broken recommender is still better than no recommender"
principle. Both guards are configurable and default to ON.

## Public API

### Top-level helpers

- `normalize_tag_set(tags) -> set[str]` — folds a heterogeneous list
  (strings, dicts with `slug`/`name`, mixed-case names) into a
  canonical `tags.slug`-format set so set-overlap is meaningful.
- `await get_tag_slugs_for_post(pool, *, post_id=None, slug=None) -> set[str]` —
  looks up a target post's tag slugs by either `post_id` (UUID
  string) or `slug`. Returns `set()` when the pool is missing,
  neither identifier was given, or the query fails.
- `await count_inbound_links_to_slug(pool, slug) -> int` — counts
  published posts whose body contains a `/posts/<slug>` link. Uses
  ILIKE for the prefilter and a regex post-filter to avoid
  false positives where one slug is a prefix of another.

### `LinkCandidate` dataclass

Fields: `slug` (required), `title` (required), `post_id` (optional —
the ILIKE-based collection path in `ResearchService` only has
title+slug), `similarity`, `tag_slugs`, `inbound_count`,
`rejection_reason`, `metadata`. The filter hydrates `tag_slugs` and
`inbound_count` lazily — callers can pre-populate them to skip those
DB calls.

### `InternalLinkCoherenceFilter`

- `InternalLinkCoherenceFilter(*, pool=None, tag_coherence_required=None, single_target_cap=None, cap_enabled=None)` —
  constructor. Each kwarg defaults to its `app_settings` value (or
  to the strict-by-default constants if the setting is unset).
- `await filt.filter_candidates(*, source_tags, candidates) -> list[LinkCandidate]` —
  main entry. Returns the survivors in input order. Rejected
  candidates are NOT returned but their `rejection_reason` is set on
  the original object so callers retaining the full list can
  introspect.
- `filt.tags_overlap(source, target) -> bool` (staticmethod) — pure
  set-intersection check after `normalize_tag_set`.
- `filt.under_cap(inbound_count) -> bool` — `True` if the cap is
  disabled OR the count is strictly below the cap.

## Key behaviors / invariants

- **Strict by default.** Both guards default to enabled. To loosen,
  set `internal_link_tag_coherence_required=false` or
  `internal_link_single_target_cap_enabled=false` in `app_settings`.
- **No source tags = reject everything** when tag-coherence is on.
  This is deliberate: silently passing every candidate when the
  source has no tags is exactly the bug the gate exists to prevent.
  Matches the project-wide "no silent defaults" stance.
- **Untagged targets are rejected** when tag-coherence is on. Legacy
  posts created before the `post_tags` migration land here. There's
  no "fall back to category match" path — fix by tagging the post.
- **Tag normalization is one-way.** `normalize_tag_set` lower-cases,
  slugifies (`re.sub(r"[^a-z0-9]+", "-", ...)`) and strips edge
  dashes. Inputs can be names (`"3D Modeling"`), slugs
  (`"3d-modeling"`), or dicts with `slug`/`name` keys — all collapse
  to the same value.
- **Single-target cap is lazy.** `count_inbound_links_to_slug`
  doesn't run unless a candidate has cleared the tag check, so
  rejected-by-tag candidates don't waste a query.
- **Inbound count is approximate but skewed safe.** The slug-prefix
  edge case (e.g. `foo` matching inside `foo-bar`) over-counts, which
  triggers the cap slightly sooner than necessary. That's the safer
  direction — recommender errs toward variety.
- **DB failure = fail open per call.** Each helper catches its own
  exceptions and returns `set()` / `0`. The filter then sees an
  unfiltered candidate and either rejects it (if tag-coherence
  requires tags it can't fetch) or passes it through (if the cap
  query fails — count defaults to 0).

## Configuration

- `internal_link_tag_coherence_required` (default `true`) — toggles
  the tag-overlap requirement.
- `internal_link_single_target_cap` (default `3`) — max inbound
  internal links per target slug. Strict less-than comparison.
- `internal_link_single_target_cap_enabled` (default `true`) —
  toggles the cap enforcement.

All three are read via `services.site_config.site_config.get` with
the constructor kwargs as overrides for tests / one-off invocations.
Reads are wrapped in try/except — broken site_config falls through
to the strict defaults, never raises.

## Dependencies

- **Reads from:**
  - `posts` + `post_tags` + `tags` tables — for target tag lookup
    via `get_tag_slugs_for_post`.
  - `posts.content` — ILIKE scan for inbound link counting via
    `count_inbound_links_to_slug` (filtered to
    `status='published'`).
  - `services.site_config.site_config` — for the three tunable
    settings.
- **Writes to:** nothing. Pure read-only filter.
- **External APIs:** none.
- **Callers:**
  - `services.research_context.build_rag_context` — wraps the
    pgvector-based candidate list. Pulls `source_tags` from the
    content task and applies the filter before rendering the
    "RELATED POSTS WE'VE PUBLISHED" prompt block.
  - `services.research_service` (mentioned in the module docstring as
    the ILIKE-based path that triggered the original GH-88 bug —
    confirm in code if you're refactoring; today only
    `research_context.py` actually imports the filter).

## Failure modes

- **DB pool is `None`** — both helpers return their empty/zero
  defaults; the filter rejects all candidates if tag-coherence is on
  (target tags can't be fetched), or passes them through if it's off.
- **Tag lookup query fails** — `get_tag_slugs_for_post` catches and
  returns `set()`. Same downstream behavior as "no tags."
- **Inbound count query fails** — `count_inbound_links_to_slug`
  catches and returns `0`, so the cap check passes. Conservative
  failure mode.
- **`filter_candidates` itself raises** (shouldn't, but): caller in
  `research_context` catches and logs `"Internal-link coherence
filter failed"` via `logger.exception` — the candidate list falls
  through un-rendered (no RAG block in the prompt for that draft).
- **Cap fires too aggressively** — usually a slug-prefix collision
  (one slug being a substring of another with a `-` boundary). The
  regex post-filter is supposed to catch this; if it doesn't, file a
  test case with the offending slug pair.

## Common ops

- **Loosen the cap globally:**
  ```bash
  poindexter set internal_link_single_target_cap 5
  ```
- **Disable the cap entirely (debug):**
  ```bash
  poindexter set internal_link_single_target_cap_enabled false
  ```
- **Disable tag-coherence (NOT recommended):**
  ```bash
  poindexter set internal_link_tag_coherence_required false
  ```
- **Find slugs that hit the cap recently:** grep production logs
  for `[LINK_COHERENCE] cap reached for <slug>: N inbound (cap=...)`.
- **Audit a specific candidate:** construct one and run it through
  the filter directly:
  ```python
  from services.internal_link_coherence import (
      InternalLinkCoherenceFilter, LinkCandidate
  )
  cand = LinkCandidate(slug="foo", title="Foo")
  survivors = await InternalLinkCoherenceFilter(pool=pool).filter_candidates(
      source_tags=["python", "asyncio"],
      candidates=[cand],
  )
  print(cand.rejection_reason or "passed")
  ```

## See also

- `services.research_context.build_rag_context` — the call site that
  wires the filter into the RAG path.
- `docs/architecture/services/research-and-web-research.md` — the
  research stage pair this filter sits between (candidate
  collection ↔ writer prompt).
- `docs/architecture/services/content_router_service.md` — the
  pipeline whose RAG block is what this filter actually constrains.
