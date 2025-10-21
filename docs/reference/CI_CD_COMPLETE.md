# Complete CI/CD Setup & Testing Guide

**Consolidates:** CI_CD_SETUP.md, TESTING_AND_CICD_REVIEW.md, TESTING_CI_CD_IMPLEMENTATION_PLAN.md, TESTING_CICD_QUICK_REFERENCE.md, TESTING_SETUP.md

**Date:** October 20, 2025  
**Status:** ✅ Production Ready

---

## 🎯 Overview

Your GLAD Labs monorepo has complete CI/CD and testing infrastructure:

- **GitHub Actions Workflows** - Automated testing on every push
- **Unit Tests** - Frontend (Jest) + Python (pytest)
- **Linting & Formatting** - Automatic code quality checks
- **Build Verification** - Ensures deployments won't fail
- **Branch-Specific Workflows** - Different actions per branch

---

## 🚀 GitHub Actions Workflows

### Test on Feature Branches (`test-on-feat.yml`)

**Trigger:** Any push to `feat/*` or `feature/*` branches

**Steps:**

1. Checkout code
2. Setup Node.js + Python environments
3. Install dependencies
4. Run frontend tests (Jest)
5. Run Python smoke tests
6. Run linting checks
7. Run build to verify no errors
8. Report results in PR

**Key Features:**

- `continue-on-error: true` - Individual test failures don't block pipeline
- Tests against local environment (`.env` defaults)
- Quick feedback loop for developers

### Deploy to Staging (`deploy-staging.yml`)

**Trigger:** Any push to `dev` branch

**Steps:**

1. Checkout code
2. Setup Node.js + Python environments
3. Install dependencies
4. Load staging environment (`.env.staging`)
5. Run full test suite
6. Build frontend with staging endpoints
7. Deploy to Railway staging environment

**Key Features:**

- Full test suite (not just smoke tests)
- Uses staging database
- Uses staging API endpoints
- Requires all tests to pass before deploy

### Deploy to Production (`deploy-production.yml`)

**Trigger:** Any push to `main` branch

**Steps:**

1. Checkout code
2. Setup Node.js + Python environments
3. Install dependencies
4. Load production environment (`.env.production`)
5. Run full test suite
6. Build frontend with production endpoints
7. Deploy to Vercel (frontend)
8. Deploy to Railway (backend)

**Key Features:**

- Full test suite required
- Uses production database
- Uses production API endpoints
- Automatic Vercel + Railway deployment

---

## 🧪 Testing Strategy

### Frontend Testing (Jest)

**Location:** `web/public-site/__tests__/` and `web/oversight-hub/src/`

**Coverage:**

- Component rendering
- API integration
- User interactions
- Page load logic

**Run locally:**

```bash
# Test all workspaces
npm run test:frontend

# Test specific workspace
npm run test --workspace=web/public-site

# Watch mode (development)
npm run test --workspace=web/public-site -- --watch

# CI mode (coverage)
npm run test:public:ci
```

### Python Testing (pytest)

**Location:** `src/cofounder_agent/tests/`

**Coverage:**

- Agent orchestration
- API endpoints
- Business logic
- Integration tests

**Run locally:**

```bash
# All tests
npm run test:python

# Smoke tests only (quick)
npm run test:python:smoke

# Coverage report
pytest --cov=cofounder_agent tests/
```

### Linting & Formatting

**Frontend:**

```bash
# Check for issues
npm run lint

# Auto-fix
npm run lint:fix

# Format code
npm run format

# Check formatting
npm run format:check
```

**Python:**

```bash
# Using pytest
pytest --lf --tb=short

# Using black (code formatting)
black src/cofounder_agent/

# Using flake8 (linting)
flake8 src/cofounder_agent/
```

---

## 📋 Test Execution Order

```
1. Frontend Tests (Jest)
   ├─ Public Site tests
   └─ Oversight Hub tests

2. Python Tests (pytest)
   ├─ Unit tests
   ├─ Integration tests
   └─ E2E smoke tests

3. Linting
   ├─ Frontend (ESLint)
   ├─ Python (flake8/pylint)
   └─ Markdown (markdownlint)

4. Build Verification
   ├─ Frontend build
   ├─ Backend build
   └─ Check for errors
```

---

## 🔄 Workflow: Local → Feature → Staging → Production

### Local Testing (Before Commit)

```bash
# 1. Make changes
git checkout -b feat/my-feature
# ... edit files

# 2. Test locally
npm run test
npm run lint:fix
npm run format

# 3. Verify build
npm run build

# 4. All green? Commit
git add .
git commit -m "feat: my feature"
```

### Feature Branch Testing (GitHub Actions)

```bash
# 1. Push to feature branch
git push origin feat/my-feature

# 2. GitHub Actions automatically:
#    - Runs test-on-feat.yml
#    - Tests + linting + build
#    - Results shown in PR

# 3. Create PR to dev
# 4. Review + approve
```

### Staging Deployment

```bash
# 1. Merge to dev
git checkout dev
git merge --squash feat/my-feature
git push origin dev

# 2. GitHub Actions automatically:
#    - Runs deploy-staging.yml
#    - Full test suite against staging DB
#    - Deploys to Railway staging
#    - Available at: https://staging-cms.railway.app

# 3. Manual testing on staging
# 4. If all good, create PR to main
```

### Production Deployment

