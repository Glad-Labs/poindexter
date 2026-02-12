# 🔧 Complete Environment Setup Guide

**Last Updated:** February 11, 2026  
**Version:** 3.0  
**Status:** ✅ Single Authoritative Source for Environment Configuration

> **All three services (Backend, Oversight Hub, Public Site) read from a single `.env.local` file at the project root.**

---

## 📋 Quick Start

1. **Copy template:** `cp .env.example .env.local`
2. **Fill required values:** Database URL, at least one LLM API key
3. **Validate setup:** `npm run setup:validate` (runs validation script)
4. **Start services:** `npm run dev` (all three services use same config)

---

## 📦 Complete Environment Variables Reference

### **🗂 Database (REQUIRED)**

PostgreSQL connection for persistent data storage. All three services need this.

```env
# PostgreSQL 15+ connection string
# Format: postgresql://username:password@host:port/database
DATABASE_URL=postgresql://postgres:password@localhost:5432/glad_labs

# Enable query logging for debugging (log all asyncpg queries)
SQL_DEBUG=false
```

**Connection Pool Details:**

- Pool size: 10 (configurable)
- Timeout: 30 seconds
- Retry on failure: 3 attempts
- Required for: Users, tasks, content, logging, writing samples

**Test Connection:**

```bash
psql $DATABASE_URL -c "SELECT 1;"
```

---

### **🧠 LLM API Keys (AT LEAST ONE REQUIRED)**

The model router automatically selects based on availability and cost tier.

```env
# OpenAI (GPT-4, GPT-3.5-turbo) - $$$
OPENAI_API_KEY=sk-proj-...

# Anthropic (Claude 3 Opus, Sonnet) - $$
ANTHROPIC_API_KEY=sk-ant-...

# Google (Gemini) - $$
GOOGLE_API_KEY=AIza-...

# Local models (FREE - requires local Ollama)
OLLAMA_BASE_URL=http://localhost:11434
```

**Selection Hierarchy:**

1. Ollama (if running, ~0 cost)
2. Anthropic (if key set, low cost)
3. OpenAI (if key set, higher cost)
4. Google Gemini (if key set, medium cost)
5. Mock response (if none available)

**Recommended Setup for Development:**

- Running locally? Use Ollama only (free)
- Need cloud fallback? Add Anthropic key (cheapest cloud option)
- Need specific models? Add OpenAI for GPT-4

**Test LLM Connection:**

```bash
curl http://localhost:8000/api/health/models
```

---

### **🖼 Media & Search (OPTIONAL BUT RECOMMENDED)**

For image generation and web search functionality.

```env
# Cloudinary for image generation and optimization
# Sign up: https://cloudinary.com
CLOUDINARY_URL=cloudinary://api_key:api_secret@cloud_name

# Serper API for web search (Research Agent)
# Sign up: https://serper.dev
SERPER_API_KEY=...

# HuggingFace for ML models (optional)
HUGGINGFACE_API_KEY=hf_...
```

**Without these:**

- Image generation: Skipped (placeholder images used)
- Web search: Skipped (local search only)
- ML models: Uses cloud alternatives

---

### **⚙️ System Configuration (OPTIONAL)**

Fine-tune system behavior without code changes.

```env
# Logging
LOG_LEVEL=info           # debug, info, warning, error, critical
SENTRY_DSN=              # Error tracking (optional, for production)

# LLM Parameters
DEFAULT_MODEL_TEMPERATURE=0.7    # Creativity (0.0-1.0)
DEFAULT_MODEL_TOP_P=0.9          # Diversity (0.0-1.0)

# Task Execution
TASK_POLLING_INTERVAL=5          # Seconds between task checks
TASK_MAX_RETRIES=3               # Attempts before task fails
TASK_TIMEOUT_SECONDS=300         # Max runtime per task

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60         # Concurrent requests
RATE_LIMIT_PER_DAY=1000          # Daily limit

# Content
MAX_CONTENT_LENGTH=10485760      # Max upload size (10MB)
ALLOWED_FILE_TYPES=pdf,docx,txt  # Comma-separated
```

---

### **🌐 Frontend Configuration (REACT_APP_*)**

Frontend services read these to connect to backend.

```env
# Oversight Hub (React dashboard, port 3001)
REACT_APP_API_URL=http://localhost:8000         # Backend API endpoint
REACT_APP_USE_MOCK_AUTH=true                    # For testing without auth

# Public Site (Next.js, port 3000)
NEXT_PUBLIC_API_URL=http://localhost:8000       # Backend API endpoint
NEXT_PUBLIC_GA_ID=                              # Google Analytics (optional)
NEXT_PUBLIC_SITE_URL=http://localhost:3000      # For canonical URLs
```

**Environment-Specific Values:**

| Environment | REACT_APP_API_URL          | NEXT_PUBLIC_API_URL        |
|------------|----------------------------|----------------------------|
| **Local** | <http://localhost:8000>      | <http://localhost:8000>      |
| **Staging** | <https://api-staging.railway.app> | <https://staging.example.com> |
| **Production** | <https://api.gladlabs.ai>  | <https://gladlabs.ai>        |

---

### **🔑 Authentication & Secrets (FOR DEPLOYMENT)**

These are **never** in `.env.local` (only in GitHub Secrets for CI/CD).

```env
# OAuth Providers (set by CI/CD from GitHub Secrets)
GITHUB_OAUTH_ID=                  # GitHub app ID
GITHUB_OAUTH_SECRET=              # GitHub app secret
GOOGLE_OAUTH_ID=                  # Google OAuth ID
GOOGLE_OAUTH_SECRET=              # Google OAuth secret

# JWT Signing (generated during setup, stored securely)
JWT_SECRET_KEY=                   # Random 32+ char string

# Database (in CI/CD from GitHub Secrets)
DB_USER=                          # Deployed DB username
DB_PASSWORD=                      # Deployed DB password
```

