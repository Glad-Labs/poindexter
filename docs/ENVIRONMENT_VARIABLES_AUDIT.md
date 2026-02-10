# Environment Variables Complete Audit - February 9, 2026

## Executive Summary

This document contains a **complete audit** of all environment variables actually used by the codebase, not just documentation. Variables are categorized by criticality and environment (development, staging, production).

---

## CRITICAL VARIABLES (Required for Operation)

### Database Configuration

| Variable            | Used By                | Purpose                      | Current Value                                                 | Status |
| ------------------- | ---------------------- | ---------------------------- | ------------------------------------------------------------- | ------ |
| `DATABASE_URL`      | Backend, Migrations    | PostgreSQL connection string | `postgresql://postgres:postgres@localhost:5432/glad_labs_dev` | ✅     |
| `DATABASE_HOST`     | Backend, Content Agent | Database hostname            | `localhost`                                                   | ✅     |
| `DATABASE_PORT`     | Backend, Content Agent | Database port                | `5432`                                                        | ✅     |
| `DATABASE_NAME`     | Backend, Content Agent | Database name                | `glad_labs_dev`                                               | ✅     |
| `DATABASE_USER`     | Backend, Content Agent | Database user                | `postgres`                                                    | ✅     |
| `DATABASE_PASSWORD` | Backend, Content Agent | Database password            | `postgres`                                                    | ✅     |

### LLM API Keys (At least ONE required)

| Variable            | Provider  | Purpose              | Current Value | Status           |
| ------------------- | --------- | -------------------- | ------------- | ---------------- |
| `OPENAI_API_KEY`    | OpenAI    | GPT-4/GPT-3.5 models | _Not set_     | ⚠️ Optional      |
| `ANTHROPIC_API_KEY` | Anthropic | Claude models        | _Not set_     | ⚠️ Optional      |
| `GOOGLE_API_KEY`    | Google    | Gemini models        | _Not set_     | ⚠️ Optional      |
| `OLLAMA_BASE_URL`   | Ollama    | Local LLM server     | _Not set_     | ⚠️ Using default |

### JWT & Authentication

| Variable               | Used By                                              | Purpose                           | Current Value                                 | Status |
| ---------------------- | ---------------------------------------------------- | --------------------------------- | --------------------------------------------- | ------ |
| `JWT_SECRET`           | Backend Token Validator, Frontend MockTokenGenerator | Secret key for signing JWT tokens | `development-secret-key-change-in-production` | ✅     |
| `JWT_ALGORITHM`        | Backend                                              | JWT signing algorithm             | `HS256`                                       | ✅     |
| `JWT_EXPIRATION_HOURS` | Backend                                              | Token expiration time             | `24`                                          | ✅     |

### OAuth Configuration (GitHub)

| Variable                 | Used By             | Purpose                    | Current Value | Status                         |
| ------------------------ | ------------------- | -------------------------- | ------------- | ------------------------------ |
| `GH_OAUTH_CLIENT_ID`     | Backend Auth Routes | GitHub OAuth client ID     | _Not set_     | ⚠️ Optional (for GitHub OAuth) |
| `GH_OAUTH_CLIENT_SECRET` | Backend Auth Routes | GitHub OAuth client secret | _Not set_     | ⚠️ Optional (for GitHub OAuth) |

---

## IMPORTANT VARIABLES (Functionality-Dependent)

### Image & Media Services

| Variable                | Used By                      | Service    | Purpose                 | Current Value                                              | Status                   |
| ----------------------- | ---------------------------- | ---------- | ----------------------- | ---------------------------------------------------------- | ------------------------ |
| `PEXELS_API_KEY`        | Image Service, Content Agent | Pexels     | Stock photo sourcing    | `wdq7jNG49KWxBipK90hu32V5RLpXD0I5J81n61WeQzh31sdGJ9sua1qT` | ⚠️ May be expired        |
| `SERPER_API_KEY`        | Content Agent Tools          | Serper     | Web search capabilities | _Not set_                                                  | ⚠️ Optional (for search) |
| `CLOUDINARY_CLOUD_NAME` | Media Routes, CMS Service    | Cloudinary | Cloud storage           | _Not set_                                                  | ⚠️ Optional              |
| `CLOUDINARY_API_KEY`    | Media Routes, CMS Service    | Cloudinary | Cloud storage auth      | _Not set_                                                  | ⚠️ Optional              |
| `CLOUDINARY_API_SECRET` | Media Routes, CMS Service    | Cloudinary | Cloud storage auth      | _Not set_                                                  | ⚠️ Optional              |

