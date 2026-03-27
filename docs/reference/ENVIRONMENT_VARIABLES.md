# Environment Variables Reference

Complete reference for all environment variables across the Glad Labs monorepo.
Last updated: 2026-03-25.

---

## Railway (Backend) - Required

| Variable                 | Description                                                          | Example                                      |
| ------------------------ | -------------------------------------------------------------------- | -------------------------------------------- |
| `DATABASE_URL`           | PostgreSQL connection string                                         | `postgresql://user:pass@host:5432/glad_labs` |
| `ENVIRONMENT`            | Must be `production` in prod                                         | `production`                                 |
| `JWT_SECRET_KEY`         | JWT signing secret (32+ chars). Also read as `JWT_SECRET` (alias).   | `openssl rand -base64 32`                    |
| `SECRET_KEY`             | App-level secret (64 chars). Startup crashes if placeholder in prod. | `openssl rand -base64 48`                    |
| `GH_OAUTH_CLIENT_ID`     | GitHub OAuth App Client ID                                           | `Ov23li...`                                  |
| `GH_OAUTH_CLIENT_SECRET` | GitHub OAuth App Secret                                              | `abc123...`                                  |
| `ALLOWED_ORIGINS`        | CORS origins (comma-separated, no trailing slashes)                  | `https://glad-labs.com`                      |
| At least ONE LLM key     | See AI Model section below                                           |                                              |

## Railway - AI Model Keys (need at least ONE)

| Variable                | Provider                   | Fallback Order             |
| ----------------------- | -------------------------- | -------------------------- |
| `ANTHROPIC_API_KEY`     | Claude (`sk-ant-...`)      | 1st                        |
| `OPENAI_API_KEY`        | GPT (`sk-...`)             | 2nd                        |
| `GOOGLE_API_KEY`        | Gemini (`AIza...`)         | 3rd                        |
| `GEMINI_API_KEY`        | Alias for `GOOGLE_API_KEY` | (same as above)            |
| `HUGGINGFACE_API_TOKEN` | HuggingFace (`hf_...`)     | Not in main fallback chain |

Fallback chain: Ollama -> Anthropic -> OpenAI -> Google -> Echo/Mock

## Railway - Optional (Caching & Infrastructure)

| Variable                      | Default                    | Description                                      |
| ----------------------------- | -------------------------- | ------------------------------------------------ |
| `REDIS_URL`                   | `redis://localhost:6379/0` | Redis connection string for query caching        |
| `REDIS_ENABLED`               | `true`                     | Set `false` to skip Redis (app works without it) |
| `OLLAMA_BASE_URL`             | (none)                     | Local Ollama server. Alias: `OLLAMA_HOST`        |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15`                       | JWT token lifetime in minutes                    |

## Railway - Optional (Logging & Observability)

| Variable                      | Default                           | Description                              |
| ----------------------------- | --------------------------------- | ---------------------------------------- |
| `LOG_LEVEL`                   | `INFO`                            | DEBUG, INFO, WARNING, ERROR              |
| `LOG_FORMAT`                  | `json` (prod) / `text` (dev)      | Log output format                        |
| `LOG_ALL_QUERIES`             | `false`                           | Log all DB queries regardless of speed   |
| `ENABLE_QUERY_MONITORING`     | `true`                            | Slow query logging                       |
| `SLOW_QUERY_THRESHOLD_MS`     | `100`                             | Log queries slower than this (ms)        |
| `SENTRY_DSN`                  | (none)                            | Sentry error tracking endpoint           |
| `SENTRY_ENABLED`              | `true`                            | Enable/disable Sentry (needs SENTRY_DSN) |
| `ENABLE_TRACING`              | `false`                           | OpenTelemetry distributed tracing        |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318/v1/traces` | OTLP collector endpoint                  |

## Railway - Optional (External APIs)

| Variable                | Default          | Description                                           |
| ----------------------- | ---------------- | ----------------------------------------------------- |
| `PEXELS_API_KEY`        | (none)           | Stock image search for blog posts                     |
| `SERPER_API_KEY`        | (none)           | Web search for content research                       |
| `REVALIDATE_SECRET`     | `dev-secret-key` | ISR cache revalidation token (must match public site) |
| `CLOUDINARY_CLOUD_NAME` | (none)           | Cloudinary image hosting                              |
| `CLOUDINARY_API_KEY`    | (none)           | Cloudinary credentials                                |
| `CLOUDINARY_API_SECRET` | (none)           | Cloudinary credentials                                |
| `AWS_ACCESS_KEY_ID`     | (none)           | AWS S3 upload credentials                             |
| `AWS_SECRET_ACCESS_KEY` | (none)           | AWS S3 upload credentials                             |
| `AWS_S3_BUCKET`         | (none)           | S3 bucket name                                        |
| `AWS_S3_REGION`         | `us-east-1`      | AWS region                                            |
| `AWS_CLOUDFRONT_DOMAIN` | (none)           | CloudFront CDN domain                                 |
| `MERCURY_API_KEY`       | (none)           | Mercury Markets API (financial agent)                 |

