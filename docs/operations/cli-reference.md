# Poindexter CLI Reference

**Last Updated:** 2026-04-23

The `poindexter` command is installed as a console script when you
`pip install -e src/cofounder_agent`. It's the primary operator
interface for everything you'd otherwise do by hand with `psql` or
`curl`.

All commands take `--help` for inline documentation. This page is the
consolidated reference.

---

## Quick reference

| Command group | What it does                                                     |
| ------------- | ---------------------------------------------------------------- |
| `setup`       | First-run wizard — generates secrets, tests DB, writes bootstrap |
| `memory`      | Query and write the shared pgvector memory store                 |
| `tasks`       | Manage the content pipeline task queue                           |
| `posts`       | Query and manage published/draft blog posts                      |
| `settings`    | Read and write `app_settings` (DB-first config)                  |
| `costs`       | Pipeline spending and operational metrics                        |
| `vercel`      | Vercel deployment status via the REST API                        |
| `premium`     | Manage Poindexter Pro subscription license                       |
| `schedule`    | Queue scheduled publishes (batch, list, shift, clear)            |
| `publish-at`  | Schedule a single approved post for a specific time              |
| `topics`      | Topic-decision approval queue (list/show/approve/reject/propose) |
| `approve`     | Clear any HITL gate by task id                                   |
| `reject`      | Reject any HITL gate by task id                                  |
| `gates`       | List + toggle HITL approval gates                                |
| `migrate`     | Schema migration runner (status / up / down)                     |

Run `poindexter --help` for the top-level list and
`poindexter <group> --help` for subcommands.

---

## `setup`

First-run wizard. Generates secrets, tests DB connectivity, and writes
`~/.poindexter/bootstrap.toml`.

```bash
poindexter setup                # Interactive wizard
poindexter setup --auto         # Non-interactive (uses sensible defaults)
poindexter setup --check        # Validate an existing bootstrap.toml
poindexter setup --force        # Overwrite existing config
poindexter setup --db-url postgresql://...   # Pre-populate the DSN
```

The `--auto` mode spins up local Postgres automatically if Docker is
available and no `--db-url` is provided. Use `--check` in CI to
validate that an operator's config is well-formed.

---

## `memory`

Semantic memory store (pgvector). Shared spine for all agents.

### `memory search <query>`

Semantic search across the memory store. Top-k cosine similarity.

```bash
poindexter memory search "how do we handle hallucinations"
poindexter memory search "retention policy" --min-similarity 0.7 --limit 10
poindexter memory search "deploy" --source-table audit --writer brain
poindexter memory search "topic" --json-output
```

Flags:

- `--writer TEXT` — filter by writer (e.g. `brain`, `claude`, `operator`)
- `--source-table TEXT` — filter by source (`brain`, `audit`, `posts`, `memory`, `claude_sessions`, `issues`)
- `--min-similarity FLOAT` — default 0.5
- `--limit INT` — default 5
- `--json-output` — machine-readable JSON

### `memory status`

Aggregate counts per `source_table` and per `writer`.

```bash
poindexter memory status
poindexter memory status --json-output
```

### `memory store`

Store a memory note.

```bash
poindexter memory store --text "Decision: use Ollama-only" --writer operator --tags decision,ollama
poindexter memory store --file notes.md --writer operator
```

Flags:

- `--text TEXT` or `--file PATH` — content source (pick one)
- `--writer TEXT` — required
- `--source-id TEXT` — optional ID to link back
- `--source-table TEXT` — defaults to `memory`
- `--tags TEXT` — comma-separated

### `memory embed <text>`

Print the raw 768-dim embedding vector for debugging.

```bash
poindexter memory embed "hello world" --json-output
```

### `memory backfill-posts`

Re-embed all published posts into pgvector (source_table='posts').

```bash
poindexter memory backfill-posts --dry-run
poindexter memory backfill-posts --since 2026-04-01
```

---

## `tasks`

Content pipeline task queue.

### `tasks list`

```bash
poindexter tasks list                              # Most recent 20
poindexter tasks list --status awaiting_approval   # Queue for review
poindexter tasks list --limit 50 --json-output
```

### `tasks get <task_id>`