### AWS S3 (Optional - if using for media)

| Variable                | Used By      | Service        | Purpose           | Current Value         | Status      |
| ----------------------- | ------------ | -------------- | ----------------- | --------------------- | ----------- |
| `AWS_ACCESS_KEY_ID`     | Media Routes | AWS S3         | S3 authentication | _Not set_             | ⚠️ Optional |
| `AWS_SECRET_ACCESS_KEY` | Media Routes | AWS S3         | S3 authentication | _Not set_             | ⚠️ Optional |
| `AWS_S3_BUCKET`         | Media Routes | AWS S3         | S3 bucket name    | _Not set_             | ⚠️ Optional |
| `AWS_S3_REGION`         | Media Routes | AWS S3         | AWS region        | `us-east-1` (default) | ⚠️ Optional |
| `AWS_CLOUDFRONT_DOMAIN` | Media Routes | AWS CloudFront | CDN domain        | _Not set_             | ⚠️ Optional |

### Email Publishing (SMTP)

| Variable        | Used By         | Service | Purpose                      | Current Value           | Status      |
| --------------- | --------------- | ------- | ---------------------------- | ----------------------- | ----------- |
| `SMTP_HOST`     | Email Publisher | SMTP    | Email server hostname        | _Not set_               | ⚠️ Optional |
| `SMTP_PORT`     | Email Publisher | SMTP    | Email server port            | `587` (default)         | ⚠️ Optional |
| `SMTP_USER`     | Email Publisher | SMTP    | SMTP authentication username | _Not set_               | ⚠️ Optional |
| `SMTP_PASSWORD` | Email Publisher | SMTP    | SMTP authentication password | _Not set_               | ⚠️ Optional |
| `EMAIL_FROM`    | Email Publisher | SMTP    | From email address           | (defaults to SMTP_USER) | ⚠️ Optional |
| `SMTP_USE_TLS`  | Email Publisher | SMTP    | Enable TLS encryption        | `true` (default)        | ⚠️ Optional |

### Social Media & OAuth

| Variable                 | Used By        | Service | Purpose                    | Current Value | Status      |
| ------------------------ | -------------- | ------- | -------------------------- | ------------- | ----------- |
| `FACEBOOK_CLIENT_ID`     | Facebook OAuth | OAuth   | Facebook app ID            | _Not set_     | ⚠️ Optional |
| `FACEBOOK_CLIENT_SECRET` | Facebook OAuth | OAuth   | Facebook app secret        | _Not set_     | ⚠️ Optional |
| `GOOGLE_CLIENT_ID`       | Google OAuth   | OAuth   | Google OAuth client ID     | _Not set_     | ⚠️ Optional |
| `GOOGLE_CLIENT_SECRET`   | Google OAuth   | OAuth   | Google OAuth client secret | _Not set_     | ⚠️ Optional |

### CMS & Content Integration (Strapi)

| Variable                       | Used By                    | Service    | Purpose                         | Current Value           | Status        |
| ------------------------------ | -------------------------- | ---------- | ------------------------------- | ----------------------- | ------------- |
| `STRAPI_URL`                   | Public Site, Search        | Strapi CMS | Strapi API URL                  | `http://localhost:1337` | ✅            |
| `STRAPI_API_TOKEN`             | Public Site Search, Routes | Strapi CMS | Strapi API authentication token | (set in .env.local)     | ✅            |
| `NEXT_PUBLIC_STRAPI_API_URL`   | Next.js Frontend           | Strapi CMS | Public Strapi API URL           | _Not explicitly set_    | ⚠️ Should add |
| `NEXT_PUBLIC_STRAPI_API_TOKEN` | Next.js Frontend           | Strapi CMS | Public Strapi token             | _Not explicitly set_    | ⚠️ Should add |

---

## ENVIRONMENT & CONFIGURATION VARIABLES

### Core Environment

