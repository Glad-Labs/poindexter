# npm-run-all: Configuration Guide

**Purpose:** Run multiple npm scripts in parallel or series  
**Current Usage:** Your `dev`, `dev:frontend`, and `dev:full` scripts use this  
**Date:** October 30, 2025

---

## 📚 What is npm-run-all?

`npm-run-all` (also known as `npm-run-all2`) is a CLI tool that allows you to run multiple npm scripts in different ways:

- **`--parallel`**: Run all scripts at the same time (concurrent)
- **`--serial`**: Run scripts one after another (sequential)
- Mix both: Run some in series, some in parallel

**Current Installation:**

```json
"devDependencies": {
  "npm-run-all": "^4.1.5"
}
```

---

## 🎯 Your Current Configuration

### Current Scripts

```json
"dev": "npx npm-run-all --parallel dev:frontend",
"dev:frontend": "npx npm-run-all --parallel dev:public dev:oversight",
"dev:full": "npx npm-run-all --parallel dev:*",
"dev:strapi": "npm run develop --workspace=cms/strapi-main",
"dev:oversight": "npm start --workspace=web/oversight-hub",
"dev:public": "npm run dev --workspace=web/public-site",
"dev:cofounder": "python src/cofounder_agent/start_server.py"
```

### What Each Does

| Command                | Runs                           | Concurrency                           |
| ---------------------- | ------------------------------ | ------------------------------------- |
| `npm run dev`          | `dev:public` + `dev:oversight` | **Parallel** (both at once)           |
| `npm run dev:full`     | All `dev:*` scripts            | **Parallel** (all at once - no order) |
| `npm run dev:frontend` | `dev:public` + `dev:oversight` | **Parallel**                          |

---

## ✅ Configure Backend-First Execution

### Option 1: Sequential (Recommended for Startup)

Run backend services **first**, then frontend:

```json
"dev:ordered": "npx npm-run-all --serial dev:cofounder dev:strapi dev:frontend"
```

**Behavior:**

1. Starts Co-Founder Agent (port 8000)
2. Waits until it's ready
3. Starts Strapi CMS (port 1337)
4. Waits until it's ready
5. Starts Public Site + Oversight Hub in parallel

**Issue:** Services might not fully start before next one begins. Better approach below.

### Option 2: Mixed Serial + Parallel (Best Practice) ⭐

```json
"dev:backends": "npx npm-run-all --parallel dev:cofounder dev:strapi",
"dev:backends-then-frontend": "npx npm-run-all --serial dev:backends dev:frontend"
```

**Behavior:**

1. Starts Co-Founder Agent + Strapi together (parallel)
2. Waits for both to be ready
3. Then starts Public Site + Oversight Hub (parallel)

**Better yet, with timeout safety:**

```json
"dev:ordered": "npx wait-on http://localhost:8000 http://localhost:1337 && npm run dev:frontend"
```

This waits for services to be actually responsive before starting frontend.

### Option 3: Custom Startup Script (Most Control)

Create a new npm script that uses conditional logic:

```json
"dev:smartstart": "npx npm-run-all --parallel dev:cofounder dev:strapi && npx wait-on http://localhost:8000 http://localhost:1337 && npx npm-run-all --parallel dev:public dev:oversight"
```

---

## 🔧 Recommended Updated package.json

Here's what I'd suggest for your `package.json` scripts section:

```json
"scripts": {
  "//": "--- DEVELOPMENT (RECOMMENDED: Use this for local development) ---",
  "dev": "npx npm-run-all --parallel dev:frontend",
  "dev:frontend": "npx npm-run-all --parallel dev:public dev:oversight",
  "dev:backends": "npx npm-run-all --parallel dev:cofounder dev:strapi",
  "dev:backends-first": "npx npm-run-all --serial dev:backends dev:frontend",
  "dev:full": "npx npm-run-all --parallel dev:*",
  "dev:ordered": "npx wait-on http://localhost:8000 http://localhost:1337 && npm run dev:frontend",
  "dev:strapi": "npm run develop --workspace=cms/strapi-main",
  "dev:oversight": "npm start --workspace=web/oversight-hub",
  "dev:public": "npm run dev --workspace=web/public-site",
  "dev:cofounder": "python src/cofounder_agent/start_server.py",
  "//": "--- BACKEND SERVICES (Run manually if needed) ---",
  "start:backends": "npx npm-run-all --parallel dev:cofounder dev:strapi",
  "start:backend-cofounder": "npm run dev:cofounder",
  "start:backend-strapi": "npm run dev:strapi"
}
```

---

## 🎯 Usage Examples

### Start Everything (All at Once)

```bash
npm run dev:full
# Starts: Cofounder Agent + Strapi + Public Site + Oversight Hub (all parallel)
```

### Start Backend First, Then Frontend ⭐

```bash
npm run dev:backends-first
# 1. Starts Cofounder Agent + Strapi (parallel)
# 2. Waits for both to start
# 3. Then starts Public Site + Oversight Hub (parallel)
```

### Start Frontend Only (Quick Development)

```bash
npm run dev
# Starts: Public Site + Oversight Hub (assumes backends already running)
```

### Start Backends Only

```bash
npm run start:backends
# Starts: Cofounder Agent + Strapi (parallel, no frontend)
```

### Wait for Backends to be Ready

```bash
npm run dev:ordered
# Starts backends, waits for them to respond, then starts frontend
```

---

## 📊 Comparison: Parallel vs Serial

