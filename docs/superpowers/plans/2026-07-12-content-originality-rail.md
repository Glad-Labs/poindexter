# content_originality Rail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the `qa.opening_originality` rail to `qa.content_originality`, broaden it from opening-only to whole-post chunked scoring, fix its frozen qa_gates telemetry, and build a series-exclusion so it is hard-block-ready — all while it stays advisory.

**Architecture:** The rail is one graph*def atom (`modules/content/atoms/qa*\*.py`) that embeds draft text, finds the nearest published-post neighbor by pgvector cosine, and appends an advisory `ReviewerResult`to the`qa_rail_reviews`channel that`qa.aggregate`reads. The rename touches the atom + its graph_def node id, which forces a contract-fingerprint snapshot regen and a`pipeline_templates` re-seed migration (else prod's stored graph_def references a now-missing atom and the load-time drift gate halts the pipeline — the #1876 failure class). Behavior changes (whole-post scope, telemetry, series-exclusion) layer on after the rename lands clean.

**Tech Stack:** Python 3.13, asyncpg, pgvector, LangGraph graph_def, pytest. Embeddings via `services.topic_ranking.embed_text` (nomic, resident). Migrations via `scripts/new-migration.py` (Convention A: `async def up(pool)`).

## Global Constraints

- **Advisory now, no gate-decision change today.** The rail stays `qa_gates.content_originality.required_to_pass=false`. It SCORES and flags but never vetoes. (spec §2.4)
- **Backcompat required.** The atom reads the new `content_originality_*` settings first and falls back to the old `opening_originality_*` keys, so a lingering prod row under the old name still works. Old defaults were untuned (0.83 / true) → no value carry-over. (spec §2.1, [feedback_backcompat_now_required])
- **A node rename is a graph_def change.** Regenerate the contract-fingerprint snapshot (`REGEN_GRAPH_DEF_FP=1`) AND add a `pipeline_templates` re-seed migration, or prod halts on next boot. (spec §2.1, [reference_graph_def_contract_reseed])
- **Atom independence.** The atom may import `services.*` and `_`-prefixed helpers but MUST NOT import a sibling atom or a `modules.content.stages.*` stage. (CLAUDE.md)
- **Config in DB, not code.** Every tunable (chunk floors, thresholds, excluded series) is an `app_settings` key seeded in `settings_defaults.py`, never a hardcoded literal a customer might tune. (CLAUDE.md, [feedback_db_first_config])
- **No invented numbers.** Thresholds come from calibration against the real corpus, not guessed. Ship a documented provisional value only where live calibration needs prod data, and mark it for recalibration. ([feedback_no_hardcoded_lengths_in_prompts])
- **Docs + tests default.** Every change ships contract tests + doc updates (`anti-hallucination.md`, `CLAUDE.md`, regen `services.md`). ([feedback_docs_and_tests_default])
- **Seeds in baseline, not migrations.** New/renamed seed _state_ goes in `0000_baseline.seeds.sql` / `settings_defaults.py`; a migration only handles the prod _transition_ (renaming an existing live row). ([feedback_seed_data_in_baseline_not_new_migrations])

**Verify tests in a fresh worktree** with the main checkout's poetry venv python and `-o addopts=""` (a fresh worktree has no venv; `addopts=""` skips `--forked`):
`"<main-venv>/python.exe" -m pytest <paths> -o addopts=""` run from `src/cofounder_agent`. ([reference_run_worktree_tests])

---

## File Structure

| File                                                                                         | Responsibility                                                                   | Task    |
| -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ------- |
| `modules/content/atoms/qa_content_originality.py` (renamed from `qa_opening_originality.py`) | The rail atom: extract → chunk → embed → nearest-neighbor → advisory review.     | 1, 4, 5 |
| `services/canonical_blog_spec.py`                                                            | graph_def node id + edges (`qa_opening_originality` → `qa_content_originality`). | 1       |
| `services/settings_defaults.py`                                                              | `content_originality_*` DEFAULTS + owner map + chunk/threshold keys.             | 1, 4, 5 |
| `services/settings_categories.py`                                                            | settings-taxonomy prefix (`opening_originality` → `content_originality`).        | 1       |
| `tests/unit/services/atoms/test_qa_content_originality_atom.py` (renamed)                    | Atom contract + decision + chunking + series-exclusion tests.                    | 1, 4, 5 |
| `tests/unit/services/test_canonical_blog_spec.py`                                            | graph_def shape/edge assertions.                                                 | 1       |
| `tests/unit/services/graph_def_contract_fingerprints.json`                                   | Regenerated contract-fingerprint snapshot.                                       | 1       |
| `services/migrations/<ts>_rename_opening_to_content_originality.py`                          | Prod transition: rename qa_gates row + re-seed `pipeline_templates.graph_def`.   | 2       |
| `services/migrations/0000_baseline.seeds.sql`                                                | qa_gates seed row rename (fresh installs).                                       | 2       |
| `services/qa_gates_db_writer.py`                                                             | `_REVIEWER_TO_GATE` identity alias for the counter fix.                          | 3       |
| `tests/unit/services/test_qa_gates_db_writer.py`                                             | `must_be_documented` guard entry.                                                | 3       |
| `scripts/calibrate_content_originality_threshold.py`                                         | One-off chunk-vs-corpus threshold calibration.                                   | 4       |
| `docs/architecture/anti-hallucination.md`, `CLAUDE.md`, `docs/reference/services.md`         | Doc references.                                                                  | 6       |

**Ordering:** Task 1 (rename) and Task 2 (migrations) MUST land in the same PR — a renamed atom without the re-seed migration halts prod. Tasks 3–6 layer on and could split into a "2b" follow-up PR if review prefers, but the plan executes them in one branch.

---

## Task 1: Rename the rail (pure rename, no behavior change) + regen snapshot

**Files:**

- Rename: `src/cofounder_agent/modules/content/atoms/qa_opening_originality.py` → `qa_content_originality.py`
- Rename: `src/cofounder_agent/tests/unit/services/atoms/test_qa_opening_originality_atom.py` → `test_qa_content_originality_atom.py`
- Modify: `src/cofounder_agent/services/canonical_blog_spec.py` (node id + 2 edges + comment)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (DEFAULTS keys + owner map)
- Modify: `src/cofounder_agent/services/settings_categories.py:154`
- Modify: `src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py`
- Regenerate: `src/cofounder_agent/tests/unit/services/graph_def_contract_fingerprints.json`

**Interfaces:**

- Produces: atom `ATOM_META.name == "qa.content_originality"`, reviewer string `"content_originality"`, graph_def node id `qa_content_originality`, settings keys `content_originality_enabled` / `content_originality_max_similarity`.
- Consumes: nothing new — same `content` in, `qa_rail_reviews` out (contract unchanged, only the name key moves).

- [ ] **Step 1: Rename the atom file with git mv (preserves history)**

```bash
cd src/cofounder_agent
git mv modules/content/atoms/qa_opening_originality.py modules/content/atoms/qa_content_originality.py
git mv tests/unit/services/atoms/test_qa_opening_originality_atom.py tests/unit/services/atoms/test_qa_content_originality_atom.py
```

- [ ] **Step 2: Rewrite the atom's identifiers with backcompat settings reads**

In `modules/content/atoms/qa_content_originality.py`:

- `ATOM_META.name`: `"qa.opening_originality"` → `"qa.content_originality"`.
- `ATOM_META.description`: replace "opening" framing with "whole-post" and `qa_gates.opening_originality` → `qa_gates.content_originality`.
- `run()`: `ReviewerResult(reviewer="opening_originality", ..., provider="opening_originality_gate")` → `reviewer="content_originality"`, `provider="content_originality_gate"`.
- `run()`: `MultiModelQA._mark_advisory_if_configured(review, gate_states, "opening_originality")` → `"content_originality"`.
- `_is_enabled`: read the new key first, fall back to the old (backcompat):

```python
def _is_enabled(site_config: Any) -> bool:
    try:
        raw = site_config.get(
            "content_originality_enabled",
            site_config.get("opening_originality_enabled", "true"),
        )
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        # silent-ok: optional master switch — default the advisory rail ON (it
        # only scores, never vetoes) when a stubbed/misconfigured site_config
        # raises, rather than letting a config-read blip crash the QA block.
        return True
    return str(raw).lower() in ("true", "1", "yes")
```

- `_evaluate`: threshold read with backcompat fallback:

```python
    try:
        threshold = float(
            site_config.get_float(
                "content_originality_max_similarity",
                site_config.get_float(
                    "opening_originality_max_similarity", _DEFAULT_MAX_SIMILARITY
                ),
            )
        )
    except Exception:  # noqa: BLE001 — stubbed site_config
        threshold = _DEFAULT_MAX_SIMILARITY
```

- Update the log tags `[qa.opening_originality]` → `[qa.content_originality]` and the module docstring's first line. (Whole-post logic is Task 4 — this task keeps the opening-only `_extract_opening`/`_evaluate` bodies unchanged except the identifier/log renames.)

- [ ] **Step 3: Update the atom's test file identifiers**

In `tests/unit/services/atoms/test_qa_content_originality_atom.py`:

- `import ... qa_opening_originality` → `qa_content_originality`; replace every `qa_opening_originality.` reference.
- `_ADVISORY_STATES = {"opening_originality": (True, False)}` → `{"content_originality": (True, False)}`; same for `_HARD_GATE_STATES`.
- `test_meta`: `assert m.name == "qa.content_originality"`.
- `_Cfg.get`/`get_bool`: the `"opening_originality_enabled"` branch → `"content_originality_enabled"`.
- Reviewer assertions: `rev["reviewer"] == "content_originality"`, `rev["provider"] == "content_originality_gate"`.
- `aggregate_rail_reviews` veto assertions: `"opening_originality"` → `"content_originality"`.

- [ ] **Step 4: Run the atom tests to verify the rename is internally consistent**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/atoms/test_qa_content_originality_atom.py -o addopts="" -q`
Expected: PASS (behavior unchanged; only names moved).

- [ ] **Step 5: Rename the graph_def node + edges**

In `services/canonical_blog_spec.py`:

- Node (was line ~129): `{"id": "qa_opening_originality", "atom": "qa.opening_originality"}` → `{"id": "qa_content_originality", "atom": "qa.content_originality"}`.
- Edge (was line ~200): `{"from": "qa_self_consistency", "to": "qa_opening_originality"}` → `"to": "qa_content_originality"`.
- Edge (was line ~201): `{"from": "qa_opening_originality", "to": "qa_web_factcheck"}` → `"from": "qa_content_originality"`.
- Update the comment block above the node (references "OPENING near-duplicates" and `qa_gates.opening_originality`) to describe whole-post scope + `qa_gates.content_originality`.

- [ ] **Step 6: Update the graph_def spec tests**

In `tests/unit/services/test_canonical_blog_spec.py`:

- Line ~29: `"qa.opening_originality"` → `"qa.content_originality"` in the node-atoms subset assertion.
- Lines ~111-115: `qa_opening_originality` → `qa_content_originality` in the edge assertions (`qa_self_consistency → qa_content_originality`, `qa_content_originality → qa_web_factcheck`).
- Line ~213 comment: `qa.opening_originality` → `qa.content_originality`. `test_node_count_is_43` stays 43 (rename, not add/remove).

- [ ] **Step 7: Rename the settings keys + owner map**

In `services/settings_defaults.py`:

- DEFAULTS (was lines ~893-894): `'opening_originality_enabled': 'true'` → `'content_originality_enabled': 'true'`; `'opening_originality_max_similarity': '0.83'` → `'content_originality_max_similarity': '0.83'`. Update the comment block above them (rail median/p95 framing) to note whole-post scope + recalibration pending (Task 4).
- Owner map (was lines ~2124-2125): `'opening_originality_enabled'` → `'content_originality_enabled'`, `'opening_originality_max_similarity'` → `'content_originality_max_similarity'` (keep `'owner': 'multi_model_qa'`).

In `services/settings_categories.py:154`: `("opening_originality", "content")` → `("content_originality", "content")`.

- [ ] **Step 8: Regenerate the contract-fingerprint snapshot**

The rename re-keys the atom in the snapshot (old key orphaned, new atom absent) → both freshness tests go red until regenerated.

Run: `cd src/cofounder_agent && REGEN_GRAPH_DEF_FP=1 "<main-venv>/python.exe" -m pytest tests/unit/services/test_graph_def_contract_freshness.py::test__regenerate_snapshot -o addopts="" -q`
Expected: rewrites `graph_def_contract_fingerprints.json` (now keyed `qa.content_originality`, no `qa.opening_originality`).

- [ ] **Step 9: Run the graph_def + spec tests green**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/test_canonical_blog_spec.py tests/unit/services/test_graph_def_contract_freshness.py -o addopts="" -q`
Expected: PASS. (`test_active_specs_match_committed_snapshot` + `test_committed_snapshot_has_no_stale_entries` prove the regen was correct.)

- [ ] **Step 10: Commit**

```bash
git add modules/content/atoms/qa_content_originality.py tests/unit/services/atoms/test_qa_content_originality_atom.py services/canonical_blog_spec.py services/settings_defaults.py services/settings_categories.py tests/unit/services/test_canonical_blog_spec.py tests/unit/services/graph_def_contract_fingerprints.json
git commit -m "refactor(qa): rename opening_originality rail to content_originality"
```

---

## Task 2: Migrations — rename the live qa_gates row + re-seed the graph_def

**Files:**

- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` (qa_gates row, for fresh installs)
- Create: `src/cofounder_agent/services/migrations/<ts>_rename_opening_to_content_originality.py`

**Interfaces:**

- Consumes: `CANONICAL_BLOG_GRAPH_DEF` (renamed node from Task 1).
- Produces: prod `qa_gates` row `name/reviewer='content_originality'`; prod `pipeline_templates.canonical_blog.graph_def` referencing `qa.content_originality`.

- [ ] **Step 1: Update the baseline qa_gates seed row (fresh-install end state)**

In `0000_baseline.seeds.sql` (was line ~740), change the `opening_originality` row so a fresh DB seeds the new name directly (keep the same `id` UUID so the migration's rename no-ops on fresh installs):

```sql
INSERT INTO qa_gates (id, name, stage_name, execution_order, reviewer, required_to_pass, enabled, config, metadata) VALUES ('7a3f9e21-4c8b-4d15-9e6a-2b1c8f0d3a57', 'content_originality', 'qa', 315, 'content_originality', false, true, '{}'::jsonb, '{"atom": "qa.content_originality", "rail": "content_originality", "description": "RAG self-echo net — flags a draft whose CONTENT near-duplicates an existing published post (whole-post chunked scan; the 2026-06 VRAM-hook cluster). Advisory-first: scores but does not veto until graduated."}'::jsonb) ON CONFLICT (id) DO NOTHING;
```

- [ ] **Step 2: Generate the migration**

Run: `python scripts/new-migration.py "rename opening to content originality"`
This writes `services/migrations/<ts>_rename_opening_to_content_originality.py` with the `async def up(pool)` scaffold.

- [ ] **Step 3: Write the migration body (qa_gates rename + graph_def reseed)**

Replace the scaffold `up`/`down` (mirror `20260623_035500_reseed_media_podcast_graph_defs.py` for the reseed half):

```python
async def up(pool) -> None:
    import json

    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        # 1. Rename the live prod qa_gates row (seeds won't rename an existing
        #    row — ON CONFLICT DO NOTHING). No-op on fresh installs (baseline
        #    already seeds 'content_originality').
        gate_tag = await conn.execute(
            """
            UPDATE qa_gates
               SET name = 'content_originality',
                   reviewer = 'content_originality',
                   metadata = jsonb_set(
                       jsonb_set(metadata, '{atom}', '"qa.content_originality"'),
                       '{rail}', '"content_originality"')
             WHERE name = 'opening_originality'
            """
        )
        # 2. Re-seed the canonical_blog graph_def so the stored spec references
        #    the renamed node/atom. Write the RAW spec (no per-node _contract_fp)
        #    — the boot self-heal ensure_active_graph_defs_stamped re-stamps
        #    fingerprints on next worker boot (poindexter#755).
        gd_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, updated_at = now() "
            "WHERE slug = 'canonical_blog'",
            raw,
        )
    logger.info(
        "rename_opening_to_content_originality up: qa_gates=%s graph_def=%s",
        gate_tag, gd_tag,
    )


