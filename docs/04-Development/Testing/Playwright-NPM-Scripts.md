# Recommended NPM Scripts for Testing

Add these scripts to your `package.json` for easy test execution:

```json
{
  "scripts": {
    "comments": "=== PLAYWRIGHT E2E TESTS ===",

    "test:e2e": "npx playwright test",
    "test:e2e:ui": "npx playwright test --ui",
    "test:e2e:debug": "npx playwright test --debug",
    "test:e2e:headed": "npx playwright test --headed",
    "test:e2e:report": "npx playwright show-report test-results/playwright",

    "comments2": "=== PUBLIC SITE TESTS (Port 3000) ===",

    "test:public": "npx playwright test",
    "test:public:chrome": "npx playwright test --project=chromium",
    "test:public:mobile": "npx playwright test --project='Pixel 5' --project='iPhone 12'",
    "test:public:a11y": "npx playwright test --project=chromium-axe",
    "test:public:visual": "npx playwright test --project=chromium-visual",

    "comments3": "=== ADMIN DASHBOARD TESTS (Port 3001) ===",

    "test:admin": "npx playwright test -c playwright.oversight.config.ts",
    "test:admin:ui": "npx playwright test -c playwright.oversight.config.ts --ui",
    "test:admin:debug": "npx playwright test -c playwright.oversight.config.ts --debug",
    "test:admin:report": "npx playwright show-report test-results/oversight",

    "comments4": "=== API TESTS (Port 8000) ===",

    "test:api": "npx playwright test -c playwright.api.config.ts",
    "test:api:smoke": "npx playwright test -c playwright.api.config.ts --project=api-smoke",
    "test:api:perf": "npx playwright test -c playwright.api.config.ts --project=api-performance",
    "test:api:security": "npx playwright test -c playwright.api.config.ts --project=api-security",
    "test:api:report": "npx playwright show-report test-results/api",

    "comments5": "=== ALL TESTS ===",

    "test:all": "npm run test:public && npm run test:admin && npm run test:api",
    "test:all:ci": "CI=1 npm run test:all",

    "comments6": "=== QUICK / SMOKE TESTS ===",

    "test:smoke": "npx playwright test --grep @smoke",
    "test:critical": "npx playwright test --grep @critical",
    "test:quick": "npx playwright test --project=chromium -c playwright.config.ts && npx playwright test -c playwright.oversight.config.ts && npx playwright test --project=api-smoke -c playwright.api.config.ts",

    "comments7": "=== TEST WITH OPTIONS ===",

    "test:watch": "npx playwright test --watch",
    "test:last-failed": "npx playwright test --last-failed",
    "test:verbose": "npx playwright test --reporter=verbose",
    "test:parallel": "npx playwright test --workers=8",
    "test:serial": "npx playwright test --workers=1",
    "test:no-server": "SKIP_SERVER_START=1 npx playwright test"
  }
}
```

## Quick Reference

### Most Common Commands

```bash
# Run all tests
npm run test:all

# Run public site only
npm run test:public

# Run admin dashboard only
npm run test:admin

# Run API tests only
npm run test:api

# Quick smoke tests
npm run test:smoke

# Debug mode with visual inspector
npm run test:e2e:debug

# UI mode (visual test runner)
npm run test:e2e:ui

# View test reports
npm run test:e2e:report
npm run test:admin:report
npm run test:api:report
```

### By Use Case

**Before Committing:**

```bash
npm run test:smoke     # Quick check
npm run test:critical  # Important features
```

**Local Development:**

```bash
npm run test:e2e:ui              # Visual test runner
npm run test:e2e:debug           # Debugger with inspector
npm run test:watch               # Re-run on changes
```

**Before Merge:**

```bash
npm run test:all       # Everything
npm run test:all:ci    # With CI settings
```

**Specific Areas:**

```bash
npm run test:public:a11y         # Accessibility
npm run test:public:mobile       # Mobile responsiveness
npm run test:admin:debug         # Admin workflows
npm run test:api:security        # API security
npm run test:api:perf            # API performance
```

## Installation Instructions

1. Open `package.json`
2. Find the `"scripts"` section
3. Add the scripts above (or just the ones you need)
4. Save and use: `npm run test:e2e`

## Notes

- Scripts use relative paths, so they work from any directory
- Some scripts use `-c` for config file selection
- Use `npm run test:all:ci` in CI/CD pipelines
- Install Playwright once: `npx playwright install`
- Update Playwright: `npm install -D @playwright/test@latest`
