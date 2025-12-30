# GitHub Secrets Quick Reference

## Add These to GitHub Settings → Secrets and Variables → Actions

### 🔵 STAGING (for `dev` branch → Railway)

Copy-paste these names into GitHub (exact spelling, case-sensitive):

```
DATABASE_URL_STAGING
DATABASE_HOST_STAGING
DATABASE_NAME_STAGING
DATABASE_USER_STAGING
DATABASE_PASSWORD_STAGING
ANTHROPIC_API_KEY
JWT_SECRET_STAGING
SENTRY_DSN_STAGING
PEXELS_API_KEY
```

### 🔴 PRODUCTION (for `main` branch → Vercel + Railway)

```
DATABASE_URL_PRODUCTION
DATABASE_HOST_PRODUCTION
DATABASE_NAME_PRODUCTION
DATABASE_USER_PRODUCTION
DATABASE_PASSWORD_PRODUCTION
REDIS_HOST_PRODUCTION
REDIS_PASSWORD_PRODUCTION
ANTHROPIC_API_KEY
OPENAI_API_KEY
JWT_SECRET_PRODUCTION
SENTRY_DSN_PRODUCTION
PEXELS_API_KEY
```

---

## Where to Get Each Value

| Secret                      | Get From                                                      | Example                                                    |
| --------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------- |
| `DATABASE_*_STAGING`        | Railway → PostgreSQL service → Connect                        | `postgresql://postgres:xxx@host:5432/db`                   |
| `DATABASE_*_PRODUCTION`     | Railway → PostgreSQL service → Connect                        | `postgresql://postgres:xxx@host:5432/db`                   |
| `REDIS_HOST_PRODUCTION`     | Railway → Redis service → Connect                             | `redis-host.railway.internal`                              |
| `REDIS_PASSWORD_PRODUCTION` | Railway → Redis service → Connect                             | Your Redis password                                        |
| `ANTHROPIC_API_KEY`         | [Anthropic Console](https://console.anthropic.com) → API Keys | `sk-ant-xxx...`                                            |
| `OPENAI_API_KEY`            | [OpenAI Platform](https://platform.openai.com/api-keys)       | `sk-proj-xxx...`                                           |
| `JWT_SECRET_STAGING`        | Generate: `openssl rand -base64 32`                           | Any 32-byte random string                                  |
| `JWT_SECRET_PRODUCTION`     | Generate: `openssl rand -base64 32`                           | Any 32-byte random string                                  |
| `SENTRY_DSN_STAGING`        | [Sentry](https://sentry.io) → Project Settings                | `https://xxx@o123.ingest.us.sentry.io/456`                 |
| `SENTRY_DSN_PRODUCTION`     | [Sentry](https://sentry.io) → Project Settings                | `https://xxx@o123.ingest.us.sentry.io/789`                 |
| `PEXELS_API_KEY`            | Already have (from .env.local)                                | `wdq7jNG49KWxBipK90hu32V5RLpXD0I5J81n61WeQzh31sdGJ9sua1qT` |

---

## Add to GitHub in 3 Steps

1. Go to: **github.com/youruser/glad-labs-website/settings/secrets/actions**
2. Click: **New repository secret**
3. Paste each name and value from tables above

---

## Verify They Work

After adding all secrets, deploy to test:

```bash
# Push to dev branch - triggers staging deployment
git checkout dev
git push origin dev

# Check GitHub Actions tab to see deployment progress
# Should succeed with all secrets loaded correctly
```

If deployment fails, check:

- Secret names match exactly (case-sensitive)
- Values are correct (no extra spaces)
- GitHub Actions workflow references correct secret names

---

## About Shared Secrets

These secrets are used by BOTH staging and production:

- `ANTHROPIC_API_KEY` (Anthropic Claude for both)
- `PEXELS_API_KEY` (Image search for both)

No suffix needed for these.

Optional production-only fallbacks:

- `OPENAI_API_KEY` (if you want GPT-4 fallback)
- `GOOGLE_API_KEY` (if you want Gemini fallback)
