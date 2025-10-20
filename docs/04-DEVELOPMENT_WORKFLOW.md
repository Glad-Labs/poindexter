# 04 - Development Workflow & Git Strategy

**Role**: All Developers, DevOps  
**Reading Time**: 15-20 minutes  
**Last Updated**: October 18, 2025

---

## 🚀 Quick Navigation

- **[← Back to Docs](./00-README.md)** | **[↑ Setup](./01-SETUP_AND_OVERVIEW.md)** | **[↑ Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** | **Next: [AI Agents](./05-AI_AGENTS_AND_INTEGRATION.md) →**

---

## Overview

This document covers the daily development workflow, git strategy, npm scripts, testing, and code quality standards for GLAD Labs. All developers follow this workflow to maintain consistency and prevent conflicts.

---

## 📋 Table of Contents

1. [Development Setup](#development-setup)
2. [Git Workflow](#git-workflow)
3. [NPM Scripts Reference](#npm-scripts-reference)
4. [Code Quality](#code-quality)
5. [Testing Strategy](#testing-strategy)
6. [Debugging](#debugging)
7. [Common Tasks](#common-tasks)

---

## Development Setup

### Initial Clone & Setup

```bash
# Clone repository
git clone https://github.com/mattg-stack/glad-labs-website.git
cd glad-labs-website

# Install dependencies
npm install

# Setup git hooks (pre-commit linting)
npm run prepare

# Create feature branch
git checkout -b feature/my-feature
```

### Start Development Servers

#### Option 1: Start All Services (Recommended)

```bash
# Terminal 1: Start all services with one command
npm run start:all

# This starts:
# - Strapi CMS (http://localhost:1337)
# - Oversight Hub (http://localhost:3000)
# - Public Site (http://localhost:3001)
# - Intervene Trigger (background)
# - Co-founder Agent (http://localhost:8000)
```

#### Option 2: Start Individual Services

```bash
# Terminal 1: CMS
cd cms/strapi-main
npm run develop

# Terminal 2: Frontend
cd web/public-site
npm run dev

# Terminal 3: React App
cd web/oversight-hub
npm start

# Terminal 4: AI Agent
cd src/cofounder_agent
npm run server
```

### Verify All Services Running

```bash
# Quick health check
curl http://localhost:1337/admin
curl http://localhost:3000
curl http://localhost:3001
curl http://localhost:8000/docs

# All should return success (200)
```

---

## Git Workflow

### Branch Strategy

We use **Git Flow** for branch management:

```
main (production-ready)
├── develop (integration branch)
│   ├── feature/feature-name (feature development)
│   ├── bugfix/issue-name (bug fixes)
│   ├── hotfix/critical-issue (production fixes)
│   └── release/v1.0.0 (release preparation)
```

### Creating a Feature

```bash
# 1. Update develop
git checkout develop
git pull origin develop

# 2. Create feature branch
git checkout -b feature/user-authentication
# or
git checkout -b bugfix/strapi-api-error
# or
git checkout -b hotfix/critical-security-issue

# 3. Work on feature
# ... make changes ...

# 4. Commit regularly
git add .
git commit -m "Add login form component"

# 5. Push to remote
git push origin feature/user-authentication
```

### Commit Message Convention

Follow **Conventional Commits**:

```
<type>(<scope>): <subject>

<body>

<footer>
```

#### Types

- **feat**: New feature
- **fix**: Bug fix
- **docs**: Documentation
- **style**: Code style (formatting, missing semicolons, etc.)
- **refactor**: Code refactoring
- **test**: Adding/updating tests
- **chore**: Dependencies, build tools, etc.

#### Examples

```bash
# Feature commit
git commit -m "feat(auth): add JWT token refresh mechanism"

# Bug fix
git commit -m "fix(strapi): resolve null pointer in api resolver"

# Documentation
git commit -m "docs(setup): update installation instructions for Windows"

# Multiple changes
git commit -m "feat(dashboard): add analytics widget

- Integrated chart library
- Added real-time data streaming
- Updated layout responsive design

Closes #123"
```

### Pull Request Workflow

```bash
# 1. Push feature branch
git push origin feature/my-feature

# 2. Create Pull Request on GitHub
# - Add description
# - Link to issues
# - Request reviewers

# 3. Address review comments
git add .
git commit -m "Address review feedback"
git push origin feature/my-feature

# 4. Merge (after approval)
# GitHub: Click "Squash and merge"
```

### Common Git Tasks

#### Check Status

```bash
git status
# Shows modified, staged, untracked files
```

#### Undo Changes

```bash
# Discard local changes (WARNING: permanent)
git checkout -- .

# Undo last commit (keep changes)
git reset --soft HEAD~1

# Undo last commit (discard changes)
git reset --hard HEAD~1

# Undo pushed commit (creates reverse commit)
git revert HEAD
git push origin develop
```

#### Stash Changes

```bash
# Save changes temporarily
git stash

# List stashed changes
git stash list

# Restore stashed changes
git stash pop

# Discard stashed changes
git stash drop
```

#### Merge Conflicts

```bash
# When merging causes conflicts:

# 1. See conflicted files
git status

# 2. Open file and resolve conflicts
# Look for markers:
# <<<<<<<< HEAD
# Your changes
# ========
# Their changes
# >>>>>>>> branch-name

# 3. Remove conflict markers and keep desired code

# 4. Stage resolved files
git add .

# 5. Complete merge
git commit -m "Resolve merge conflicts"
```

---

## NPM Scripts Reference

### Core Commands

```bash
# Install dependencies (run after git pull)
npm install

# Start all services
npm run start:all

# Start individual service
cd cms/strapi-main && npm run develop

# Build for production
npm run build

# Run tests
npm test

# Run linting
npm run lint

# Fix linting issues
npm run lint:fix
```

### Available Scripts by Service

#### Root Workspace

```bash
npm run start:all          # Start all services
npm run test              # Run all tests
npm run lint              # Lint all code
npm run lint:fix          # Fix linting issues
npm run build             # Build all services
```

#### CMS (Strapi)

```bash
cd cms/strapi-main

npm run develop           # Start development server (watch mode)
npm run build             # Build for production
npm start                 # Start production server
npm run seed             # Seed initial data
npm run seed:prod        # Seed production database
npm test                 # Run tests
```

#### Public Site (Next.js)

```bash
cd web/public-site

npm run dev              # Start dev server (http://localhost:3000)
npm run build            # Build for production
npm start                # Start production server
npm run lint             # Lint code
npm test                 # Run tests
```

#### Oversight Hub (React)

```bash
cd web/oversight-hub

npm start                # Start dev server (http://localhost:3000)
npm run build            # Build for production
npm test                 # Run tests
npm run eject            # Eject from CRA (⚠️ irreversible)
```

#### AI Agent

```bash
cd src/cofounder_agent

python -m uvicorn cofounder_agent.main:app --reload
# or
npm run server           # If wrapped in npm
```

### Health Check Scripts

```bash
# Check if all npm packages are installed correctly
npm ls

# Check for outdated packages
npm outdated

# Check for security vulnerabilities
npm audit

# Fix security vulnerabilities
npm audit fix
```

---

## Code Quality

### Linting

#### ESLint Configuration

```bash
# Lint all JavaScript/TypeScript
npm run lint

# Fix automatically fixable issues
npm run lint:fix

# Lint specific directory
npx eslint src/

# Lint and fix
npx eslint src/ --fix
```

#### Common ESLint Issues

```
error  Unexpected var, use let or const instead  (no-var)
  ✅ Fix: Use let/const

error  Unused variable  (no-unused-vars)
  ✅ Fix: Delete unused variable or add to .eslintignore

error  Missing semicolon  (semi)
  ✅ Fix: Add semicolon or run --fix
```

### Code Formatting

#### Prettier

```bash
# Format code
npx prettier --write .

# Check formatting
npx prettier --check .

# Format specific file
npx prettier --write src/components/Button.tsx
```

### Pre-commit Hooks

```bash
# Husky runs linting before commits
# If lint fails, commit is blocked

# To bypass (NOT recommended):
git commit --no-verify
```

---

## Testing Strategy

### Unit Tests

#### Run Tests

```bash
npm test

# Watch mode (auto-rerun on changes)
npm test -- --watch

# Coverage report
npm test -- --coverage
```

#### Write Tests

```javascript
// Example: src/lib/math.test.ts
import { add } from './math';

describe('add', () => {
  it('adds two numbers', () => {
    expect(add(2, 3)).toBe(5);
  });

  it('handles negative numbers', () => {
    expect(add(-2, 3)).toBe(1);
  });
});
```

### E2E Tests

#### Cypress

```bash
# Open Cypress
npm run cy:open

# Run Cypress headless
npm run cy:run

# Run specific spec
npx cypress run --spec "cypress/e2e/login.cy.js"
```

#### Writing E2E Tests

```javascript
// Example: cypress/e2e/login.cy.js
describe('User Login', () => {
  it('logs in successfully', () => {
    cy.visit('/login');
    cy.get('input[name="email"]').type('user@example.com');
    cy.get('input[name="password"]').type('password123');
    cy.get('button[type="submit"]').click();
    cy.url().should('include', '/dashboard');
  });
});
```

### Testing Checklist

- [ ] Unit tests for business logic
- [ ] Component tests for UI
- [ ] E2E tests for user flows
- [ ] All tests passing before PR
- [ ] Coverage above 80% (goal)

---

## Debugging

### Browser DevTools

```
F12 or Ctrl+Shift+I → Open Developer Tools

Tabs:
- Elements: Inspect HTML/CSS
- Console: View logs and errors
- Network: Monitor API calls
- Performance: Profile performance
- Storage: View localStorage, cookies
```

### Debug API Calls

```javascript
// 1. Open Network tab (F12 → Network)
// 2. Make API call
// 3. Click request to see:
//    - Request headers
//    - Request body
//    - Response
//    - Status code (200, 400, 500, etc.)

// Common status codes:
// 200 = Success
// 400 = Bad request (check params)
// 401 = Unauthorized (check auth token)
// 404 = Not found (check URL)
// 500 = Server error (check server logs)
```

### Debug Backend

```bash
# 1. Add console.log statements
console.log('Value:', myVar);

# 2. Check logs in terminal
# Look for your output

# 3. Use debugger
node --inspect cms/strapi-main/server.js
# Then open chrome://inspect in browser
```

### Debug GraphQL

```bash
# Open GraphQL Playground
curl http://localhost:1337/graphql

# In browser (if available):
http://localhost:1337/admin/graphql

# Test query:
query {
  articles {
    id
    title
  }
}
```

---

## Common Tasks

### Adding a New Package

```bash
# Install package
npm install some-package

# Install dev dependency
npm install --save-dev @types/some-package

# Install globally (rare)
npm install -g nodemon

# Install from GitHub
npm install github:user/repo#branch
```

### Updating Dependencies

```bash
# Check outdated
npm outdated

# Update all packages
npm update

# Update specific package
npm update some-package

# Upgrade to latest major version
npm install some-package@latest
```

### Creating a New Component

```bash
# 1. Create component file
touch src/components/MyComponent.tsx

# 2. Write component
# 3. Create test file
touch src/components/MyComponent.test.tsx

# 4. Write tests
# 5. Add to exports
# 6. Use in app

# 7. Run tests
npm test

# 8. Commit
git add .
git commit -m "feat: add MyComponent"
```

### Debugging Package Issues

```bash
# Clear cache
npm cache clean --force

# Reinstall everything
rm -rf node_modules package-lock.json
npm install

# Check for dependency conflicts
npm ls

# Show why a package is installed
npm ls some-package
```

### Hot Reload Not Working?

```bash
# 1. Check process is running
ps aux | grep node

# 2. Restart service
npm run develop

# 3. Check file watching
# Some systems need manual config

# On Windows (if needed):
npm run dev -- --poll=1000
```

---

## Best Practices

### ✅ Do

- [ ] Create feature branches for all work
- [ ] Write tests for new code
- [ ] Run lint before committing
- [ ] Write clear commit messages
- [ ] Keep commits small and focused
- [ ] Review your own code first
- [ ] Request reviews from team
- [ ] Update documentation
- [ ] Test locally before pushing
- [ ] Pull latest before starting work

### ❌ Don't

- [ ] Commit directly to main
- [ ] Push without testing
- [ ] Leave console.log in code
- [ ] Commit secrets/passwords
- [ ] Ignore linting warnings
- [ ] Make huge commits
- [ ] Rewrite history (git reset --hard)
- [ ] Leave unfinished work uncommitted
- [ ] Skip testing

---

## Next Steps

1. **[← Back to Documentation](./00-README.md)**
2. **Read**: [05 - AI Agents & Integration](./05-AI_AGENTS_AND_INTEGRATION.md)
3. **Practice**: Create a feature branch and make a commit
4. **Review**: Check git history: `git log --oneline -10`

---

**Last Updated**: October 18, 2025 | **Version**: 1.0