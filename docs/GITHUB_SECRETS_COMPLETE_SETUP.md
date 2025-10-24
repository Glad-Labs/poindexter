# GitHub Secrets Configuration Checklist

**Last Updated:** October 24, 2025  
**Total Secrets Needed:** 82 (38 staging + 38 production + 6 shared)

---

## 🔑 How to Add GitHub Secrets

1. Go to: **GitHub Repository → Settings → Secrets and variables → Actions**
2. Click **New repository secret** for shared secrets
3. Click **New environment secret** (after creating environments) for environment-specific secrets

---

## 📋 Step 1: Create GitHub Environments

**Important:** Create these environments FIRST before adding environment-specific secrets:

1. Go to: **Settings → Environments**
2. Click **New environment**
3. Create **"staging"** environment
4. Create **"production"** environment
5. (Optional) Add deployment protection rules for production

---

## 🔐 SHARED Repository Secrets (6 total)

These go in: **Settings → Secrets and variables → Actions → Repository secrets**

```bash
RAILWAY_TOKEN
  Source: https://railway.app → Account settings → API Token
  Description: Authorization token for Railway CLI deployments

RAILWAY_STAGING_PROJECT_ID
  Source: Railway Dashboard → Project ID for staging
  Description: Railroad staging project ID

RAILWAY_PROD_PROJECT_ID
  Source: Railway Dashboard → Project ID for production
  Description: Railway production project ID

VERCEL_TOKEN
  Source: https://vercel.com/account/tokens
  Description: Authorization token for Vercel CLI deployments

VERCEL_ORG_ID
  Source: Vercel Dashboard → Settings → Organization ID
  Description: Your Vercel organization ID

VERCEL_PROJECT_ID
  Source: Vercel Project → Settings → Project ID
  Description: Your Vercel project ID (if single project)
```

---

## 🚀 STAGING Environment Secrets (38 total)

These go in: **Settings → Environments → staging → Environment secrets**

### Strapi Staging (8 secrets)

```bash
STAGING_STRAPI_TOKEN
  Source: Strapi Admin → Settings → API Tokens → Create New
  Description: Full-access API token for Strapi staging

STAGING_STRAPI_ADMIN_JWT_SECRET
  Generate: node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
  Description: JWT secret for Strapi admin authentication

STAGING_STRAPI_API_TOKEN_SALT
  Generate: node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
  Description: Salt for Strapi API token generation

STAGING_STRAPI_APP_KEYS
  Generate: Create 4 keys separated by commas (each 32 bytes base64)
  Description: Application encryption keys (comma-separated)

STAGING_STRAPI_JWT_SECRET
  Generate: node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
  Description: JWT secret for Strapi token signing

STAGING_ADMIN_PASSWORD
  Set: A strong password for Strapi admin user
  Description: Strapi admin panel password (change after first login!)

STAGING_ADMIN_EMAIL
  Set: admin@staging.glad-labs.com
  Description: Email for Strapi admin user

STAGING_GA_ID
  Source: Google Analytics → Admin → Tracking ID
  Description: Google Analytics ID for staging site
```

### Database Staging (4 secrets)

```bash
STAGING_DB_HOST
  Source: Railway Database → Database service URL hostname
  Description: PostgreSQL database hostname for staging

STAGING_DB_USER
  Source: Railway Database → Connection string
  Description: PostgreSQL username for staging

STAGING_DB_PASSWORD
  Source: Railway Database → Connection string
  Description: PostgreSQL password for staging

STAGING_DB_NAME
  Default: glad_labs_staging
  Description: PostgreSQL database name for staging
```

### Redis Staging (2 secrets)

```bash
STAGING_REDIS_HOST
  Source: Railway Redis → Redis service URL hostname
  Description: Redis hostname for staging

STAGING_REDIS_PASSWORD
  Source: Railway Redis → Connection string password
  Description: Redis password for staging
```

### AI Model API Keys Staging (Choose ONE or more - 3 secrets)

```bash
STAGING_OPENAI_API_KEY
  Source: https://platform.openai.com/api-keys
  Description: OpenAI API key (starts with "sk-proj-")

STAGING_ANTHROPIC_API_KEY
  Source: https://console.anthropic.com/account/keys
  Description: Anthropic Claude API key (starts with "sk-ant-")

STAGING_GOOGLE_API_KEY
  Source: https://makersuite.google.com/app/apikey
  Description: Google Gemini API key (starts with "AIza")
```

### External Services Staging (5 secrets)

