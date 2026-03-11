# 06 - Operations & Maintenance

**Last Updated:** March 10, 2026
**Version:** 3.0.39
**Status:** ✅ Operational

---

## Health Checks

### Backend Health Endpoint

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "cofounder-agent"}
```

This endpoint has no dependencies (no database query) and returns instantly. Use it for load-balancer health checks and uptime monitors.

### Task & System Metrics

```bash
curl http://localhost:8000/api/metrics
```

Returns aggregated system metrics:

| Field                | Description                 |
| :------------------- | :-------------------------- |
| `total_tasks`        | Total tasks ever created    |
| `completed_tasks`    | Successfully completed      |
| `failed_tasks`       | Failed tasks                |
| `pending_tasks`      | Queued or in-progress       |
| `success_rate`       | Percentage (0–100)          |
| `avg_execution_time` | Average duration in seconds |
| `total_cost`         | Estimated total cost in USD |

### WebSocket Connection Stats

```bash
curl http://localhost:8000/api/ws/stats
```

Returns per-namespace connection counts for real-time monitoring.

---

## Logging

### Log Level

Set `LOG_LEVEL` in `.env.local` to control verbosity:

```env
LOG_LEVEL=INFO        # Default — info, warnings, errors
LOG_LEVEL=DEBUG       # Verbose — includes model routing, phase details
LOG_LEVEL=WARNING     # Quiet — warnings and errors only
```

The logger factory is in `services/logger_config.py`. All modules retrieve a logger via `logging.getLogger(__name__)`.

### SQL Query Logging

Set `SQL_DEBUG=true` in `.env.local` to log every database query with timing:

```env
SQL_DEBUG=true
```

Useful for identifying slow queries or unexpected N+1 patterns during development.

---

## Database Maintenance

### Running Migrations

Migrations use Alembic with SQLAlchemy 2.0 async:

```bash
cd src/cofounder_agent
poetry run alembic upgrade head      # Apply all pending migrations
poetry run alembic current           # Show current revision
poetry run alembic history           # List migration history
poetry run alembic downgrade -1      # Roll back one migration
```

The custom `MigrationsService` in `services/migrations.py` handles schema-parity checks at startup.

### Connection Pool

Connection pool size is derived from `DATABASE_URL` in `.env.local`. The async pool is configured in `services/database_service.py`. Monitor active connections at the OS level with:

```bash
psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();"
```

---

## Backups

### Local Development

```bash
scripts/backup-local-postgres.sh
```

Creates a timestamped `.sql` dump of the local PostgreSQL database.

### Production (Railway)

```bash
scripts/backup-production-db.sh
```

Requires Railway CLI to be authenticated. Dumps the production database to a local file. Run before any schema migration or major deployment.

---

## Restarting Services

### All Services

```bash
npm run dev          # Start all three services concurrently
```

### Individual Services

```bash
npm run dev:cofounder   # Backend FastAPI (port 8000)
npm run dev:public      # Next.js public site (port 3000)
npm run dev:oversight   # React admin dashboard (port 3001)
```

### Kill Stuck Ports

```bash
scripts/kill-all-dev-ports.sh
```

Kills any processes holding ports 8000, 3000, or 3001.

---

## Common Maintenance Tasks

### Pre-deployment Verification

```bash
scripts/pre-deployment-verify.sh
```

Runs smoke tests, checks environment variables, and verifies service connectivity before a production deploy.

### Checking Railway Logs (Production)

```bash
railway logs --service cofounder-agent
railway logs --service public-site
```

### Environment Variable Audit

All variables are documented in `docs/01-Getting-Started/Environment-Variables.md` and `.env.example`. Required minimum:

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs_dev
# Plus at least ONE of:
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
OLLAMA_BASE_URL=...
```

Missing required variables cause a startup error with a descriptive message.