## Railway - Optional (LLM Model Selection)

These override which models are used for each pipeline stage. Defaults come from `agents/content_agent/config.py`.

| Variable                    | Default              | Description                    |
| --------------------------- | -------------------- | ------------------------------ |
| `LLM_PROVIDER`              | `ollama`             | Primary LLM provider selection |
| `MODEL_FOR_RESEARCH`        | `ollama/gpt-oss:20b` | Research phase model           |
| `MODEL_FOR_CREATIVE`        | `ollama/gpt-oss:20b` | Creative draft model           |
| `MODEL_FOR_QA`              | `ollama/gpt-oss:20b` | QA critique model              |
| `MODEL_FOR_IMAGE`           | `ollama/gpt-oss:20b` | Image selection model          |
| `MODEL_FOR_PUBLISHING`      | `ollama/gpt-oss:20b` | Publishing prep model          |
| `GEMINI_MODEL`              | `gemini-2.0-flash`   | Gemini model name              |
| `GEMINI_FALLBACK_MODEL`     | `gemini-2.5-flash`   | Gemini fallback model          |
| `GEMINI_TIMEOUT_SECONDS`    | `120`                | Gemini API timeout             |
| `SUMMARIZER_MODEL`          | `gemini-2.0-flash`   | Model for summarization        |
| `DEFAULT_OLLAMA_CHAT_MODEL` | `llama2`             | Default model for /api/chat    |
| `OLLAMA_WARMUP_MODEL`       | `mistral:latest`     | Model to pull during warmup    |

## Railway - Feature Flags

| Variable                  | Default | Description                                                     |
| ------------------------- | ------- | --------------------------------------------------------------- |
| `DEVELOPMENT_MODE`        | `false` | **Must be false in production.** Enables dev-token auth bypass. |
| `DISABLE_AUTH_FOR_DEV`    | `false` | **Must be false in production.** Legacy auth bypass.            |
| `ENABLE_TRAINING_CAPTURE` | `false` | Capture training data for fine-tuning                           |
| `ENABLE_MCP_SERVER`       | `true`  | Model Context Protocol support                                  |
| `ENABLE_MEMORY_SYSTEM`    | `true`  | AI memory/learning system                                       |
| `DISABLE_SDXL_WARMUP`     | `false` | Skip SDXL model warmup on startup                               |

## Railway - Database (Alternative to DATABASE_URL)

Only needed if NOT using `DATABASE_URL`:

| Variable                 | Default     | Description             |
| ------------------------ | ----------- | ----------------------- |
| `DATABASE_HOST`          | `localhost` | PostgreSQL host         |
| `DATABASE_PORT`          | `5432`      | PostgreSQL port         |
| `DATABASE_NAME`          | `glad_labs` | Database name           |
| `DATABASE_USER`          | `postgres`  | Database user           |
| `DATABASE_PASSWORD`      | (empty)     | Database password       |
| `DATABASE_POOL_MIN_SIZE` | `20`        | Connection pool minimum |
| `DATABASE_POOL_MAX_SIZE` | `50`        | Connection pool maximum |

---

## Vercel - Public Site

These are set in the **Vercel dashboard** for the public-site project, or passed via the CI deploy workflow.

| Variable                         | Required    | Description                                                           | Set By CI                    |
| -------------------------------- | ----------- | --------------------------------------------------------------------- | ---------------------------- |
| `NEXT_PUBLIC_FASTAPI_URL`        | YES         | Railway backend URL (primary — this is what the deploy workflow sets) | Yes                          |
| `NEXT_PUBLIC_API_BASE_URL`       | Recommended | Same value. Code checks this first, falls back to FASTAPI_URL.        | No (set in Vercel dashboard) |
| `NEXT_PUBLIC_SITE_URL`           | YES         | `https://glad-labs.com`                                               | Yes                          |
| `NEXT_PUBLIC_GA_ID`              | No          | Google Analytics 4 ID (`G-XXXXXXXXXX`)                                | Yes                          |
| `NEXT_PUBLIC_GA4_ID`             | No          | Alias for GA_ID (set by CI to same value)                             | Yes                          |
| `NEXT_PUBLIC_SENTRY_DSN`         | No          | Sentry client-side error tracking                                     | Yes                          |
| `SENTRY_DSN`                     | No          | Sentry server-side error tracking                                     | Yes                          |
| `NEXT_PUBLIC_ADSENSE_ID`         | No          | Google AdSense Publisher ID (`ca-pub-...`)                            | No                           |
| `NEXT_PUBLIC_ADSENSE_SLOT_ID`    | No          | AdSense ad unit slot ID                                               | No                           |
| `NEXT_PUBLIC_GISCUS_REPO`        | No          | GitHub repo for Giscus comments                                       | No                           |
| `NEXT_PUBLIC_GISCUS_REPO_ID`     | No          | Giscus Repo ID                                                        | No                           |
| `NEXT_PUBLIC_GISCUS_CATEGORY_ID` | No          | Giscus Category ID                                                    | No                           |

