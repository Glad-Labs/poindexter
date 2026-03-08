# CLI Commands Reference

This document lists available command-line commands for development, testing, building, and deployment of Glad Labs.

## Package.json Scripts

Run scripts with `npm run {script-name}` from project root.

### Development & Running

#### `npm run dev`

**Description:** Start all three services concurrently (primary development command)  
**Services Started:**

- FastAPI backend (port 8000): `npm run dev:cofounder`
- Next.js public site (port 3000): `npm run dev:public`
- React oversight hub (port 3001): `npm run dev:oversight`

```bash
npm run dev
```

#### `npm run dev:cofounder`

**Description:** Start FastAPI backend with hot reload  
**Port:** 8000  
**Details:** Uses poetry + uvicorn with reload enabled

```bash
npm run dev:cofounder
```

#### `npm run dev:public`

**Description:** Start Next.js development server  
**Port:** 3000  
**Details:** TypeScript compilation, hot module replacement

```bash
npm run dev:public
```

#### `npm run dev:oversight`

**Description:** Start React admin dashboard  
**Port:** 3001  
**Details:** Vite dev server with hot reload

```bash
npm run dev:oversight
```

#### `npm run dev:frontend`

**Description:** Start both React apps (next.js + oversight hub)

```bash
npm run dev:frontend
```

### Installation & Setup

#### `npm run install:all`

**Description:** Install all Python and Node dependencies  
**Details:**

- npm install for workspaces
- poetry install for Python backend

```bash
npm run install:all
```

#### `npm run clean:install`

**Description:** Full clean reset and reinstall  
**Details:**

- Remove node_modules and lock files
- Remove Python venv and poetry cache
- Fresh install of all dependencies

```bash
npm run clean:install
```

#### `npm run setup`

**Description:** Complete project setup with environment configuration

```bash
npm run setup
```

#### `npm run setup:all`

**Description:** Comprehensive setup including all dependencies

```bash
npm run setup:all
```

### Testing

#### `npm run test`

**Description:** Run all tests (JavaScript/TypeScript + Python)  
**Details:** Jest for frontend, pytest for backend

```bash
npm run test
```

#### `npm run test:python`

**Description:** Run full Python test suite (integration + e2e)

```bash
npm run test:python
```

#### `npm run test:python:unit`

**Description:** Run Python unit tests only

```bash
npm run test:python:unit
```

#### `npm run test:python:smoke`

**Description:** Run fast smoke tests  
**Details:** Quick validation of core functionality

```bash
npm run test:python:smoke
```

#### `npm run test:python:coverage`

**Description:** Run Python tests with coverage report

```bash
npm run test:python:coverage
```

#### `npm run test:playwright`

**Description:** Run Playwright browser automation tests (headless)

```bash
npm run test:playwright
```

#### `npm run test:playwright:headed`

**Description:** Run Playwright tests with visible browser

```bash
npm run test:playwright:headed
```

### Code Quality & Formatting

#### `npm run lint`

**Description:** Lint all workspaces with ESLint

```bash
npm run lint
```

#### `npm run lint:fix`

**Description:** Automatically fix ESLint issues

```bash
npm run lint:fix
```

#### `npm run lint:python`

**Description:** Python linting with pylint

```bash
npm run lint:python
```

#### `npm run format`

**Description:** Format all code with Prettier (JS/TS/JSON/MD)

```bash
npm run format
```

#### `npm run format:check`

**Description:** Check formatting without writing changes

```bash
npm run format:check
```

#### `npm run format:python`

**Description:** Format Python code with Black + isort

```bash
npm run format:python
```

#### `npm run type:check`

**Description:** Run Python type checking with mypy

```bash
npm run type:check
```

### Building

#### `npm run build`

**Description:** Build all workspaces  
**Outputs:**

- Oversight Hub: `web/oversight-hub/build/`
- Public Site: `web/public-site/.next/`

```bash
npm run build
```

#### `npm run build:public`

**Description:** Build Next.js production bundle

```bash
npm run build:public
```

#### `npm run build:oversight`

**Description:** Build React admin dashboard

```bash
npm run build:oversight
```

### Health & Monitoring

#### `npm run health:check`

**Description:** Check health of all running services  
**Verifies:**

- Backend API responding (port 8000)
- Database connectivity
- Ollama availability
- Cached responses

```bash
npm run health:check
```

#### `npm run monitor`

**Description:** Continuous monitoring dashboard for services

```bash
npm run monitor
```

