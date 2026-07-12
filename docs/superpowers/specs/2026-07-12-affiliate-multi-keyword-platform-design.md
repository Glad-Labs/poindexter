# Affiliate links: multi-keyword matching, platform tracking, CSV import

**Status:** Approved design, ready for implementation planning.
**Date:** 2026-07-12
**Related:** `docs/superpowers/plans/2026-07-12-affiliate-referrals-page.md` (prior work — schema `description`/`category`, `/referrals` page), `docs/operations/affiliate-links.md` (operator runbook, to be updated by this work).

## Motivation

The affiliate-link injection atom (`content.inject_affiliate_links`) matches
exactly one keyword phrase per link (`affiliate_links.keyword`, a single
`UNIQUE` text column). Matt's real hardware-affiliate spreadsheet (13 rows,
9 of them Amazon hardware products) makes the gap obvious: each product's
only available text is a long, full Amazon listing title (e.g. "ASUS ROG
Astral NVIDIA GeForce RTX 5090 32GB GDDR7 OC Edition Gaming Graphics Card
(PCIe 5.0, HDMI/DP 2.1, ...)"), which will essentially never appear verbatim
in blog prose. A post is far more likely to say "RTX 5090" or "the ASUS ROG
Astral" — several short aliases, not the one long title. Single-keyword
matching means most of these links would simply never fire.

This work:

1. Lets one affiliate link match on **multiple keyword phrases**.
2. Adds a **`platform`** tracking column (Amazon, direct-referral, etc.).
3. Ships a **CSV bulk-import** path so Matt's spreadsheet can be loaded
   directly instead of typed in one row at a time.

## Scope

**In scope:** schema change (child keyword table + `platform` column),
matcher support for shared keywords across links (co-occurrence + LRU
tie-break), CRUD/CLI updates, CSV import command, docs.

**Explicitly out of scope** (flagged during design, not requested):

- `Commission Rate` and `Promo Code` CSV columns are read but **not** stored
  anywhere. Easy to add later if wanted.
- An LLM-judge tier for shared-keyword disambiguation. Ship co-occurrence +
  LRU rotation only; revisit if rotation proves unsatisfying in practice.
- Semantic/embedding-based matching (discussed separately, deferred — the
  catalog isn't large enough yet for near-miss phrasing to be the bottleneck).

## Schema

New migration, generated via `python scripts/new-migration.py "multi keyword
and platform for affiliate links"` (repo convention: the script prefixes a
UTC timestamp onto the filename at generation time):

```sql
-- One row per keyword/alias a link can match on.
CREATE TABLE IF NOT EXISTS affiliate_link_keywords (
  id SERIAL PRIMARY KEY,
  link_id INTEGER NOT NULL REFERENCES affiliate_links(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (link_id, keyword)
);
CREATE INDEX IF NOT EXISTS idx_affiliate_link_keywords_link_id
  ON affiliate_link_keywords(link_id);

-- Free-text platform tracking (Amazon, direct, etc. — no CHECK constraint;
-- this is a label field, not something pipeline logic branches on).
ALTER TABLE affiliate_links
  ADD COLUMN IF NOT EXISTS platform VARCHAR(50) NOT NULL DEFAULT '';

-- Backfill: each existing row's single `keyword` becomes its first alias.
INSERT INTO affiliate_link_keywords (link_id, keyword)
  SELECT id, keyword FROM affiliate_links
  ON CONFLICT (link_id, keyword) DO NOTHING;

-- Drop the old column (and its global UNIQUE constraint goes with it —
-- uniqueness is now scoped per-link via the child table, not global,
-- because different links are allowed to share a keyword — see "Shared
-- keywords" below).
ALTER TABLE affiliate_links DROP COLUMN IF EXISTS keyword;
```

`down()` reverses it: re-add `keyword TEXT`, backfill each link from one of
its child rows (`MIN(id)` per `link_id`, arbitrary but deterministic), drop
`affiliate_link_keywords` and `platform`.

This is a one-way cutover, not a backward-compat shim. The singular
`keyword` column had exactly one internal consumer — this module, its CLI,
and its tests, all touched in the same change — nothing external depends on
the old shape, so there's nothing to preserve.

**Note on uniqueness:** deliberately **not** globally unique. See "Shared
keywords" below — different links are allowed to claim the same keyword
string (e.g. "Corsair" on five different Corsair products), and the matcher
resolves which one wins per-mention at match time.

## Service layer (`modules/content/affiliate_links.py`)

### `AffiliateLink` dataclass

```python
@dataclass
class AffiliateLink:
    code: str
    keywords: list[str] = field(default_factory=list)   # was: keyword: str
    url: str = ""
    display_text: str = ""
    platform: str = ""
```

### `list_active(pool)`

Single query, joins the child table, aggregates keywords in insertion order:

```python
async def list_active(pool: Any) -> list[AffiliateLink]:
    rows = await pool.fetch(
        "SELECT al.code, al.url, COALESCE(al.display_text, '') AS display_text, "
        "al.platform, "
        "COALESCE(array_agg(alk.keyword ORDER BY alk.id) "
        "  FILTER (WHERE alk.keyword IS NOT NULL), '{}') AS keywords "
        "FROM affiliate_links al "
        "LEFT JOIN affiliate_link_keywords alk ON alk.link_id = al.id "
        "WHERE al.is_active = true GROUP BY al.id ORDER BY al.id"
    )
    return [
        AffiliateLink(code=r["code"], url=r["url"], display_text=r["display_text"],
                      platform=r["platform"], keywords=list(r["keywords"]))
        for r in rows
    ]
```

A `list_all(pool)` variant (same query, no `WHERE is_active` clause) backs
the new `affiliate list --all` CLI flag.

### `add_link(pool, ...)`

Full-replace semantics for the keyword set, matching how every other field
already upserts. Requires at least one keyword. De-duplicates the input
list (preserving order) so a caller accidentally passing the same keyword
twice doesn't trip the `UNIQUE (link_id, keyword)` constraint:

```python
async def add_link(
    pool: Any, *, code: str, keywords: list[str], url: str,
    display_text: str = "", program: str = "", description: str = "",
    category: str = "product", platform: str = "",
) -> None:
    if not keywords:
        raise ValueError("add_link requires at least one keyword")
    deduped = list(dict.fromkeys(keywords))
    async with pool.acquire() as conn, conn.transaction():
        link_id = await conn.fetchval(
            "INSERT INTO affiliate_links "
            "(code, url, display_text, program, description, category, platform) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (code) DO UPDATE SET url = EXCLUDED.url, "
            "display_text = EXCLUDED.display_text, program = EXCLUDED.program, "
            "description = EXCLUDED.description, category = EXCLUDED.category, "
            "platform = EXCLUDED.platform, updated_at = now() RETURNING id",
            code, url, display_text or None, program or None,
            description, category, platform,
        )
        await conn.execute("DELETE FROM affiliate_link_keywords WHERE link_id = $1", link_id)
        for kw in deduped:
            await conn.execute(
                "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                link_id, kw,
            )
```

A genuine unique-constraint violation can't happen here anymore (uniqueness
is per-link, and the row's own keyword list was just de-duplicated) — the
transaction is still worth keeping for atomicity (a mid-batch DB error rolls
back the whole update rather than leaving a link with a half-replaced
keyword set).

`set_active`, `remove_link` are unchanged (the child table's `ON DELETE
CASCADE` handles keyword cleanup on `remove_link` automatically).

## Shared keywords: matcher changes

Different links are allowed to claim the same keyword (e.g. "Corsair" as an
alias on five different Corsair products in Matt's catalog). The matcher
must therefore resolve, per matched keyword, **which** of the links sharing
it actually gets linked for a given mention.

**Matching order:** keywords are tried longest-first across the _whole_
active-link catalog (not per-link), so a more specific phrase ("Corsair
HX1500i") wins over a shorter, more generic one ("Corsair") when both exist
as distinct keyword strings and both would otherwise match the same text.

**Tie-break when a matched keyword has 2+ active candidate links:**

1. **Co-occurrence (always on, free, deterministic).** Does exactly one
   candidate have another of _its own_ keywords also present elsewhere in
   the same post? If so, that candidate wins — e.g. if the post also
   mentions "HX1500i" somewhere, a bare "Corsair" mention resolves to the
   PSU, not the headset.
2. **LRU rotation (always on, free, deterministic fallback).** If
   co-occurrence doesn't disambiguate (zero or multiple candidates
   corroborated), pick whichever candidate was least-recently injected into
   a **published** post. Mirrors the existing image-style rotation pattern
   (`_select_style_lru` / `_load_style_last_used` in
   `modules/content/stages/source_featured_image.py`): ties within the LRU
   step itself break randomly.

No LLM-judge tier in this pass (see Scope) — this keeps
`content.inject_affiliate_links` fully free/deterministic, matching its
existing `ATOM_META` (`cost_class="free"`, "Deterministic — no LLM" in its
docstring). Both stay true after this change.

**Computing "least recently injected" — no new column.** Reuses the
existing convention (`_load_style_last_used` scans published-post content
rather than maintaining a redundant tracking column): a single query run by
the atom (not the pure matcher function) derives a `code -> ISO timestamp`
map from existing `posts` data:

```sql
SELECT al.code, MAX(p.published_at) AS last_used
FROM affiliate_links al
LEFT JOIN posts p
  ON p.body LIKE '%/go/' || al.code || '%' AND p.status = 'published'
GROUP BY al.code
```

### Matcher implementation shape

`inject_affiliate_links` stays a **synchronous, pure function** (same
testability contract as today — the existing matcher unit tests construct
`AffiliateLink`s and call it directly with no DB). The LRU dict is computed
by the caller (the atom) and passed in:

```python
def inject_affiliate_links(
    markdown: str,
    links: list[AffiliateLink],
    *,
    base: str = "/go",
    cap: int = 3,
    last_used: dict[str, str] | None = None,
    rng: Any = random,
) -> tuple[str, list[str]]:
    if not markdown or not links or cap <= 0:
        return markdown, []
    last_used = last_used or {}

    keyword_to_links: dict[str, list[AffiliateLink]] = {}
    for link in links:
        for kw in link.keywords:
            keyword_to_links.setdefault(kw, []).append(link)
    ordered_keywords = sorted(keyword_to_links, key=len, reverse=True)

    used: set[str] = set()
    injected: list[str] = []
    consumed_keywords: set[str] = set()
    out_lines: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence or line.lstrip().startswith("#") or len(injected) >= cap:
            out_lines.append(line)
            continue
        for kw in ordered_keywords:
            if kw in consumed_keywords or len(injected) >= cap:
                continue
            candidates = [l for l in keyword_to_links[kw] if l.code not in used]
            if not candidates:
                continue
            span = _find_eligible_span(line, kw)  # existing word-boundary +
            if span is None:                       # inline-code eligibility
                continue                            # rules, refactored out
            link = _resolve_link(candidates, kw, markdown, last_used, rng)
            start, end = span
            text = link.display_text or kw
            url = f"{base.rstrip('/')}/{link.code}"
            line = line[:start] + f"[{text}]({url})" + line[end:]
            used.add(link.code)
            injected.append(link.code)
            consumed_keywords.add(kw)
        out_lines.append(line)
    return "\n".join(out_lines), injected


def _resolve_link(candidates, keyword, markdown, last_used, rng):
    if len(candidates) == 1:
        return candidates[0]
    corroborated = [
        c for c in candidates
        if any(kw in markdown for kw in c.keywords if kw != keyword)
    ]
    if len(corroborated) == 1:
        return corroborated[0]
    oldest = min(last_used.get(c.code, "") for c in candidates)
    tied = [c for c in candidates if last_used.get(c.code, "") == oldest]
    return rng.choice(tied)
```

`_find_eligible_span` is the existing `_link_first_mention` matching logic
(word-boundary regex, inline-code-span exclusion) factored to return a
`(start, end)` span instead of doing the injection itself, since injection
now needs to happen only after `_resolve_link` has picked a winner.

The atom (`content_inject_affiliate_links.py`) gains one extra step: after
`list_active(pool)`, if any keyword is shared by 2+ returned links, run the
`last_used` query above and pass it through. (Cheap to just always compute
it whenever there are 2+ active links — no need to specifically detect
sharing first.)

## CSV import (`modules/content/affiliate_import.py`, new file)

**Expected columns** (matching Matt's spreadsheet export exactly):
`Status, Product Name, Category, Platform, Commission Rate, Affiliate Link,
Description, Promo Code`. `Commission Rate` and `Promo Code` are read but
not persisted (see Scope).

**Per-row flow:**

1. **`is_active` is set directly from `Status`**: `"active"`
   (case-insensitive) → `true`, anything else (including blank) → `false`.
   Rows are never skipped based on `Status` — a non-`"Active"` row is still
   imported, just deactivated on arrival, so Matt can stage a future
   `Paused`-style row by writing it into the sheet ahead of time.
2. Skip rows with a blank `Product Name` (nothing to import).
3. Derive `code` **deterministically** from `Product Name` — lowercase,
   first 6 significant words, non-alphanumeric runs collapsed to a single
   hyphen, truncated to 60 chars. Never LLM-touched, so re-running the
   import on an unchanged sheet always derives the same code for the same
   row (idempotency doesn't depend on LLM determinism).
4. **If that `code` already exists in the DB, skip the row** (report
   `skipped_exists`) unless `--force` is passed. This is the safety rail:
   the sheet's 4 pre-existing rows (google-workspace, mercury, prime-gaming,
   audible) have blank `Description`/`Category`/`Platform` in the CSV —
   without insert-only-by-default, a re-import would blank out the real
   descriptions written during the referrals-page work.
   `--force` fully replaces an existing row's fields (same semantics as
   calling `affiliate add` again) — use it deliberately, not as the default.
   A `--force`-ed row is treated exactly like a new code for the rest of
   this flow (steps 5-8 below all run, including a fresh LLM derivation —
   `--force` does not selectively refresh only the CSV-native fields).
5. For genuinely new codes (or existing codes under `--force`): call a
   local LLM to propose a short `display_text` and a keyword list, using
   the exact pattern `content.llm_reconcile_citations` already uses
   (`services.llm_text.resolve_structured_model` +
   `services.llm_text.ollama_chat_text`, prompt via
   `services.prompt_manager.get_prompt_manager()` under a new catalog key
   `cli.affiliate.derive_keywords` with an inline fallback string for
   bootstrap/test contexts, same as `_PROMPT_FALLBACK` in that atom).
   Fails open **per row**: any LLM/JSON error falls back to
   `(product_name[:60], [])` rather than aborting the batch — a row with an
   empty keyword list still gets created (inactive-by-status or
   active-by-status per its `Status` field) so nothing is silently lost;
   Matt can add keywords afterward via `affiliate add` on the same code.
6. `Category` (CSV) maps to our `category` CHECK column: `"service"`
   (case-insensitive) → `"service"`, everything else (including `"Hardware"`,
   blank, or anything unrecognized) → `"product"`.
7. `Platform` (CSV) maps verbatim (trimmed) to the new `platform` column.
8. Insert via `add_link()` (Section above), then `set_active(pool, code,
is_active)` to apply the `Status`-derived value from step 1 (`add_link`
   doesn't take an `is_active` param — it isn't part of upsertable "content"
   fields anywhere else in this module either, so this stays consistent
   with the existing `add_link` → `set_active` split).

**Report:** `import_csv()` returns an `ImportReport` (list of per-row
results: `code, product_name, status ("created"|"skipped_exists"|"error"),
display_text, keywords, detail`). The CLI renders this as a table.

## CLI (`poindexter/cli/affiliate.py`)

| Command              | Change                                                                                                                                                                                                                  |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `add`                | `--keyword` becomes `multiple=True` (repeatable, at least one required, replaces the old single `--keyword`). New optional `--platform` (default `""`).                                                                 |
| `list`               | New `--all` flag — includes inactive rows (default behavior unchanged: active-only). Output line now shows comma-joined keywords + platform.                                                                            |
| `enable`             | `CODE` argument becomes optional; new `--all` flag activates every currently-inactive row in one batch with a single republish call at the end. Exactly one of `CODE` / `--all` must be given (`UsageError` otherwise). |
| `disable`, `rm`      | Unchanged.                                                                                                                                                                                                              |
| `import-csv` _(new)_ | `poindexter affiliate import-csv <path> [--force]`. Runs the flow above, prints the summary table, does a single republish at the end covering every row written (not one per row).                                     |

## Docs (`docs/operations/affiliate-links.md`)

- CLI reference table: reflect all changes above (repeatable `--keyword`,
  `--platform`, `list --all`, `enable --all`, `import-csv`).
- "Data model" section: `affiliate_links` loses `keyword`, gains `platform`;
  document the new `affiliate_link_keywords` table
  (`id, link_id FK → affiliate_links(id) ON DELETE CASCADE, keyword,
created_at`, `UNIQUE(link_id, keyword)`).
- "How injection works": add a short paragraph on shared-keyword resolution
  (co-occurrence check, then LRU rotation) so a future reader understands
  why two links can list the same keyword string.
- New "Bulk import from a spreadsheet" section: expected CSV columns,
  the LLM-derivation step and its fail-open behavior, insert-only-by-default
  - `--force` semantics, and the `enable --all` review step.

## Testing

Existing files to update (call sites broadly change from `keyword=` to
`keywords=`, all constructors/fixtures across these need touching per the
"finish migrations completely" convention — no partial cutover):

- `tests/integration_db/test_affiliate_links_schema.py` — replace the
  `keyword` column assertion with `platform`; add a new test asserting
  `affiliate_link_keywords` exists with the expected columns and that
  `UNIQUE(link_id, keyword)` allows the same keyword under two different
  `link_id`s but rejects a repeat under the same `link_id`. Uses the
  existing disposable `test_pool` fixture (confirmed via
  `test_affiliate_links_ship_empty` — this is a throwaway DB with the full
  migration tree applied, not live data).
- `tests/unit/modules/content/test_affiliate_links_service.py` — update
  every `AffiliateLink(..., keyword="X", ...)` construction to
  `keywords=["X"]`; add new tests: two links sharing a keyword resolve via
  co-occurrence when one has corroborating context; resolve via LRU
  (passed-in `last_used` dict) when co-occurrence doesn't disambiguate;
  longer keyword phrase wins over a shorter one both present in the same
  text.
- `tests/unit/modules/content/test_affiliate_links_crud.py` — update
  `_FakePool` to support `.acquire()` / transaction / per-keyword insert
  loop; update `add_link` call assertions for `keywords=[...]` and
  `platform=`.
- `tests/unit/poindexter/cli/test_affiliate_cli.py` — update for repeatable
  `--keyword`, new `--platform`, `list --all`, `enable --all`, and the new
  `import-csv` subcommand (introspect via Click's `.params`, matching the
  existing `test_add_requires_category_and_description` pattern).
- New `tests/unit/modules/content/test_affiliate_import.py` — `slugify_code`
  determinism across repeated calls on the same title; `Status` → boolean
  mapping including unrecognized/blank values; `Category` → `service`/
  `product` mapping; skip-existing-by-default vs `--force` overwrite;
  per-row fail-open when the LLM call raises (mock `ollama_chat_text` to
  throw, assert the row still gets created with an empty keyword list
  rather than the batch aborting).

## Out of scope / deferred

- `Commission Rate` / `Promo Code` columns (not requested; flagged during
  design).
- LLM-judge tier for shared-keyword disambiguation (deferred; co-occurrence
  - LRU shipped instead).
- Semantic/embedding-based matching (separate, larger topic; deferred until
  the catalog is large enough that near-miss phrasing is the actual
  bottleneck, not missing keyword coverage).
