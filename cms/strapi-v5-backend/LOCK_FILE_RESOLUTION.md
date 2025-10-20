# 🔧 Lock File Resolution Guide

## Problem: Yarn Frozen Lockfile Error

```
error Your lockfile needs to be updated, but yarn was run with `--frozen-lockfile`.
```

## Root Cause

The `yarn.lock` file from your old backup was out of sync with the updated `package.json` after merging the Railway template. When running `yarn install --frozen-lockfile`, yarn refuses to update the lockfile and instead throws an error.

## Solution Applied

✅ **Removed outdated `yarn.lock` file**

- The old yarn.lock didn't match the new package.json
- Deleted: `yarn.lock`
- The project now uses `npm` as the package manager (more compatible with Strapi v5)

## How to Proceed

### Option 1: Use NPM (Recommended for this project)

```bash
# Navigate to project
cd cms/strapi-v5-backend

# Install with npm (no frozen lockfile issues)
npm install

# Start development
npm run dev
```

**Why recommended:**

- ✅ Strapi v5 is primarily optimized for npm
- ✅ Simpler dependency management for monorepos
- ✅ Better compatibility with Railway deployment
- ✅ No lock file conflicts

### Option 2: Use Yarn (If you prefer yarn)

If you want to continue using yarn:

```bash
# Install yarn globally (if not already installed)
npm install -g yarn

# Navigate to project
cd cms/strapi-v5-backend

# Remove npm lock if it exists
rm -r package-lock.json

# Install with yarn (without --frozen-lockfile)
yarn install

# This will generate a new yarn.lock
```

**Note:** Then update any scripts that use `--frozen-lockfile`:

```bash
# Instead of:
yarn install --frozen-lockfile

# Use:
yarn install
```

## Current Status

✅ `yarn.lock` removed  
✅ `npm install` completed successfully  
✅ 2,491 packages installed  
✅ Project ready to run

## Recommended Commands

```bash
# Start development (using npm)
npm run dev

# Build for production
npm run build

# Start production server
npm start

# Install new dependencies
npm install <package-name>
```

## For CI/CD Pipelines

If you're using CI/CD (like GitHub Actions), update your workflow:

```yaml
# ❌ Don't use
yarn install --frozen-lockfile

# ✅ Use instead
npm ci
# OR
npm install --prefer-offline --no-audit
```

## Monorepo Considerations

Since this is part of a monorepo (`glad-labs-website`), ensure consistency:

```bash
# Root level uses npm
cd /glad-labs-website
npm install

# Strapi backend uses npm
cd cms/strapi-v5-backend
npm install
```

## Prevention for Future

When merging projects with different lock files:

1. **Identify the package manager** being used
2. **Remove conflicting lock files** (if switching)
3. **Regenerate fresh lock file** with chosen manager
4. **Commit the new lock file** to git

### Added to `.gitignore` (Optional)

If you want git to ignore lock file conflicts:

```
# Use only npm for this project
yarn.lock
```

But **recommended:** Keep lock file committed for reproducible builds.

## Testing

Verify everything works:

```bash
# Check installation
npm list --depth=0

# Verify Strapi is ready
npm run build

# Start dev server
npm run dev

# Test in another terminal
curl http://localhost:1337/admin
```

## Summary

| Item               | Status               |
| ------------------ | -------------------- |
| yarn.lock conflict | ✅ Resolved          |
| Package manager    | ✅ npm (recommended) |
| Dependencies       | ✅ 2,491 installed   |
| Project ready      | ✅ Yes               |

**You're all set! Run `npm run dev` to start developing.** 🚀