### Database

#### `npm run db:migrate`

**Description:** Run Alembic migrations on PostgreSQL

```bash
npm run db:migrate
```

#### `npm run db:setup`

**Description:** Initialize database schema

```bash
npm run db:setup
```

#### `npm run db:seed`

**Description:** Populate database with sample data

```bash
npm run db:seed
```

### Utilities

#### `npm run clean`

**Description:** Clean build artifacts and caches

```bash
npm run clean
```

#### `npm run reset`

**Description:** Reset services and clear state

```bash
npm run reset
```

#### `npm run version:bump`

**Description:** Bump version number

```bash
npm run version:bump
```

#### `npm run release`

**Description:** Create release tag and push

```bash
npm run release
```

## Custom Scripts (in /scripts directory)

### Python Scripts

#### `scripts/health-check.js`

**Usage:** `node scripts/health-check.js`  
**Purpose:** Validate all services are running

#### `scripts/generate-content-batch.py`

**Usage:** `cd src/cofounder_agent && poetry run python ../../scripts/generate-content-batch.py`  
**Purpose:** Batch content generation for testing

#### `scripts/evaluate_content_quality.py`

**Usage:** `cd src/cofounder_agent && poetry run python ../../scripts/evaluate_content_quality.py`  
**Purpose:** Quality assessment of generated content

#### `scripts/diagnose_task_metadata.py`

**Usage:** `cd src/cofounder_agent && poetry run python ../../scripts/diagnose_task_metadata.py`  
**Purpose:** Debug task metadata flow

### Shell Scripts

#### `scripts/kill-all-dev-ports.sh`

**Usage:** `bash scripts/kill-all-dev-ports.sh`  
**Purpose:** Kill processes on development ports (3000, 3001, 8000)

#### `scripts/backup-local-postgres.sh`

**Usage:** `bash scripts/backup-local-postgres.sh`  
**Purpose:** Backup local database to file

#### `scripts/init-db.ps1`

**Usage:** `.\scripts\init-db.ps1` (Windows PowerShell)  
**Purpose:** Initialize local PostgreSQL database

## Environment Variables

All scripts read from `.env.local` at project root. Key variables:

**Database:**

```bash
DATABASE_URL=postgresql://user:password@localhost:5432/glad_labs_dev
DATABASE_POOL_MIN_SIZE=5
DATABASE_POOL_MAX_SIZE=20
```

**LLM Keys (at least one required):**

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
```

**Ollama (for local models):**

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=mistral
```

**Development:**

```bash
LOG_LEVEL=debug
SQL_DEBUG=false
ENABLE_DEBUG_LOGS=false
```

## Common Workflows

### Complete Fresh Start

```bash
npm run clean:install
npm run setup:all
npm run dev
```

### Run Tests Locally

```bash
# All tests
npm run test

# Python only (fast)
npm run test:python:smoke

# With coverage
npm run test:python:coverage
```

### Database Operations

```bash
# Reset and initialize
npm run db:setup

# Run migrations
npm run db:migrate

# Seed sample data
npm run db:seed
```

### Format and Lint Code

```bash
# Format everything
npm run format
npm run format:python

# Lint everything
npm run lint
npm run lint:python

# Fix issues automatically
npm run lint:fix
```

### Full Build & Deploy

```bash
# Build all
npm run build

# Full test suite
npm run test

# Format check
npm run format:check
```

## Debugging

### View Service Logs

```bash
# Backend logs (if using npm run dev)
# Logs appear in the same terminal as npm run dev

# Or check log files if configured
tail -f src/cofounder_agent/logs/cofounder_agent.log
```

### Run Python Tests with Print Statements

```bash
cd src/cofounder_agent
poetry run pytest tests/ -s -v
```

### Debug Database Queries

```bash
# Enable SQL debug logging
export SQL_DEBUG=true
npm run dev:cofounder
```

### Health Check All Services

```bash
npm run health:check
```

## Key Implementation Files

- [package.json](../../package.json) - All npm scripts
- [scripts/](../../scripts/) - Utility scripts
- [pyproject.toml](../../pyproject.toml) - Python configuration
- [.env.example](../../.env.example) - Environment variable template

## Notes

- All services run on localhost in development
- Ports: Backend 8000, Public Site 3000, Oversight Hub 3001
- PostgreSQL defaults to port 5432 locally
- Ollama defaults to port 11434
- `.env.local` is git-ignored for security; copy from `.env.example`