| Variable      | Used By           | Purpose           | Current Value     | Status                   |
| ------------- | ----------------- | ----------------- | ----------------- | ------------------------ |
| `NODE_ENV`    | Frontend, Backend | Environment mode  | `development`     | ✅                       |
| `ENVIRONMENT` | Backend Config    | Environment flag  | _Not always set_  | ⚠️ Should set explicitly |
| `LOG_LEVEL`   | Backend Config    | Logging verbosity | `DEBUG`           | ✅                       |
| `LOG_FORMAT`  | Backend Config    | Log output format | (auto-determined) | ✅                       |
| `DEBUG`       | Frontend          | Debug mode        | `true`            | ✅                       |

### Ports & URLs

| Variable                   | Used By             | Purpose               | Current Value           | Status                       |
| -------------------------- | ------------------- | --------------------- | ----------------------- | ---------------------------- |
| `COFOUNDER_AGENT_PORT`     | Backend             | FastAPI port          | `8000`                  | ✅                           |
| `PUBLIC_SITE_PORT`         | Next.js             | Next.js port          | `3000`                  | ✅                           |
| `OVERSIGHT_HUB_PORT`       | React               | React dev server port | `3001`                  | ✅                           |
| `API_BASE_URL`             | Backend Routes      | API base URL          | `http://localhost:8000` | ✅                           |
| `NEXT_PUBLIC_API_BASE_URL` | Next.js             | Public API URL        | `http://localhost:8000` | ✅                           |
| `REACT_APP_API_URL`        | React Oversight Hub | React API URL         | _Not set_               | ⚠️ Should add                |
| `NEXT_PUBLIC_SITE_URL`     | Next.js             | Public site URL       | _Not set_               | ⚠️ Optional                  |
| `NEXT_PUBLIC_FASTAPI_URL`  | Next.js             | FastAPI endpoint      | _Not set_               | ⚠️ Should add for production |
| `NEXT_PUBLIC_BACKEND_URL`  | Next.js             | Backend URL           | _Not set_               | ⚠️ Duplicate of API_BASE_URL |

### Observability & Monitoring

| Variable                      | Used By        | Service | Purpose                 | Current Value                     | Status                      |
| ----------------------------- | -------------- | ------- | ----------------------- | --------------------------------- | --------------------------- |
| `SENTRY_DSN`                  | Backend Config | Sentry  | Error tracking endpoint | _Not set_                         | ⚠️ Optional but recommended |
| `SENTRY_ENABLED`              | Backend        | Sentry  | Enable error tracking   | `true` (default)                  | ✅                          |
| `SENTRY_ENVIRONMENT`          | Backend        | Sentry  | Environment tag         | _Not set_                         | ⚠️ Should set               |
| `SENTRY_TRACES_SAMPLE_RATE`   | Backend        | Sentry  | Tracing sample rate     | _Not set_                         | ⚠️ Optional                 |
| `ENABLE_TRACING`              | Backend        | OTEL    | Enable OpenTelemetry    | _Not set_                         | ⚠️ Optional                 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Backend        | OTEL    | OpenTelemetry endpoint  | `http://localhost:4318/v1/traces` | ⚠️ Optional                 |

### Feature Flags

| Variable                    | Used By              | Purpose                  | Current Value             | Status |
| --------------------------- | -------------------- | ------------------------ | ------------------------- | ------ |
| `ENABLE_ANALYTICS`          | Backend Config       | Google Analytics         | `false`                   | ✅     |
| `ENABLE_ERROR_REPORTING`    | Backend Config       | Error reporting          | `false`                   | ✅     |
| `ENABLE_DEBUG_LOGS`         | Backend Config       | Debug logging            | `true`                    | ✅     |
| `ENABLE_PAYMENT_PROCESSING` | Backend Config       | Payment processing       | `false`                   | ✅     |
| `DISABLE_DOTENV`            | Content Agent Config | Disable .env loading     | _Not set_ (only in tests) | ✅     |
| `DISABLE_SDXL_WARMUP`       | Startup Manager      | SDXL preload             | _Not set_                 | ✅     |
| `SKIP_MIGRATION`            | Main.py              | Skip database migrations | `true`                    | ✅     |

### Rate Limiting & Timeouts