### Parallel (Current)

```
Time: 0s         3s                    10s
      |----------|---------------------|
      Start      Cofounder Agent       Frontend Done
                 + Strapi ready

All services compete for resources. Can start faster but backend might
not be ready when frontend tries to connect.
```

### Serial (Backend First)

```
Time: 0s    5s         8s         15s
      |-----|----------|----------|
      Start  Cofounder  Strapi     Frontend
             + Strapi   ready      ready

Each service waits for previous to start. Slower startup but
guaranteed order. Good for initial setup.
```

### Mixed (Recommended)

```
Time: 0s    5s              8s         15s
      |-----|--------------|----------|
      Start  Cofounder +    Frontend   All done
             Strapi ready   ready

Backends run in parallel, frontend waits for both.
Best of both worlds: Fast AND guaranteed ready.
```

---

## 🛠️ Advanced Configuration

### Run with Specific Services Only

```bash
# Just backend
npx npm-run-all --parallel dev:cofounder dev:strapi

# Just frontend
npx npm-run-all --parallel dev:public dev:oversight

# Strapi only (useful for CMS-only work)
npm run dev:strapi

# Cofounder Agent only (useful for API-only work)
npm run dev:cofounder
```

### Wait for Services to be Ready

You already have `wait-on` in devDependencies. Use it:

```json
"dev:wait-for-backends": "npx npm-run-all --parallel dev:cofounder dev:strapi & npx wait-on http://localhost:8000/docs http://localhost:1337/admin && npm run dev:frontend"
```

This starts backends and frontend, but frontend waits for both backends to respond.

### Add Health Checks

```json
"dev:healthy-start": "npm run dev:backends && npx wait-on --timeout 30000 http://localhost:8000/api/health http://localhost:1337/admin && npm run dev:frontend"
```

Waits up to 30 seconds for health check endpoints to respond.

---

## 🚀 My Recommendation

For your project, I'd suggest using this updated setup:

```json
"dev": "npm run dev:backends-first",
"dev:backends-first": "npx npm-run-all --serial dev:backends dev:frontend",
"dev:backends": "npx npm-run-all --parallel dev:cofounder dev:strapi",
"dev:frontend": "npx npm-run-all --parallel dev:public dev:oversight",
"dev:full": "npx npm-run-all --parallel dev:*"
```

Then:

```bash
npm run dev              # Start everything backend-first (best for full startup)
npm run dev:backends    # Just backends (quick for API-only work)
npm run dev:frontend    # Just frontend (quick if backends running)
npm run dev:full        # All parallel (fastest but no guarantee of order)
```

---

## 🔍 Syntax Reference

### Basic Syntax

```bash
# Run in parallel (all at once)
npx npm-run-all --parallel task1 task2 task3

# Run in series (one after another)
npx npm-run-all --serial task1 task2 task3

# Mix: Run task1+task2 parallel, then task3
npx npm-run-all --serial task1 task2 --then task3

# Run all tasks matching pattern
npx npm-run-all --parallel dev:*

# Short flags
npx npm-run-all -p task1 task2     # --parallel
npx npm-run-all -s task1 task2     # --serial
```

### Common Patterns

```bash
# Start service, wait for it, then start frontend
npx npm-run-all --serial service1 --then service2

# Parallel, with output prefix
npx npm-run-all --parallel --prefix-all "prefix" task1 task2

# Continue even if one fails
npx npm-run-all --continue-on-error task1 task2

# Print output from each task
npx npm-run-all --print-label task1 task2
```

---

## ⚠️ Important Notes

### Service Startup Order Matters

**Recommended order for your project:**

1. **Co-Founder Agent** (FastAPI, port 8000) - Core API
2. **Strapi CMS** (Node.js, port 1337) - Content backend
3. **Oversight Hub** (React, port 3001) - Depends on APIs
4. **Public Site** (Next.js, port 3000) - Depends on Strapi

### Why Backend First?

- Frontend needs backend APIs to be ready
- Prevents connection errors at startup
- Better user experience (no "API unreachable" errors)
- Easier debugging

### Gotcha: npm-run-all with workspaces

When using `--workspace` flags with `npm-run-all`:

```bash
# ❌ DON'T do this (won't work as expected)
npx npm-run-all --parallel npm:dev:public npm:dev:oversight

# ✅ DO this (correct syntax)
npx npm-run-all --parallel "npm:workspace1:dev" "npm:workspace2:dev"

# Or just use npm run (simpler)
npm run dev:public &
npm run dev:oversight
```

---

## 🎓 Learning More

```bash
# Get full help
npx npm-run-all --help

# See all available options
npx npm-run-all --?
```

Key flags:

- `--parallel (-p)`: Run tasks concurrently
- `--serial (-s)`: Run tasks sequentially
- `--continue-on-error`: Don't stop if one fails
- `--print-label`: Show which task is running
- `--prefix-all`: Add prefix to output

---

## Summary

| Goal                             | Command                      |
| -------------------------------- | ---------------------------- |
| Run all services (backend-first) | `npm run dev:backends-first` |
| Run backend only                 | `npm run dev:backends`       |
| Run frontend only                | `npm run dev:frontend`       |
| Run all at once                  | `npm run dev:full`           |
| Just Strapi                      | `npm run dev:strapi`         |
| Just Co-Founder Agent            | `npm run dev:cofounder`      |

**Best for your use case:** `npm run dev:backends-first` (runs Co-Founder Agent + Strapi first, then frontend)