```bash
# 1. Merge to main
git checkout main
git merge --no-ff dev
git push origin main

# 2. GitHub Actions automatically:
#    - Runs deploy-production.yml
#    - Full test suite against prod DB
#    - Deploys to Vercel (frontend)
#    - Deploys to Railway (backend)
#    - Live at: https://glad-labs.vercel.app

# 3. Monitor deployment
# 4. Verify live site
```

---

## 📊 npm Scripts Reference

### Development

```bash
npm run dev                  # Start all services (auto env selection)
npm run dev:strapi          # Strapi only
npm run dev:public          # Public site only
npm run dev:oversight       # Oversight hub only
npm run dev:cofounder       # Co-founder agent only
```

### Testing

```bash
npm run test                # All tests (frontend + Python)
npm run test:frontend       # Frontend tests (watch mode)
npm run test:frontend:ci    # Frontend tests (CI mode, no watch)
npm run test:public:ci      # Public site tests only
npm run test:oversight:ci   # Oversight hub tests only
npm run test:python         # All Python tests
npm run test:python:smoke   # Quick Python smoke tests
```

### Building

```bash
npm run build               # Build all workspaces
npm run build --workspace=web/public-site    # Single workspace
```

### Code Quality

```bash
npm run lint                # Check linting
npm run lint:fix            # Fix linting issues
npm run format              # Format all code
npm run format:check        # Check formatting
```

### Utilities

```bash
npm run env:select          # Manually select environment
npm run services:check      # Health check all services
npm run services:kill       # Stop all background services
npm run services:restart    # Restart all services
npm run clean               # Clean all build artifacts
npm run clean:install       # Clean + fresh install
```

---

## ✅ CI/CD Checklist

**Before merging to dev:**

- [ ] `npm run test` passes locally
- [ ] `npm run lint:fix` runs clean
- [ ] `npm run build` succeeds
- [ ] Feature branch pushes successfully
- [ ] GitHub Actions `test-on-feat.yml` passes
- [ ] PR reviewed and approved

**Before merging to main:**

- [ ] Tested on staging environment
- [ ] Staging deployment succeeded
- [ ] Verified functionality on staging
- [ ] No errors in logs
- [ ] Ready for production

**After pushing to main:**

- [ ] Monitor GitHub Actions `deploy-production.yml`
- [ ] Check Vercel deployment: https://vercel.com/dashboard
- [ ] Visit production: https://glad-labs.vercel.app
- [ ] Verify all pages load
- [ ] Check API integrations
- [ ] Monitor error logs

---

## 🐛 Debugging CI/CD Issues

### Tests Fail Locally

```bash
# 1. Update dependencies
npm run clean:install

# 2. Run tests with verbose output
npm run test -- --verbose

# 3. Check for specific errors
npm test -- --testNamePattern="specific test"

# 4. Run Python tests in debug mode
pytest -vv -s tests/
```

### GitHub Actions Fail

```bash
# 1. Check workflow file syntax
cat .github/workflows/test-on-feat.yml

# 2. View full logs in GitHub → Actions → [workflow]

# 3. Re-run failed workflow
# Click "Re-run failed jobs" in GitHub

# 4. Check environment variables
# Are secrets set in GitHub?
```

### Build Verification Fails

```bash
# 1. Build locally to reproduce
npm run build

# 2. Check for errors
npm run build 2>&1 | grep -i error

# 3. Clear cache
npm run clean
npm run clean:install
npm run build

# 4. Check node version compatibility
node --version
```

### Linting Issues

```bash
# 1. Auto-fix
npm run lint:fix

# 2. Format code
npm run format

# 3. Check for remaining issues
npm run lint

# 4. Review specific file
npm run lint -- path/to/file.js
```

---

## 📈 Monitoring & Metrics

### GitHub Actions Dashboard

- Go to: Repository → Actions
- View all workflow runs
- See pass/fail status
- Access detailed logs

### Vercel Deployment Dashboard

- Go to: https://vercel.com/dashboard
- See all frontend deployments
- View build logs
- Monitor performance

### Railway Dashboard

- Go to: https://railway.app/dashboard
- See backend deployments
- View database status
- Monitor logs

---

## 🚀 Continuous Improvement

### Local Testing Best Practices

- Always run `npm run test` before committing
- Use `npm run lint:fix` to auto-correct issues
- Format code with `npm run format`
- Test in watch mode during development

### Code Quality Standards

- Maintain test coverage above 80%
- Fix all linting errors before merging
- Follow code formatting conventions
- Add tests for new features

### Performance Optimization

- Monitor build times (target < 5 minutes)
- Watch bundle size in Vercel
- Optimize slow tests
- Cache dependencies in workflows

---

## 📚 Related Documentation

- **`.github/workflows/`** - Workflow definitions
- **`docs/04-DEVELOPMENT_WORKFLOW.md`** - Development process
- **`docs/guides/BRANCH_SETUP_COMPLETE.md`** - Branch-specific setup
- **`.github/copilot-instructions.md`** - AI guidance

---

## ✨ Summary

✅ **Automated testing** on every push  
✅ **Three-tier workflow** (dev, staging, production)  
✅ **Quality gates** before deployment  
✅ **Production-ready** CI/CD pipeline  
✅ **Developer-friendly** with clear feedback

**You're all set for professional development!** 🚀

---

**Status:** ✅ Complete  
**Last Updated:** October 20, 2025
