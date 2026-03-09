# Integration Guide: Adding npm Scripts to package.json

**Status:** Ready to implement  
**Time Required:** 5 minutes  
**Difficulty:** Easy

---

## What to Do

Your npm scripts are documented in `PLAYWRIGHT_NPM_SCRIPTS.md`. This guide shows you how to add them to your actual `package.json` file.

---

## Step 1: Backup Current package.json

```bash
# Make a backup first (just in case)
cp package.json package.json.backup
```

---

## Step 2: Locate the Scripts Section

Open `package.json` and find the `"scripts"` section:

```json
{
  "name": "glad-labs-website",
  "version": "1.0.0",
  "scripts": {
    "dev": "..."
    // ADD NEW SCRIPTS HERE
  }
}
```

---

## Step 3: Add the New Scripts

Add all scripts from `PLAYWRIGHT_NPM_SCRIPTS.md` to the `"scripts"` section.

### Core Testing Commands (Add These First)

```json
"scripts": {
  "test": "jest --passWithNoTests",
  "test:watch": "jest --watch",
  "test:coverage": "jest --coverage",
  "test:ci": "jest --ci --coverage --maxWorkers=2",

  "test:e2e": "playwright test",
  "test:e2e:ui": "playwright test --ui",
  "test:e2e:debug": "playwright test --debug",
  "test:e2e:headed": "playwright test --headed",
  "test:e2e:report": "playwright show-report",
  "test:e2e:codegen": "playwright codegen http://localhost:3000",

  "test:public": "playwright test --config=playwright.config.ts",
  "test:public:chrome": "playwright test --config=playwright.config.ts --project=chromium",
  "test:public:mobile": "playwright test --config=playwright.config.ts --project='Pixel 5'",
  "test:public:headed": "playwright test --config=playwright.config.ts --headed",
  "test:public:debug": "playwright test --config=playwright.config.ts --debug",

  "test:admin": "playwright test --config=playwright.oversight.config.ts",
  "test:admin:headed": "playwright test --config=playwright.oversight.config.ts --headed",
  "test:admin:debug": "playwright test --config=playwright.oversight.config.ts --debug",
  "test:admin:auth-debug": "playwright test --config=playwright.oversight.config.ts --project=chromium --debug",

  "test:api": "playwright test --config=playwright.api.config.ts",
  "test:api:perf": "playwright test --config=playwright.api.config.ts --project=api-performance",
  "test:api:security": "playwright test --config=playwright.api.config.ts --project=api-security",
  "test:api:smoke": "playwright test --config=playwright.api.config.ts --project=api-smoke",

  "test:smoke": "playwright test -g @smoke",
  "test:critical": "playwright test -g @critical",
  "test:a11y": "playwright test --project='*-a11y'",
  "test:visual": "playwright test --project='*-visual'",

  "test:quick": "npm test && npm run test:smoke",
  "test:all": "npm test && npm run test:e2e",
  "test:all:ci": "npm run test:ci && npm run test:e2e",

  "test:results": "playwright show-report"
}
```

### Optional: Add More Variants

```json
"test:parallel": "playwright test --fully-parallel",
"test:serial": "playwright test --workers=1",
"test:last-failed": "playwright test --last-failed",
"test:verbose": "playwright test --reporter=verbose",
"test:json": "playwright test --reporter=json",
"test:junit": "playwright test --reporter=junit",
"test:update-snapshots": "playwright test --update-snapshots",
"test:update-visual": "playwright test --project='*-visual' --update-snapshots"
```

---

## Step 4: Verify Integration

After adding the scripts, verify they work:

```bash
# Test 1: Run Jest tests
npm test
# Should run all Jest tests (378 tests)

# Test 2: Check Playwright config
npm run test:public -- --list
# Should show tests from public site config

# Test 3: Verify admin config
npm run test:admin -- --list
# Should show tests from admin config

# Test 4: Verify API config
npm run test:api -- --list
# Should show tests from API config
```

---

## Step 5: Verify All Scripts Work

```bash
# Quick verification
npm run test:smoke -- --list
# Should list smoke-tagged tests

# Check what tests would run
npm run test:critical -- --list
# Should list tests marked @critical
```

---

## Expected Output

After running the verification commands, you should see:

```
✅ npm test                  # Jest runs 378 tests
✅ npm run test:public       # Playwright finds public tests
✅ npm run test:admin        # Playwright finds admin tests
✅ npm run test:api          # Playwright finds API tests
✅ npm run test:e2e:ui       # UI mode opens browser
✅ npm run test:results      # Report viewer works
```

---

## If Scripts Don't Work

### Issue: Command not found

```bash
# Ensure Playwright is installed
npm install --save-dev @playwright/test

# Ensure Jest is installed
npm install --save-dev jest
```

### Issue: Configs not found

```bash
# Verify config files exist
ls playwright.config.ts
ls playwright.oversight.config.ts
ls playwright.api.config.ts

# All three should exist in project root
```

### Issue: Tests directory not found

