# Environment Setup (Authoritative)

**Last Updated:** March 4, 2026  
**Status:** Canonical source for local environment variables and aliases

---

## Source of Truth

- Root template: `.env.example`
- Local runtime file: `.env.local` (never committed)
- CI/CD secret reference: `docs/reference/GITHUB_SECRETS_SETUP.md`

All services are configured from the project root environment file during local development.

---

## Quick Start

1. Copy `.env.example` to `.env.local`
2. Set `DATABASE_URL`
3. Configure at least one model provider (`OLLAMA_BASE_URL` or cloud key)
4. Start all services with `npm run dev`

---

## Canonical Variable Names

Use these names for new configuration and documentation.

### Backend / Shared

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
JWT_SECRET_KEY=change-me
LOG_LEVEL=DEBUG

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=mistral:latest

OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
HUGGINGFACE_API_TOKEN=
```

### Public Site (Next.js)

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000
NEXT_PUBLIC_COFOUNDER_AGENT_URL=http://localhost:8000
NEXT_PUBLIC_SITE_URL=http://localhost:3000
```

### Oversight Hub (Vite)

```env
VITE_API_URL=http://localhost:8000
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
VITE_AGENT_URL=http://localhost:8000
VITE_GH_OAUTH_CLIENT_ID=
```

---

## Frontend Variable Prefix Conventions

Each frontend service uses a different build-tool-mandated prefix. This is a framework constraint,
not a project choice. All three prefixes serve the same role: they tell the build tool which
variables are safe to embed into client-side bundles.

| Service             | Build Tool | Required Prefix | Access Pattern              |
| ------------------- | ---------- | --------------- | --------------------------- |
| `web/public-site`   | Next.js 15 | `NEXT_PUBLIC_`  | `process.env.NEXT_PUBLIC_*` |
| `web/oversight-hub` | Vite       | `VITE_`         | `import.meta.env.VITE_*`    |

**Why the difference?** Next.js statically replaces `process.env.NEXT_PUBLIC_*` at build time.
Vite statically replaces `import.meta.env.VITE_*` at build time. Variables without the required
prefix are stripped from the client bundle entirely — they are server-only (Next.js) or not
exposed at all (Vite).

**REACT*APP*?** This prefix was used by Create React App (CRA). The oversight-hub previously
used CRA but was migrated to Vite. `REACT_APP_` keys are NOT supported by Vite unless manually
mapped. Do not introduce new `REACT_APP_` variables; use `VITE_` instead.

**Each app reads its own `.env.local`:**

- `web/public-site/.env.local` — read by Next.js (not the root `.env.local`)
- `web/oversight-hub/.env.local` — read by Vite (not the root `.env.local`)
- Root `.env.local` — read only by the FastAPI backend via Python's `dotenv` loader

---

## Compatibility Aliases (Supported, Deprecated for New Use)

These exist for backward compatibility and should not be introduced in new code/docs.

- `OLLAMA_HOST` → `OLLAMA_BASE_URL`
- `JWT_SECRET` → `JWT_SECRET_KEY`
- `NEXT_PUBLIC_FASTAPI_URL` remains supported, but prefer `NEXT_PUBLIC_API_BASE_URL`
- `VITE_API_BASE_URL` remains supported alongside `VITE_API_URL`

---

## Secrets and Security Rules

- Never commit `.env.local`, production secrets, API tokens, or private keys.
- Use placeholder values in committed templates.
- Generate JWT secret with: `openssl rand -base64 32`
- Rotate secrets immediately if exposed.
- Store deployment secrets in GitHub Environment Secrets, not in repo files.

For full CI/CD secret mapping, use `docs/reference/GITHUB_SECRETS_SETUP.md`.

---

## Local Validation Checklist

- `DATABASE_URL` points to reachable PostgreSQL
- At least one model provider is configured
- `NEXT_PUBLIC_API_BASE_URL` points to backend (`http://localhost:8000` in dev)
- `VITE_API_URL` points to backend (`http://localhost:8000` in dev)
- `npm run dev` starts backend, public site, and oversight hub

---

## Notes on Legacy Templates

Legacy templates in service subfolders are retained temporarily for historical context only.  
Use the root `.env.example` as the primary configuration template.