| Variable                         | Used By           | Purpose                  | Current Value | Status |
| -------------------------------- | ----------------- | ------------------------ | ------------- | ------ |
| `API_TIMEOUT`                    | Frontend, Backend | API request timeout (ms) | `30000`       | ✅     |
| `API_RETRY_ATTEMPTS`             | Frontend, Backend | Retry count              | `3`           | ✅     |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | Backend Config    | Rate limit               | `1000`        | ✅     |
| `MAX_CONCURRENT_REQUESTS`        | Backend Config    | Concurrent requests      | `5`           | ✅     |

### Database Connection Pooling

| Variable                 | Used By          | Purpose              | Current Value  | Status |
| ------------------------ | ---------------- | -------------------- | -------------- | ------ |
| `DATABASE_POOL_MIN_SIZE` | Database Service | Min pool connections | `20` (default) | ✅     |
| `DATABASE_POOL_MAX_SIZE` | Database Service | Max pool connections | `50` (default) | ✅     |

### Caching & Redis

| Variable            | Used By          | Service   | Purpose               | Current Value            | Status                         |
| ------------------- | ---------------- | --------- | --------------------- | ------------------------ | ------------------------------ |
| `REDIS_URL`         | Backend          | Redis     | Redis connection URL  | `redis://localhost:6379` | ✅                             |
| `REDIS_HOST`        | Backend (config) | Redis     | Redis hostname        | _Not set_                | ⚠️ Optional if using REDIS_URL |
| `REDIS_PORT`        | Backend (config) | Redis     | Redis port            | `6379`                   | ✅                             |
| `REDIS_PASSWORD`    | Backend (config) | Redis     | Redis password        | _Not set_                | ✅ (optional)                  |
| `RIDE_DB`           | Backend (config) | Redis     | Redis database number | `0`                      | ✅                             |
| `CACHE_TTL_SECONDS` | Backend Config   | Cache TTL | `3600`                | ✅                       |

### Next.js Analytics & Ads

| Variable                 | Used By           | Purpose               | Current Value | Status      |
| ------------------------ | ----------------- | --------------------- | ------------- | ----------- |
| `NEXT_PUBLIC_GA_ID`      | Next.js Layout    | Google Analytics ID   | _Not set_     | ⚠️ Optional |
| `NEXT_PUBLIC_GA4_ID`     | Next.js Analytics | Google Analytics 4 ID | _Not set_     | ⚠️ Optional |
| `NEXT_PUBLIC_ADSENSE_ID` | Next.js AdSense   | Google AdSense ID     | _Not set_     | ⚠️ Optional |

### Comments & Community

| Variable                     | Used By        | Service | Purpose                  | Current Value | Status      |
| ---------------------------- | -------------- | ------- | ------------------------ | ------------- | ----------- |
| `NEXT_PUBLIC_GISCUS_REPO`    | Next.js Giscus | Giscus  | GitHub repo for comments | _Not set_     | ⚠️ Optional |
| `NEXT_PUBLIC_GISCUS_REPO_ID` | Next.js Giscus | Giscus  | GitHub repo ID           | _Not set_     | ⚠️ Optional |

### Deployment & Security

| Variable                | Used By              | Purpose              | Current Value          | Status           |
| ----------------------- | -------------------- | -------------------- | ---------------------- | ---------------- |
| `SECRET_KEY`            | Backend Config       | General secret key   | `your-secret-key-here` | ⚠️ Should change |
| `APP_VERSION`           | Backend Config       | App version          | `3.0.1`                | ✅               |
| `ALLOWED_ORIGINS`       | CORS Middleware      | CORS allowed origins | (computed from env)    | ✅               |
| `REVALIDATE_SECRET`     | Next.js Revalidation | ISR secret           | `dev-secret-key`       | ⚠️ Should change |
| `HUGGINGFACE_API_TOKEN` | HuggingFace Client   | HF authentication    | _Not set_              | ⚠️ Optional      |

### Content Agent Specific