```bash
# Create directories if they don't exist
mkdir -p web/public-site/e2e
mkdir -p web/oversight-hub/e2e
mkdir -p playwright/api
```

---

## Team Usage Examples

After integration, your team can use:

```bash
# Developer checking their work
npm test                              # Run Jest
npm run test:public                   # Run public site tests

# Before commit
npm run test:quick                    # Fast smoke tests

# Full validation
npm run test:all                      # Everything

# CI/CD
npm run test:all:ci                   # CI mode

# Debug
npm run test:e2e:debug                # Debugger
npm run test:e2e:ui                   # Visual runner

# Review results
npm run test:results                  # Open report
```

---

## Complete package.json Scripts Section

Here's the complete scripts section you should have:

```json
{
  "scripts": {
    "dev": "concurrently \"npm run dev:cofounder\" \"npm run dev:public\" \"npm run dev:oversight\"",
    "dev:cofounder": "...",
    "dev:public": "...",
    "dev:oversight": "...",

    "test": "jest --passWithNoTests",
    "test:watch": "jest --watch",
    "test:coverage": "jest --coverage",
    "test:ci": "jest --ci --coverage --maxWorkers=2",

    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui",
    "test:e2e:debug": "playwright test --debug",
    "test:e2e:headed": "playwright test --headed",
    "test:e2e:report": "playwright show-report",
    "test:e2e:codegen": "playwright codegen http://localhost:3000",

    "test:public": "playwright test --config=playwright.config.ts",
    "test:public:chrome": "playwright test --config=playwright.config.ts --project=chromium",
    "test:public:mobile": "playwright test --config=playwright.config.ts --project='Pixel 5'",
    "test:public:headed": "playwright test --config=playwright.config.ts --headed",
    "test:public:debug": "playwright test --config=playwright.config.ts --debug",

    "test:admin": "playwright test --config=playwright.oversight.config.ts",
    "test:admin:headed": "playwright test --config=playwright.oversight.config.ts --headed",
    "test:admin:debug": "playwright test --config=playwright.oversight.config.ts --debug",

    "test:api": "playwright test --config=playwright.api.config.ts",
    "test:api:perf": "playwright test --config=playwright.api.config.ts --project=api-performance",
    "test:api:security": "playwright test --config=playwright.api.config.ts --project=api-security",
    "test:api:smoke": "playwright test --config=playwright.api.config.ts --project=api-smoke",

    "test:smoke": "playwright test -g @smoke",
    "test:critical": "playwright test -g @critical",
    "test:a11y": "playwright test --project='*-a11y'",
    "test:visual": "playwright test --project='*-visual'",

    "test:quick": "npm test && npm run test:smoke",
    "test:all": "npm test && npm run test:e2e",
    "test:all:ci": "npm run test:ci && npm run test:e2e",

    "test:results": "playwright show-report",

    "build": "...",
    "format": "..."
  }
}
```

---

## Quick Reference After Integration

| Command                | Purpose            | Time        |
| ---------------------- | ------------------ | ----------- |
| `npm test`             | All Jest tests     | 2 min       |
| `npm run test:quick`   | Jest + smoke tests | 3 min       |
| `npm run test:public`  | Public site E2E    | 3 min       |
| `npm run test:admin`   | Admin E2E          | 2 min       |
| `npm run test:api`     | API E2E            | 1 min       |
| `npm run test:e2e`     | All E2E            | 6 min       |
| `npm run test:all`     | Full suite         | 8 min       |
| `npm run test:e2e:ui`  | Visual runner      | Interactive |
| `npm run test:results` | View HTML report   | Browser     |

---

## Next Steps

✅ **Completed:**

- playwright.config.ts (public site) - Ready
- playwright.oversight.config.ts (admin) - Ready
- playwright.api.config.ts (API) - Ready
- PLAYWRIGHT_GUIDE.md - Ready
- PLAYWRIGHT_NPM_SCRIPTS.md - Ready

⏳ **To Complete (Now):**

- Add npm scripts to package.json ← **You are here**

📋 **To Complete (Later):**

- Create global setup/teardown files
- Create example E2E test files
- Create page objects
- Set up GitHub Actions workflow

---

## Verification Checklist

After adding scripts to package.json:

- [ ] Save package.json
- [ ] Run `npm test` - see 378 Jest tests
- [ ] Run `npm run test:public -- --list` - see public tests
- [ ] Run `npm run test:admin -- --list` - see admin tests
- [ ] Run `npm run test:api -- --list` - see API tests
- [ ] Run `npm run test:e2e:ui` - see visual runner
- [ ] Run `npm run test:results` - see report viewer
- [ ] Share new scripts with team

**Once verified, you're ready to start implementing E2E tests!**

---

## Support

If you need help with:

- **Playwright syntax:** See PLAYWRIGHT_GUIDE.md
- **npm scripts:** See PLAYWRIGHT_NPM_SCRIPTS.md
- **Test architecture:** See TESTING_ARCHITECTURE.md
- **Overall testing:** See TESTING_QUICK_START.md

All files are in your project root directory.
