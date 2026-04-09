# Security Policy

**Last Updated:** March 27, 2026

## Reporting Vulnerabilities

Email: **support@gladlabs.io** (Subject: `[SECURITY] Vulnerability Report`)

Do NOT create public GitHub issues for security vulnerabilities. Allow 48-72 hours for initial response, 30 days for a fix before public disclosure.

## Authentication

API endpoints are protected by Bearer token authentication (`API_TOKEN` environment variable). All requests must include:

```
Authorization: Bearer <API_TOKEN>
```

- `API_TOKEN` is a 64-character hex string, generated via `openssl rand -hex 32`
- Stored in Railway environment variables (never in code)
- `DEVELOPMENT_MODE=true` allows `Bearer dev-token` for local development only
- Production startup validates that `DEVELOPMENT_MODE` is not `true`

## Secrets Management

### Never commit to git:

- `API_TOKEN` — API authentication
- `DATABASE_URL` — contains database password
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` — LLM provider keys
- `PEXELS_API_KEY`, `SERPER_API_KEY` — external service keys
- `REVALIDATE_SECRET` — Next.js ISR cache invalidation
- `SENTRY_DSN` — error tracking (not truly secret, but keep private)

### Where secrets live:

- **Development:** `.env.local` (gitignored)
- **Staging/Production:** Railway environment variables
- **Vercel:** Vercel environment variables (for public site)

### Rotation schedule:

- `API_TOKEN` — rotate every 90 days or on suspected compromise
- LLM provider keys — rotate on compromise only (providers handle expiry)
- `DATABASE_URL` — Railway manages; rotate if exposed
- `REVALIDATE_SECRET` — rotate with `API_TOKEN`

### If secrets are exposed:

1. Immediately rotate the compromised credential at the provider
2. Update in app_settings table or via API
3. Verify with: `curl -H "Authorization: Bearer NEW_TOKEN" https://your-url/api/health`

## Security Architecture

- **Input validation:** FastAPI Pydantic schemas validate all request bodies
- **SQL injection:** All queries use parameterized placeholders (`$N`), no f-string SQL
- **Rate limiting:** 100 requests/minute per IP (configurable via `RATE_LIMIT_PER_MINUTE`)
- **CORS:** Explicit origin whitelist (no wildcards)
- **Error responses:** Internal error details stripped from HTTP responses (logged server-side only)
- **HTTPS:** Enforced by Railway/Vercel (TLS termination at edge)
- **Dependencies:** Dependabot monitors for CVEs on the `dev` branch

## Dependency Audits

```bash
npm audit                    # JavaScript dependencies
poetry show --outdated       # Python dependencies
```

## Contact

Security concerns: **support@gladlabs.io**

---

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).