| Variable               | Used By              | Purpose                | Current Value                | Status |
| ---------------------- | -------------------- | ---------------------- | ---------------------------- | ------ |
| `LLM_PROVIDER`         | Content Agent Config | Primary LLM provider   | `ollama`                     | ✅     |
| `MODEL_FOR_RESEARCH`   | Content Agent Config | Research model         | `ollama/mistral`             | ✅     |
| `MODEL_FOR_CREATIVE`   | Content Agent Config | Creative writing model | `ollama/mistral`             | ✅     |
| `MODEL_FOR_QA`         | Content Agent Config | QA model               | `ollama/mistral`             | ✅     |
| `MODEL_FOR_IMAGE`      | Content Agent Config | Image model            | `ollama/mistral`             | ✅     |
| `MODEL_FOR_PUBLISHING` | Content Agent Config | Publishing model       | `ollama/phi`                 | ✅     |
| `GEMINI_MODEL`         | Content Agent Config | Gemini model           | `gemini-2.0-flash`           | ✅     |
| `SUMMARIZER_MODEL`     | Content Agent Config | Summarizer model       | `gemini-2.0-flash`           | ✅     |
| `LOCAL_LLM_API_URL`    | Content Agent Config | Ollama API URL         | `http://localhost:11434`     | ✅     |
| `LOCAL_LLM_MODEL_NAME` | Content Agent Config | Ollama model name      | `llava:13b`                  | ✅     |
| `MAX_LOG_SIZE_MB`      | Content Agent Config | Max log file size      | `5`                          | ✅     |
| `MAX_LOG_BACKUP_COUNT` | Content Agent Config | Log file backups       | `3`                          | ✅     |
| `PUBSUB_TOPIC`         | Content Agent Config | Pub/Sub topic          | `agent-commands`             | ✅     |
| `PUBSUB_SUBSCRIPTION`  | Content Agent Config | Pub/Sub subscription   | `content-agent-subscription` | ✅     |

---

## VARIABLE STATUS SUMMARY

### 🟢 Complete & Configured (Ready)

- Database configuration
- JWT & authentication
- Core ports & URLs
- Basic LLM fallback (Ollama)
- Content agent models
- Redis/caching
- Feature flags

### 🟡 Missing or Incomplete (Optional but Recommended)

- **Optional API Keys**: OpenAI, Anthropic, Google API keys (at least one needed for production)
- **OAuth Providers**: GitHub, Facebook, Google OAuth (optional)
- **Analytics**: Google Analytics, Google Ads Sense, Sentry (optional)
- **Strapi CMS**: NEXT*PUBLIC_STRAPI*\* variables not explicitly configured
- **Media Storage**: Cloudinary, AWS S3 (optional)
- **Email**: SMTP configuration (optional)
- **Community**: Giscus comments (optional)

### 🔴 Critical Issues Found

- None currently blocking development

---

## RECOMMENDATIONS

### For Current Development (.env.local)

✅ **Currently adequate** - All development essentials are configured

### For Production Deployment

Add these to GitHub Secrets or Railway/Vercel:

```
DATABASE_URL_PRODUCTION
JWT_SECRET_PRODUCTION  (change from dev default)
ANTHROPIC_API_KEY  (or OPENAI_API_KEY for backup)
GOOGLE_API_KEY  (tertiary fallback)
SENTRY_DSN_PRODUCTION
PEXELS_API_KEY  (if not already set)
STRAPI_URL_PRODUCTION
STRAPI_API_TOKEN_PRODUCTION
NEXT_PUBLIC_SITE_URL
NEXT_PUBLIC_FASTAPI_URL
```

### For Enhanced Features

Consider adding these when time permits:

```
# Analytics
NEXT_PUBLIC_GA4_ID
NEXT_PUBLIC_ADSENSE_ID

# Comments
NEXT_PUBLIC_GISCUS_REPO
NEXT_PUBLIC_GISCUS_REPO_ID

# OAuth
GH_OAUTH_CLIENT_ID
GH_OAUTH_CLIENT_SECRET
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
```

---

## File Locations Reference

**.env Files in Project:**

- `/.env.local` - Local development (actual)
- `/.env.production` - Production template
- `/.env.staging` - Staging template
- `/src/cofounder_agent/.env` - Backend env (if exists)
- `/web/oversight-hub/.env.local` - React admin env (if exists)
- `/web/public-site/.env.local` - Next.js frontend env (if exists)

---

## Next Steps

1. **Verify product-specific keys** (Pexels, Strapi tokens)
2. **Add production API keys** to GitHub Secrets
3. **Configure optional services** as needed
4. **Document any custom env vars** added to codebase
