# Environment Variables Reference

Complete reference for all environment variables across the Glad Labs monorepo.
Last updated: 2026-03-25.

---

## Quick Reference: What to Set Where

### Railway (Backend) - Required

| Variable                 | Description                    | Example                                              |
| ------------------------ | ------------------------------ | ---------------------------------------------------- |
| `DATABASE_URL`           | PostgreSQL connection string   | `postgresql://user:pass@host:5432/glad_labs`         |
| `ENVIRONMENT`            | Must be `production`           | `production`                                         |
| `JWT_SECRET_KEY`         | JWT signing secret (32+ chars) | `openssl rand -base64 32`                            |
| `GH_OAUTH_CLIENT_ID`     | GitHub OAuth App Client ID     | `Ov23li...`                                          |
| `GH_OAUTH_CLIENT_SECRET` | GitHub OAuth App Secret        | `abc123...`                                          |
| `ALLOWED_ORIGINS`        | CORS origins (comma-separated) | `https://glad-labs-website-oversight-hub.vercel.app` |
| At least ONE LLM key     | See AI Model section below     |                                                      |

### Railway (Backend) - Optional

| Variable               | Default          | Description                       |
| ---------------------- | ---------------- | --------------------------------- |
| `LOG_LEVEL`            | `INFO`           | DEBUG, INFO, WARNING, ERROR       |
| `PEXELS_API_KEY`       | (none)           | Stock image search for blog posts |
| `SERPER_API_KEY`       | (none)           | Web search for research phase     |
| `SENTRY_DSN`           | (none)           | Error tracking                    |
| `REVALIDATE_SECRET`    | `dev-secret-key` | ISR cache revalidation token      |
| `DEVELOPMENT_MODE`     | `false`          | **Must be false in production**   |
| `DISABLE_AUTH_FOR_DEV` | `false`          | **Must be false in production**   |

### Railway - AI Model Keys (need at least ONE)

| Variable            | Provider            | Fallback Order |
| ------------------- | ------------------- | -------------- |
| `ANTHROPIC_API_KEY` | Claude (sk-ant-...) | 1st            |
| `OPENAI_API_KEY`    | GPT (sk-...)        | 2nd            |
| `GOOGLE_API_KEY`    | Gemini (AIza...)    | 3rd            |

Fallback chain: Ollama -> Anthropic -> OpenAI -> Google -> Echo/Mock

### Vercel - Public Site

| Variable                   | Required | Description                 |
| -------------------------- | -------- | --------------------------- |
| `NEXT_PUBLIC_API_BASE_URL` | YES      | Railway backend URL         |
| `NEXT_PUBLIC_FASTAPI_URL`  | YES      | Alias (same value as above) |
| `NEXT_PUBLIC_SITE_URL`     | YES      | `https://glad-labs.com`     |
| `NEXT_PUBLIC_GA_ID`        | No       | Google Analytics ID         |
| `NEXT_PUBLIC_SENTRY_DSN`   | No       | Sentry error tracking       |
| `SENTRY_DSN`               | No       | Server-side Sentry          |

### Vercel - Oversight Hub

Set in Vercel Project Settings OR committed in `web/oversight-hub/.env.production`:

| Variable                       | Required | Description                               |
| ------------------------------ | -------- | ----------------------------------------- |
| `REACT_APP_API_URL`            | YES      | Railway backend URL                       |
| `REACT_APP_API_BASE_URL`       | YES      | Same as above                             |
| `REACT_APP_WS_BASE_URL`        | YES      | Same as above (WebSocket)                 |
| `REACT_APP_AGENT_URL`          | YES      | Same as above (legacy alias)              |
| `REACT_APP_GH_OAUTH_CLIENT_ID` | YES      | Must match Railway's `GH_OAUTH_CLIENT_ID` |
| `REACT_APP_USE_MOCK_AUTH`      | YES      | Must be `false` in production             |
| `REACT_APP_LOG_LEVEL`          | No       | `warn` recommended for production         |

### GitHub Secrets (production environment)

These are used by the CI/CD deploy workflow:

| Secret                              | Purpose                             |
| ----------------------------------- | ----------------------------------- |
| `RAILWAY_TOKEN`                     | Railway deploy authentication       |
| `RAILWAY_PROD_PROJECT_ID`           | Railway project identifier          |
| `VERCEL_TOKEN`                      | Vercel deploy authentication        |
| `VERCEL_ORG_ID`                     | Vercel organization                 |
| `PUBLIC_SITE_PROD_PROJECT_ID`       | Vercel public-site project          |
| `OVERSIGHT_PROD_PROJECT_ID`         | Vercel oversight-hub project        |
| `PUBLIC_SITE_PROD_FASTAPI_URL`      | Backend URL for public site build   |
| `PUBLIC_SITE_PROD_SITE_URL`         | Public site canonical URL           |
| `PUBLIC_SITE_PROD_GA_ID`            | Google Analytics ID                 |
| `PUBLIC_SITE_PROD_SENTRY_DSN`       | Public site Sentry DSN              |
| `OVERSIGHT_PROD_API_URL`            | Backend URL for oversight hub build |
| `OVERSIGHT_PROD_GH_OAUTH_CLIENT_ID` | OAuth Client ID for oversight hub   |
| `OVERSIGHT_PROD_URL`                | Oversight hub URL (for smoke tests) |
| `COFOUNDER_PROD_URL`                | Backend URL (for smoke tests)       |

---

## GitHub OAuth App Setup

1. Go to https://github.com/settings/developers -> OAuth Apps
2. Create new OAuth App:
   - **Homepage URL**: `https://glad-labs-website-oversight-hub.vercel.app`
   - **Authorization callback URL**: `https://glad-labs-website-oversight-hub.vercel.app/auth/callback`
3. Copy **Client ID** -> set as `GH_OAUTH_CLIENT_ID` on Railway AND `REACT_APP_GH_OAUTH_CLIENT_ID` on Vercel
4. Generate **Client Secret** -> set as `GH_OAUTH_CLIENT_SECRET` on Railway only

---

## Common Issues

### "Invalid or expired token" on all API calls

- Check `JWT_SECRET_KEY` is set on Railway (not a placeholder)
- Check `DEVELOPMENT_MODE` is `false` in production

### CORS errors in browser console

- Check `ALLOWED_ORIGINS` on Railway includes your oversight-hub Vercel domain
- Must be exact match including protocol: `https://glad-labs-website-oversight-hub.vercel.app`

### 404 on /auth/callback

- Oversight Hub needs SPA rewrite rule in `web/oversight-hub/vercel.json`

### All LLM providers fail

- Check API keys on Railway are actual keys, not template strings like `{{secrets.KEY}}`
- API keys must be set directly in Railway Variables, not via GitHub secrets passthrough

### "Cannot find module 'tailwindcss'" on Vercel build

- `tailwindcss`, `postcss`, `autoprefixer`, `@types/react` must be in `dependencies` (not `devDependencies`) for the public-site

---

## Local Development Setup

```bash
# 1. Copy env templates
cp .env.example .env.local
cp web/public-site/.env.example web/public-site/.env.local
cp web/oversight-hub/.env.example web/oversight-hub/.env.local

# 2. Set minimum required vars in .env.local:
DATABASE_URL=postgresql://postgres:password@localhost:5432/glad_labs_dev
DEVELOPMENT_MODE=true

# 3. Set at least ONE LLM key (or run local Ollama)
OLLAMA_BASE_URL=http://localhost:11434
# OR: ANTHROPIC_API_KEY=sk-ant-...

# 4. Start all services
npm run dev
```
