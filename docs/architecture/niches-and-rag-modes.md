# Niches and Writer RAG Modes

Poindexter 1.0 ships a niche-aware topic-discovery flow and four writer
RAG modes. The engine no longer assumes an evergreen-tech editorial
voice — your install is one of many possible niches (real estate,
fashion, finance, indie game devlog, etc.), and the writer can be
configured per niche to ground drafts in your own internal corpus
instead of summarizing external feeds.

This doc is for operators who want to understand what the new flow does,
when each writer mode is the right pick, and how to drive it from the
CLI or MCP. The full design rationale lives in
[`docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md`](../superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md);
this page is the operator surface only.

## Why this exists

Before 1.0, the pipeline summarized external content (Hacker News,
dev.to, web search) and proposed one topic at a time. Most proposals
were rejected by the operator at the title gate, which meant the full
research → draft → QA cost was burned on posts that lost at the first
human touch.

The new flow gates earlier and grounds deeper:

- **Earlier:** one LLM call ranks a batch of 5 candidates against your
  niche's weighted goals. You pick a winner before any draft is written.
- **Deeper:** the writer can pull from your own corpus (past sessions,
  brain knowledge, audit log, git history, prior decisions, memory
  files, post history) instead of paraphrasing what someone else
  already published.

Existing installs are seeded with a `glad-labs` niche so the new flow is
the default; you can configure additional niches per install to run the
same engine on different editorial voices.

## Concepts

### Niche

A row in the `niches` table. One row per audience the operator
publishes for. Each niche owns its `writer_rag_mode`, `batch_size`,
`discovery_cadence_minute_floor`, an optional writer prompt override,
and a list of `target_audience_tags`.

### Niche goal

A weighted entry in `niche_goals`. Each goal pulls from a fixed
vocabulary so the ranker can score candidates consistently across
niches:

| Goal type     | What it scores for                                                                          |
| ------------- | ------------------------------------------------------------------------------------------- |
| `TRAFFIC`     | Organic search potential — trending keyword, broad appeal, evergreen demand.                |
| `EDUCATION`   | Teaches the reader something concrete and useful they didn't know.                          |
| `BRAND`       | Reinforces the operator's positioning and unique perspective.                               |
| `AUTHORITY`   | Demonstrates depth and expertise on something specific.                                     |
| `REVENUE`     | Drives a commercial outcome: signups, sales, conversions, paid feature awareness.           |
| `COMMUNITY`   | Resonates with the existing audience; sparks discussion, shares, replies.                   |
| `NICHE_DEPTH` | Goes deep on the niche specialty rather than broad-audience content.                        |

Weights are integer percentages and must sum to ~100 per niche.

### Topic batch

The unit of operator interaction. A discovery sweep produces one
`topic_batches` row per niche with `status='open'` and `batch_size`
candidates (default 5). At most one open batch per niche exists at a
time (enforced by a partial unique index). Open batches expire after a
configurable window so dead batches don't block the niche forever.

### Candidate

Either a `topic_candidates` row (external — HN, dev.to, web_search,
etc.) or an `internal_topic_candidates` row (RAG-derived from your
corpus). Both carry a `score`, a `score_breakdown` JSONB of
goal-contribution percentages, a `rank_in_batch`, an `operator_rank`,
and an optional operator title/angle rewrite.

Unpicked candidates **carry forward** with a `decay_factor` of 0.7
applied each cycle, so a near-miss this week stays in the running next
week with diminishing weight.

## Discovery flow

A sweep runs **per niche** and is triggered by either:

- **Reactive trigger** — the previous batch transitioned to `resolved`,
  and the niche's `discovery_cadence_minute_floor` has elapsed.
- **Operator on demand** — `poindexter topics discover --niche <slug>`
  (also subject to the floor; pass `--force` to bypass).

The sweep:

1. Picks a niche where the floor has elapsed and no open batch exists.
2. Asks each enabled `niche_source` plugin for candidates proportional
   to its `weight_pct`. Target pool size is ~20.
3. Loads carry-forward candidates from the previous batch and applies
   the 0.7 decay.
4. **Embedding pre-rank.** Cosine-similarity against a precomputed
   "goal vector" per goal type, weighted-summed across the niche's
   goals × the candidate's `decay_factor`. Top 10 advance.
5. **LLM final scoring.** One call returns a JSON `{candidate_id:
   {score, score_breakdown}}` against the niche's weighted goals.
6. Top `batch_size` candidates land in `topic_candidates` /
   `internal_topic_candidates`, a new `topic_batches` row opens, and
   the existing `topic_decision` gate flags "operator action needed."
7. The run is logged to `discovery_runs` for observability.

## Writer RAG modes

Set per niche on `niches.writer_rag_mode`. Tasks without a
`writer_rag_mode` set fall back to the legacy generator, so pre-niche
pipelines are unchanged.

### `TOPIC_ONLY`

Writer gets the topic + angle, runs **one** embedding query against
your corpus, and gets the top-N internal snippets dropped into the
prompt as background context. Single-pass, no enforcement.

When to use it:

- You want internal grounding without a hard citation contract.
- The niche is broad enough that any one query covers the surface area.
- Lowest LLM cost of the four modes.

