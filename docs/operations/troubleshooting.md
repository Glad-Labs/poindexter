# Troubleshooting

Common issues and fixes for Glad Labs services.

---

## Railway Deployment

### Build failure: missing LICENSE.md

**Error:** `Build Failed: lstat /LICENSE.md: no such file or directory`

**Fix:** Ensure `.dockerignore` does not exclude `LICENSE.md` or `README.md`. Railpack needs these files in the Docker build context.

### Railway deploy commands

```bash
railway login
railway link
railway up
railway logs
```

Or use GitHub integration (recommended): Railway auto-deploys on push to `staging`/`main`.

### Required Railway environment variables

```env
DATABASE_URL=postgresql://...
ENVIRONMENT=production
# At least one LLM key (ANTHROPIC_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY)
```

---

## Build & Compilation

### ESLint Unicode BOM error

**Error:** `Unexpected Unicode BOM (Byte Order Mark)` on a JS file.

**Fix:** Remove the 3-byte UTF-8 BOM (0xEF 0xBB 0xBF) from the file start. Often caused by Windows editors or copy-paste.

### "Cannot find module 'tailwindcss'" on Vercel

`tailwindcss`, `postcss`, `autoprefixer`, `@types/react` must be in `dependencies` (not `devDependencies`) for the public-site.

---

## Authentication

### "Invalid or expired token" on all API calls

- Check `JWT_SECRET_KEY` is set on Railway (not a placeholder)
- Check `DEVELOPMENT_MODE` is `false` in production

### GitHub OAuth 404 errors

- Backend endpoint is `/api/auth/github/callback` (with slash, not dash)
- Frontend must send CSRF state token in request body
- Callback URL in GitHub OAuth App settings must match production domain

### Mock auth in production

- `REACT_APP_USE_MOCK_AUTH` must NOT be set in production
- Mock auth is blocked unless `NODE_ENV === 'development'`

---

## CORS

### CORS errors in browser console

- Check `ALLOWED_ORIGINS` on Railway includes your public site domain
- Must be exact match including protocol: `https://glad-labs.com`
- No trailing slash

---

## LLM Providers

### All LLM providers fail

- Check API keys on Railway are actual keys, not template strings like `{{secrets.KEY}}`
- API keys must be set directly in Railway Variables, not via GitHub secrets passthrough
- Fallback chain: Ollama -> Anthropic -> OpenAI -> Google -> Echo/Mock

---

## Redis

### Redis connection errors on startup

Set `REDIS_ENABLED=false` on Railway if you don't have Redis provisioned. The app works without Redis (no query caching).

---

## Database

### Connection pool exhaustion

Monitor active connections:

```bash
psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();"
```

Pool configured in `services/database_service.py`. Defaults: min 20, max 50.

---

## Ports

### Kill stuck dev ports

```bash
scripts/kill-all-dev-ports.sh
```

Kills processes on ports 8000, 3000, and 3001.

---

## Debug Logging

### Backend

```env
LOG_LEVEL=DEBUG
ENABLE_QUERY_MONITORING=true
```

### Check Railway logs

```bash
railway logs --service cofounder-agent | grep -i auth
railway logs --service cofounder-agent | grep -i error
```
