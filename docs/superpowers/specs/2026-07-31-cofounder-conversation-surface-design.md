# Cofounder conversation surface — console chat design

**Date:** 2026-07-31
**Status:** designed — phases not started
**Author:** Claude (with Matt)
**Epic:** Glad-Labs/poindexter (filed alongside this spec; phase issues linked from the epic)

## Problem

The original vision for this project is a **cofounder, not a control panel**: a
system Matt converses with, that acts on the business by request, reports back,
and carries memory across sessions. The core business capabilities exist (blog,
podcast, video, distribution, QA rails, architect-composed pipelines), and the
operator console can _act_ (approve, retry, reschedule) — but every interaction
is widget-shaped. There is no surface where you _ask for things in plain
language_ and watch them happen.

Concretely missing:

1. A **conversation page** in the console: chatbox + a realtime view of what
   the agent is doing + results rendered back into the thread.
2. A way to **attach context** (brand guide, product specs, CSVs) and have the
   backend use it as grounding for generation _and_ QA.
3. An operator-facing entry point for the **architect**
   (`pipeline_architect.compose` exists and is validated, but nothing
   user-facing calls it).

This page is also deliberately the **seed of the SaaS dashboard for non-tech
customers**: chat is the UI that survives contact with users who will never
learn `qa_gates` or Prefect. Design choices below are made with that second
audience in mind (progressive disclosure, plain-language cards, per-user
columns from day one) without building multi-tenant auth now.

## What already exists (inventory)

Roughly 70% of the machinery is already in the tree, built for the voice
surface:

