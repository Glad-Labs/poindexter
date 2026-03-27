# Deployment & Operations

**Last Updated:** March 2026

---

## Deployment Workflow

### Branch Strategy

- `dev` -- runs tests only, no deployment
- `staging` -- auto-deploys to Railway staging + Vercel staging
- `main` -- auto-deploys to Railway production + Vercel production + GitHub Release tag

### Deploying to Staging

```bash
# Create PR from dev to staging
gh pr create --base staging --head dev --title "Deploy to staging"
```

1. All tests passing on `dev`
2. PR reviewed and approved
3. Merge triggers staging deployment to Railway

### Release Please (on staging)

After merging to `staging`, Release Please automatically opens a PR with:

- `CHANGELOG.md` updated from conventional commits
- Version bumped across all package files
- Review the changelog, then merge the release PR into `staging`

### Deploying to Production

```bash
gh pr create --base main --head staging --title "Release v0.x.x"
```

The production workflow (`deploy-production-with-environments.yml`) handles:

- Backend deployed to Railway
- Public site deployed to Vercel
- Smoke tests (automated)
- GitHub Release + tag created on `main` (automated)

### Post-Deployment Verification

```bash
curl https://api.glad-labs.com/health
# Visit https://glad-labs.com
# Check logs for errors
# Verify GitHub Release was created
```

### Emergency Rollback

**Via GitHub Actions:**

1. Actions > Emergency Version Rollback
2. Enter version to rollback to
3. Click Run workflow

**Manual:**

```bash
git revert -m 1 <merge-commit-sha>
git push origin main
```

---

## CI/CD Workflow Files

| File                                      | Trigger            | Purpose                               |
| ----------------------------------------- | ------------------ | ------------------------------------- |
| `release-please.yml`                      | Push to `staging`  | Changelog + version bump PR           |
| `deploy-staging-with-environments.yml`    | Push to `staging`  | Deploy to Railway staging             |
| `deploy-production-with-environments.yml` | Push to `main`     | Deploy to production + GitHub Release |
| `test-on-dev.yml`                         | Push to `dev`      | Full test suite                       |
| `test-on-feat.yml`                        | Feature branch PRs | Lint + tests                          |
| `version-rollback.yml`                    | Manual             | Emergency version rollback            |

---

## Version File Synchronization

Release Please keeps all files in sync automatically:

| File                                 | Type |
| ------------------------------------ | ---- |
| `package.json` (root)                | JSON |
| `web/public-site/package.json`       | JSON |
| `src/cofounder_agent/package.json`   | JSON |
| `pyproject.toml` (root)              | TOML |
| `src/cofounder_agent/pyproject.toml` | TOML |

Configuration: `release-please-config.json` + `.release-please-manifest.json`

---

## Health Checks

### Backend Health Endpoint

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "cofounder-agent"}
```

No dependencies (no database query). Use for load-balancer health checks and uptime monitors.

### Task & System Metrics

```bash
curl http://localhost:8000/api/metrics
```

| Field                | Description                 |
| :------------------- | :-------------------------- |
| `total_tasks`        | Total tasks ever created    |
| `completed_tasks`    | Successfully completed      |
| `failed_tasks`       | Failed tasks                |
| `pending_tasks`      | Queued or in-progress       |
| `success_rate`       | Percentage (0-100)          |
| `avg_execution_time` | Average duration in seconds |
| `total_cost`         | Estimated total cost in USD |

### WebSocket Connection Stats

```bash
curl http://localhost:8000/api/ws/stats
```

---

## Logging

### Log Level

Set `LOG_LEVEL` in `.env.local`:

```env
LOG_LEVEL=INFO        # Default
LOG_LEVEL=DEBUG       # Verbose (model routing, phase details)
LOG_LEVEL=WARNING     # Quiet (warnings and errors only)
```

Logger factory: `services/logger_config.py`. All modules use `logging.getLogger(__name__)`.

### SQL Query Logging

```env
ENABLE_QUERY_MONITORING=true
```

Logs slow queries. Useful for N+1 detection during development.

---

## Database Maintenance

### Migrations

Custom Python modules with raw SQL in `services/migrations/`. Applied at startup by `MigrationsService` in `services/migrations.py`. No Alembic or ORM.

### Connection Pool

Configured in `services/database_service.py`. Monitor active connections:

```bash
psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();"
```

### Backups

```bash
scripts/backup-local-postgres.sh       # Local dev
scripts/backup-production-db.sh        # Production (requires Railway CLI auth)
```

Run production backup before any schema migration or major deployment.

---

## Restarting Services

```bash
npm run dev              # All three services
npm run dev:cofounder    # Backend only (port 8000)
npm run dev:public       # Next.js only (port 3000)
npm run dev:oversight    # React admin only (port 3001)
```

Kill stuck ports: `scripts/kill-all-dev-ports.sh`

---

## Pre-deployment Checklist

```bash
npm run test:python:smoke   # Smoke tests
npm run lint                # Lint all workspaces
npm run build               # Verify builds succeed
```

### Railway Logs

```bash
railway logs --service cofounder-agent
railway logs --service public-site
```
