# Security Policy

**Last Updated:** February 7, 2026

## 🔒 Reporting Security Vulnerabilities

If you discover a security vulnerability in Glad Labs, please **report it responsibly** to:

📧 **<support@gladlabs.io>** (Subject: `[SECURITY] Vulnerability Report`)

**Do NOT:**

- ❌ Create a public GitHub issue
- ❌ Post on social media
- ❌ Share vulnerability details publicly

**Do:**

- ✅ Email us directly with details
- ✅ Give us time to respond (48-72 hours)
- ✅ Include reproduction steps
- ✅ Wait for fix before disclosure (30-90 days)

### Responsible Disclosure Timeline

1. **Day 1:** Report vulnerability with details
2. **Day 1-2:** We acknowledge receipt
3. **Day 3-30:** We investigate and develop fix
4. **Day 31:** Patch released (if critical)
5. **Day 30-90:** Public disclosure (after fix is released)

---

## 🔐 Credentials & Secrets Security

### What NEVER Goes in Git

These should ALWAYS be in `.env.local` (which is `.gitignore`'d):

```bash
# ❌ NEVER COMMIT:
DATABASE_URL=postgresql://...              # Contains passwords
OPENAI_API_KEY=sk-...                      # API credentials
ANTHROPIC_API_KEY=sk-ant-...               # API credentials
GOOGLE_API_KEY=AIza...                     # API credentials
GH_OAUTH_CLIENT_SECRET=ghp_...             # OAuth secrets
JWT_SECRET=random_64_char_string           # Signing secrets
PEXELS_API_KEY=...                         # API credentials
```

### Checking If Secrets Leaked

```bash
# Search git history for common patterns
git log -p | grep -i "api_key\|secret\|password"
```

```bash
# Check if .env.local is properly ignored
git check-ignore .env.local
# Should return: .env.local
```

```bash
# Verify staged changes don't contain secrets
git diff --cached | grep -i "password\|token\|key"
# Should return nothing
```

### If Secrets Are Accidentally Committed

1. **Immediately revoke the credential** (API key, OAuth token, etc.)
1. **Rotate the secret** (generate a new one)
1. **Force-push to remove from history** (⚠️ Only for private repos):

```bash
# Remove sensitive file from recent commits
git filter-branch --tree-filter 'rm -f .env.local' HEAD
```

```bash
# Or use BFG Repo Cleaner (simpler)
bfg --delete-files .env.local
```

1. **Notify maintainers immediately**

---

## 🛡️ Environment Variable Best Practices

### Development (`.env.local`)

```bash
# ✅ SAFE: Use fake/test credentials
OPENAI_API_KEY=sk-test-1234567890          # Test key
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev

# ✅ SAFE: Use localhost for development
NEXT_PUBLIC_SITE_URL=http://localhost:3000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### Production (`.env.production`)

```bash
# ⚠️ CRITICAL: Use real production credentials
# STORE IN: Vercel Secrets, Railway Secrets, or AWS Secrets Manager
# NEVER COMMIT to git

OPENAI_API_KEY=sk-proj-[PRODUCTION_KEY]
DATABASE_URL=postgresql://[PROD_USER]:[PROD_PASS]@[PROD_HOST]:5432/glad_labs

# Use real domain URLs
NEXT_PUBLIC_SITE_URL=https://glad-labs.com
NEXT_PUBLIC_API_BASE_URL=https://api.glad-labs.com
```

### Managing Production Secrets

**Vercel:**

```bash
# Set secret via Vercel CLI
vercel env add OPENAI_API_KEY
# This is NOT stored in git, only in Vercel dashboard
```

**Railway:**

```bash
# Set via Railway dashboard or CLI
railway variables set OPENAI_API_KEY=sk-...
# This is encrypted and not in git
```

**AWS Secrets Manager:**

```bash
# Fetch at runtime, never store in code
aws secretsmanager get-secret-value --secret-id openai-key
```

---

### OpenAI / Anthropic / Google

```bash
# ✅ Do this:
# 1. Generate key in provider dashboard
# 2. Add to .env.local (DEV) or Secrets Manager (PROD)
# 3. Use via process.env.API_KEY in code
# 4. Rotate key every 90 days

# ❌ Never do this:
const API_KEY = "sk-..."; // Hardcoded in code
export const API_KEY = process.env.API_KEY || "sk-..."; // Fallback
```

### GitHub OAuth Credentials

```bash
# Credentials needed:
GH_OAUTH_CLIENT_ID=your-client-id           # ✅ CAN be public
GH_OAUTH_CLIENT_SECRET=ghp_xxxxxxxxxxxx     # ❌ MUST stay secret

# Safe in .env.local / Secrets Manager
# Never commit GH_OAUTH_CLIENT_SECRET to git
```

### Database Credentials

```bash
# DATABASE_URL contains password
DATABASE_URL=postgresql://user:PASSWORD@host:5432/db

# ❌ NEVER log this
console.log(process.env.DATABASE_URL);  // BAD!

# ✅ Log safely
console.log(`Connected to database: ${process.env.DATABASE_HOST}`);
```

---

## 👤 Contributor Security Guidelines

### Before Submitting a PR

1. **Check for secrets:**

   ```bash
   git diff origin/main...HEAD | grep -i "api_key\|secret\|password"
   ```

1. **Verify .env.local is ignored:**

   ```bash
   git check-ignore .env.local
   # Should show: .env.local
   ```

1. **Review code for sensitive info:**
   - No hardcoded API keys
   - No database credentials
   - No OAuth secrets
   - No personal tokens

1. **Test with `.env.example`:**

   ```bash
   cp .env.example .env.test
   # Fill with PUBLIC values only
   npm run test
   ```

### Safe Code Patterns

```tsx
// ✅ SAFE: Read from environment
const apiKey = process.env.OPENAI_API_KEY;

// ✅ SAFE: Use constants for public info
const API_ENDPOINT = "https://api.openai.com/v1/chat/completions";

// ❌ UNSAFE: Hardcoded secrets
const apiKey = "sk-proj-abc123def456";

// ❌ UNSAFE: Logging secrets
console.log(`Using API key: ${process.env.OPENAI_API_KEY}`);

// ✅ SAFE: Log reference info only
console.log("API key configured:", !!process.env.OPENAI_API_KEY);
```

---

## 🚨 Common Security Mistakes

### 1. Logging API Responses

```tsx
// ❌ BAD: API response might contain secrets
const response = await fetch(...);
console.log(response.data);  // Could expose tokens!

// ✅ GOOD: Log selectively
console.log({
  status: response.status,
  hasData: !!response.data,
});
```

### 2. Error Messages Revealing Secrets

```tsx
// ❌ BAD: Error includes API key
try {
  await openai.createCompletion({key: apiKey});
} catch (error) {
  console.error(error.message);  // Might contain key!
}

// ✅ GOOD: Redact sensitive fields
catch (error) {
  console.error("API call failed:", error.code);
}
```

### 3. Caching to Local Storage

```tsx
// ❌ BAD: Storing API keys in localStorage
localStorage.setItem('apiKey', process.env.OPENAI_API_KEY);

// ✅ GOOD: Never store secrets on client
// Make authenticated calls to backend instead
fetch('/api/chat', {
  headers: {
    Authorization: `Bearer ${jwtToken}`,
  },
});
```

### 4. Exposing Internal URLs

```tsx
// ❌ BAD: Revealing internal services
const INTERNAL_API = "http://internal-api.company.com:8000";

// ✅ GOOD: Use proxied endpoints
const API_ENDPOINT = process.env.NEXT_PUBLIC_API_BASE_URL;
// Set this to public-facing URL only
```

---

## 🔍 Security Checklist

### Before Every Commit

- [ ] No API keys in code
- [ ] No passwords in code
- [ ] No OAuth secrets in code
- [ ] `.env.local` is in `.gitignore`
- [ ] No `credentials.json` or auth files committed
- [ ] No console.log of sensitive data

### Before Pushing to Main

- [ ] All secrets use environment variables
- [ ] Production `.env` values are in Vercel/Railway/AWS
- [ ] `.env.example` shows only public placeholders
- [ ] Code doesn't hardcode any URLs with credentials
- [ ] HTTPS used for all external API calls

### Before Production Deployment

- [ ] Database connection string uses TLS
- [ ] API keys are rotated
- [ ] JWT secrets are 64+ random characters
- [ ] CORS origins whitelist is set
- [ ] Rate limiting is enabled
- [ ] Error reporting doesn't expose secrets (Sentry config)

---

## 🔐 Using This Repository Safely

### For Developers

1. **Clone the repo**

   ```bash
   git clone https://github.com/Glad-Labs/glad-labs-codebase
   cd glad-labs-codebase
   ```

2. **Copy environment template**

   ```bash
   cp .env.example .env.local
   ```

3. **Add YOUR secrets** (get from Vercel/Railway/password manager)

   ```bash
   # Edit .env.local with:
   # - Your OpenAI API key
   # - Your database password
   # - Your OAuth credentials
   ```

4. **Verify nothing leaked**

   ```bash
   git diff --cached | grep -i "api_key\|secret"
   # Should return nothing
   ```

5. **Never commit `.env.local`**

   ```bash
   git check-ignore .env.local  # Should confirm it's ignored
   ```

### For DevOps / Infrastructure

1. **Set secrets in your platform:**
   - **Vercel:** Dashboard → Settings → Environment Variables
   - **Railway:** Project → Variables
   - **AWS:** Secrets Manager or Parameter Store

2. **Reference in `.env.example`:**
   - Show the variable NAME only
   - Use placeholder values (e.g., `sk-XXXXXXXX`)
   - Document where to get the real value

3. **Never store in Git:**
   - Use `.gitignore` for `.env.local`
   - Use platform-specific secret management
   - Rotate secrets every 90 days

---

## 📋 Third-Party Dependency Security

This project uses the following dependencies with security considerations:

| Dependency         | License             | Audit Status     | Notes                                       |
| ------------------ | ------------------- | ---------------- | ------------------------------------------- |
| FastAPI            | MIT                 | ✅ Maintained    | Security updates: watch release notes      |
| React              | MIT                 | ✅ Maintained    | Keep updated via npm audit                 |
| Next.js            | MIT                 | ✅ Maintained    | Security patches in minor versions         |
| PostgreSQL         | PostgreSQL License  | ✅ Maintained    | Update regularly for security fixes        |

**Running Security Audits:**

```bash
# Check for vulnerable dependencies
npm audit

# Python dependencies
pip-audit

# Auto-fix common vulnerabilities
npm audit fix
```

---

## 🔗 Additional Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/) - Common security vulnerabilities
- [Git Secrets](https://github.com/awslabs/git-secrets) - Prevent secret leaks
- [npm audit](https://docs.npmjs.com/cli/v9/commands/npm-audit) - Dependency scanning
- [Vercel Security](https://vercel.com/security) - Deployment security
- [Railway Security](https://railway.app/security) - Infrastructure security

---

## 📧 Contact

For security concerns, questions, or responsible disclosure:

**Email:** <support@gladlabs.io>  
**Subject:** `[SECURITY]` + description

---

## 📝 License & Attribution

This security policy is part of the Glad Labs project, licensed under GNU AGPL 3.0.

For commercial licensing and enterprise security arrangements, contact: <sales@gladlabs.io>

**Last Review:** February 7, 2026  
**Next Review:** April 7, 2026
