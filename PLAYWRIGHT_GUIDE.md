# Playwright Configuration Guide

**Updated:** March 8, 2026  
**Status:** ✅ Complete - Comprehensive multi-config testing setup

---

## Overview

Three dedicated Playwright configurations provide complete test coverage:

| Config                             | Purpose         | Tests                  | Port | Focus           |
| ---------------------------------- | --------------- | ---------------------- | ---- | --------------- |
| **playwright.config.ts**           | Public Website  | Content & SEO          | 3000 | User experience |
| **playwright.oversight.config.ts** | Admin Dashboard | Workflows & Auth       | 3001 | Admin features  |
| **playwright.api.config.ts**       | Backend API     | Endpoints & Validation | 8000 | API reliability |

---

## Quick Commands

### Run All Tests (All Configs)

```bash
# Run public site tests
npx playwright test

# Run oversight hub tests
npx playwright test -c playwright.oversight.config.ts

# Run API tests
npx playwright test -c playwright.api.config.ts
```

### Development Mode

```bash
# UI mode (visual test runner)
npx playwright test --ui

# Debug mode (with inspector)
npx playwright test --debug

# Watch mode (re-run on changes)
npx playwright test --watch
```

### Specific Test Runs

```bash
# Run single test file
npx playwright test posts.spec.ts

# Run tests matching pattern
npx playwright test --grep @login

# Run specific browser
npx playwright test --project=chromium

# Run only desktop browsers (skip mobile)
npx playwright test --project=chromium --project=firefox --project=webkit
```

### Reporting & Results

```bash
# View HTML report
npx playwright show-report

# View specific report
npx playwright show-report test-results/oversight/html-report

# Generate coverage (if enabled)
npm run test:coverage
```

### Advanced Options

```bash
# Run with trace collection
npx playwright test --trace on

# Run with video recording
npx playwright test --video on

# Run with screenshots
npx playwright test --screenshot on

# Maximum parallelization
npx playwright test --workers=8

# Sequential execution (stability)
npx playwright test --workers=1

# Stop on first failure
npx playwright test --x

# Run with verbose output
npx playwright test --reporter=verbose
```

---

## Configuration Details

### playwright.config.ts (Public Site)

**Purpose:** Test public-facing website features

**Key Features:**

- ✅ 8 browsers (Desktop: Chrome, Firefox, Safari + Tablet: iPad, iPad Mini + Mobile: 4 devices)
- ✅ Parallel execution (4 workers by default)
- ✅ Accessibility testing (chromium-axe project)
- ✅ Visual regression (chromium-visual project)
- ✅ Full reporting (HTML, JSON, JUnit, Markdown, GitHub)

**Test Types:**

```
web/public-site/e2e/
├── content.spec.ts          # Blog content, posts, categories
├── search.spec.ts           # Search functionality
├── seo.spec.ts              # SEO metadata, structured data
├── navigation.spec.ts       # Site navigation, links
├── responsive.spec.ts       # Mobile/tablet layouts
├── performance.spec.ts      # Page load times, Core Web Vitals
├── accessibility.a11y.spec.ts    # ARIA, screen reader
└── visual.visual.spec.ts    # Visual regression
```

**Run Specific Suite:**

```bash
# Content tests
npx playwright test content.spec.ts

# SEO tests
npx playwright test seo.spec.ts

# Accessibility tests
npx playwright test --project=chromium-axe

# Visual regression
npx playwright test --project=chromium-visual
```

### playwright.oversight.config.ts (Admin Hub)

**Purpose:** Test admin dashboard workflows

**Key Features:**

- ✅ Sequential execution (1 worker) for data consistency
- ✅ Pre-authenticated state (OAuth tokens)
- ✅ Longer timeouts (45s) for complex workflows
- ✅ Screenshot on failure for verification
- ✅ 5 projects (Chrome primary, Firefox/Safari backup, iPad, Accessibility)

**Test Types:**

```
web/oversight-hub/e2e/
├── auth.spec.ts             # Login, OAuth, token management
├── workflows.spec.ts        # Workflow creation, execution
├── model-selection.spec.ts  # Model picking, switching
├── progress.spec.ts         # Real-time progress updates
├── monitoring.spec.ts       # Dashboard metrics, health
├── admin.spec.ts            # Admin-only features
└── accessibility.a11y.spec.ts    # Admin accessibility
```

**Run Specific Suite:**

```bash
# Admin tests with auth
npx playwright test -c playwright.oversight.config.ts

# Workflow tests
npx playwright test workflows.spec.ts -c playwright.oversight.config.ts

# With UI
npx playwright test --ui -c playwright.oversight.config.ts
```

### playwright.api.config.ts (Backend API)

**Purpose:** Test API endpoints and business logic

**Key Features:**

- ✅ Parallel execution (can run simultaneously)
- ✅ Retry on network errors (3x in CI)
- ✅ Pure API testing (no UI)
- ✅ 4 test categories (General, Performance, Security, Smoke)

