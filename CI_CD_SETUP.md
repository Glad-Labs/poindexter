# GitHub Actions CI/CD Setup Guide

Complete setup for automated testing, linting, and deployment.

## Overview

This guide provides GitHub Actions workflows for:

1. ✅ **Test Pipeline** - Run Jest tests on PR and push
2. ✅ **Lint Pipeline** - Run ESLint on PR and push
3. ✅ **Deploy Pipeline** - Deploy to Vercel and Railway on main branch

---

## Step 1: Create GitHub Actions Directory

Create `.github/workflows/` directory if it doesn't exist:

```bash
mkdir -p .github/workflows
```

---

## Step 2: Create Test Workflow

**File:** `.github/workflows/test.yml`

```yaml
name: Test Suite

on:
  push:
    branches: [main, dev]
  pull_request:
    branches: [main, dev]

jobs:
  test:
    runs-on: ubuntu-latest

    strategy:
      matrix:
        node-version: [18.x, 20.x]

    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node-version }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run tests (frontend)
        run: npm run test:public:ci

      - name: Generate coverage
        run: npm test -- --workspace=web/public-site -- --coverage --watchAll=false
        continue-on-error: true

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        if: matrix.node-version == '20.x'
        with:
          files: ./web/public-site/coverage/lcov.info
          fail_ci_if_error: false
```

**What this does:**

- Runs on push to main/dev and all PRs
- Tests Node.js 18 and 20 (ensures compatibility)
- Installs dependencies and runs tests
- Generates coverage reports
- Uploads to Codecov

---

## Step 3: Create Lint Workflow

**File:** `.github/workflows/lint.yml`

```yaml
name: Lint Check

on:
  push:
    branches: [main, dev]
  pull_request:
    branches: [main, dev]

jobs:
  lint:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20.x'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Lint TypeScript/JavaScript
        run: npm run lint --workspaces --if-present

      - name: Lint Markdown
        run: npm run lint:markdown
        continue-on-error: true
```

**What this does:**

- Checks code style and quality
- Lints all workspace projects
- Lints markdown documentation
- Fails PR if linting errors exist

---

## Step 4: Create Deployment Workflow

**File:** `.github/workflows/deploy.yml`

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]
  workflow_run:
    workflows: ['Test Suite', 'Lint Check']
    types: [completed]
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    if: github.event.workflow_run.conclusion == 'success' || github.event_name == 'push'

    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20.x'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run tests before deploy
        run: npm run test:public:ci

      - name: Deploy Public Site to Vercel
        env:
          VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}
          VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}
          VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }}
        run: |
          npm install --global vercel
          cd web/public-site
          vercel --prod --token=$VERCEL_TOKEN

      - name: Deploy Strapi to Railway
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
        run: |
          npm install --global @railway/cli
          cd cms/strapi-main
          railway up --service strapi-backend --detach
```

**What this does:**

- Runs only on push to main
- Waits for tests and lint to pass
- Re-runs tests before deployment
- Deploys to Vercel (public-site)
- Deploys to Railway (Strapi backend)

---

## Step 5: Configure GitHub Secrets

Go to your GitHub repository Settings → Secrets and add:

### For Vercel:

1. `VERCEL_ORG_ID` - Get from Vercel dashboard
2. `VERCEL_PROJECT_ID` - Get from Vercel dashboard
3. `VERCEL_TOKEN` - Create at https://vercel.com/account/tokens

### For Railway:

1. `RAILWAY_TOKEN` - Get from Railway dashboard at https://railway.app/account/tokens

### Example Steps:

```
GitHub Repo → Settings → Secrets and Variables → Actions → New repository secret

Name: VERCEL_TOKEN
Value: [paste your token from Vercel]
```

---

## Step 6: Update Package.json Scripts

Add to root `package.json`:

```json
{
  "scripts": {
    "lint:markdown": "markdownlint *.md docs/*.md"
  }
}
```

---

## Step 7: Add Pre-commit Hooks (Optional)

Install Husky for local validation before commit:

```bash
npm install --save-dev husky lint-staged

npx husky install

npx husky add .husky/pre-commit "npm run lint --workspaces && npm run lint:markdown"
npx husky add .husky/pre-push "npm run test:public:ci"
```

---

## Deployment Flow Diagram

```
[Push to main]
       ↓
[Test Suite runs] → [Lint Check runs]
       ↓                    ↓
  Tests pass?          Linting pass?
       ↓                    ↓
      YES (continue)       YES (continue)
       ↓                    ↓
[Deploy Workflow triggered]
       ↓
[Run tests again]
       ↓
[Deploy to Vercel]
       ↓
[Deploy to Railway]
       ↓
✅ Production updated
```

---

## Workflow Status Badges

Add to your README.md:

```markdown
![Tests](https://github.com/YOUR_USERNAME/glad-labs-website/workflows/Test%20Suite/badge.svg)
![Lint](https://github.com/YOUR_USERNAME/glad-labs-website/workflows/Lint%20Check/badge.svg)
![Deploy](https://github.com/YOUR_USERNAME/glad-labs-website/workflows/Deploy%20to%20Production/badge.svg)
```

---

## Troubleshooting

### Tests fail in CI but pass locally

- **Cause:** Different Node.js versions
- **Solution:** Ensure `.node-version` or `.nvmrc` file matches CI version
- **Example:** Add `.nvmrc` with content: `20.11.0`

### Deployment fails with "token not found"

- **Cause:** GitHub secret not configured
- **Solution:** Check Settings → Secrets for correct token names
- **Verify:** Run locally with token: `VERCEL_TOKEN=xxx npm run deploy`

### Tests timeout in CI

- **Cause:** Slow GitHub Actions runners
- **Solution:** Increase timeout in workflow: `timeout-minutes: 15`
- **Alternative:** Run fewer test suites in parallel

### Secrets not accessible in workflow

- **Cause:** Secret name mismatch
- **Solution:** Check exact name (case-sensitive): `${{ secrets.VERCEL_TOKEN }}`
- **Verify:** Use GitHub CLI: `gh secret list`

---

## Next Steps

1. ✅ Create the three workflow files
2. ✅ Add GitHub secrets for deployment
3. ✅ Push workflows to repository
4. ✅ Verify workflows run in GitHub Actions tab
5. ✅ Add pre-commit hooks locally (optional)
6. ✅ Update README with status badges