async def down(pool) -> None:
    # One-way forward reseed: the boot self-heal owns stamping and the old atom
    # name no longer exists in the registry, so a revert would re-break prod.
    logger.info("rename_opening_to_content_originality down: no-op")
```

Fill the docstring `ISSUE:` with the tracking poindexter issue and a two-sentence background.

- [ ] **Step 4: Smoke the migration against a fresh DB**

Run: `python scripts/ci/migrations_smoke.py`
Expected: applies cleanly (baseline seeds `content_originality`; the rename UPDATE no-ops on the fresh row; the reseed UPDATE writes the graph_def). Then `python scripts/ci/migrations_lint.py` — Expected: no collision/interface errors.

> NOTE: `migrations_smoke` runs the real runner against `DATABASE_URL` (bootstrap DSN = prod). Confirm it targets a throwaway DB, not prod, before running. ([reference_migrations_smoke_hits_live_db])

- [ ] **Step 5: Run the seeded-graph-def integration guard (if a DB is available)**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/integration_db/test_seeded_graph_defs_current.py -o addopts="" -q` (needs a Postgres; skip if none and rely on CI's `integration-db` job).
Expected: PASS — the migrated DB's `pipeline_templates.canonical_blog` graph_def matches the Python spec.

- [ ] **Step 6: Commit**

```bash
git add services/migrations/0000_baseline.seeds.sql services/migrations/*_rename_opening_to_content_originality.py
git commit -m "feat(qa): migrate qa_gates row + reseed canonical_blog graph_def for content_originality"
```

---

## Task 3: Fix the frozen qa_gates telemetry (alias + guard)

**Files:**

- Modify: `src/cofounder_agent/services/qa_gates_db_writer.py` (`_REVIEWER_TO_GATE`)
- Modify: `src/cofounder_agent/tests/unit/services/test_qa_gates_db_writer.py` (`must_be_documented`)

**Interfaces:**

- Consumes: reviewer string `"content_originality"` (Task 1) + qa_gates row `content_originality` (Task 2).
- Produces: `reviewer_to_gate("content_originality") == "content_originality"` so `record_chain_run` bumps the counter.

**Root cause (verified):** `qa.aggregate` already calls `record_chain_run(pool, reviews)` for every rail review, but `record_chain_run` skips any reviewer whose `reviewer_to_gate()` is `None`. `opening_originality` was never in `_REVIEWER_TO_GATE` NOR in the guard's `must_be_documented` set — so its counter froze at 0 and the guard test didn't catch it (the fifth recurrence of the alias-drop class, this time slipping past the guard too).

- [ ] **Step 1: Write the failing guard-test entry**

In `tests/unit/services/test_qa_gates_db_writer.py`, add to the `must_be_documented` set (after `"citation_grounding"`):

```python
        # content_originality (renamed from opening_originality) — advisory
        # RAG self-echo rail, gate row seeded in 0000_baseline. It emits a
        # ReviewerResult on every canonical_blog QA pass; without the alias its
        # counter froze at total_runs=0 (the fifth alias-drop — this one slipped
        # past this guard too, because the name was never listed here).
        "content_originality",
```

- [ ] **Step 2: Run the guard test to verify it fails**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/test_qa_gates_db_writer.py::test_alias_table_covers_every_known_inline_reviewer -o addopts="" -q`
Expected: FAIL — `_REVIEWER_TO_GATE is missing aliases for ['content_originality']`.

- [ ] **Step 3: Add the identity alias**

In `services/qa_gates_db_writer.py`, add to `_REVIEWER_TO_GATE` (after `"citation_grounding": "citation_grounding",`):

```python
    # content_originality (renamed from opening_originality, 2026-07-12) — the
    # advisory RAG self-echo rail. Identity alias: the reviewer string and the
    # qa_gates row name are both 'content_originality'. Without this,
    # record_chain_run() drops the UPDATE and /d/qa-rails shows total_runs=0 —
    # the exact evidence gap that blocks graduating it to a hard veto.
    "content_originality": "content_originality",
```

- [ ] **Step 4: Run the guard + writer tests to verify they pass**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/test_qa_gates_db_writer.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/qa_gates_db_writer.py tests/unit/services/test_qa_gates_db_writer.py
git commit -m "fix(qa): alias content_originality so its qa_gates counter increments"
```

---

## Task 4: Whole-post chunked scoring + recalibration

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/qa_content_originality.py` (add `_chunk_draft`, rework `_evaluate`)
- Modify: `src/cofounder_agent/tests/unit/services/atoms/test_qa_content_originality_atom.py`
- Create: `src/cofounder_agent/scripts/calibrate_content_originality_threshold.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py` (chunk-size keys + recalibrated threshold)

**Interfaces:**

- Consumes: `content` (draft), `embed_text` (nomic), the existing pgvector nearest-neighbor query.
- Produces: `_chunk_draft(content, *, min_chars, max_chars) -> list[str]`; `_evaluate` returns `(passed, originality_score, reason)` where `reason` names the worst chunk's offending post slug. Contract (`requires=("content",)`, `produces=("qa_rail_reviews",)`) is UNCHANGED — no snapshot regen for this task.

- [ ] **Step 1: Write the failing chunking test**

Add to `test_qa_content_originality_atom.py` a new class:

```python
@pytest.mark.unit
class TestChunkDraft:
    def test_merges_short_paragraphs_to_floor(self):
        # Three one-line paragraphs below the 200-char floor merge into one chunk.
        content = "Short one.\n\nShort two.\n\nShort three."
        chunks = qa_content_originality._chunk_draft(content, min_chars=200, max_chars=600)
        assert len(chunks) == 1
        assert "Short one." in chunks[0] and "Short three." in chunks[0]

    def test_windows_long_paragraph(self):
        # A single paragraph above the 600-char max yields multiple chunks.
        content = "word " * 400  # ~2000 chars, one paragraph
        chunks = qa_content_originality._chunk_draft(content, min_chars=200, max_chars=600)
        assert len(chunks) >= 3
        assert all(len(c) <= 600 for c in chunks)

    def test_strips_leading_media(self):
        content = (
            '<img src="x.webp" alt="hero">\n\n'
            + ("The real analysis paragraph is long enough to stand alone. " * 6)
        )
        chunks = qa_content_originality._chunk_draft(content, min_chars=200, max_chars=600)
        assert chunks and "<img" not in chunks[0]

    def test_empty_returns_empty(self):
        assert qa_content_originality._chunk_draft("   ", min_chars=200, max_chars=600) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/atoms/test_qa_content_originality_atom.py::TestChunkDraft -o addopts="" -q`
Expected: FAIL with `AttributeError: module ... has no attribute '_chunk_draft'`.

- [ ] **Step 3: Implement `_chunk_draft`**

In `qa_content_originality.py`, add (keep `_LEADING_IMAGE_RE`; `_extract_opening` may stay for the opening-echo test but is no longer the primary path):

```python
def _chunk_draft(content: str, *, min_chars: int, max_chars: int) -> list[str]:
    """Split a draft into paragraph-based chunks for whole-post scoring.

    Strips leading media boilerplate + a leading H1, then: merges paragraphs
    under ``min_chars`` (a one-line paragraph is a noise vector) and windows
    paragraphs over ``max_chars`` (a long section still yields a focused
    vector). Mirrors the granularity ``embeddings`` stores post chunks at.
    """
    text = (content or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    # Drop a leading heading-only or media-only paragraph.
    while paras and (
        paras[0].startswith("#") or _LEADING_IMAGE_RE.match(paras[0])
    ):
        paras.pop(0)
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if len(para) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(para), max_chars):
                chunks.append(para[i : i + max_chars])
            continue
        buf = f"{buf}\n\n{para}".strip() if buf else para
        if len(buf) >= min_chars:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks and len(buf) < min_chars:
            chunks[-1] = f"{chunks[-1]}\n\n{buf}"
        else:
            chunks.append(buf)
    return chunks
```

- [ ] **Step 4: Run the chunking test to verify it passes**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/atoms/test_qa_content_originality_atom.py::TestChunkDraft -o addopts="" -q`
Expected: PASS.

- [ ] **Step 5: Write the failing whole-post `_evaluate` test**

Add to the atom test file:

```python
@pytest.mark.unit
class TestWholePostEvaluate:
    async def test_flags_on_worst_chunk(self, monkeypatch):
        """Max-over-chunks: an original opening but a body chunk that lifts a
        published post still flags, naming the worst chunk's offender."""
        # Two chunks: chunk 0 novel (sim 0.20), chunk 1 a lift (sim 0.94).
        sims = iter([("novel-post", 0.20), ("the-vram-currency-problem", 0.94)])
        monkeypatch.setattr(
            qa_content_originality, "_chunk_draft",
            lambda content, *, min_chars, max_chars: ["chunk zero", "chunk one"],
        )
        async def fake_embed(text, *, site_config):
            return [0.0]
        monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

        class _Conn:
            async def fetchrow(self, _sql, _vec):
                slug, sim = next(sims)
                return {"slug": slug, "similarity": sim}
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        class _Pool:
            def acquire(self): return _Conn()

        passed, score, reason = await qa_content_originality._evaluate(
            content="x", site_config=_Cfg(content_originality_max_similarity=0.83),
            pool=_Pool(),
        )
        assert passed is False
        assert "the-vram-currency-problem" in reason  # worst chunk named
        assert "0.94" in reason
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/atoms/test_qa_content_originality_atom.py::TestWholePostEvaluate -o addopts="" -q`
Expected: FAIL (current `_evaluate` embeds the opening only, one query).

- [ ] **Step 7: Rework `_evaluate` to chunk + max-over-chunks**

Replace the `_evaluate` body (keep the signature and the `_decide` helper). Read chunk-size settings; embed each chunk; track the max similarity + its slug + chunk index:

```python
async def _evaluate(
    *, content: str, site_config: Any, pool: Any,
) -> tuple[bool, float, str]:
    try:
        threshold = float(
            site_config.get_float(
                "content_originality_max_similarity",
                site_config.get_float(
                    "opening_originality_max_similarity", _DEFAULT_MAX_SIMILARITY
                ),
            )
        )
    except Exception:  # noqa: BLE001 — stubbed site_config
        threshold = _DEFAULT_MAX_SIMILARITY

    def _int(key: str, default: int) -> int:
        try:
            return int(site_config.get_int(key, default))
        except Exception:  # noqa: BLE001 — stubbed site_config
            return default

    min_chars = _int("content_originality_chunk_min_chars", _DEFAULT_CHUNK_MIN)
    max_chars = _int("content_originality_chunk_max_chars", _DEFAULT_CHUNK_MAX)

    chunks = _chunk_draft(content, min_chars=min_chars, max_chars=max_chars)
    if not chunks or pool is None:
        return _decide(max_similarity=0.0, threshold=threshold, nearest_slug=None)

    from services.topic_ranking import embed_text

    best_sim = 0.0
    best_slug: str | None = None
    async with pool.acquire() as conn:
        for chunk in chunks:
            qvec = await embed_text(chunk, site_config=site_config)
            qvec_str = "[" + ",".join(str(v) for v in qvec) + "]"
            row = await conn.fetchrow(
                """
                SELECT p.slug AS slug,
                       1 - (e.embedding <=> $1::vector) AS similarity
                  FROM embeddings e
                  JOIN posts p ON p.id = e.source_id::uuid
                 WHERE e.source_table = 'posts'
                   AND p.status = 'published'
                 ORDER BY e.embedding <=> $1::vector
                 LIMIT 1
                """,
                qvec_str,
            )
            if row is not None and float(row["similarity"]) > best_sim:
                best_sim = float(row["similarity"])
                best_slug = row["slug"]
    return _decide(max_similarity=best_sim, threshold=threshold, nearest_slug=best_slug)
```

Add the new constants near `_DEFAULT_MAX_SIMILARITY`:

```python
# Chunk-size floors for whole-post scoring. DB-tunable via
# content_originality_chunk_{min,max}_chars.
_DEFAULT_CHUNK_MIN = 200
_DEFAULT_CHUNK_MAX = 600
```

Extend `_Cfg` in the test file with a `get_int`:

```python
    def get_int(self, key, default=0):
        return int(self._extra.get(key, default))
```

- [ ] **Step 8: Run the whole-post + full atom tests**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/atoms/test_qa_content_originality_atom.py -o addopts="" -q`
Expected: PASS (whole-post flags on worst chunk; the pre-existing opening-echo test still passes — chunk 0 subsumes it).

- [ ] **Step 9: Seed the chunk-size settings + recalibration note**

In `services/settings_defaults.py` DEFAULTS, next to the `content_originality_*` keys:

```python
    'content_originality_chunk_min_chars': '200',
    'content_originality_chunk_max_chars': '600',
```

Add both to the owner map alongside the other `content_originality_*` entries (`'owner': 'multi_model_qa', 'value_type': 'integer'`).

- [ ] **Step 10: Write the calibration script**

Create `scripts/calibrate_content_originality_threshold.py` — mirror `scripts/calibrate_topic_dedup_threshold.py` (from WS1) but chunk-vs-corpus: for each published post, chunk its body, embed each chunk via the Ollama nomic endpoint, run the same pgvector nearest-neighbor query EXCLUDING the post's own chunks, and print the distribution of max-over-chunks similarity (median / p90 / p95 / p99) plus where the VRAM draft body lands. Output a recommended `content_originality_max_similarity`. It reads the DB via `from brain.bootstrap import resolve_database_url` (a one-off `scripts/` tool, not a service).

- [ ] **Step 11: Run calibration + set the threshold (or ship provisional)**

Run the script against a DB with the live corpus. Set `content_originality_max_similarity` in `settings_defaults.py` to the calibrated value (max-over-chunks scores higher than a single opening embed, so it will likely rise above 0.83). If no live corpus is available in this environment, leave the provisional `0.83` with a comment `# TODO recalibrate: max-over-chunks shifts the distribution up (spec §2.3)` and record the live recalibration as a post-merge verification step.

- [ ] **Step 12: Commit**

```bash
git add modules/content/atoms/qa_content_originality.py tests/unit/services/atoms/test_qa_content_originality_atom.py scripts/calibrate_content_originality_threshold.py services/settings_defaults.py
git commit -m "feat(qa): whole-post chunked scoring for content_originality rail"
```

---

## Task 5: Series-exclusion — hard-block-ready (advisory unchanged)

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/qa_content_originality.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py` (excluded-series key)
- Modify: `src/cofounder_agent/tests/unit/services/atoms/test_qa_content_originality_atom.py`

**Interfaces:**

- Produces: `_series_excluded(template_slug, excluded) -> bool`; the atom skips a would-be HARD veto (not the advisory score) when the draft's series is excluded.

- [ ] **Step 1: Verify dev_diary doesn't run the rail (design confirmation)**

Confirm `dev_diary` runs the 5-node legacy `TEMPLATES` factory subset (`verify_task → narrate_bundle → generate_seo_metadata → source_featured_image → finalize_task`) with NO `qa.*` atoms — so the rail never runs on dev_diary today. Grep: `grep -n "dev_diary" services/pipeline_templates/__init__.py`. Record the finding in the atom docstring. This means series-exclusion is defensive for a FUTURE graduation, not a live filter — so the minimal safe mechanism is a `template_slug` guard on the hard-veto path (not corpus-neighbor filtering).

- [ ] **Step 2: Write the failing series-exclusion test**

```python
@pytest.mark.unit
class TestSeriesExclusion:
    def test_excluded_series_matches(self):
        assert qa_content_originality._series_excluded("dev_diary", {"dev_diary", "narrate_bundle"}) is True
    def test_included_series_not_excluded(self):
        assert qa_content_originality._series_excluded("canonical_blog", {"dev_diary"}) is False
    def test_blank_never_excluded(self):
        assert qa_content_originality._series_excluded("", {"dev_diary"}) is False

    async def test_hard_veto_skipped_for_excluded_series(self, monkeypatch):
        """Even graduated to hard-block, an excluded-series draft that lifts a
        sibling is downgraded to advisory (flag, not veto)."""
        async def mock_eval(*, content, site_config, pool):
            return (False, 0.05, "content near-duplicate of 'x' (cosine 0.95)")
        monkeypatch.setattr(qa_content_originality, "_evaluate", mock_eval)
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_content_originality.run(
            _state(template_slug="dev_diary",
                   site_config=_Cfg(content_originality_excluded_series="dev_diary,narrate_bundle"))
        )
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True  # downgraded despite the hard gate
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "content_originality" not in decision["vetoed_by"]
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/atoms/test_qa_content_originality_atom.py::TestSeriesExclusion -o addopts="" -q`
Expected: FAIL (`_series_excluded` missing; `template_slug` not read).

- [ ] **Step 4: Implement the exclusion**

Add the helper + wire it into `run()` after `_mark_advisory_if_configured`, forcing advisory when the series is excluded:

```python
def _series_excluded(template_slug: str, excluded: set[str]) -> bool:
    """True when the draft belongs to a templated recurring series exempt from
    the HARD veto tier. Recurring series (dev_diary 'what-we-shipped') share a
    cadence by template and would false-veto once graduated — they still SCORE
    (advisory), just never hard-block."""
    return bool(template_slug) and template_slug in excluded
```

In `run()`, after the advisory mark:

```python
    excluded_raw = ""
    try:
        excluded_raw = site_config.get("content_originality_excluded_series", "dev_diary,narrate_bundle") or ""
    except Exception:  # noqa: BLE001 — stubbed site_config
        # silent-ok: exclusion is a graduation-safety guard; on a config-read
        # blip default to the known recurring series rather than crash the rail.
        excluded_raw = "dev_diary,narrate_bundle"
    excluded = {s.strip() for s in excluded_raw.split(",") if s.strip()}
    template_slug = str(state.get("template_slug") or "")
    if not review.advisory and not review.approved and _series_excluded(template_slug, excluded):
        # Graduated hard veto downgraded to advisory for an exempt recurring
        # series. Mirror _mark_advisory_if_configured's transform exactly: set
        # BOTH bits (aggregate_rail_reviews vetoes on a non-advisory FAILING
        # review) and annotate the feedback so the audit log shows the downgrade.
        review.advisory = True
        review.approved = True
        review.feedback = f"[series-excluded:{template_slug}] {review.feedback}"
```

`ReviewerResult` is a non-frozen `@dataclass` with mutable `advisory`/`approved` fields (`multi_model_qa.py:169-184`), so direct assignment is correct — the same in-place transform `_mark_advisory_if_configured` uses. The guard is a no-op while the rail is advisory (`review.advisory` is already True from the advisory mark), so it changes nothing today; it only takes effect after a future graduation to `required_to_pass=true`.

- [ ] **Step 5: Seed the excluded-series setting**

In `services/settings_defaults.py` DEFAULTS:

```python
    # Templated recurring series exempt from the content_originality HARD veto
    # (they still score/flag). dev_diary/narrate_bundle share a by-template
    # opening cadence — graduating the rail without this would false-veto every
    # shipping log. CSV of template_slug values.
    'content_originality_excluded_series': 'dev_diary,narrate_bundle',
```

Add to the owner map (`'owner': 'multi_model_qa', 'value_type': 'string'`). (The test's `_state(**over)` helper already forwards any kwarg into the state dict via `base.update(over)`, so `_state(template_slug=...)` needs no change.)

- [ ] **Step 6: Run the atom tests**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/atoms/test_qa_content_originality_atom.py -o addopts="" -q`
Expected: PASS (advisory-now behavior unchanged; the graduated-hard-veto path respects exclusion).

- [ ] **Step 7: Commit**

```bash
git add modules/content/atoms/qa_content_originality.py services/settings_defaults.py tests/unit/services/atoms/test_qa_content_originality_atom.py
git commit -m "feat(qa): series-exclusion makes content_originality hard-block-ready"
```

---

## Task 6: Docs + services.md regen + full verification

**Files:**

- Modify: `docs/architecture/anti-hallucination.md`
- Modify: `CLAUDE.md`
- Regenerate: `docs/reference/services.md`

- [ ] **Step 1: Update anti-hallucination.md**

`grep -n opening_originality docs/architecture/anti-hallucination.md` and update every reference to `content_originality`, noting the whole-post (chunked, max-over-chunks) scope and that it remains advisory.

- [ ] **Step 2: Update CLAUDE.md references**

`grep -n opening_originality CLAUDE.md` and update the Content-pipeline-stages section: `qa.opening_originality (advisory RAG self-echo net — flags a near-duplicate opening)` → `qa.content_originality (advisory RAG self-echo net — flags a whole-post near-duplicate, chunked max-over-chunks)`.

- [ ] **Step 3: Regenerate services.md**

Run: `python scripts/regen-services-doc.py`
Expected: the row for `qa_opening_originality.py` becomes `qa_content_originality.py` with the updated docstring first line; the file count is unchanged (rename, not add).

- [ ] **Step 4: Run the full affected-test sweep**

Run: `cd src/cofounder_agent && "<main-venv>/python.exe" -m pytest tests/unit/services/atoms/test_qa_content_originality_atom.py tests/unit/services/test_canonical_blog_spec.py tests/unit/services/test_graph_def_contract_freshness.py tests/unit/services/test_qa_gates_db_writer.py -o addopts="" -q`
Expected: all PASS.

- [ ] **Step 5: Confirm no stray old-name references remain**

Run: `grep -rn "opening_originality" src/cofounder_agent docs CLAUDE.md`
Expected: only intentional backcompat fallbacks (the atom's old-key settings reads) and historical migration/CHANGELOG references. No live graph_def node, reviewer string, or qa_gates row references the old name.

- [ ] **Step 6: Run the silent-except lint + services-doc check**

Run: `python scripts/ci/lint_silent_excepts.py && python scripts/regen-services-doc.py && git diff --quiet -- docs/reference/services.md && echo CLEAN`
Expected: lint exit 0; services.md CLEAN after regen.

- [ ] **Step 7: Commit**

```bash
git add docs/architecture/anti-hallucination.md CLAUDE.md docs/reference/services.md
git commit -m "docs(qa): content_originality rename + whole-post scope references"
```

---

## Post-merge (operator steps, not code)

- Rebuild + restart the worker (bind-mounted atom + the graph_def re-seed migration take effect on restart). ([feedback_rebuild_authority])
- Re-run `scripts/calibrate_content_originality_threshold.py` against prod and set `content_originality_max_similarity` via `set_setting` if the provisional value was shipped.
- Confirm `qa_gates.content_originality` counters increment on the next `canonical_blog` run (`poindexter qa-gates list` / the /d/qa-rails dashboard) — the evidence needed before any future graduation to hard-block.
- Confirm no graph_def drift (CI `integration-db` snapshot guard green; worker boot logs show the row re-stamped by `ensure_active_graph_defs_stamped`).

## Verification against spec

- §2.1 rename map — Tasks 1, 2, 3, 6 cover atom file, ATOM_META.name/reviewer, node id, settings, qa_gates row, docs. ✓
- §2.2 whole-post scoring — Task 4 (`_chunk_draft` + max-over-chunks `_evaluate`). ✓
- §2.3 recalibrate — Task 4 Steps 10-11 (calibration script + threshold). ✓
- §2.4 advisory + series-exclusion + telemetry — Task 5 (exclusion) + Task 3 (telemetry). ✓
- §2.5 tests — whole-body lift, opening echo (subsumed), unrelated pass, series-exclusion, advisory honored, counter increments. ✓
