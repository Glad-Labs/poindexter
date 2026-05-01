# poindexter

Unified command-line interface for the [Poindexter](https://github.com/Glad-Labs/poindexter)
public AI content pipeline. Built and maintained by [Glad Labs LLC](https://gladlabs.io).

`poindexter` is the thin client layer over the same memory store, app_settings
database, and task queue the worker, MCP servers, and OpenClaw use. One schema,
one client, zero drift.

## Install

```bash
pip install poindexter
# or, recommended — isolated install:
uv tool install poindexter
```

Python 3.10 – 3.13 supported.

## Quick start

```bash
# 1. Point the CLI at a Postgres instance (pgvector enabled) and run migrations.
poindexter setup

# Non-interactive — takes an explicit DSN:
poindexter setup --db-url="postgresql://user:pass@host:5432/poindexter_brain"

# Re-run the sanity checks against an existing bootstrap.toml:
poindexter setup --check

# 2. Ask questions of the shared memory spine:
poindexter memory search "why gemma3"

# 3. Inspect the current pipeline task queue:
poindexter tasks list --limit 20

# 4. Read or change runtime app_settings:
poindexter settings list
poindexter settings set enable_pyroscope true
```

Every command respects `-v / --verbose` for client-side info logs and `-h /
--help` for a per-subcommand usage summary.

## Command groups

| Group      | What it does                                                                               |
| ---------- | ------------------------------------------------------------------------------------------ |
| `setup`    | First-run wizard — writes `~/.poindexter/bootstrap.toml`, runs migrations, seeds defaults. |
| `memory`   | Query, store, and stat the shared pgvector memory store.                                   |
| `tasks`    | Browse and manage the content pipeline task queue.                                         |
| `posts`    | Query and manage published / draft blog posts.                                             |
| `settings` | Read and write `app_settings` (DB-first config, no `.env` needed).                         |
| `costs`    | Pipeline spending and operational metrics.                                                 |
| `sprint`   | Gitea issues dashboard for the primary repo.                                               |
| `vercel`   | Vercel deployment status via the REST API (no Vercel CLI needed).                          |
| `premium`  | Manage a Poindexter Premium subscription (license activation / revalidation).              |

Run any group with `--help` for the full subcommand list, e.g.
`poindexter memory --help`.

## Configuration

`poindexter` reads its infrastructure secrets (mostly: a Postgres DSN) from
`~/.poindexter/bootstrap.toml`, which `poindexter setup` writes for you.
Everything else — API keys, feature flags, quality thresholds, model pins — is
stored in the `app_settings` table in Postgres and can be managed with
`poindexter settings`.

There are no required environment variables. `DATABASE_URL`, if present, is
accepted as a convenience override.

## How versions work

The PyPI version of this package tracks the upstream
[poindexter](https://github.com/Glad-Labs/poindexter) repository via
[release-please](https://github.com/googleapis/release-please). Each release on
`main` cuts a tag `poindexter-v<major>.<minor>.<patch>`; that tag fires the
publish workflow and ships a matching wheel to PyPI.

Breaking changes in the CLI show up in the repo's
[CHANGELOG](https://github.com/Glad-Labs/poindexter/blob/main/CHANGELOG.md).

## Development / working from a clone

If you've cloned the upstream repo you don't need PyPI — everything is already
wired up:

```bash
# From repo root:
cd src/cofounder_agent
poetry install
poetry run poindexter --help
```

Or, to iterate on the package manifest directly:

```bash
cd src/cofounder_agent/poindexter
python -m build          # produces dist/poindexter-*.whl + .tar.gz
pip install dist/poindexter-*.whl
poindexter --help
```

## License

Apache 2.0. See [LICENSE](https://github.com/Glad-Labs/poindexter/blob/main/LICENSE)
in the upstream repo.

## Support

- Bugs and feature requests: <https://github.com/Glad-Labs/poindexter/issues>
- Documentation: <https://github.com/Glad-Labs/poindexter#readme>
- Security: see [SECURITY.md](https://github.com/Glad-Labs/poindexter/blob/main/SECURITY.md)