**Note:** `NEXT_PUBLIC_API_BASE_URL` is NOT set by the CI deploy workflow. The workflow only sets `NEXT_PUBLIC_FASTAPI_URL`. The code falls back gracefully, but for clarity you should set both to the same value in the Vercel dashboard.

---

---

## GitHub Secrets - Production Environment

Used by `.github/workflows/deploy-production-with-environments.yml`:

| Secret                         | Purpose                                   | Validated at startup |
| ------------------------------ | ----------------------------------------- | -------------------- |
| `RAILWAY_TOKEN`                | Railway deploy authentication             | YES                  |
| `RAILWAY_PROD_PROJECT_ID`      | Railway project identifier                | YES                  |
| `VERCEL_TOKEN`                 | Vercel deploy authentication              | YES                  |
| `VERCEL_ORG_ID`                | Vercel organization                       | YES                  |
| `PUBLIC_SITE_PROD_PROJECT_ID`  | Vercel public-site project                | YES                  |
| `PUBLIC_SITE_PROD_FASTAPI_URL` | Backend URL -> `NEXT_PUBLIC_FASTAPI_URL`  | YES                  |
| `PUBLIC_SITE_PROD_SITE_URL`    | Public site URL -> `NEXT_PUBLIC_SITE_URL` | YES                  |
| `COFOUNDER_PROD_URL`           | Backend URL (smoke tests + notifications) | YES                  |
| `PUBLIC_SITE_PROD_GA_ID`       | Google Analytics ID (optional)            | No                   |
| `PUBLIC_SITE_PROD_SENTRY_DSN`  | Public site Sentry DSN (optional)         | No                   |

## GitHub Secrets - Staging Environment

Used by `.github/workflows/deploy-staging-with-environments.yml`:

| Secret                            | Purpose                            |
| --------------------------------- | ---------------------------------- |
| `RAILWAY_STAGING_PROJECT_ID`      | Railway staging project            |
| `PUBLIC_SITE_STAGING_PROJECT_ID`  | Vercel staging public-site project |
| `PUBLIC_SITE_STAGING_FASTAPI_URL` | Staging backend URL                |
| `PUBLIC_SITE_STAGING_SITE_URL`    | Staging public site URL            |
| `PUBLIC_SITE_STAGING_GA_ID`       | Staging Google Analytics ID        |
| `PUBLIC_SITE_STAGING_SENTRY_DSN`  | Staging Sentry DSN                 |
| `COFOUNDER_STAGING_URL`           | Staging backend URL (smoke tests)  |

**Note:** Staging and production share `RAILWAY_TOKEN`, `VERCEL_TOKEN`, and `VERCEL_ORG_ID`.

---

## GitHub OAuth App Setup

1. Go to https://github.com/settings/developers -> OAuth Apps
2. Create new OAuth App:
   - **Homepage URL**: `https://glad-labs.com`
   - **Authorization callback URL**: `https://glad-labs.com/auth/callback`
3. Copy **Client ID** -> set as `GH_OAUTH_CLIENT_ID` on Railway
4. Generate **Client Secret** -> set as `GH_OAUTH_CLIENT_SECRET` on Railway only (never in frontend)

---

## Common Issues

### "Invalid or expired token" on all API calls

- Check `JWT_SECRET_KEY` is set on Railway (not a placeholder)
- Check `DEVELOPMENT_MODE` is `false` in production

### CORS errors in browser console

- Check `ALLOWED_ORIGINS` on Railway includes your public site domain
- Must be exact match including protocol: `https://glad-labs.com`
- No trailing slash

### All LLM providers fail

- Check API keys on Railway are actual keys, not template strings like `{{secrets.KEY}}`
- API keys must be set directly in Railway Variables, not via GitHub secrets passthrough

### "Cannot find module 'tailwindcss'" on Vercel build

- `tailwindcss`, `postcss`, `autoprefixer`, `@types/react` must be in `dependencies` (not `devDependencies`) for the public-site

### Redis connection errors on startup

- Set `REDIS_ENABLED=false` on Railway if you don't have Redis provisioned
- The app works without Redis, just no query caching

---

## Local Development Setup

```bash
# 1. Copy env templates
cp .env.example .env.local
cp web/public-site/.env.example web/public-site/.env.local
# 2. Set minimum required vars in .env.local:
DATABASE_URL=postgresql://postgres:password@localhost:5432/glad_labs_dev
DEVELOPMENT_MODE=true

# 3. Set at least ONE LLM key (or run local Ollama)
OLLAMA_BASE_URL=http://localhost:11434
# OR: ANTHROPIC_API_KEY=sk-ant-...

# 4. Start all services
npm run dev
```

---

## Known Inconsistencies

1. **`JWT_SECRET_KEY` and `JWT_SECRET`** are both read (backward compat). Only `JWT_SECRET_KEY` is needed.
2. **`OLLAMA_BASE_URL` and `OLLAMA_HOST`** are both read. Only `OLLAMA_BASE_URL` is needed.
3. **`NEXT_PUBLIC_API_BASE_URL` vs `NEXT_PUBLIC_FASTAPI_URL`** — code checks both. CI only sets `FASTAPI_URL`.