```bash
STAGING_GCP_PROJECT_ID
  Source: Google Cloud Console → Project ID
  Description: GCP project ID for staging

STAGING_GCP_SERVICE_ACCOUNT_EMAIL
  Source: Google Cloud Console → Service Account
  Description: Service account email for GCP

STAGING_GCP_SERVICE_ACCOUNT_KEY
  Source: Google Cloud Console → Service Account → Keys (JSON format)
  Description: GCP service account private key (full JSON as string)

STAGING_SENTRY_DSN
  Source: https://sentry.io → Project → Settings → Client Keys (DSN)
  Description: Sentry error tracking DSN (optional)

STAGING_SERPER_API_KEY
  Source: https://serper.dev → API Keys
  Description: Serper search API key for content generation

STAGING_NEWSLETTER_API_KEY
  Source: Newsletter provider (Mailchimp, Brevo, etc.)
  Description: Newsletter service API key

STAGING_TEST_PASSWORD
  Set: A test user password for automation
  Description: Test user password for staging automation

STAGING_SMTP_HOST
  Example: smtp.gmail.com
  Description: SMTP host for email sending

STAGING_SMTP_USER
  Example: your-email@gmail.com
  Description: SMTP username/email

STAGING_SMTP_PASSWORD
  Source: Email provider → App-specific password
  Description: SMTP password (NOT regular email password!)
```

---

## 🏢 PRODUCTION Environment Secrets (38 total)

These go in: **Settings → Environments → production → Environment secrets**

### Strapi Production (8 secrets - same structure as staging)

```bash
PROD_STRAPI_TOKEN
PROD_STRAPI_ADMIN_JWT_SECRET
PROD_STRAPI_API_TOKEN_SALT
PROD_STRAPI_APP_KEYS
PROD_STRAPI_JWT_SECRET
PROD_ADMIN_PASSWORD
PROD_ADMIN_EMAIL
PROD_GA_ID
```

### Database Production (4 secrets)

```bash
PROD_DB_HOST
PROD_DB_USER
PROD_DB_PASSWORD
PROD_DB_NAME
```

### Redis Production (2 secrets)

```bash
PROD_REDIS_HOST
PROD_REDIS_PASSWORD
```

### AI Model API Keys Production (Choose ONE or more - 3 secrets)

```bash
PROD_OPENAI_API_KEY
PROD_ANTHROPIC_API_KEY
PROD_GOOGLE_API_KEY
```

### External Services Production (5 secrets)

```bash
PROD_GCP_PROJECT_ID
PROD_GCP_SERVICE_ACCOUNT_EMAIL
PROD_GCP_SERVICE_ACCOUNT_KEY
PROD_SENTRY_DSN
PROD_SERPER_API_KEY
PROD_NEWSLETTER_API_KEY
```

### Production-Specific (6 secrets)

```bash
PROD_STRIPE_PUBLIC_KEY
  Source: https://dashboard.stripe.com → API Keys
  Description: Stripe public key for production

PROD_STRIPE_SECRET_KEY
  Source: https://dashboard.stripe.com → API Keys
  Description: Stripe secret key for production (keep secret!)

PROD_STRIPE_WEBHOOK_SECRET
  Source: https://dashboard.stripe.com → Webhooks
  Description: Stripe webhook secret for payment notifications
```

---

## ✅ Setup Verification Checklist

After adding all secrets:

- [ ] All 6 shared repository secrets added
- [ ] "staging" environment created
- [ ] All 38 staging environment secrets added
- [ ] "production" environment created (with deployment protection if desired)
- [ ] All 38 production environment secrets added
- [ ] Tested by pushing to `dev` branch → watch GitHub Actions
- [ ] Verified staging deployment completed successfully
- [ ] Tested by pushing to `main` branch → watch GitHub Actions
- [ ] Verified production deployment completed successfully

---

## 🔒 Security Best Practices

**DO:**

- Use strong, unique passwords for all secrets
- Rotate secrets every 90 days
- Use service accounts for API keys (not personal accounts)
- Enable 2FA on all external accounts (GitHub, Vercel, Railway, etc.)
- Review secrets quarterly

**DON'T:**

- Commit secrets to git (use .gitignore)
- Share secrets in Slack, email, or unencrypted channels
- Use personal API keys in production
- Leave test credentials in production
- Reuse passwords across services

---

## 🚀 Quick Reference: Environment Variables Flow

```text
GitHub Secrets (secured)
    ↓
GitHub Actions Workflow (reads secrets)
    ├→ Passes to Railway CLI as env vars
    ├→ Passes to Vercel CLI as env vars
    └→ Reads .env.staging/.env.production for non-secrets
        ↓
Railway (Strapi + Co-founder Agent)
Vercel (Public Site + Oversight Hub)
```

---

## 📞 Need Help?

- [Railway Docs](https://docs.railway.app)
- [Vercel Docs](https://vercel.com/docs)
- [GitHub Actions](https://docs.github.com/en/actions)
- [Secret Management](https://docs.github.com/en/actions/security-guides/encrypted-secrets)

---

**Document Status:** ✅ Complete
**Last Review:** October 24, 2025
