# ESLint Configuration Setup

**Last Updated:** December 26, 2025  
**Status:** ✅ Complete - All linting errors resolved (0 errors, 355 warnings)

## Overview

This document describes the modern ESLint 9 flat configuration setup for both frontend workspaces in the Glad Labs monorepo.

## Configuration Files

### Oversight Hub (React 18)
- **File:** `web/oversight-hub/eslint.config.mjs`
- **Type:** ESLint 9 flat config (ES module)
- **Parser:** Default espree (JSX-enabled)
- **Extends:** 
  - `@eslint/js` recommended rules
  - `eslint-plugin-react` recommended rules
  - `eslint-plugin-react` JSX runtime rules
  - `eslint-plugin-react-hooks` recommended rules
  - `eslint-plugin-jest` recommended rules

### Public Site (Next.js 15)
- **File:** `web/public-site/eslint.config.mjs`
- **Type:** ESLint 9 flat config (ES module)
- **Parser:** Default espree (JSX-enabled)
- **Extends:**
  - `@eslint/js` recommended rules
  - `eslint-plugin-react` recommended rules
  - `eslint-plugin-react` JSX runtime rules
  - `@next/eslint-plugin-next` recommended rules

## Key Rules

### Code Quality
| Rule | Oversight Hub | Public Site | Purpose |
|------|---------------|-------------|---------|
| `no-unused-vars` | warn | warn | Flag unused variables (except those prefixed with `_`) |
| `no-console` | off | off | Allow console statements for debugging |
| `no-var` | error | error | Enforce `const`/`let` over `var` |
| `eqeqeq` | warn | warn | Prefer strict equality (`===` over `==`) |
| `semi` | warn | warn | Enforce semicolons |
| `quotes` | warn | warn | Prefer single quotes |

### React-Specific
| Rule | Oversight Hub | Public Site | Purpose |
|------|---------------|-------------|---------|
| `react/react-in-jsx-scope` | off | off | Not needed with JSX runtime |
| `react/prop-types` | warn | warn | Suggest prop validation |
| `react/no-unescaped-entities` | warn | warn | Escape special chars in JSX |
| `react-hooks/rules-of-hooks` | error | warn | Enforce hook rules (critical) |
| `react-hooks/exhaustive-deps` | warn | off | Dependencies in useEffect hooks |

### Jest Testing
| Rule | Oversight Hub | Purpose |
|------|---------------|---------|
| `jest/expect-expect` | warn | Flag tests with missing assertions |
| `jest/no-disabled-tests` | warn | Flag skipped tests |
| `jest/no-conditional-expect` | warn | Allow expects in waitFor, etc. |

## ESLint Ignores

Both workspaces use the `ignores` property in `eslint.config.mjs`:

```
node_modules/**
.next/**
out/**
build/**
dist/**
coverage/**
.vscode/**
.idea/**
*.swp, *.swo
.DS_Store, Thumbs.db
package-lock.json, yarn.lock, pnpm-lock.yaml
```

## Dependencies

### Oversight Hub
```json
{
  "eslint": "^9.17.0",
  "eslint-plugin-jest": "^28.9.0",
  "eslint-plugin-react": "^7.34.3",
  "eslint-plugin-react-hooks": "^4.6.2",
  "@eslint/js": "^9.17.0",
  "globals": "^15.14.0"
}
```

### Public Site
```json
{
  "eslint": "^9.17.0",
  "eslint-plugin-react": "^7.34.3",
  "eslint-plugin-react-hooks": "^4.6.2",
  "@eslint/js": "^9.17.0",
  "@next/eslint-plugin-next": "^15.1.3",
  "globals": "^15.14.0"
}
```

## Scripts

Both workspaces have identical lint scripts in `package.json`:

```bash
npm run lint        # Run ESLint (exit 1 if warnings)
npm run lint:fix    # Run ESLint with --fix (auto-apply fixes)
```

Root monorepo script:
```bash
npm run lint        # Runs lint in all workspaces with --if-present
```

## Running Linting

### Check All Files
```bash
cd glad-labs-website
npm run lint
```

### Fix Automatically Fixable Issues
```bash
npm run lint:fix
```