### `CITATION_BUDGET`

Writer must hit at least N internal citations. The existing
`content_validator` extends its citation rules to enforce the floor;
drafts under-budget are rejected before QA.

When to use it:

- You're publishing under an "authority" or "niche-depth" goal where
  unsupported claims need to be cut.
- You have enough internal corpus to support N citations on most
  topics in the niche.

### `STORY_SPINE`

A preprocessing LLM call reads the top 10–15 internal snippets and
produces a structured outline. The writer then expands the outline.

When to use it:

- Long-form posts where structure matters more than prose density.
- Topics where internal sources naturally tell a story (decision
  history, postmortems, journey-style retrospectives).

### `TWO_PASS`

The Glad Labs default and the most expensive mode.

1. **First pass:** internal-context-only draft. No external research
   call. The writer uses only the corpus snippets the retriever
   pulled.
2. **Second pass:** the writer detects `[EXTERNAL_NEEDED]` markers in
   its own draft, runs a bounded external research step for each, and
   revises. The state machine is capped at **3 revision loops** so it
   can't run away.

`TWO_PASS` is implemented as a LangGraph state machine, which gives the
flow operator-interrupt checkpointing for free — a batch sitting with
the operator for days resumes cleanly when they come back.

When to use it:

- First-person reporting on something nobody else has covered (your
  own infrastructure, your own decisions, your own data).
- Niches where authenticity beats coverage.
- You can afford the extra LLM passes.

## CLI

The new operator surface lives under `poindexter topics` and a new
`poindexter niche` subgroup.

```bash
# Trigger a sweep manually (subject to the niche's cadence floor):
poindexter topics discover --niche glad-labs

# Inspect the current open batch for a niche:
poindexter topics show-batch --niche glad-labs

# Rank the batch 1..N (operator's call, doesn't have to match system rank):
poindexter topics rank-batch <batch_id> --order id1,id2,id3,id4,id5

# Optionally rewrite the winner's title or angle:
poindexter topics edit-winner <batch_id> --topic "New title" --angle "New framing"

# Finalize: marks batch resolved, advances operator_rank=1 to a content_task:
poindexter topics resolve-batch <batch_id>

# Or discard the whole batch and schedule a fresh sweep:
poindexter topics reject-batch <batch_id> --reason "all stale"
```

Niche management lives under `poindexter niche`:

```bash
poindexter niche list
poindexter niche show <slug>
poindexter niche create <slug> --name "..." --writer-rag-mode TWO_PASS
poindexter niche set-goal <slug> AUTHORITY 30
poindexter niche enable-source <slug> internal_rag --weight 40
```

## MCP

The same surface is exposed as MCP tools so Claude Code (and the voice
bot) can drive the gate end-to-end:

- `topics_show_batch`
- `rank_batch`
- `edit_winner`
- `resolve_batch`
- `reject_batch`

All MCP tools are thin wrappers over the same service-layer functions
the CLI uses. The legacy single-topic `topics_show / topics_approve /
topics_reject / topics_propose` tools remain available for backwards
compatibility (operator approve = "advance system rank #1 unedited",
operator reject = "discard batch").

## Configuring a niche

A minimal configuration: create the niche, set goal weights summing to
100, enable the sources you want, and pick a writer mode.

```bash
# Create the niche
poindexter niche create real-estate \
  --name "Real Estate Investing" \
  --writer-rag-mode CITATION_BUDGET \
  --batch-size 5 \
  --cadence-floor 120

# Goal weights (must sum to ~100)
poindexter niche set-goal real-estate AUTHORITY 40
poindexter niche set-goal real-estate EDUCATION 30
poindexter niche set-goal real-estate TRAFFIC   20
poindexter niche set-goal real-estate REVENUE   10

# Enable candidate sources with relative weights
poindexter niche enable-source real-estate web_search   --weight 40
poindexter niche enable-source real-estate internal_rag --weight 40
poindexter niche enable-source real-estate hackernews   --weight  0
poindexter niche enable-source real-estate devto        --weight  0
poindexter niche enable-source real-estate knowledge    --weight 20
```

Trigger the first sweep:

```bash
poindexter topics discover --niche real-estate --force
poindexter topics show-batch --niche real-estate
```

## Provenance

Every published post carries a `topic_batch_id` pointer back to the
batch the topic came from, so you can query `posts → topic_batches →
topic_candidates` to answer "which batch did this post come from, what
were the alternatives, and how did they score?"

This is the data trail behind any future analytics on which goal
weights drive your accept rate.

## Compatibility notes

- Existing installs are seeded with a `glad-labs` niche pre-configured
  to `TWO_PASS`, so the new flow is the default after upgrade.
- Tasks created without a `writer_rag_mode` (e.g. via direct
  `POST /api/tasks` calls that bypass the niche layer) fall back to
  the legacy generator. Pre-niche pipelines keep working unchanged.
- Sweeps run **one niche at a time** in v1. Schema is multi-niche
  ready; the worker is serial.
- Pipeline gateway caps (max-N tasks awaiting approval) are tracked as
  a separate concern and out of scope for the niche flow.
