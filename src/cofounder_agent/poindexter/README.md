# poindexter

The open-source AI content pipeline from [Glad Labs](https://gladlabs.io), as one
installable package: the `poindexter` command-line client, the service layer it
delegates to, the plugin registry, and the standalone brain watchdog daemon. The
CLI, the worker, and the MCP servers all import the same `poindexter.*` modules —
one schema, one client, zero drift.

## Install

```bash
pip install poindexter
poindexter --help
```

Python 3.13. The package talks to a PostgreSQL database with the `pgvector`
extension; `poindexter setup` writes `~/.poindexter/bootstrap.toml` and runs the
schema migrations against it.

To run the whole stack (worker, brain daemon, Prefect, Grafana, ...) rather than
the client alone, clone the repository and start the consumer compose file:

```bash
git clone https://github.com/Glad-Labs/poindexter
cd poindexter
docker compose -f docker-compose.consumer.yml up -d
```

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
| `doctor`   | Aggregate every health probe into one report.                                              |
| `auth`     | Manage OAuth 2.1 client credentials for the API and MCP servers.                           |
| `vercel`   | Vercel deployment status via the REST API (no Vercel CLI needed).                          |
| `pro`      | Operate the Poindexter Pro delivery chain.                                                 |

Run `poindexter --help` for the full list (forty-odd groups) and any group with
`--help` for its subcommands, e.g. `poindexter memory --help`.

## Import root

Everything ships under one package:

```python
from poindexter.services.site_config import SiteConfig
from poindexter.plugins.registry import get_taps
from poindexter.brain.bootstrap import resolve_database_url
```

The pre-1046 flat spellings (`import services.x`) are gone — there is exactly
one module object per name, and nothing is installed at the top level except
`poindexter`.

## Configuration

`poindexter` reads its infrastructure secrets (mostly: a Postgres DSN) from
`~/.poindexter/bootstrap.toml`, which `poindexter setup` writes for you.
Everything else — API keys, feature flags, quality thresholds, model pins — is
stored in the `app_settings` table in Postgres and can be managed with
`poindexter settings`.

There are no required environment variables. `DATABASE_URL`, if present, is
accepted as a convenience override.

## How versions work

The PyPI version tracks the upstream
[poindexter](https://github.com/Glad-Labs/poindexter) repository via
[release-please](https://github.com/googleapis/release-please). Each release on
`main` cuts a tag `v<major>.<minor>.<patch>`; that tag fires the publish
workflow, which builds the wheel, installs it into a clean venv, runs
`poindexter --help`, and only then ships it.

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

To build the distribution yourself (the manifest is `src/cofounder_agent/pyproject.toml`):

```bash
cd src/cofounder_agent
uv build                     # or: python -m build
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