**Test Types:**

```
playwright/api/
├── posts.spec.ts            # Post CRUD endpoints
├── search.spec.ts           # Search API
├── workflows.spec.ts        # Workflow execution API
├── auth.spec.ts             # Authentication endpoints
├── errors.spec.ts           # Error handling
├── performance.perf.spec.ts # Response time baselines
├── security.security.spec.ts # Auth checks, input validation
└── smoke.smoke.spec.ts      # Quick health checks
```

**Run Specific Suite:**

```bash
# All API tests
npx playwright test -c playwright.api.config.ts

# Performance tests only
npx playwright test --project=api-performance -c playwright.api.config.ts

# Security tests
npx playwright test --project=api-security -c playwright.api.config.ts

# Smoke tests (fast)
npx playwright test --project=api-smoke -c playwright.api.config.ts
```

---

## Environment Variables

### Global

```bash
# Set base URLs
export PLAYWRIGHT_TEST_BASE_URL=http://localhost:3000
export PLAYWRIGHT_ADMIN_URL=http://localhost:3001
export PLAYWRIGHT_API_URL=http://localhost:8000

# Set output directory
export PLAYWRIGHT_OUTPUT_DIR=test-results/

# Skip web server startup (if already running)
export SKIP_SERVER_START=1

# Enable CI mode
export CI=1
```

### Test Timeouts

```bash
# Override default timeout (30s)
export PLAYWRIGHT_TIMEOUT=60000

# Mock network delays for testing
export MOCK_NETWORK_DELAY=1000
```

### Authentication

```bash
# API key for API tests
export PLAYWRIGHT_API_KEY=your-api-key

# Auth storage state file
export PLAYWRIGHT_AUTH_STATE=playwright/.auth/custom.json
```

---

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '18'

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright
        run: npx playwright install --with-deps

      - name: Start services
        run: npm run dev &
        env:
          CI: 1

      - name: Run public site tests
        run: npx playwright test

      - name: Run admin tests
        run: npx playwright test -c playwright.oversight.config.ts

      - name: Run API tests
        run: npx playwright test -c playwright.api.config.ts

      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: playwright-report
          path: test-results/
```

### Local CI Simulation

```bash
# Run tests as if in CI (no server restart, retries enabled)
CI=1 npx playwright test
CI=1 npx playwright test -c playwright.oversight.config.ts
CI=1 npx playwright test -c playwright.api.config.ts
```

---

## Project Selection Guide

### By Development Area

**Working on Public Site (Content)?**

```bash
npx playwright test                    # Full suite
npx playwright test --project=chromium # Fast feedback
npx playwright test --grep @navigation # Specific feature
```

**Working on Admin Dashboard?**

```bash
npx playwright test -c playwright.oversight.config.ts
npx playwright test workflows.spec.ts -c playwright.oversight.config.ts
```

**Working on Backend API?**

```bash
npx playwright test -c playwright.api.config.ts
npx playwright test -c playwright.api.config.ts --project=api-smoke
```

### By Testing Level

**Smoke Tests (Quick):**

```bash
# Run basic health checks
npx playwright test --project=api-smoke -c playwright.api.config.ts

# Single browser
npx playwright test --project=chromium

# Skip mobile/tablet
npx playwright test -c playwright.config.ts \
  --project=chromium \
  --project=firefox \
  --project=webkit
```

**Full Suite (Comprehensive):**

```bash
# All tests, all browsers, all configs
npx playwright test
npx playwright test -c playwright.oversight.config.ts
npx playwright test -c playwright.api.config.ts
```

**Mobile Focus:**

```bash
# Mobile devices only
npx playwright test -c playwright.config.ts \
  --project=Pixel\ 5 \
  --project=iPhone\ 12 \
  --project=Galaxy\ S9+
```

**Accessibility Focus:**

```bash
# Accessibility tests
npx playwright test --project=chromium-axe
npx playwright test --project=chromium-a11y -c playwright.oversight.config.ts
```

---

## Debugging & Troubleshooting

### Debug Mode

```bash
# Open Playwright Inspector
npx playwright test --debug

# Pause on test failure
npx playwright test --pause-on-error

# Print detailed logs
npx playwright test --reporter=verbose
```

### View Traces & Videos

```bash
# Automatically opens inspector with traces
npx playwright show-trace test-results/playwright/traces/trace.zip

# Review videos on failure
ls test-results/playwright/videos/

