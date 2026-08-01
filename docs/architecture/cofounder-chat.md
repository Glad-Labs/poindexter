# Cofounder chat — backend agent service (P1)

Phase 1 of the Cofounder conversation surface (Glad-Labs/poindexter#946,
P1 = #947): the backend that lets an operator chat with an agent that acts
through tools. The console UI arrives in P2; until then the surface is
exercisable with `curl` (examples below).

Design spec (private operator repo):
`docs/superpowers/specs/2026-07-31-cofounder-conversation-surface-design.md`.

## Pieces

| Piece              | Where                                                                                              | What                                                                                            |
| ------------------ | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Conversation store | `services/chat_conversation_store.py` + `chat_conversations` / `chat_messages` / `chat_task_links` | Threads + typed message `parts` + persisted `turn_status`; lazy interrupted-turn repair on read |
| Agent loop         | `services/chat_agent.py::run_turn`                                                                 | Async generator of NDJSON events; tool loop with deadline / max-call / repeat-call guards       |
| Tool registry      | `services/chat_tools.py`                                                                           | 8 curated tools; a 4th thin adapter over the service layer (ADR 2026-06-10)                     |
| Routes             | `routes/chat_routes.py` (`/api/chat/*`)                                                            | Thin adapters; OAuth-JWT; gated by `console_chat_enabled`                                       |
| System prompt      | `skills/chat/agent/SKILL.md` (`chat.system`) via `services/chat_prompts.py`                        | Langfuse-overridable; drift-guarded inline fallback                                             |
| LLM seam           | `dispatch_complete(..., tools=…)` → `Completion.tool_calls`                                        | litellm + openai_compat support tools; ollama_native fails loud                                 |
| Audit              | `audit_log` `event_type='chat_tool_call'` (`services/audit_event_schemas.py`)                      | One row per tool execution — the reviewable action trail                                        |
| Panels             | Grafana → Integrations & Admin → "Cofounder chat" row                                              | Turns, tool calls/errors, p95 tool ms, bad turns, tokens today                                  |
| Model evals        | `scripts/chat_brain_evals.py`                                                                      | Golden intents, parser-verified; run on every `console_chat_model` swap                         |

## Turn protocol

`POST /api/chat/conversations/{id}/messages` (body `{"text": …}`) streams
`application/x-ndjson`, one event object per line:

```
{"event":"turn_started","message_id":…}
{"event":"tool_start","name":…,"args_digest":…}
{"event":"tool_result","name":…,"ok":…,"ms":…,"digest":…}
{"event":"task_linked","task_id":…}
{"event":"text","text":…}
{"event":"error","reason":…,"detail":…}
{"event":"done","turn_status":…,"prompt_tokens":…,"completion_tokens":…,"cost_usd":…}
```

The assistant row is inserted `streaming` before the first LLM call and
finalized `complete` / `failed` / `interrupted` — a worker crash mid-turn is
repaired to `interrupted` by the next conversation read, so the UI never
shows an eternal spinner. `error` events always carry an operator-actionable
`detail` (which setting to change, which limit fired).

Token streaming is deliberately deferred: the realtime feel comes from the
tool events, and assistant text arrives as one `text` event per turn. A
`dispatch_stream` upgrade slots in behind the same protocol without breaking
consumers.

## Tools (P1)

`list_tasks`, `get_task`, `get_budget`, `search_memory`,
`find_similar_posts`, `get_audit_summary`, `get_setting` (read tier —
auto-run), and `create_post` (write tier — auto-runs in P1 because its
output lands in the existing approval inbox; **it stages, never publishes**).
P3 (#949) added the approval-carded write tools (`set_setting`,
`restart_service`, `cancel_task` — see the P3 section below); the
registry's unit test pins the full write surface with its approval flags
so a new write tool cannot land quietly.

`create_post` shares the exact HTTP-route creation path via
`services/blog_task_creation.py` (extracted from `routes/task_routes.py`):
semantic topic dedup (409 + `force=true` override), `auto` topic pool claim,
weighted length picker, approval-queue throttle flag.

## Enabling it

```bash
poindexter settings set console_chat_enabled true
# The chat tier's provider must support tool calling — litellm is the prod
# default; a fresh install on ollama_native must flip it:
poindexter settings set plugin.llm_provider.primary.standard litellm
```

Then, worker running:

```bash
TOKEN=$(curl -s -X POST localhost:8002/token -d 'grant_type=client_credentials&client_id=…&client_secret=…' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
CONV=$(curl -s -X POST localhost:8002/api/chat/conversations -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -N -X POST "localhost:8002/api/chat/conversations/$CONV/messages" -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{"text": "how many tasks are pending?"}'
```

## Settings

| Key                                  | Default      | Meaning                                      |
| ------------------------------------ | ------------ | -------------------------------------------- |
| `console_chat_enabled`               | `false`      | Master switch for `/api/chat`                |
| `console_chat_brain`                 | `local`      | `claude_code` Deep mode lands in P6 (#952)   |
| `console_chat_model`                 | `qwen2.5:7b` | Small + resident; coexists with render lanes |
| `console_chat_turn_timeout_s`        | `120`        | Whole-turn deadline → `interrupted`          |
| `console_chat_max_tool_calls`        | `8`          | Executions per turn before failing loud      |
| `console_chat_daily_token_budget`    | `200000`     | Per-day chat cap, independent of cost_guard  |
| `console_chat_context_recent_turns`  | `12`         | Context tail length                          |
| `console_chat_tool_result_max_chars` | `2000`       | Digest cap for tool results in context       |
| `agent_persona_name`                 | `Poindexter` | One identity across chat + voice             |

## Guard rails (why each exists)

- **Repeat-call detection** — the second identical (tool, args) call gets a
  corrective message instead of a re-execution; a third aborts. The voice
  agent's lost-tool-result infinite loop is the precedent.
- **Round cap** (`max_tool_calls + 2` LLM rounds) — a model that never stops
  requesting calls fails loud instead of burning the deadline.
- **Digested results** — prompt + output share `num_ctx`; a full draft in a
  7B context starves generation
  (`reference_num_ctx_is_the_article_length_ceiling`).
- **Provider capability probe** — `supports_tools=False` (ollama_native)
  fails the turn with the exact remediation command rather than silently
  answering without tools.
- **GPU coexistence** — dispatches ride `dispatch_complete` with
  `priority="operator"`, so gpu-scheduler admission (#914) applies as its
  phases land; the default model is small enough to stay resident next to
  render lanes.

## P3 — act + watch (poindexter#949)

**Approval cards.** Write tools with `requires_approval` (`set_setting`,
`restart_service`, `cancel_task`) are never executed by the loop: the turn
queues a `chat_approvals` row, renders an amber card, tells the model the
action awaits sign-off, and completes. `POST
/api/chat/approvals/{id}/{approve|deny}` resolves atomically (one-shot —
double clicks and replays get `already_resolved`), executes the stored call
via the same registry handler on approve, stamps the card + appends a
`system` message with the real outcome, and audits
`chat_approval_resolved`. `agent_permissions`
(`console_chat`/<tool>/`execute`) is the override lane: `allowed=false`
forbids; `allowed=true, requires_approval=false` runs inline; indeterminate
checks fail closed to the card. The post-lifecycle buttons on completion
cards (Approve/Reject/Publish) reuse the console's existing task endpoints
client-side — approve≠publish untouched. Agent-side `approve_post` /
`reject_post` / `publish_post` tools are deliberately deferred until the
task-publishing route logic is extracted to a service (route-locked today).

**Watch + completion.** Tasks created from chat are linked
(`chat_task_links`); the rail polls `GET /api/chat/watch/{task_id}` (slim
`atom_runs` spine + expected node count) every
`console_chat_watch_poll_seconds` while live. `ChatTaskWatchJob` (every
minute) appends ONE completion message per link reaching a terminal status
— a `task_result` card + text — even if the tab is closed, stamps
`completed_notified_at`, and pings via `console_chat_watch_notify`
(`discord` default / `telegram` / `none`) with a `console_public_url`
deeplink when set.

Client-side, every thread read (refresh poll, watch-effect reload,
post-turn swap) is **token-serialized**: a read superseded by a
later-started one is discarded on arrival, and a failed refresh never
blanks rows already on screen. Without this, a slow stale GET landing
after a fresher one regressed the pane — messages visibly vanished until
the next poll restored them.

**Stop.** The composer's STOP aborts the fetch; the worker cancels the
generator and finalizes the turn `interrupted` (shielded write; the lazy
repair is the backstop). Turn-level `chat_turn_completed` audit rows land
in the loop's `finally`.

## P4 — the architect in the loop (poindexter#950)

`plan_pipeline(intent, topic)` is the architect's first operator entry
point. The model calls it for "design/plan a pipeline" asks; the handler
runs `pipeline_architect.compose` (max_attempts=2 to fit the turn
deadline), **namespaces the spec so its `pipeline_templates` slug always
starts `plan_`** (cache_template upserts by slug — an LLM naming its spec
"canonical blog" must never overwrite the production template; the guard
refuses on escape), caches it (fingerprint-stamped, active), inserts a
`chat_plans` row, and emits a **plan card**: plain-language step blocks
(client-derived from node ids — "Write & polish · Quality checks ×13 ·
SEO"), a technical toggle with raw node ids, and Run/Adjust.

- **Run** (`POST /api/chat/plans/{id}/run`, one-shot draft→ran): creates
  the `pipeline_tasks` row with `template_slug` = the cached slug — Prefect
  claims it like any pending task, `load_active_graph_def` loads the
  composed graph, the contract-fingerprint gate still applies — links the
  task to the conversation (the P3 watch rail + completion watcher take
  over), stamps the card, appends a system message, audits
  `chat_plan_run`. No semantic-dedup guard: a hand-designed run is an
  explicit operator action.
- **Adjust** is conversational: the button prefills the composer
  ("Adjust the plan: …") and the model composes a NEW plan — the thread is
  the adjust loop, no bespoke state machine.
- Compose failures surface the architect's `FIX:` errors verbatim (they
  are written as the repair signal).

Compose runs on the architect model (`pipeline_architect_model`, else the
local-writer resolver) — a cold large model can push a plan turn toward
the `console_chat_turn_timeout_s` deadline; raise it if plan turns
interrupt.
