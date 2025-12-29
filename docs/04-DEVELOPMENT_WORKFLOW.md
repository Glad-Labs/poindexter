# 04 - Development Workflow

**Last Updated:** November 5, 2025  
<<<<<<< HEAD
**Version:** 3.0  
**Status:** ✅ Production Ready | 267 Tests Validated | 4-Tier Branch Strategy Active
=======
**Version:** 1.1  
**Status:** ✅ Production Ready
>>>>>>> feat/refine

---

## 🎯 Quick Links

- **[Branch Strategy](#branch-strategy)** - Git workflow
- **[Commit Standards](#commit-standards)** - Conventional commits
- **[Testing](#testing)** - Unit and integration tests
- **[Code Quality](#code-quality)** - Linting and formatting
- **[Pull Requests](#pull-requests)** - Review process

---

## 🌳 Branch Strategy

### Four-Tier Branch Hierarchy

Glad Labs uses a strategic four-tier branching model that optimizes cost, testing, and deployment efficiency:

```text
┌─────────────────────────────────────────────────────────────┐
│                      TIER 4: PRODUCTION                     │
│ main branch → Vercel (frontend) + Railway (backend)         │
│ Cost: ~$230/month | Testing: Full suite | Live traffic     │
└─────────────────────────────────────────────────────────────┘
                           ↑
                    (PR + Approval)
                           │
┌─────────────────────────────────────────────────────────────┐
│                     TIER 3: STAGING                         │
│ dev branch → Railway staging environment                    │
│ Cost: ~$115/month | Testing: Full suite | Pre-production  │
└─────────────────────────────────────────────────────────────┘
                           ↑
                    (Automated - dev push)
                           │
┌─────────────────────────────────────────────────────────────┐
│                 TIER 2: FEATURE TESTING                     │
│ feature/*, bugfix/*, docs/* → Local environment            │
│ Cost: $0/month | Testing: Unit + integration | Developer   │
└─────────────────────────────────────────────────────────────┘
                           ↑
                     (Local development)
                           │
┌─────────────────────────────────────────────────────────────┐
│                  TIER 1: LOCAL DEVELOPMENT                  │
│ Your laptop → npm run dev (all services locally)            │
│ Cost: $0/month | Testing: Manual + jest/pytest             │
└─────────────────────────────────────────────────────────────┘
```

**Key Features:**

- **Cost Optimization:** Features branches cost $0 (no CI/CD), production costs ~$230/month
- **Automated Deployments:** dev push → staging auto-deploys, main push → production auto-deploys
- **Zero Friction Features:** Create branches freely without triggering expensive CI/CD
- **GitHub Actions Efficiency:** Workflows trigger only on strategic branches (dev, main)

**Detailed Reference:**

- See [docs/reference/ci-cd/BRANCH_HIERARCHY_IMPLEMENTATION_SUMMARY.md](../reference/ci-cd/BRANCH_HIERARCHY_IMPLEMENTATION_SUMMARY.md) for implementation details
- See [docs/reference/ci-cd/BRANCH_HIERARCHY_QUICK_REFERENCE.md](../reference/ci-cd/BRANCH_HIERARCHY_QUICK_REFERENCE.md) for quick lookup
- See [docs/reference/ci-cd/GITHUB_ACTIONS_REFERENCE.md](../reference/ci-cd/GITHUB_ACTIONS_REFERENCE.md) for workflow analysis

### Main Branches

```text
main (Tier 4)        Production releases (stable, live traffic)
  ↓
dev (Tier 3)         Active development (staging environment)
  ↓
feature/* (Tier 2)   New features (local + PR testing)
bugfix/* (Tier 2)    Bug fixes (local + PR testing)
docs/* (Tier 2)      Documentation updates (local only)
```

### Branch Naming

```bash
# Feature
git checkout -b feature/content-generation-agent

# Bugfix
git checkout -b bugfix/database-connection-error

# Documentation
git checkout -b docs/deployment-guide

# Hotfix (from main)
git checkout -b hotfix/security-vulnerability
```

### Workflow

```bash
# 1. Create feature branch from dev
git checkout dev
git pull origin dev
git checkout -b feature/my-feature

# 2. Make commits (see COMMIT STANDARDS below)
git add .
git commit -m "feat: add new feature"

# 3. Push to origin
git push origin feature/my-feature

# 4. Create Pull Request (GitHub/GitLab)
# - Base: dev
# - Compare: feature/my-feature
# - Add description and link to issues

# 5. After approval and tests pass, merge
# - Use "Squash and merge" for feature branches
# - Use "Create a merge commit" for release PRs

# 6. Delete feature branch
git branch -d feature/my-feature
git push origin --delete feature/my-feature
```

---

## 📝 Commit Standards

### Conventional Commits

```bash
<type>: <subject>

<body>

<footer>
```

### Types

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation
- `style:` - Formatting (no code change)
- `refactor:` - Code restructuring
- `test:` - Test changes
- `chore:` - Dependency updates, build changes

### Examples

```bash
# Feature
git commit -m "feat: add multi-provider model router"

# Bug fix
git commit -m "fix: resolve database connection timeout"

# Documentation
git commit -m "docs: update deployment guide"

# With body
git commit -m "feat: implement memory system

- Add short-term context storage
- Add long-term semantic search
- Add automatic cleanup"
```

### Commit Best Practices

- ✅ Commit frequently (every logical change)
- ✅ Write clear, descriptive messages
- ✅ Reference related issues: `fixes #123`
- ✅ Keep commits focused (one thing per commit)
- ❌ Don't commit debug code or commented-out code
- ❌ Don't commit secrets or API keys

---

## 🧪 Testing

> **📚 For comprehensive testing documentation, see [TESTING.md](../reference/TESTING.md)**

Glad Labs uses **Jest** for frontend testing and **pytest** for backend testing. Our test suite includes **93+ passing tests** with >80% coverage on critical paths.

### Quick Test Commands

```bash
# All tests (frontend + backend)
npm test

# Frontend only
npm run test:frontend

# Backend only
npm run test:python

# Quick smoke tests (5-10 min)
npm run test:python:smoke

# With coverage
npm run test:frontend:ci
npm run test:python -- --cov=.
```

### Test Structure

**Frontend:** `web/{public-site,oversight-hub}/__tests__/` + component `.test.js` files  
**Backend:** `src/cofounder_agent/tests/` (unit, integration, performance, e2e)

**Current Coverage - Phase 1-5 Complete:**

- **Phase 2 (Unit Tests):** 116 tests ✅ (99%+ coverage)
- **Phase 3 (Integration Tests):** 101 tests ✅ (Good coverage)
- **Phase 4 (Performance Tests):** 18 tests ✅ (Benchmarks validated)
- **Phase 5 (E2E Scenario Tests):** 32 tests ✅ (Complete workflows)
- **TOTAL:** 267 tests passing in 1.01 seconds ✅

**Breakdown:**

- Unit tests: 116 tests (99%+ coverage on critical paths)
- Integration tests: 101 tests (multi-service workflows)
- Performance tests: 18 tests (benchmarks, baselines)
- E2E tests: 32 tests (complete business scenarios)

### Writing Tests: Quick Start

**Frontend (Jest + React Testing Library):**

```javascript
import { render, screen } from '@testing-library/react';
import PostCard from './PostCard';

describe('PostCard', () => {
  it('renders post title', () => {
    render(<PostCard post={{ title: 'Test' }} />);
    expect(screen.getByText('Test')).toBeInTheDocument();
  });
});
```

**Backend (pytest):**

```python
from fastapi.testclient import TestClient
from src.cofounder_agent.main import app

client = TestClient(app)

def test_create_task():
    response = client.post("/api/tasks", json={"title": "Test"})
    assert response.status_code == 201
```

### Before Committing: Full Checklist

```bash
# 1. Run all tests
npm test
npm run test:python

# 2. Check coverage
npm run test:coverage

# 3. Lint code
npm run lint

# 4. Format code
npm run format

# 5. Type check
npm run type-check

# 6. Ready to commit!
git add .
git commit -m "feat: your changes"
```

### Coverage Goals

| Target         | Current | Status  | Phase          |
| -------------- | ------- | ------- | -------------- |
| Unit Tests     | 85%+    | ✅ 99%+ | Phase 2        |
| Integration    | 80%+    | ✅ Good | Phase 3        |
| Critical Paths | 90%+    | ✅ 92%  | Phase 2        |
| API Endpoints  | 85%+    | ✅ 90%  | Phase 2-3      |
| Core Logic     | 85%+    | ✅ 88%  | Phase 2        |
| **Overall**    | >80%    | ✅ 95%  | **ALL PHASES** |

**Phase Status:**

- Phase 1: Infrastructure (51 tests) ✅
- Phase 2: Unit Tests (116 tests) ✅
- Phase 3: Integration (101 tests) ✅
- Phase 4: Performance (18 tests) ✅
- Phase 5: E2E Scenarios (32 tests) ✅

→ **[See full Testing Guide](../reference/TESTING.md)** for detailed examples, patterns, fixtures, mocking, and troubleshooting.

---

## 🔍 Code Quality

### Linting

```bash
# ESLint (JavaScript/TypeScript)
npm run lint

# Fix auto-fixable issues
npm run lint:fix

# Pylint (Python)
pylint src/

# Flake8 (Python)
flake8 src/
```

### Code Formatting

```bash
# Prettier (formatting)
npm run format

# Black (Python formatting)
black src/
```

### Type Checking

```bash
# TypeScript
npm run type-check

# MyPy (Python)
mypy src/
```

### Markdown

```bash
# Check markdown
npm run lint:md

# Fix markdown
npm run lint:md:fix
```

---

## 📋 Pull Requests

### PR Template

```markdown
## Description

Brief description of changes

## Related Issue

Fixes #123

## Type of Change

- [ ] New feature
- [ ] Bug fix
- [ ] Documentation
- [ ] Breaking change

## Checklist

- [ ] Tests pass locally
- [ ] Code follows style guidelines
- [ ] Documentation updated
- [ ] No breaking changes
```

### Review Process

1. **Create PR** → dev branch
2. **Automated Checks:**
   - ✅ Tests pass
   - ✅ Linting passes
   - ✅ No merge conflicts
3. **Code Review:**
   - Minimum 1 approval required
   - Team reviews for:
     - Code quality
     - Test coverage
     - Documentation
     - Performance
4. **Merge:**
   - "Squash and merge" for features
   - Delete branch after merge

---

## 🚀 Release Process

### Version Numbering (Semantic Versioning)

- `1.0.0` = MAJOR.MINOR.PATCH
- `1.0.0` → `1.1.0` = New feature
- `1.0.0` → `1.0.1` = Bug fix
- `1.0.0` → `2.0.0` = Breaking change

### Creating a Release

```bash
# 1. Create release branch from dev
git checkout -b release/1.2.0

# 2. Update version numbers
# - package.json
# - docs/00-README.md
# - src/cofounder_agent/__init__.py

# 3. Update CHANGELOG.md
# - Add new features
# - Add bug fixes
# - Add breaking changes

# 4. Commit
git commit -m "chore: version 1.2.0"

# 5. Merge to main
git checkout main
git merge release/1.2.0

# 6. Create git tag
git tag -a v1.2.0 -m "Release version 1.2.0"
git push origin main --tags

# 7. Merge back to dev
git checkout dev
git merge main

# 8. Delete release branch
git branch -d release/1.2.0
```

---

## ❓ Frequently Asked Questions

### Q1: How do I get automatic deployments from dev → staging and main → production?

**Answer:** GitHub Actions workflows handle this automatically.

**Setup:**

1. Add GitHub Secrets (see [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md#automated-deployment-workflow-github-actions))
2. Connect Railway to GitHub
3. Connect Vercel to GitHub
4. Push to dev branch → auto-deploys to staging
5. Push to main branch → auto-deploys to production

**Workflows:**

- `.github/workflows/deploy-staging.yml` (triggers on dev push)
- `.github/workflows/deploy-production.yml` (triggers on main push)

---

### Q2: How do Railway and Vercel share environment variables?

**Answer:** They don't - GitHub Actions is the orchestrator.

```
GitHub Secrets (centralized, never exposed)
    ↓
GitHub Actions (reads all secrets)
    ├→ Railway (gets: database, backend config)
    └→ Vercel (gets: frontend URLs, API tokens)
```

**Key Security Points:**

- ✅ Secrets never exposed to either platform
- ✅ Each platform gets only what it needs
- ✅ GitHub Secrets are the single source of truth
- ✅ Backend variables stay in Railway only
- ✅ Frontend variables stay in Vercel only

**Why This Matters:**

- Maximum security
- Clear separation of concerns
- Easy to rotate secrets
- No cross-platform dependencies

---

### Q3: Does local development get affected by deployment changes?

**Answer:** NO - Zero impact on local development.

**Why:**

- Local dev uses `.env` (local only, never commits)
- Staging uses `.env.staging` (committed, no secrets)
- Production uses `.env.production` (committed, no secrets)
- GitHub Actions handles passing actual secrets at deploy time
- Your local environment is completely isolated

**Development Workflow:**

```bash
# Local (your machine)
.env → npm run dev → localhost:3000, 3001, 8000

# Staging (after merge to dev)
.env.staging + GitHub Secrets → GitHub Actions → Railway staging

# Production (after merge to main)
.env.production + GitHub Secrets → GitHub Actions → Railway production
```

---

### Q4: What if a deployment fails?

**Answer:** Manual rollback is simple.

```bash
# 1. Identify the problem
git log --oneline main | head -5

# 2. Revert to previous version
git revert <commit-hash>
git push origin main
# ← GitHub Actions auto-deploys the revert

# 3. Verify
curl https://example.com/api/health

# 4. Document
# Write down what went wrong and how to prevent it
```

---

### Q5: Can I deploy without GitHub Actions?

**Answer:** Yes - Manual deployment is always possible.

```bash
# Manual deployment to Railway
railway login
railway link --project <project-id>
railway up

# Manual deployment to Vercel
vercel --prod

# But GitHub Actions is recommended for:
# ✅ Consistency
# ✅ Testing before deploy
# ✅ Automatic rollbacks
# ✅ Audit trail
```

---

## 📚 Quick Command Reference

```bash
# Git workflow
git clone <repo>                    # Clone repository
git checkout -b feature/name        # Create feature branch
git add .                           # Stage changes
git commit -m "type: message"       # Commit changes
git push origin feature/name        # Push to remote
git pull origin dev                 # Pull latest changes

# Testing
npm test                            # Run tests
npm run test:watch                  # Watch mode
npm run test:coverage               # Coverage report

# Code quality
npm run lint                        # Check linting
npm run lint:fix                    # Fix issues
npm run format                      # Format code
npm run type-check                  # Type checking

# Building
npm run build                       # Build for production
npm start                           # Start production server
npm run dev                         # Start development server
```

---

## 🔗 Related Documentation

- **[Setup Guide](./01-SETUP_AND_OVERVIEW.md)** - Getting started
- **[Architecture](./02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Production deployment
- **[AI Agents](./05-AI_AGENTS_AND_INTEGRATION.md)** - Agent development

---

**[← Back to Documentation Hub](./00-README.md)**

[Setup](./01-SETUP_AND_OVERVIEW.md) • [Architecture](./02-ARCHITECTURE_AND_DESIGN.md) • [Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) • [Operations](./06-OPERATIONS_AND_MAINTENANCE.md)