# View screenshots
ls test-results/playwright/*/screenshots/
```

### Network Inspection

```bash
# Capture HAR (HTTP Archive)
npx playwright test --headed

# Mock slow network
MOCK_NETWORK_DELAY=1000 npx playwright test
```

### Local Test Recording

```bash
# Record new test
npx playwright codegen http://localhost:3000

# Generate from API responses
npx playwright codegen -c playwright.api.config.ts http://localhost:8000
```

---

## Reporters in Detail

### HTML Report

```bash
# Generate and open
npx playwright test
npx playwright show-report

# View specific config's report
npx playwright show-report test-results/oversight/html-report
```

**Shows:**

- Test execution timeline
- Screenshots/videos on failure
- Traces for debugging
- Browser compatibility matrix

### JSON Report

```bash
# Parse results programmatically
jq . test-results/results.json

# Check test status
jq '.tests[] | {title, status}' test-results/results.json
```

### JUnit Report

```bash
# Integrate with CI tools
# Jenkins, GitLab CI, Azure DevOps automatically parse junit.xml
```

### Markdown Report

```bash
# Human-readable summary
cat test-results/results.md
```

---

## Performance Optimization

### Parallel Execution

```bash
# Maximize parallelization
npx playwright test --workers=8

# Check CPU usage
npx playwright test --workers=$(nproc)
```

### Sequential Execution (for stability)

```bash
# Run tests one at a time
npx playwright test --workers=1
```

### Selective Testing

```bash
# Run only changed tests
npx playwright test --last-failed

# Run with tags
npx playwright test --grep @critical

# Skip slow tests
npx playwright test --grep -v @slow
```

---

## Project Maintenance

### Update Playwright

```bash
npm install -D @playwright/test@latest
npx playwright install
```

### Update Browsers

```bash
npx playwright install
npx playwright install --with-deps
```

### Regenerate Snapshots

```bash
npx playwright test --update-snapshots
```

### Clear Cache

```bash
rm -rf test-results/
rm -rf playwright/.cache/
npx playwright install
```

---

## Troubleshooting Matrix

| Issue                             | Solution                                                     |
| --------------------------------- | ------------------------------------------------------------ |
| Tests timeout                     | Increase `timeout` in config or `PLAYWRIGHT_TIMEOUT` env var |
| Port already in use               | Set `SKIP_SERVER_START=1` or kill process on port            |
| Auth state expired                | Delete `playwright/.auth/` and regenerate                    |
| Screenshots not captured          | Ensure `screenshot: 'only-on-failure'` in use config         |
| Flaky mobile tests                | Use `--workers=1` for sequential execution                   |
| Tests pass locally but fail in CI | Check CI env vars, check timezone issues                     |
| Video not recording               | Ensure `video: 'retain-on-failure'` and not headless         |
| Trace collection failing          | Check disk space, ensure outputDir writable                  |

---

## Best Practices

1. **Use Config-Specific Tests**
   - Keep public site tests in `web/public-site/e2e/`
   - Keep admin tests in `web/oversight-hub/e2e/`
   - Keep API tests in `playwright/api/`

2. **Tag Critical Tests**

   ```typescript
   test('@critical posts display', async ({ page }) => { ... });
   ```

   Then run: `npx playwright test --grep @critical`

3. **Use Page Objects for Maintainability**

   ```typescript
   const postsPage = new PostsPage(page);
   await postsPage.navigateToCategory('Tech');
   ```

4. **Test User Flows, Not Implementation**

   ```typescript
   // ✅ Good: What user sees
   await page.click('text=Share Post');

   // ❌ Avoid: Implementation details
   await page.evaluate(() => document.querySelector('.share-btn').click());
   ```

5. **Run Smoke Tests First**

   ```bash
   npx playwright test --grep @smoke  # Fast feedback
   npx playwright test                # Full suite
   ```

---

## Example Workflows

### Before Commit

```bash
# Quick smoke tests
npx playwright test --grep @smoke

# Changed file tests
npx playwright test --last-failed

# Full suite if making critical changes
npx playwright test
```

### CI/CD Pipeline

```bash
# Public site
npx playwright test

# Admin dashboard
npx playwright test -c playwright.oversight.config.ts

# API endpoints
npx playwright test -c playwright.api.config.ts

# Report results
npx playwright show-report
```

### Investigation & Debugging

```bash
# Debug UI mode
npx playwright test --ui

# Step through test
npx playwright test --debug

# Record video on failure
npx playwright test --reporter=verbose
```

---

## Resources

- [Playwright Docs](https://playwright.dev/docs/intro)
- [Best Practices](https://playwright.dev/docs/best-practices)
- [Debugging Guide](https://playwright.dev/docs/debug)
- [Configuration Reference](https://playwright.dev/docs/api/class-testoptions)
- [Reporters](https://playwright.dev/docs/test-reporters)

---

## Summary

These three configurations provide **complete test coverage**:

| Aspect         | Coverage                                |
| -------------- | --------------------------------------- |
| **Browsers**   | Chrome, Firefox, WebKit ✅              |
| **Devices**    | Desktop, Tablet, Mobile ✅              |
| **Features**   | Content, Admin, API ✅                  |
| **Test Types** | Unit, Integration, E2E, A11y, Visual ✅ |
| **Reporting**  | HTML, JSON, JUnit, Markdown ✅          |
| **CI/CD**      | GitHub Actions, Jenkins, GitLab ✅      |
| **Debugging**  | Traces, Videos, Screenshots ✅          |

**Status: Production-Ready** ✅
