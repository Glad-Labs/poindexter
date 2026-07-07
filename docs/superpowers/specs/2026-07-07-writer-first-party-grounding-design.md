# Writer first-party grounding: scrub + claude_sessions re-enable

**Status:** approved design, pre-implementation
**Date:** 2026-07-07
**Related memory:** `project_rag_corpus_pollution`, `feedback_amplify_operator_knowledge`

## Problem

The canonical_blog writer grounds its draft on `rag_source_filter='posts'` — clamped
to posts-only because unfiltered RAG pulled ops-log sludge (`claude_sessions` /
`brain` / `audit` are ~98% of the `embeddings` table) that the critic vetoed.

But posts-only grounding causes **corpus autophagy**: the writer paraphrases the
nearest prior post, the new post echoes it, the new post gets embedded, and the
next writer grounds on _that_ — the 2026-06 "VRAM is the only currency" cluster
(four posts opening near-verbatim). Posts are the contaminant _because_ they are
the writer's own downstream output.

`claude_sessions` cannot self-echo — they are not written by the writer. They are
first-party operator knowledge (what we actually did), which is also the content
strategy we want: "write about what we're doing and why," not reuse topics from
other sources. The blocker to re-enabling them has always been that raw session
embeds carry operator-info leaks (name, paths, private-repo refs, secrets) that
must never reach public content.

## Goal (v1)

Ground the writer primarily on **scrubbed `claude_sessions`**, with `posts` capped
low so they can't re-form the echo loop. Deterministic only — no LLM distillation
in v1 (the `claude_code_sessions` tap already filters transcript sludge).

**In scope:** operator-info scrub (write + read), per-source caps, re-enable
`claude_sessions` for the writer path only.
**Out of scope (follow-ups):** LLM distillation of sessions; `memory` as a source
(operator-substance leak risk — scrub catches tokens, not substance); niche-
conditional grounding; narrowing niches to AI/ML; widening the writer to rent the
hybrid+rerank `rag_engine`.

## Design

### 1. Writer grounding source set + per-source cap

- New setting **`writer_rag_source_filter`** (CSV) — the writer's snippet source
  allowlist, **decoupled** from the general `rag_source_filter` that other
  consumers read (`rag_engine.get_rag_retriever` for internal-link discovery /
  dedup). Prevents sessions from leaking into internal-link suggestions.
  - OSS default: `posts` (unchanged behavior).
  - `_resolve_snippet_source_filter` (in `two_pass_writer`) reads
    `writer_rag_source_filter`, falling back to `rag_source_filter` → `posts`.
    Still never queries unfiltered.