| Asset                             | Where                                                                         | Relevance                                                                                                                                                                                                                         |
| --------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Local-LLM agent w/ tools ("Emma") | `services/voice_agent_livekit.py`                                             | Proves local tool-calling works with a curated set; tool-budget + loop-bug lessons encoded                                                                                                                                        |
| Claude Code bridge                | `services/voice_agent_claude_code.py`                                         | Full-harness deep brain on the Max sub; `--session-id/--resume` persistence; host-brain exec seam (#1006). Currently blocking `--output-format json`; host side is `scripts/voice-brain-host.ps1` (Windows-era, needs Linux port) |
| Architect                         | `services/pipeline_architect.py::compose`                                     | intent → validated graph spec with FIX-feedback retries; `cache_template()` persists; **no operator entry point yet**                                                                                                             |
| Permission gate                   | `services/agent_permissions.py` + `agent_permissions`/`approval_queue` tables | Per agent/resource/action `allowed` + `requires_approval`; used by MCP `set_setting`                                                                                                                                              |
| Per-node run capture              | `atom_runs` (+ `_RecordingSink` persists each node the instant it lands)      | The realtime pipeline-progress feed, already instrumented                                                                                                                                                                         |
| Console shell                     | `src/cofounder_agent/console/`                                                | React rail + `api.js` single seam + OAuth JWT + drawer/card primitives + ⌘K palette + mock-first mode                                                                                                                             |
| RAG stack                         | `storage_*` S3 layer, `embeddings` pgvector + hybrid RRF, `rag_engine.py`     | Attachment storage + retrieval slots straight in                                                                                                                                                                                  |
| Service layer + adapter contract  | ADR `docs/architecture/2026-06-10-transport-adapter-contract.md`              | The chat tool registry is a 4th thin adapter                                                                                                                                                                                      |
| Notifications                     | `services/integrations/operator_notify.py`, findings routing                  | Completion pings + future `chat` delivery policy                                                                                                                                                                                  |
| Voice message store               | `voice_messages` (+ embedding recall)                                         | To be unified into the new conversation store (decision below)                                                                                                                                                                    |

## Non-goals

- **Multi-tenant SaaS auth.** Tables carry `user_id` and budgets are per-user
  keys from day one, but login/roles/quotas are a separate epic. The console's
  OAuth client-credentials flow stays the auth for now.
- **Replacing Grafana/console panels.** The chat surface _drives_ actions and
  reports results; deep telemetry stays where it is.
- **Autonomous Claude-bridge turns.** Deep-brain turns are operator-initiated
  only (the 2026-06-15 billing split closed automated-on-subscription; the
  scheduled-agents rewire is precedent). Anything self-initiated runs local.
- **A new daemon.** The agent loop is a library inside the worker, using
  Postgres as the bus, like everything else.
- **Editing published posts, deleting data, or spending money from chat.**
  No tools exist for these in any phase of this spec.

## UX design

One new rail tab — **Cofounder** — with three regions:

**Thread (center).** Messages are **typed parts**, not plain text:
`markdown`, `tool_call` (collapsed chip: name + duration + status glyph,
expandable to args/result digest), and `card`. Cards carry the product:

- **Plan card** — architect output in plain language ("Research → Write →
  Fact-check ×2 → Images → QA → SEO"), a `show technical detail` toggle
  revealing the node-level graph, and `Run pipeline` / `Adjust` actions.
- **Result card** — draft title, quality score, `Preview` / `Approve` /
  `Reject`. Buttons POST structured actions into the conversation and call
  the _same service functions_ the approvals inbox calls.
- **Approval card** (amber) — a write-tier tool wants to act; states exactly
  what will happen; `Approve once` / `Deny`. One-shot: buttons disable after
  use and the outcome is stamped into the message part.
- **Image grid / metric / table** as needed.

**Activity rail (right).** Merges (1) the current turn's live tool events and
(2) watched pipeline runs — per-atom progress from `atom_runs` with step
counts and a progress bar; plus **Resources** (attachments with processing
status) and **Session** (brain/model, tool calls + denies, cost + energy).
At ≤920px (the console's existing breakpoint) the rail collapses to an
expandable strip under the streaming message.

**Composer (bottom).** Text + paperclip (resources) + mic affordance (voice
unification later). Slash commands reuse the palette: `/brief`,
`/create-post`, `/plan`.

**Empty state** answers "what can you do?" from the tool catalog itself
(AI-first: the catalog is the capability list) with 3–4 suggested prompts.
The rail tab shows an unread badge for messages that landed while away.

## Architecture

### Conversation store (unified — decision)

One store for all agent conversation, with voice folded in later rather than
a parallel silo:

```sql
chat_conversations(
  id uuid pk, user_id text not null default 'operator',
  title text, brain text not null default 'local',     -- local | claude_code
  status text not null default 'active',               -- active | archived
  transport text not null default 'console',           -- console | voice | api
  metadata jsonb not null default '{}',
  created_at timestamptz, last_message_at timestamptz
)
chat_messages(
  id uuid pk, conversation_id uuid fk,
  role text not null,                                  -- user | assistant | system
  parts jsonb not null default '[]',                   -- typed parts (markdown/tool_call/card)
  turn_status text not null default 'complete',        -- pending|streaming|complete|failed|interrupted
  tokens int, cost_usd numeric, model text,
  created_at timestamptz
)
chat_task_links(conversation_id, pipeline_task_id, purpose, created_at)
resources(
  id uuid pk, user_id text not null default 'operator',
  conversation_id uuid null,                           -- null = library (pinned)
  filename text, mime text, bytes int, storage_url text,
  extracted_text text, status text not null default 'processing',  -- processing|ready|failed
  created_at timestamptz
)
```

Messages embed into the existing `embeddings` table (`source='chat'`,
`source='resource'`) for recall; retention rows (below) prune vectors like
`claude_sessions`. `voice_messages` migrates into this store in phase 6
(`transport='voice'`) so the cofounder is one identity with one memory
regardless of input surface.

### Turn protocol + lifecycle

`POST /api/chat/conversations/{id}/messages` responds with a **streamed
NDJSON body** of typed events (fetch-streaming works same-origin with the
JWT; no EventSource header problem):

```
{"event":"turn_started","message_id":…}
{"event":"token","text":"…"}                  (when dispatch_stream lands; else one "text" chunk)
{"event":"tool_start","name":"list_tasks","args_digest":…}
{"event":"tool_result","name":…,"ok":true,"digest":…,"ms":412}
{"event":"card","card":{…}}
{"event":"approval_required","action":…,"summary":…}
{"event":"task_linked","task_id":…}
{"event":"error","reason":…}
{"event":"done","tokens":…,"cost_usd":…}
```

Turn lifecycle is **persisted** (`turn_status`) so a worker restart mid-turn
renders an honest "interrupted — retry", never a hang. Guards: per-turn
timeout (`console_chat_turn_timeout_s`), max tool calls per turn
(`console_chat_max_tool_calls`), and repeat-call detection (same tool + same
args twice in a turn → inject a corrective error into context; third → abort
the turn) — the voice agent's in-progress-tool-result loop bug is the
precedent. A **Stop** button cancels the loop / kills the bridge subprocess
and stamps `interrupted`.

Background progress after a turn ends: the rail polls
`GET /api/tasks/{id}/atom-runs` (new thin read route over `atom_runs`) at
~5s while a watched run is live. A persistent SSE channel is a later
upgrade, not a v1 need.

### Brain seam (local-first — decision)

Same shape voice already has, formalized:

|        | `local` (default, ships with Poindexter)         | `claude_code` ("Deep" — operator overlay)       |
| ------ | ------------------------------------------------ | ----------------------------------------------- |
| Engine | Tool loop over the routed LLM seam               | Existing bridge + `--output-format stream-json` |
| Cost   | $0 local                                         | $0 incremental (Max sub), interactive-only      |
| Scope  | Shallow loops (status, create, approve, compose) | Multi-step work, code edits, "go fix it"        |
| SaaS   | The customer default                             | BYO-key later                                   |

The SaaS framing settles the default: customers don't have Matt's Max sub, so
the shipped brain must be the local loop. Emma proves the pattern; the
architect's FIX-retry shows how small models self-correct. The bridge is a
per-conversation toggle. Proactive/scheduled messages **never** use the
bridge.

### Tool registry — a 4th thin adapter

`services/chat_tools.py`: declarations of `{name, description, json_schema,
handler, tier, idempotency_key_fn}` that **delegate to the same service
functions** the HTTP/CLI/MCP adapters call. No business logic, no inline SQL
(adapter-purity ratchet applies). Tiers:

- **read** — auto-runs (briefing, list/status, search_memory, budget, settings read).
- **write** — in-chat approval card unless `agent_permissions`
  (`agent_name='console_chat'`) grants unattended; seeds ship
  `requires_approval=true` for publish / settings-write / restart / spend.
  `create_post` is write-tier but low-risk: its output lands in the existing
  approval inbox anyway.
- **absent** — destructive surfaces (delete, bulk ops, money) have no tool.

Every execution writes an `audit_log` row (`event_type='chat_tool_call'`,
details: conversation, tool, args digest, outcome, ms) — the cofounder's
actions must be reviewable like `brain_decisions`. Registered in
`services/audit_event_schemas.py`. Long-term the chat and MCP catalogs
converge into one declared catalog; not in scope here.

Idempotency: turn retries and double-sends must not double-act. Card actions
are one-shot; create-flows lean on topic dedup (#2764); write handlers accept
an idempotency key derived from (conversation, message, tool, args).

### LLM seam additions (new work surfaced by code check)

The dispatcher exposes `dispatch_complete`/`dispatch_embed` only, and the
LiteLLM provider plumbs no `tools=` — Emma's tool-calling lives inside
pipecat. Phase 1 therefore includes:

1. **`tools=` + tool-call response plumbing** through
   `llm_providers/litellm_provider.py` (LiteLLM supports it natively) and a
   `dispatch_complete` passthrough.
2. **`dispatch_stream`** mirroring `dispatch_complete` (the provider already
   has `stream()` / `supports_streaming = True`).

Fallback if (2) drags: v1 streams tool/activity events (loop progress needs
no LLM streaming) and delivers assistant text as one chunk — the realtime
feel mostly comes from tool events.

### Context management (small-model survival)

`num_ctx` is a hard ceiling and prompt+output share it
(`reference_num_ctx_is_the_article_length_ceiling`). Rules:

- Tool results enter model context as **digests + references** (cards point
  at DB rows; the model sees summaries, capped per-result).
- Thread context = system prompt + rolling summary + recent tail
  (`console_chat_context_recent_turns`) + embedding recall over older turns.
- The system prompt (persona, capabilities, house rules) is a SKILL.md pack
  via UnifiedPromptManager — DB-configurable per prompt policy.

### GPU policy

Chat is an interactive-latency workload on shared VRAM. Defaults: a small
resident model (`console_chat_model`, default `qwen2.5:7b` — the voice
precedent) that coexists with render lanes; calls route through the GPU
scheduler as an interactive class when #914 P2 lands. When the card is
occupied (e.g. wan video render), the UI shows an honest
"waiting for GPU — video render in progress" state, never a dead spinner.

### Resources (attachments)

Paperclip → `storage_*` S3-compatible upload → `resources` row → async
extract/chunk/embed job (`source='resource'`) → status chip flips to
"embedded". Caps: `console_chat_resource_max_mb`, mime allowlist
(pdf/docx/md/txt/csv/html/png/jpg). Two consumption paths:

1. **Chat**: RAG over the conversation's resource set.
2. **Pipelines**: tasks created from chat carry `resource_ids` in
   `pipeline_tasks.metadata`; a `content.load_resources` atom injects them
   into PipelineState for the writer — and the **faithfulness QA rails ground
   against the same corpus**. The brand guide is writing input _and_
   fact-check reference.

Conversation-scoped by default; "pin to library" (`conversation_id=null`)
promotes to the permanent knowledge base — the future customer-onboarding
surface.

**Injection hardening (hard rules).** Resources create the dangerous triad:
untrusted content + tools that act + private data. Therefore:

- Resource text is always framed as **quoted data**, never system-prompt
  material.
- Approval requirements are **never relaxed** in a conversation that has
  resources attached; write-tier approval cards cannot be auto-approved there
  regardless of `agent_permissions`.
- Approval cards state the _actual_ action + target so a hijacked request is
  visible at the approval step.
- The extract step strips active content (scripts/macros) and stores plain
  text only.

## Safety + cost

- Tiered tools + `agent_permissions` seeds as above; fail-closed on
  indeterminate checks (#750 precedent).
- Approve≠publish survives untouched — tools call the same gated services.
- Every LLM call routes through the dispatcher → cost_guard caps + Langfuse
  tracing come free. Session chip shows tokens / $ / energy live.
- `console_chat_daily_token_budget` bounds the loop independently of the
  global caps (a runaway conversation can't eat the day's budget).

## Observability

- `audit_log`: `chat_tool_call` (above) + `chat_turn_completed` (turn-level:
  brain, model, tokens, cost, tool count, outcome).
- Grafana: panels land same-commit (per `feedback_grafana_everything`) —
  turns/day, tool error rate, approval denies, turn latency p95, chat spend —
  natural home: Integrations & Admin or a small Cofounder row on Mission
  Control.
- GlitchTip captures loop exceptions; Langfuse traces every turn via the
  router callback.

## Settings (all `app_settings`, defaults in `settings_defaults.py`)

```
console_chat_enabled=false            console_chat_brain=local
console_chat_model=qwen2.5:7b         console_chat_turn_timeout_s=120
console_chat_max_tool_calls=8         console_chat_daily_token_budget=200000
console_chat_context_recent_turns=12  console_chat_tool_result_max_chars=2000
console_chat_resource_max_mb=25       console_chat_resource_mime_allowlist=…
console_chat_watch_poll_seconds=5     agent_persona_name=Poindexter
console_chat_bridge_enabled=false     (operator overlay flips; interactive-only)
```

`agent_persona_name` is deliberately a setting: one persona across voice and
chat, and SaaS customers name their own cofounder. (Matt's overlay may keep
"Emma" for voice TTS continuity; recommendation is one name everywhere.)

## Testing

- **Backend**: unit tests with a fake LLM for the loop (tool dispatch, caps,
  repeat-call detection, turn-status persistence, injection rules);
  contract tests for the tool registry (schema ↔ handler ↔ tier); route
  tests for the NDJSON protocol.
- **Console**: node:test + vm contract tests for stream parsing, card
  actions (one-shot), reconnect/interrupted rendering — same harness as
  `api.token.test.js`.
- **Golden-conversation evals**: ~15 canned intents with parser-verified
  expected tool calls (convergence-watchdog pattern — count with a parser,
  not a prompt), run when `console_chat_model` changes. Local-model
  tool-calling is the joint that breaks silently on model swaps.
- **Mock brain**: the console is mock-first; a scripted mock conversation
  (plan card, running rail, result card) keeps the zero-backend OSS demo
  working.

## Retention

Day-one `retention_policies` rows: `chat_messages` (ttl on archived
conversations), chat/resource embeddings pruned like `claude_sessions`,
`resources` orphan cleanup (conversation deleted → unpinned resources go).
Sweeper measured by backlog per `reference_audit_sweepers_by_backlog`.

## Phasing (→ issue per phase)

1. **P1 — agent service + tool seam + store (backend).** Tables, NDJSON turn
   protocol + lifecycle, local-brain loop, tool registry (~8 read tools +
   `create_post`), LLM seam `tools=` plumbing (+ `dispatch_stream` or evented
   fallback), context digests, audit rows, settings, retention, evals harness.
2. **P2 — console Cofounder tab.** Thread + parts + tool chips, activity
   rail (session + live turn events), composer, conversation list/resume,
   mock brain, mobile collapse, unread badge, contract tests.
3. **P3 — act + watch.** Write tools behind approval cards +
   `agent_permissions` seeds, one-shot card actions, `GET
/api/tasks/{id}/atom-runs` + live run rail, completion system messages +
   Telegram deeplink, Stop/cancel + interrupted recovery.
4. **P4 — architect in the loop.** `architect.compose` tool → plan card
   (plain language + technical detail) → Adjust (feedback → recompose) → Run
   (`cache_template` + create task). The marquee feature.
5. **P5 — resources.** Upload → extract/embed pipeline, consumption paths
   (chat RAG + `content.load_resources` + QA grounding), injection
   hardening, pin-to-library, caps + retention.
6. **P6 — deep brain + proactive + voice unification.** Linux host-exec
   service (port `voice-brain-host.ps1`), `stream-json` event mapping, brain
   toggle UI + interactive-only guard; proactive threads (morning briefing
   opener, findings `chat` delivery policy) local-brain-only; migrate
   `voice_messages` into the unified store.

## Decisions taken

- **Tab + product name: Cofounder.** The surface is the product story.
- **Local-first default brain**; Claude bridge is the operator "Deep" toggle.
- **One conversation store** with `transport`; voice migrates in (P6).
- **Persona is a setting** (`agent_persona_name`), one identity across
  surfaces, OSS default "Poindexter".
- **Resources always harden approvals** (never auto-approve writes in a
  resourced conversation).

## Open questions

- Does the P1 tool loop need structured-output enforcement
  (`structured_extraction_model` pattern) for models that flake on
  tool-call JSON, or does LiteLLM's native tool API suffice for the
  qwen2.5/qwen3 family? Evals harness answers this empirically.
- SSE channel vs. poll for post-turn watches — revisit after P3 ships with
  polling.
- When the SaaS auth epic lands, whether `user_id` maps to oauth_clients or
  a new users table.

## Risks

- **Small-model tool fumbling** — mitigated by curated shallow tools, FIX-style
  corrective errors, evals on model swap; worst case the local brain stays a
  command-runner while Deep mode carries complex asks.
- **Injection via resources** — hard rules above; security review question #1
  before any SaaS exposure.
- **Scope creep toward a Claude Code clone** — the tool registry is curated
  and shallow by design; "go fix arbitrary things" is what Deep mode is for.
- **VRAM contention degrading chat latency** — small resident default +
  honest waiting states; scheduler interactive class when #914 P2 lands.
