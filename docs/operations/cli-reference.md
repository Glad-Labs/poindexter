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
| `sprint`      | Gitea issues dashboard (Glad Labs internal)                      |
| `vercel`      | Vercel deployment status via the REST API                        |
| `premium`     | Manage Poindexter Pro subscription license                       |

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
poindexter settings get api_token --json-output
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

## `sprint`

Gitea issues dashboard for the primary repo. Glad-Labs-internal — uses
the Gitea token in `~/.poindexter/bootstrap.toml`.

```bash
poindexter sprint issues
poindexter sprint issues --state open --label tech-debt --limit 20
poindexter sprint milestones
poindexter sprint milestones --state open
poindexter sprint recent --days 7       # Issues closed in last 7 days
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
