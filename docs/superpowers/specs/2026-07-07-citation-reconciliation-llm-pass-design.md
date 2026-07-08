# Citation reconciliation — deterministic grammar fix + grounded-LLM pass

**Date:** 2026-07-07
**Issue:** Glad-Labs/poindexter#765 follow-up (citation reconcile / unlinked-attribution)
**Status:** design approved — proceeding to plan

## Problem

Real research-corpus sources cited **by name but without a link** slip through
`content.reconcile_citations` unrepaired **and** through `qa.unlinked_attribution`
unflagged — the worst cell, because the reader can't tell a genuine dropped-link
citation from a hallucinated source.

Concrete evidence — task `249a74ca` ("The Autopilot Trap", `glad-labs` niche,
`canonical_blog`, quality 90, awaiting_approval):

- _"…the brain shifts into what **Full Brim Safety** calls low-power mode."_
- _"A **Topular Strategy** piece makes a point worth sitting with…"_
- _"A piece on **LinkedIn** frames this well…"_

All three are **real** sources that were in the writer's `research_context`
(`fullbrimsafety.com`, `topularstrategy.com`, a LinkedIn/Sastry article) — none
linked, none flagged.

Running the **actual** `_citation_match` code against the **actual** draft +
corpus (`scratchpad/repro_citation.py`) proves two stacked root causes:

1. **Grammar gap.** `find_attributions()` returns `[]` (both advisory and repair
   frames). The attribution grammar recognizes a fixed verb set (`notes`,
   `argues`, `explains`…) and three subject shapes. The writer used verbs/shapes
   outside it — `what X calls Y`, `an X piece makes a point`, `a piece on X
frames this` — so **no attribution site is detected, so none of the four
   scans fire** (link / re-point / strip / advisory-flag).
2. **Handle-matcher gap.** Even with detection, `_domain_match("Full Brim
Safety")` = **MISS**: the matcher compares whole tokens (`full`/`brim`/
   `safety`) and the space-joined subject against the domain handle, never a
   **space-collapsed** form, so a multi-word brand never matches its
   concatenated domain (`fullbrimsafety.com`). Single-word `LinkedIn` = HIT.

Because the advisory rail's looser `match_subject` _does_ recognize them (title
token `safety`/`topular`/`strategy`), it correctly stays silent — but repair's
stricter `_domain_match` can't link them, leaving them unlinked-and-unflagged.

The grammar gap is unwinnable by whack-a-mole: every new writer phrasing is a new
regex — the exact "regex trap" the module docstring already warns against. So the
fix is two-layer: close the two proven deterministic bugs (cheap, high-precision)
**and** add a grounded-LLM pass for the arbitrary-phrasing tail.

## Goal

1. A real corpus source referenced by name gets an inline link to its corpus URL.
2. A named-source reference with **no** corpus match (possibly hallucinated) is
   surfaced — a `finding` **and** an advisory QA score — for the operator to
   soften or reject. We never auto-rewrite prose to "soften".
3. Deterministic-first, LLM-second: the LLM only ever sees the residual the
   deterministic scan couldn't handle, and never touches the prose itself.

## Non-goals

- **Auto-softening/rewriting prose** — flagging only; softening is a curation
  call (`feedback_output_vs_curation`, `feedback_human_approval`).
- **Writer-prompt changes** — repair exists precisely because prompt compliance
  is unreliable; a separate lever.
- **`dev_diary`** — its 4-node subset has no reconcile atoms / graph_def.
- Changing scan-3 (multi-tenant re-point) or the YouTube pass.

## Architecture

### Part A — deterministic fix (`modules/content/atoms/_citation_match.py`)

**A1 — grammar.** Add the frames the writer actually used, **repair-only** (the
advisory scan stays conservative — these verbs are common in plain prose, so
flagging them over-fires, but _linking_ is safe because repair is gated on a
corpus `_domain_match`):

- Extend `_REPAIR_EXTRA_VERBS` with `calls|call|called|frames|framed|dubs|dubbed|
terms|termed|labels|labeled|brands|branded` (repair-only; NOT added to
  `_SUBJECT_VERBS`).