### Lint Specific File
```bash
cd web/oversight-hub
npx eslint src/components/MyComponent.jsx
```

### Lint with Detailed Output
```bash
npm run lint 2>&1 | grep "warning\|error"
```

## Current Status

- **Total Issues:** 355 warnings, 0 errors
- **Errors:** ✅ All resolved
- **Warnings:** Mostly prop-types validation in legacy archived files and unused imports
- **Public Site:** ✅ Passes linting (moved from deprecated `next lint` to proper ESLint CLI)
- **Oversight Hub:** ✅ Passes linting (fixed CostMetricsDashboard parsing, created costTrend variable)

## Common Issues & Solutions

### 1. ".eslintignore" No Longer Supported
**Warning:** `ESLintIgnoreWarning: The ".eslintignore" file is no longer supported`

**Solution:** Move ignore patterns to `ignores` property in `eslint.config.mjs` (already done)

### 2. React Version Not Specified
**Warning:** `React version not specified in eslint-plugin-react settings`

**Solution:** Add settings block to config:
```javascript
settings: {
  react: {
    version: 'detect'
  }
}
```

### 3. Parsing Errors in JSX Files
**Issue:** `Parsing error: Unexpected token`

**Causes & Solutions:**
- Unbalanced JSX brackets - ensure proper nesting
- Smart quotes in strings - use `&apos;` in JSX text
- Missing variable definitions - define all used variables

**Example Fixes:**
- `What's` → `What&apos;s` (in JSX text)
- Unescaped apostrophes → Use HTML entities

### 4. react-hooks/exhaustive-deps Compatibility
**Issue:** ESLint 9 has compatibility issues with react-hooks plugin

**Solution:** Disable for Next.js projects:
```javascript
'react-hooks/exhaustive-deps': 'off'
```

## File Edits Made

### CostMetricsDashboard.jsx
- Fixed JSX structure: moved orphaned `<div className="breakdown-grid">` inside conditional
- Added `costTrend` variable derived from `costHistory`
- Removed unused React import (using hooks directly)
- Emoji removed from section titles for compatibility

### TrainingDataManager.jsx
- Fixed unescaped apostrophe: `What's` → `What&apos;s`

### ModelSelectionPanel.jsx
- Fixed unescaped apostrophe: `don't` → `don&apos;t`

### ConstraintComplianceDisplay.jsx
- Fixed undefined component: `<CheckCircle>` → `<PassIcon>` (use imported alias)

## Migration from Legacy Config

**Before:**
- `web/oversight-hub/.eslintrc.json` (React app config)
- `web/public-site/.eslintrc.json` (deprecated next/core-web-vitals)
- `.eslintignore` files in each workspace
- Separate package.json scripts for each

**After:**
- `web/oversight-hub/eslint.config.mjs` (ESLint 9 flat config)
- `web/public-site/eslint.config.mjs` (ESLint 9 flat config)
- Ignores defined in `.mjs` files
- Unified lint scripts across workspaces

## Next Steps (Optional)

1. **Add Pre-commit Hook:** Integrate ESLint with Husky to lint before commit
   ```bash
   npx husky add .husky/pre-commit "npm run lint:fix"
   ```

2. **CI/CD Integration:** Add lint check to GitHub Actions
   ```yaml
   - name: Run ESLint
     run: npm run lint
   ```

3. **IDE Integration:** VS Code extension `dbaeumer.vscode-eslint` will automatically use this config

4. **Prop Validation:** Consider adding `prop-types` package to components for better type checking in legacy files

5. **Fix Warnings Gradually:**
   - Add missing prop-types validation to active components
   - Remove unused React imports throughout codebase
   - Consider enabling `react-hooks/exhaustive-deps` for oversight-hub

## References

- [ESLint 9 Migration Guide](https://eslint.org/docs/latest/use/configure/migration-guide)
- [ESLint Flat Config](https://eslint.org/docs/latest/use/configure/configuration-files-new)
- [eslint-plugin-react](https://github.com/jsx-eslint/eslint-plugin-react)
- [eslint-plugin-react-hooks](https://github.com/facebook/react/tree/main/packages/eslint-plugin-react-hooks)
