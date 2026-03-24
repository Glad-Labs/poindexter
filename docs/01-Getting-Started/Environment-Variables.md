# Environment & Configuration

**Last Updated:** March 10, 2026
**Version:** 3.0.82
**Status:** ✅ Branch-Specific Reference

---

## 📍 Single Source of Truth

**For complete environment variable reference, see:** [reference/ENVIRONMENT_SETUP.md](../reference/ENVIRONMENT_SETUP.md)

This is the **complete, authoritative guide** covering:

- All required and optional variables
- Database, API keys, frontend configuration
- Local, staging, production setups
- Validation scripts and troubleshooting

**This document covers branch-specific overrides only.**

---

## 🌳 Branch Configuration Strategy

### Local Development (`feature/*`, `bugfix/*`, `docs/*` branches)

- **Config file:** `.env.local` (never committed)
- **Storage:** Local machine only
- **Services:** All 3 local (backend + Oversight Hub + Public Site)
- **Database:** Local PostgreSQL

**Example `.env.local` for local development:**

```env
# Database
DATABASE_URL=postgresql://postgres:password@localhost:5432/glad_labs

# LLM Providers
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key
GOOGLE_API_KEY=your_google_key

# Logging
LOG_LEVEL=debug

# API Endpoints
VITE_API_URL=http://localhost:8000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### Staging (`dev` branch)

- **Deployment:** GitHub Actions → Railway (auto on push)
- **Config source:** GitHub Secrets (STAGING\_\*)
- **Database:** Railway PostgreSQL staging instance
- **Services:** Public Site (Vercel), Backend (Railway)

**Variables stored in GitHub Secrets:**

- `DATABASE_STAGING_URL`
- `COFOUNDER_STAGING_OPENAI_API_KEY`

### Production (`main` branch)

- **Deployment:** GitHub Actions → Vercel/Railway (auto on push)
- **Config source:** GitHub Secrets (PROD\_\*)
- **Database:** Production PostgreSQL instance
- **Services:** Public Site (Vercel), Backend (Railway), Oversight Hub (Vercel)

**Variables stored in GitHub Secrets:**

- `DATABASE_PROD_URL`
- `COFOUNDER_PROD_OPENAI_API_KEY`
- `COFOUNDER_PROD_ANTHROPIC_API_KEY`
- `COFOUNDER_PROD_GOOGLE_API_KEY`

---

## 🔧 Feature Branch Testing Scenarios

When testing specific features, create temporary `.env.local` variations:

### Test New LLM Provider

```env
OPENAI_API_KEY=sk-proj-test-key
DEFAULT_MODEL_TEMPERATURE=1.0
```

### Test Database Issues

```env
DATABASE_URL=postgresql://fallback-db:5432/glad_labs
SQL_DEBUG=true
```

### Load Testing

```env
LOG_LEVEL=warning
RATE_LIMIT_PER_MINUTE=1000
TASK_TIMEOUT_SECONDS=60
```

---

## 📊 Variable Inheritance

| Variable     | Source               | Local          | Staging             | Production      |
| ------------ | -------------------- | -------------- | ------------------- | --------------- |
| Database URL | .env.local / Secrets | localhost      | Railway staging     | Production DB   |
| LLM Keys     | .env.local / Secrets | One key        | Multiple keys       | All keys        |
| Log Level    | .env.local / Secrets | debug          | info                | warning         |
| API Endpoint | .env.local / Secrets | localhost:8000 | staging.railway.app | api.gladlabs.ai |

---

## 🔄 CI/CD Flow

1. Push to branch → GitHub Actions triggered
2. Read `.github/workflows/*.yml` files
3. If branch = `main` or `dev`, load GitHub Secrets
4. Create `.env` file from Secrets
5. Docker build → deployment

**See:** [reference/GITHUB_SECRETS_SETUP.md](../reference/GITHUB_SECRETS_SETUP.md) for complete secrets configuration.

---

## Related Documentation

- **Complete Env Reference:** [reference/ENVIRONMENT_SETUP.md](../reference/ENVIRONMENT_SETUP.md) ← **START HERE**
- **GitHub Secrets Setup:** [reference/GITHUB_SECRETS_SETUP.md](../reference/GITHUB_SECRETS_SETUP.md)
- **Deployment Process:** [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](../03-DEPLOYMENT_AND_INFRASTRUCTURE.md)
- **Initial Setup:** [01-SETUP_AND_OVERVIEW.md](../01-SETUP_AND_OVERVIEW.md)
