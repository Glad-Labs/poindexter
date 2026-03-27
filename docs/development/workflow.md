# 04 - Development Workflow

**Last Updated:** March 10, 2026
**Version:** 0.1.0
**Status:** ✅ Production Ready

---

## 🌳 Branch Strategy (Tier 1-4)

Glad Labs uses a four-tier branch hierarchy for sustainable code management:

### Tier 1: Local Development

- **Branch Pattern:** Any local feature branch
- **Environment:** Developer's machine
- **Trigger:** Manual (`npm run dev`)
- **Cost:** $0 (Local)
- **CI/CD:** None
- **Database:** Local PostgreSQL

### Tier 2: Feature Development

- **Branch Pattern:** `feature/*`, `bugfix/*`, `docs/*`, `refactor/*`
- **Environment:** GitHub (Pull Request)
- **Trigger:** PR opened to `dev`
- **Cost:** $0 (No deployment yet)
- **CI/CD:** Lint, type check, unit tests
- **Database:** Tested against local copy

### Tier 3: Integration Testing

- **Branch:** `dev`
- **Environment:** GitHub Actions (tests only)
- **Trigger:** Push to `dev`
- **Cost:** $0 (No deployment)
- **CI/CD:** Lint, type check, unit + integration tests
- **Purpose:** Integration testing, CI validation

### Tier 4: Staging

- **Branch:** `staging`
- **Environment:** Railway Staging
- **Trigger:** Merge to `staging` (PR from `dev`)
- **Cost:** Minimal (Staging environment)
- **CI/CD:** Full pipeline (build, test, deploy) + Release Please changelog/version PR
- **Database:** Staging PostgreSQL
- **Purpose:** QA, staging validation, release preparation

### Tier 5: Production

- **Branch:** `main`
- **Environment:** Railway Production + Vercel Production
- **Trigger:** Merge to `main` (PR from `staging`)
- **Cost:** Full pricing (Production)
- **CI/CD:** Full pipeline with production checks + GitHub Release tag creation
- **Database:** Production PostgreSQL (backups enabled)
- **Purpose:** Live system availability

---

## 🔄 Development Workflow

### Creating a Feature Branch

```bash
# Update local dev
git checkout dev
git pull origin dev

# Create feature branch
git checkout -b feature/my-feature
```

### Committing Code

**Commit Message Convention:**

```
[TYPE] Brief description

Longer explanation if needed. Reference issue numbers.
```

**Types:**

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `refactor:` Code restructuring (no feature change)
- `test:` Adding/updating tests
- `chore:` Dependencies, config, etc.

### Opening a Pull Request

1. Push branch to GitHub: `git push origin feature/my-feature`
2. Open PR to `dev` branch
3. Ensure all checks pass (lint, type check, tests)
4. Request review from 1-2 team members
5. Address feedback and re-request review
6. Merge when approved

### Merging to `dev`

- Runs tests via GitHub Actions
- No deployment — `dev` is for CI validation only

### Promoting to Staging

1. Create PR from `dev` → `staging`
2. Merge triggers staging deployment to Railway
3. Release Please opens a version/changelog PR against `staging`
4. Review and merge the release PR to finalize the version

### Releasing to Production

1. Create PR from `staging` → `main`
2. Changelog and version bumps are already included from Release Please
3. Merge triggers production deployment + GitHub Release tag creation

**Post-Merge Checklist:**

- [ ] Verify health endpoints respond
- [ ] Check logs for errors
- [ ] Monitor performance metrics
- [ ] Confirm database migrations applied

---

## 🧪 Testing Strategy

### Unit Tests

Run locally before committing:

```bash
npm run test:python          # Backend pytest
npm run test                 # All workspaces
```

### Integration Tests

Automated on every PR:

```bash
npm run test:python:smoke    # Fast smoke tests
```

### E2E / Manual Testing

- Feature branches: Manual testing in Tier 2
- `dev` branch: QA team testing in Tier 3 Staging
- `main` branch: Production monitoring

---

## 🚀 CI/CD Pipeline

### GitHub Actions Workflows

Located in `.github/workflows/`:

- **test-on-feat.yml** — Runs on feature branch PRs: lint, type check, unit tests
- **test-on-dev.yml** — Runs on push to `dev`: full test suite
- **release-please.yml** — Runs on push to `staging`: manages changelog + version bump PRs
- **deploy-staging-with-environments.yml** — Runs on push to `staging`: deploys to Railway Staging
- **deploy-production-with-environments.yml** — Runs on push to `main`: deploys to Production + creates GitHub Release tag

### Deployment Environments

**GitHub Secrets Required:**

- `RAILWAY_TOKEN` - Railway API access
- `VERCEL_TOKEN` - Vercel API access
- `DATABASE_URL` - Production DB connection

See `.env.example` at the project root for per-environment configuration.

---

## 📊 Development Tools

### VS Code Extensions (Recommended)

- **Python:** ms-python.python
- **Pylance:** ms-python.vscode-pylance
- **ESLint:** dbaeumer.vscode-eslint
- **Prettier:** esbenp.prettier-vscode
- **Thunder Client:** rangav.vscode-thunder-client (API testing)

### Command Cheatsheet

```bash
# Setup
npm run setup                  # Full setup (Node + Python + env)

# Development
npm run dev                    # Both services
npm run dev:cofounder         # Backend only
npm run dev:public            # Public site only

# Testing
npm run test:python           # Full backend test
npm run test:python:smoke     # Fast smoke tests
npm run test                  # Frontend tests

# Cleanup
npm run clean:install         # Reset everything
```

---

## Related Documentation

- **[System Design](../architecture/system-design.md)** - System design
- **[Environment Variables](../operations/env-vars.md)** - Environment config
- **[Deployment](../operations/deployment.md)** - CI/CD and deployment
