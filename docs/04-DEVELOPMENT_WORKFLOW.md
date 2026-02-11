# 04 - Development Workflow

**Last Updated:** February 10, 2026  
**Version:** 3.0.0  
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

### Tier 3: Integration/Staging

- **Branch:** `dev`
- **Environment:** Railway Staging
- **Trigger:** Merge to `dev`
- **Cost:** Minimal (Staging environment)
- **CI/CD:** Full pipeline (build, test, deploy)
- **Database:** Staging PostgreSQL (read-only replicas)
- **Purpose:** QA, integration testing, team review

### Tier 4: Production

- **Branch:** `main`
- **Environment:** Railway Production + Vercel Production
- **Trigger:** Merge to `main` (requires PR approval)
- **Cost:** Full pricing (Production)
- **CI/CD:** Full pipeline with production checks
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

- Deploys automatically to Railway Staging
- Triggers full CI/CD pipeline
- Available for QA testing within 5-10 minutes

### Releasing to Production

1. Create PR from `dev` → `main`
2. Title: `Release: vX.Y.Z`
3. Include changelog in PR description
4. Require approval from team lead
5. Merge triggers production deployment

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

- **lint.yml** - Runs on PR: Checks code formatting, types
- **test.yml** - Runs on PR: Unit and integration tests
- **deploy-staging.yml** - Runs on merge to `dev`: Deploys to Railway Staging
- **deploy-production.yml** - Runs on merge to `main`: Deploys to Production

### Deployment Environments

**GitHub Secrets Required:**

- `RAILWAY_TOKEN` - Railway API access
- `VERCEL_TOKEN` - Vercel API access
- `DATABASE_URL` - Production DB connection

See [07-BRANCH_SPECIFIC_VARIABLES.md](07-BRANCH_SPECIFIC_VARIABLES.md) for per-environment configuration.

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
npm run setup:all              # Full fresh install

# Development
npm run dev                    # All three services
npm run dev:cofounder         # Backend only
npm run dev:frontend          # Public site + Oversight Hub

# Testing
npm run test:python           # Full backend test
npm run test:python:smoke     # Fast smoke tests
npm run test                  # Frontend tests

# Cleanup
npm run clean:install         # Reset everything
```

---

## 🔗 Related Documentation

- **[02-ARCHITECTURE_AND_DESIGN.md](02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[07-BRANCH_SPECIFIC_VARIABLES.md](07-BRANCH_SPECIFIC_VARIABLES.md)** - Environment config
- **[reference/ci-cd/](reference/ci-cd/)** - GitHub Actions details