```bash
poindexter tasks get 842                # by numeric id
poindexter tasks get 7038e408           # prefix match on UUID
poindexter tasks get 842 --content      # include full post body
poindexter tasks get 842 --json-output
```

### `tasks create <topic>`

Queue a new content task.

```bash
poindexter tasks create "Self-hosting Qwen 3 on a 5090" \
  --category technology \
  --target-audience "self-hosting developers" \
  --primary-keyword "self-hosted AI" \
  --style journalistic \
  --tone analytical \
  --target-length 2500
```

### `tasks approve <task_id>` / `tasks reject <task_id>`

```bash
poindexter tasks approve 842
poindexter tasks reject 842 --feedback "Off-topic for the brand"
```

`--feedback` is required on reject — the worker API enforces it.

### `tasks publish <task_id>`

Manually publish an approved task (normally handled automatically on
approval, but this is the manual override).

---

## `posts`

Published/draft blog post management.

### `posts list`

```bash
poindexter posts list
poindexter posts list --include-drafts --limit 50 --offset 100
poindexter posts list --json-output
```

### `posts search <query>`

```bash
poindexter posts search "docker"                   # substring on title+content
poindexter posts search "docker" --semantic        # pgvector similarity
poindexter posts search "docker" --limit 10 --json-output
```

### `posts get <slug>`

```bash
poindexter posts get my-post-slug
poindexter posts get my-post-slug --content        # include body
poindexter posts get my-post-slug --json-output
```

### `posts publish <post_id>` / `posts archive <post_id>`

```bash
poindexter posts publish 481           # status='published', published_at=now
poindexter posts archive 481           # soft delete, removes from live site
```

### `posts retitle <post_id> <title>`

```bash
poindexter posts retitle 481 "Why Docker Changed Everything"
```

Useful when you need to fix a title without going through the full
approval queue again (e.g. remove a first-person pronoun caught late).

---

## `settings`

Read and write `app_settings` — the DB-first config plane.

### `settings list`

```bash
poindexter settings list
poindexter settings list --category qa              # filter by category
poindexter settings list --search threshold         # substring match
poindexter settings list --include-inactive         # show disabled keys
poindexter settings list --limit 100 --json-output
```

### `settings get <key>`

```bash
poindexter settings get qa_final_score_threshold
poindexter settings get cli_oauth_client_id --json-output
```

### `settings set <key> <value>`

Upsert — creates if missing, updates if present.

```bash
poindexter settings set qa_final_score_threshold 75
poindexter settings set qa_gate_weight 0 --category qa --description "Gates are veto-only, not scored"
```

### `settings disable <key>` / `settings enable <key>`

Soft-delete a setting (`is_active=false`) while preserving the value.

```bash
poindexter settings disable feature_x_enabled
poindexter settings enable feature_x_enabled
```

---

## `costs`

Spend and operational metrics.

### `costs budget`

Month-to-date spend vs. the configured monthly budget.

```bash
poindexter costs budget
poindexter costs budget --json-output
```

### `costs operational`

Task counts, worker state, websocket connections.

```bash
poindexter costs operational
poindexter costs operational --json-output
```

---

## `vercel`

Vercel deployment status via the Vercel REST API. Requires
`vercel_token` in `app_settings`.

```bash
poindexter vercel deployments
poindexter vercel deployments --limit 10 --target production
poindexter vercel production           # Latest production deploy
poindexter vercel domains              # Linked domains
```

---

## `premium`

Manage your Poindexter Pro subscription license.

### `premium activate <license_key>`

```bash
poindexter premium activate lmsqueezy_license_key_here
```

Activates a Lemon Squeezy license and unlocks premium content
(prompts, additional dashboards, book chapters).

### `premium deactivate`

Frees your activation slot (useful when moving to a new machine).

```bash
poindexter premium deactivate
```

### `premium status`

Shows the current license state, expiration, and activation count.

```bash
poindexter premium status
```

---

## `topics`