- Add a `piece`-frame pattern for `(a|an|the) <Brand> piece <verb>` and
  `a piece (on|from|by|in) <Brand> <verb>` — a noun sits between the brand and
  the verb today, which the subject-first regex can't span. Repair-only.

**A2 — handle matcher.** Teach `_domain_match` a space-collapsed subject form:
`"Full Brim Safety"` → `fullbrimsafety`, compared to the domain sld
`fullbrimsafety`. Guarded to stay high-precision: the collapsed form must be
length ≥ 6 and equal the sld exactly (not a prefix/substring), so `"Safety
First"`→`safetyfirst` links only to `safetyfirst.com`, never a partial.

Net after Part A: `Full Brim Safety` and `Topular Strategy` link deterministically;
`an X piece` / `a piece on X` shapes are caught for corpus-matched brands.

### Part B — grounded-LLM pass (`modules/content/atoms/content_llm_reconcile_citations.py`)

New atom `content.llm_reconcile_citations`, placed **after**
`content.reconcile_citations` and **before** `quality_evaluation` — deterministic
repair runs first (cheap, handles the clean cases), the LLM only sees the
residual, and both run before the QA rails so inserted links flow through
`qa.citations`' dead-link check.

**Safety model — "LLM proposes, deterministic code verifies + applies".** The LLM
never rewrites the draft. It receives the draft + the corpus (`name → URL`) and
returns only JSON:

```json
{
  "links": [{ "text": "<verbatim span>", "url": "<corpus url>" }],
  "ungrounded": ["<named source with no corpus match>"]
}
```

`links` are applied by **code**, dropping any pair where:

- `url` is not verbatim one of the corpus URLs → **cannot hallucinate a target**,
- `text` is not a contiguous substring of the current draft → **cannot mangle prose**
  (we only wrap an existing string in `[text](url)`),
- that span is already inside a markdown link → **idempotent**.

First unlinked occurrence only; edits applied right-to-left to keep offsets valid
(mirrors `link_matched_attributions`). Produces `content`.

**Ungrounded → both surfaces** (operator choice): each named source the LLM judges
has no corpus match →

- `emit_finding(kind="unlinked_named_sources", severity="warn", …)` → Discord +
  Findings dashboard, pre-approval (side-effect, mirrors the two_pass canaries), and
- an advisory `ReviewerResult(reviewer="citation_grounding", …)` appended to
  `qa_rail_reviews`, marked advisory via `qa_gates.citation_grounding.required_to_pass`
  (seeded `false`) so it scores the weighted QA mean without vetoing; an operator
  graduates it to a hard gate via the #454 lever.