- New setting **`writer_rag_source_caps`** (CSV, e.g. `posts:2`) — a per-source
  max applied in `_select_snippets` so no single source exceeds its cap. `posts`
  capped at **2** of the ~20 grounding slots; `claude_sessions` fills the rest.
  Applied _after_ the near-duplicate ceiling and interleaved with MMR selection so
  the cap composes with the existing de-echo (#2185) rather than fighting it.

Posts stay in the DB and in every _other_ consumer (internal links, pre-gen
dedup, the `opening_originality` rail). Only the writer's snippet pull de-weights
them.

### 2. Shared RAG scrub — `services/rag_scrub.py` (ships public)

`scrub_rag_text(text: str) -> str` — the single scrub boundary. Composes:

- **Secret patterns** — promoted from `taps/claude_code_sessions._DEFAULT_SCRUB_PATTERNS`
  (sk-/ghp\_/AWS/JWT/slack/`enc:v1:`) into the shared module; the tap imports them back.
- **Private-repo refs** — consolidate the two duplicated `_scrub_private_repo_refs`
  (`two_pass_writer.py` + `narrate_bundle.py`) here; both call the shared fn.
- **Operator-identity patterns** — loaded via `_load_operator_leak_patterns()`,
  which imports the stripped overlay module (§3) and returns `[]` if absent.
  Mirrors `settings_defaults.apply_operator_overrides` exactly.

The mechanism ships to OSS; the operator-specific patterns do not.

### 3. Operator leak-pattern overlay (stripped from mirror)

The operator-identity regexes (name incl. middle-initial form, `C--users-mattm`
path, Tailnet IP, Funnel host, GitHub handle) currently live in
`scripts/ci/check_public_mirror_safety.py` (`_LEAK_PATTERNS`), which is already in
`_STRIP_FILES`. Extract the operator-identity subset into a shared importable
module — **`services/operator_leak_patterns.py`** — and add it to `_STRIP_FILES`
(it carries the name literal, so it must not ship; same posture as
`operator_overrides.py`).

- Single source of truth: both `check_public_mirror_safety.py` and `rag_scrub`'s
  hook import the patterns from this module.
- Import mechanics: `operator_leak_patterns.py` is the source of truth; `rag_scrub`
  and the tap import it directly (same package). The CI guard imports it too — but
  if its runtime `sys.path` can't reach `src/cofounder_agent` cleanly at sync-time,
  the fallback is a contract test asserting the guard's local copy stays byte-equal
  to the module (a drift guard), not a fragile cross-tree import.
- OSS: module absent → `rag_scrub` hook no-ops → generic scrub only (correct; OSS
  installs have their own operator identity, not Matt's).

### 4. Scrub application points (two layers)

- **Write path** — `claude_code_sessions` tap calls `scrub_rag_text` (replacing its
  secret-only `_scrub`) before yielding Documents. Clean vectors _and_ clean text
  at rest, so retrieval itself isn't matching on operator tokens.
- **Read backstop** — `two_pass._embed_and_fetch_snippets` runs `scrub_rag_text`
  over each `snippet['snippet']` before returning; `build_rag_context` and
  `narrate_bundle` scrub their grounding text the same way. Catches every source
  and anything the write path missed.

### 5. Re-enable (prod, after scrub verified)

`set_setting writer_rag_source_filter='claude_sessions,posts'` on prod. OSS default
stays `posts` (harmless — the tap is stripped from the mirror, so OSS has no
`claude_sessions` rows to filter to).

## Data flow

```
WRITE:  session .jsonl → tap noise-filter (drop system-reminders / thinking /
        loop sentinels, summarize tool calls) → scrub_rag_text (secrets +
        private-repo + operator-identity) → embed → embeddings[claude_sessions]

READ:   topic → embed query → pgvector kNN over writer_rag_source_filter
        → candidates → near-dup ceiling → MMR + per-source cap (posts ≤ 2)
        → scrub_rag_text (read backstop) → writer prompt
```

## Error handling / safety

- **Leak scrub fails CLOSED at read.** If `scrub_rag_text` raises on a snippet,
  drop that snippet from grounding rather than pass unscrubbed operator text to a
  public writer. Grounding degrades gracefully (other snippets remain); it never
  zeroes a run because the scrub is simple regex over one snippet at a time.
- **Per-source cap is deterministic** — no LLM, no new cost.
- **OSS no-op** is explicit and tested (overlay absent → generic scrub only).

## Testing

- Unit `rag_scrub`: secrets redacted, private-repo rewritten, operator-name
  redacted when overlay patterns injected, no-op when overlay absent.
- Unit `_select_snippets`: `posts` capped at 2 while sessions fill remaining slots;
  cap composes with the dedup ceiling + MMR.
- Unit `_resolve_snippet_source_filter`: reads `writer_rag_source_filter`, falls
  back to `rag_source_filter` → `posts`; never returns empty (never unfiltered).
- Contract: overlay operator-name patterns match the same positive/negative cases
  as the existing `test_check_public_mirror_safety_name_regex` (shared truth).
- Mirror-safety guard: assert `services/operator_leak_patterns.py` is in
  `_STRIP_FILES` (prevents the name literal shipping) — fails loud if someone
  forgets.
- Tap: yields scrubbed Documents (extend `test_claude_code_sessions_tap`).

## Rollout

1. Land scrub + overlay + cap with `writer_rag_source_filter` default `posts`
   → zero behavior change (safe to merge).
2. Verify scrub against a real session sample (no name/path/repo/secret survives).
3. `set_setting writer_rag_source_filter='claude_sessions,posts'` on prod.
4. Watch: QA pass rate, `opening_originality` flags trending down, drafts reading
   as session-grounded (what we did / why), and — critically — spot-check the next
   few drafts for any operator-substance leak the token scrub can't catch.

## Follow-ups (not this spec)

- LLM distillation of sessions into insight nuggets, if the deterministic filter
  proves too noisy.
- `memory` source and/or niche-conditional grounding (glad-labs niche only).
- Widen the writer to rent `rag_engine` (hybrid + cross-encoder rerank) — the
  retrieval-path divergence noted in `project_rag_corpus_pollution`.
- **Strategy:** narrow niches toward AI/ML and shift to first-party / build-in-
  public content (Matt, 2026-07-07) — separate strategic brainstorm.