Operator interface for the topic-decision approval queue
([Glad-Labs/poindexter#146][issue-146]). When
`pipeline_gate_topic_decision = on`, anticipation_engine output and
manual proposals both land in the same queue rather than auto-running.
Drain the queue at your own pace.

[issue-146]: https://github.com/Glad-Labs/poindexter/issues/146

The gate is **opt-in**. Enable it with:

```bash
poindexter gates set topic_decision on
```

`poindexter gates list` shows the current state and pending count.
With the gate off, manual `topics propose` calls land at
`status='pending'` and the worker runs them end-to-end exactly like
auto-discovered topics — the gate is purely additive.

### `topics list [--source NAME] [--json]`

Show every topic currently paused at `topic_decision`. Oldest-first so
you work the queue chronologically.

```bash
poindexter topics list                      # all pending
poindexter topics list --source manual      # only your hand-typed ones
poindexter topics list --source anticipation_engine
poindexter topics list --json | jq '.[].task_id'
```

Output columns: `TASK_ID` (first 8 chars of UUID), `AGE` (relative —
`5m`, `2h`, `1d`), `SOURCE`, `TOPIC`. Empty queues exit zero with a
friendly message — that's a normal state, not an error.

### `topics show <task_id> [--json]`

Pretty-print the full artifact for one queued topic. The artifact
shape:

```json
{
  "topic": "...",
  "primary_keyword": "...",
  "tags": ["...", ...],
  "category_suggestion": "...",
  "source": "anticipation_engine" | "manual" | "...",
  "research_summary": "<= ~200 words, omitted if research hasn't run yet>",
  "score_signals": {
    "novelty": <float|null>,
    "internal_link_potential": <float|null>,
    "category_balance": <string|float|null>
  }
}
```

### `topics approve <task_id> [--feedback TEXT] [--json]`

Approve a queued topic — flips it to `pending` and resumes the
pipeline.

```bash
poindexter topics approve abcd1234-5678-...
poindexter topics approve abcd1234 --feedback "great angle for hardware niche"
```

Alias for `poindexter approve <task_id> --gate topic_decision` with
the gate name asserted explicitly so a misrouted task fails loudly.

### `topics reject <task_id> [--reason TEXT] [--json]`

Reject a queued topic — flips it to `dismissed` (the topic-decision
gate's reject status) and ends the task.

```bash
poindexter topics reject abcd1234 --reason "off-brand for the gaming category"
```

### `topics propose --topic "..." [flags]`

Manually inject a topic into the queue. Lands the same way an
anticipation_engine auto-proposal would — at
`awaiting_gate='topic_decision'` when the gate is on, otherwise at
`status='pending'`.

```bash
poindexter topics propose --topic "Why custom water cooling beats AIOs in 2026" \
  --keyword "custom water cooling" \
  --tags pc-hardware,cooling \
  --category hardware
```

Flags:

- `--topic TEXT` (required) — non-empty topic string.
- `--keyword TEXT` — primary SEO keyword. Falls back to the first tag.
- `--tags A,B,C` — comma-separated tag list.
- `--category SLUG` — category hint (`hardware`, `ai-ml`, `gaming`).
- `--source LABEL` — origin label recorded on the artifact (default
  `manual`); use this to tag queue items by sub-source if you've got
  several injection paths feeding `topics propose`.
- `--target-length N` — target word count for the eventual draft
  (default `1500`).
- `--style` / `--tone` — pipeline parameters (defaults
  `technical` / `professional`).
- `--json` — machine-readable output.

Refuses to propose past `topic_discovery_max_pending` (default `50`)
when the gate is enabled — drain pending topics first. Empty topic
text exits non-zero rather than silently inserting a junk row.

### Worked example — drain the queue

```bash
$ poindexter topics list
Topic-decision queue (4 pending):

  TASK_ID    AGE    SOURCE                 TOPIC
  abcd1234   2h     anticipation_engine    Why local LLMs won 2026
  bcde2345   1h     manual                 Liquid cooling for inference rigs
  cdef3456   30m    anticipation_engine    AMD Ryzen X3D vs Threadripper
  defa4567   5m     manual                 Off-brand merchandise news

$ poindexter topics show defa4567
Task defa4567-...
  paused_at      2026-04-26T12:00:00+00:00
  age            5m
  status         in_progress

  topic              Off-brand merchandise news
  primary_keyword    merchandise
  tags               news, merch
  source             manual
  ...

$ poindexter topics approve abcd1234 --feedback "good angle"
Approved topic for task abcd1234 — pipeline resumed.

$ poindexter topics approve bcde2345
Approved topic for task bcde2345 — pipeline resumed.

$ poindexter topics approve cdef3456
Approved topic for task cdef3456 — pipeline resumed.

$ poindexter topics reject defa4567 --reason "off-brand, news/merch"
Rejected topic for task defa4567 → status='dismissed'.
```

### Telegram-side flow

When a topic lands in the queue, the operator gets a Telegram message
(if `telegram_ops` is configured in `webhook_endpoints`) with the
artifact summary. The reply path mirrors the CLI:

- `/approve_topic <task_id>` — equivalent to `topics approve`.
- `/reject_topic <task_id> [reason]` — equivalent to `topics reject`.

The Telegram bot is OpenClaw, which calls into the same
`mcp-server-gladlabs` `topics_approve` / `topics_reject` tools the
CLI uses. Single source of truth: `services.approval_service` /
`services.topic_proposal_service`.

---

## `schedule` and `publish-at`

Operator interface for the scheduled-publishing queue
([Glad-Labs/poindexter#147][issue-147]). Poindexter already has a
background loop (`services/scheduled_publisher.py`) that publishes
posts whose `posts.published_at` slot has arrived and whose
`status='scheduled'`. These commands populate, inspect, and rewrite
that queue.

[issue-147]: https://github.com/Glad-Labs/poindexter/issues/147

A post becomes eligible for batch scheduling once it has been
approved (`status IN ('approved', 'awaiting_approval')`) and has no
publish slot yet (`published_at IS NULL`). The batch command pulls
the oldest-approved-first by default; pass `--ordered-by` to change
the source ordering.

### `publish-at <post_id> <when>`

Schedule a single post.

```bash
poindexter publish-at 0fe7… "2026-04-28 09:00"
poindexter publish-at 0fe7… --in 2h
poindexter publish-at 0fe7… --in 7d
poindexter publish-at 0fe7… "tomorrow 9am"
poindexter publish-at 0fe7… "next monday 14:00"
```

Flags:

- `--in DUR` — relative scheduling (`30m`, `2h`, `1d`, `1h30m`).
- `--force` — overwrite an existing schedule.
- `--json` — machine-readable output.

### `schedule batch`

Bulk-assign slots to the approved queue.

```bash
poindexter schedule batch \
  --count 20 --interval 30m --start "tomorrow 9am" \
  --quiet-hours 22:00-07:00
```

Flags:

- `--count N` (required) — how many posts to schedule.
- `--interval DUR` (required) — slot spacing (`30m`, `1h`, `1h30m`, `1d`).
- `--start TIME` (required) — first slot. ISO 8601, `now`,
  `tomorrow 9am`, or `next monday 14:00`.
- `--quiet-hours HH:MM-HH:MM` — slots inside the window are skipped
  to the next allowed time. Falls back to the `publish_quiet_hours`
  app_setting when omitted.
- `--ordered-by` — `approved_at` (default) | `created_at` | `id` |
  `title`.
- `--force` — re-schedule posts that already have a slot.
- `--json` — machine-readable output.

If no posts are eligible the command exits non-zero with an
informative message — no silent no-op.

### `schedule list [--all] [--json]`

Print the upcoming queue in publish-time order. Pass `--all` to
include past schedules.

### `schedule show <post_id> [--json]`

Schedule + status detail for a single post.

### `schedule shift <post_id> --by DUR` / `schedule shift --all --by DUR`

Push one schedule (or every still-future scheduled post with `--all`)
back by `DUR`. Past slots are intentionally untouched — shifting an
already-published post does not un-publish it.

### `schedule clear <post_id>` / `schedule clear --all`

Drop the schedule on a single post (or every still-future scheduled
post). Clearing rolls the row back to `status='approved'` so it can
be re-scheduled.

### Worked examples

**1. Schedule 20 approved posts to publish every 30m starting tomorrow
at 9am, skipping 22:00-07:00 quiet hours**

```bash
poindexter schedule batch \
  --count 20 \
  --interval 30m \
  --start "tomorrow 9am" \
  --quiet-hours 22:00-07:00
```

Slots that would land between 22:00 and 07:00 jump forward to the
next 07:00; subsequent slots step from there. So if the 11th slot
would be 02:00, it becomes 07:00 the next morning, and slots 12, 13…
continue at 07:30, 08:00, …

**2. Show what's scheduled in the next 7 days**

```bash
poindexter schedule list --json | jq '
  .rows
  | map(select(.published_at <= (now + 7*86400 | strftime("%Y-%m-%dT%H:%M:%S+00:00"))))
'
```

For a quick human-readable view, plain `poindexter schedule list`
prints publish-time-ordered rows.

**3. Push the entire queue back by 1 hour**

```bash
poindexter schedule shift --all --by 1h
```

Every post still in the future has its `published_at` advanced by
one hour. The audit trail records the operation as a single
`schedule.shifted` event with the post-id list in the payload.

---

## `migrate`

Schema migration runner ([Glad-Labs/poindexter#226][issue-226]). The
worker auto-runs migrations on boot, but operators who don't restart
their container can have unapplied migrations sitting on disk. This
group exposes the same runner as a direct CLI surface — no worker
round-trip, no container restart.

[issue-226]: https://github.com/Glad-Labs/poindexter/issues/226

The CLI operates on the same `services/migrations/` directory and the
`schema_migrations` tracking table the worker uses, so it's safe to
mix and match (CLI `migrate up` then a worker reboot, or vice versa).

### `migrate status [--json]`

List every migration file on disk alongside its applied state.

```bash
poindexter migrate status
poindexter migrate status --json
```

Sample output:

```
[x] 0103_add_embeddings_tsvector.py                    applied 2026-04-28
[x] 0104_seed_voice_agent_defaults.py                  applied 2026-04-29
[ ] 0105_seed_brand_keywords.py                        pending

Total: 89 migrations — 87 applied, 2 pending
```

### `migrate up [--to NAME] [--json]`

Apply all pending migrations. Idempotent — already-applied migrations
are skipped via `schema_migrations`.

```bash
poindexter migrate up                       # apply everything pending
poindexter migrate up --to 0103             # stop at 0103 (everything ≤ 0103)
poindexter migrate up --to 0103_xxx.py      # also accepted
poindexter migrate up --json
```

`--to` accepts either a numeric prefix (`0103`) or a full filename
(`0103_add_embeddings_tsvector.py`). The summary line at the end
reads `applied N, skipped M, failed K, pending P`.

### `migrate down [--to NAME] [--all] [--yes] [--json]`

Roll back applied migrations. Migrations are rolled back in reverse
order. Without flags, only the most recent applied migration is
rolled back.

```bash
poindexter migrate down                     # roll back only the latest
poindexter migrate down --to 0099           # roll back everything > 0099
poindexter migrate down --all               # roll back EVERYTHING (asks first)
poindexter migrate down --all --yes         # skip the confirmation
poindexter migrate down --json
```

A migration's rollback path is whichever of these the migration
module exposes (in priority order):

- `async def down(pool)` — pool-based, the modern convention
- `async def rollback_migration(conn)` — connection-based, the older
  convention

If a migration defines neither, `migrate down` skips it and exits
non-zero with a `no down()/rollback_migration() defined — skipped`
notice. The `schema_migrations` row is left intact in that case so
nothing silently disappears from the tracker.

`--all` and `--to` are mutually exclusive. `--all` prompts for
confirmation unless `--yes` is set or `--json` is set (JSON mode
suppresses the interactive prompt).

---

## Environment variables

The CLI respects a small set of env vars for non-interactive use:

| Variable             | Purpose                                                           |
| -------------------- | ----------------------------------------------------------------- |
| `DATABASE_URL`       | PostgreSQL DSN. Overrides bootstrap.toml if set.                  |
| `POINDEXTER_API_URL` | Base URL for the worker API (default `http://localhost:8002`).    |
| `POINDEXTER_TOKEN`   | Bearer token for the worker API. Overrides bootstrap.toml if set. |

Everything else comes from `~/.poindexter/bootstrap.toml` and the
`app_settings` DB table.

---

## JSON output mode

Every list/search/get subcommand supports `--json-output`. Use it for
piping into `jq` or building shell automation:

```bash
poindexter tasks list --status rejected --json-output | jq '.[] | {id, score: .quality_score, error: .error_message}'
```

Pretty-printed tables are the default for humans. JSON is for scripts.