The prompt instructs the model to treat only **source-attribution** mentions ("X
says", "according to X", "what X calls", "an X piece") — not proper nouns merely
mentioned in passing — so `ungrounded` stays meaningful.

**Cost gate (token efficiency).** The atom fires an LLM call only when a corpus
source is plausibly unlinked: for each corpus source, if a brand token appears in
the content but the source URL does **not**, there is a candidate → call. If every
corpus source is absent or already linked → **no LLM call** (no-op). One local
structured-extraction call, `think=False`.

**Fail-open.** Disabled, no corpus, no candidate, LLM error, or unparseable JSON →
no-op (deterministic links already applied; the post always completes). LLM/parse
failures log a WARNING (not a hard fail — this is an advisory enhancement, and
`content.reconcile_citations` already ran).

### ATOM_META

- `name="content.llm_reconcile_citations"`, `type="atom"`, `version="1.0.0"`
- `requires=("content",)`, `produces=("content", "qa_rail_reviews")`
- `inputs`: `content`, `research_context` (optional)
- `cost_class="compute"`; model resolved via the `citation_reconcile_llm_model`
  pin (empty → structured-extraction default), like `two_pass`'s
  `resolve_local_model`. `capability_tier` set for lab/router observability only.
- `idempotent=True` (already-linked spans skipped), `parallelizable=False`
- `side_effects=("calls the LLM to match named sources to the corpus", "emits findings")`

## Prompt (DB-configurable)

One new prompt key `atoms.content.llm_reconcile_citations`, YAML/SKILL.md default
consumed by `UnifiedPromptManager` (per `feedback_prompts_must_be_db_configurable`),
inline fallback for bootstrap/test. System+user pair; output contract is the JSON
above, nothing else. Emphasizes: use ONLY the provided URLs; return the EXACT
verbatim span; source-attribution mentions only; a named source absent from the
list goes in `ungrounded`. Exact wording finalized during implementation.

## Config (settings_defaults.py — not a migration)

| Key                                        | Default    | Purpose                                          |
| ------------------------------------------ | ---------- | ------------------------------------------------ |
| `citation_reconcile_llm_enabled`           | `true`     | master switch for Part B                         |
| `citation_reconcile_llm_model`             | `` (empty) | model pin; empty → structured-extraction default |
| `citation_reconcile_llm_timeout_seconds`   | `60`       | per-call timeout                                 |
| `citation_reconcile_llm_max_content_chars` | `24000`    | bound the prompt                                 |

Advisory gate row `citation_grounding` (`enabled=true`, `required_to_pass=false`)
seeded via the same mechanism prior rails used — **implementation must confirm
whether qa_gates seed via a boot-time seeder or `baseline.seeds.sql`** and follow
`feedback_seed_data_in_baseline_not_new_migrations`.

## Graph wiring (`services/canonical_blog_spec.py`)

- Add node `{"id": "llm_reconcile_citations", "atom": "content.llm_reconcile_citations"}`.
- Rewire edges: `reconcile_citations → llm_reconcile_citations → quality_evaluation`.
- Update `graph_def_contract_fingerprints.json` (node count 38 → 39) + the
  `test_canonical_blog_spec` assertions. `pipeline_use_graph_def=true` is live, so
  it takes effect on the next reseed/deploy.

## Testing (TDD)

**Part A** (`test_citation_match.py` / `test_citation_atoms.py`):

- Regression: `what Full Brim Safety calls X` + corpus → linked to `fullbrimsafety.com`.
- Regression: `A Topular Strategy piece makes a point` + corpus → linked.
- `a piece on LinkedIn frames this` → linked (single-word brand, prep object).
- Space-collapse matches concatenated domain; does NOT over-match a partial/prefix.
- Advisory scan unchanged: `the function calls the API` is NOT flagged (new verbs
  are repair-only).

**Part B** (`test_llm_reconcile_citations.py`, dispatcher stubbed — no real LLM):

- Link pairs applied; content changed.
- Hallucinated `url` (not in corpus) → dropped.
- Paraphrased `text` (not verbatim) → dropped.
- Already-linked span → skipped (idempotent).
- `ungrounded` name → `emit_finding` called + advisory review appended with count.
- No corpus / disabled / no-candidate → **no dispatcher call**.
- LLM raises / bad JSON → no-op + WARNING (fail-open).
- Wiring: node + edges present, valid DAG, atom callable resolves, discovered in
  `list_atoms()`.

## Acceptance

- Replaying task `249a74ca`'s content + corpus through the two-layer path links
  `Full Brim Safety`, `Topular Strategy`, and `LinkedIn`.
- A fabricated named source (absent from corpus) yields a `finding` + an advisory
  score, and is left unlinked.
- No LLM call on a draft with no unlinked corpus source (gate holds).
- Full backend unit suite green (`feedback_docs_and_tests_default`).

## Rollout (two PRs)

- **PR 1** — Part A deterministic fix + this spec. Small, high-confidence,
  independently valuable.
- **PR 2** — Part B grounded-LLM atom + graph wiring + config + prompt + docs.

Both to `Glad-Labs/glad-labs-stack` (source of truth), squash-merged, off fresh
`origin/main` (`feedback_all_changes_via_pr`, `feedback_linear_history`).

## Out-of-scope follow-ups

- Further deterministic grammar widening — unnecessary once Part B owns the tail
  (keep only the two proven frames).
- Writer-prompt reinforcement to link inline (separate lever).
- Docs: refresh `docs/architecture/anti-hallucination.md` + the CLAUDE.md
  content-pipeline node list (38 → 39) with the new atom.