---

### **📊 Monitoring & Observability (OPTIONAL)**

For production deployments.

```env
# Error tracking
SENTRY_DSN=https://key@sentry.io/123456

# Performance monitoring
NEW_RELIC_LICENSE_KEY=            # New Relic APM

# Logging aggregation
DATADOG_API_KEY=                  # Datadog monitoring
LOGGLY_TOKEN=                     # Log aggregation
```

---

## ✅ Validation Checklist

### Minimum Setup (Local Development)

- [ ] `DATABASE_URL` points to valid PostgreSQL instance
- [ ] At least one LLM API key (or Ollama running)
- [ ] `REACT_APP_API_URL` set to `http://localhost:8000`
- [ ] All three services can start: `npm run dev`

### Complete Setup (Staging/Production)

- [ ] Database backups configured
- [ ] All LLM keys provided (no single point of failure)
- [ ] Sentry or error tracking configured
- [ ] Rate limiting tuned for expected traffic
- [ ] Log aggregation enabled
- [ ] Monitoring and alerting set up
- [ ] SSL certificates valid
- [ ] GitHub Secrets configured for CI/CD

---

## 🔍 Environment Variable Validation Script

### Run Local Validation

```bash
# Check all required variables are set
npm run validate:env

# Check specific service config
npm run validate:backend         # Does backend config work?
npm run validate:frontend        # Do frontends config work?
npm run validate:db             # Can we connect to database?
```

### Manual Verification

```bash
# Test backend can read config
curl http://localhost:8000/health

# Test database connection
psql $DATABASE_URL -c "SELECT 1;"

# Test LLM connection
curl http://localhost:8000/api/health/models

# Test frontend can reach backend
curl http://localhost:3001       # Should load Oversight Hub
```

---

## 📝 Creating .env.local from Template

### Step 1: Copy Example File

```bash
cp .env.example .env.local
```

### Step 2: Edit Required Values

**Minimum (local dev):**

```bash
# In .env.local, set these:
DATABASE_URL=postgresql://postgres:password@localhost:5432/glad_labs
OPENAI_API_KEY=sk-proj-...        # OR set ANTHROPIC_API_KEY, OR start Ollama
REACT_APP_API_URL=http://localhost:8000
NEXT_PUBLIC_API_URL=http://localhost:8000
```

**Complete (with all options):**

```bash
# Copy all variables from complete reference above
# Fill in your API keys, database URL, etc.
```

### Step 3: Validate

```bash
npm run validate:env
```

### Step 4: Start Services

```bash
npm run dev
# All three services will start and use .env.local
```

---

## 🚀 Environment-Specific Setups

### Local Development

```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/glad_labs
OLLAMA_BASE_URL=http://localhost:11434      # Use local free models
LOG_LEVEL=debug
SQL_DEBUG=true
REACT_APP_USE_MOCK_AUTH=true
```

### Staging (Railway)

```env
DATABASE_URL=postgresql://user:pass@railway-postgres-host:5432/glad_labs_staging
OPENAI_API_KEY=sk-proj-...                  # For cloud fallback
ANTHROPIC_API_KEY=sk-ant-...                # Secondary option
LOG_LEVEL=info
SENTRY_DSN=https://key@sentry.io/staging
REACT_APP_API_URL=https://api-staging.railway.app
```

### Production

```env
DATABASE_URL=postgresql://user:pass@prod-postgres:5432/glad_labs
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
LOG_LEVEL=warning                           # Reduce noise
SENTRY_DSN=https://key@sentry.io/prod
RATE_LIMIT_PER_MINUTE=100
REACT_APP_API_URL=https://api.gladlabs.ai
NEXT_PUBLIC_API_URL=https://api.gladlabs.ai
```

---

## ⚠️ Common Configuration Issues

### Issue: Backend can't connect to database

**Solution:** Check `DATABASE_URL` format and PostgreSQL is running

```bash
psql $DATABASE_URL -c "SELECT 1;"
```

### Issue: Frontend gets 404 connecting to API

**Solution:** Verify `REACT_APP_API_URL` and backend is running on port 8000

```bash
curl http://localhost:8000/health
```

### Issue: Model router can't select any model

**Solution:** Ensure at least one LLM API key is set or Ollama is running

```bash
curl http://localhost:8000/api/health/models
```

### Issue: Tasks not executing

**Solution:** Check `DATABASE_URL`, `LOG_LEVEL`, and task_executor service

```bash
npm run test:python:smoke
```

### Issue: Image generation fails

**Solution:** Set `CLOUDINARY_URL` or disable image generation in content agent

---

## 🔄 Updating Environment Variables at Runtime

### Local Development

No restart needed - services reload on `.env.local` change (ensure hot reload enabled).

### Staging/Production

**Update via GitHub Secrets:**

1. Go to repo → Settings → Secrets and variables → Actions
2. Update secret value
3. Next push/deployment uses new value

**No runtime update possible** - requires new deployment.

---

## 📚 Related Documentation

- [07-BRANCH_SPECIFIC_VARIABLES.md](../07-BRANCH_SPECIFIC_VARIABLES.md) - Branch-specific overrides
- [reference/GITHUB_SECRETS_SETUP.md](GITHUB_SECRETS_SETUP.md) - Secrets for CI/CD deployments
- [01-SETUP_AND_OVERVIEW.md](../01-SETUP_AND_OVERVIEW.md) - Initial project setup
- [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](../03-DEPLOYMENT_AND_INFRASTRUCTURE.md) - Deployment procedures

---

**Last Updated:** February 11, 2026  
**Maintained By:** Documentation Team  
**Status:** ✅ Current and Active
