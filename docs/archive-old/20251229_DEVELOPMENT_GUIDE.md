# 🚀 Glad Labs Development Workflow Guide

## Quick Start

```bash
# First time setup (installs all dependencies)
npm run setup

# Daily development
npm run dev

# Run all tests
npm run test:all

# Format code
npm run format
```

---

## 📋 Available Scripts

### Development Commands

| Command                 | Purpose                                                |
| ----------------------- | ------------------------------------------------------ |
| `npm run dev`           | Start all services (backend + frontend)                |
| `npm run dev:all`       | Start backend + public site + oversight hub separately |
| `npm run dev:cofounder` | Start only FastAPI backend on port 8000                |
| `npm run dev:public`    | Start only Next.js public site on port 3000            |
| `npm run dev:oversight` | Start only React oversight hub on port 3001            |

### Setup & Installation

| Command                | Purpose                                                     |
| ---------------------- | ----------------------------------------------------------- |
| `npm run setup`        | Full initial setup (installs all deps + Python + env setup) |
| `npm run install:all`  | Install all Node + Python dependencies                      |
| `npm run setup:python` | Install Python requirements only                            |
| `npm run setup:env`    | Configure environment variables                             |
| `npm run setup:dev`    | Prepare dev environment + health check                      |

### Building & Deployment

| Command                  | Purpose                      |
| ------------------------ | ---------------------------- |
| `npm run build`          | Build all services           |
| `npm run build:frontend` | Build frontend services only |

### Testing

| Command                        | Purpose                                        |
| ------------------------------ | ---------------------------------------------- |
| `npm run test`                 | Run all JS/TS tests                            |
| `npm run test:python`          | Run all Python tests with verbose output       |
| `npm run test:python:smoke`    | Run quick smoke tests (e2e_fixed.py)           |
| `npm run test:python:coverage` | Run tests with coverage report (HTML)          |
| `npm run test:all`             | Run Python + JavaScript tests                  |
| `npm run test:ci`              | Run tests in CI mode (no watch, with coverage) |

### Code Quality

| Command                 | Purpose                           |
| ----------------------- | --------------------------------- |
| `npm run lint`          | Lint all code (JS + Python)       |
| `npm run lint:fix`      | Auto-fix linting issues           |
| `npm run format`        | Format all code (JS + Python)     |
| `npm run format:check`  | Check if code needs formatting    |
| `npm run format:js`     | Format JavaScript/TypeScript only |
| `npm run format:python` | Format Python code                |

### Monitoring & Debugging

| Command                 | Purpose                              |
| ----------------------- | ------------------------------------ |
| `npm run health:check`  | Check health of all running services |
| `npm run env:select`    | Switch between .env configurations   |
| `npm run clean`         | Remove all build artifacts & cache   |
| `npm run clean:install` | Reset entire dev environment         |

---

## 🏗️ Project Structure

```
glad-labs-website/
├── src/cofounder_agent/          # FastAPI backend (Python)
│   ├── package.json              # Python workspace config
│   ├── requirements.txt           # Python dependencies
│   ├── main.py                   # FastAPI entry point
│   └── ...
├── web/public-site/              # Next.js public website
│   ├── package.json
│   ├── app/                      # App router (Next.js 15)
│   └── ...
├── web/oversight-hub/            # React admin dashboard
│   ├── package.json
│   ├── src/                      # React components
│   └── ...
├── package.json                  # Root monorepo config
├── requirements.txt              # Root Python requirements
└── scripts/
    ├── select-env.js             # Environment switcher
    └── health-check.js           # Service health checker
```

---

## 🔌 Service Ports

| Service         | Port | URL                     |
| --------------- | ---- | ----------------------- |
| FastAPI Backend | 8000 | `http://localhost:8000` |
| Public Site     | 3000 | `http://localhost:3000` |
| Oversight Hub   | 3001 | `http://localhost:3001` |

---

## 📦 Workspaces

This is an **npm monorepo** with **3 workspaces**:

1. **web/public-site** (Next.js 15) - Public-facing website
2. **web/oversight-hub** (React 18) - Admin control center
3. **src/cofounder_agent** (Python) - AI backend (workspace-like management)

Run commands in specific workspace:

```bash
npm run build --workspace=web/public-site
npm run test --workspace=web/oversight-hub
```

---

## 🐍 Python Workspace Management

The Python backend (`src/cofounder_agent`) is managed like a workspace through its `package.json` file.

**Python-specific scripts:**

```bash
# From root
npm run dev:cofounder        # Start Python backend
npm run test:python          # Run Python tests
npm run format:python        # Format Python code

# From src/cofounder_agent directory
npm run dev                  # Start with auto-reload
npm run test                 # Run tests
npm run format               # Format code
npm run typecheck            # Run mypy type checking
npm run security:audit       # Check for security issues
```

---

## 🔄 Development Workflow

### 1. Initial Setup

```bash
npm run setup
# This will:
# - Install all Node dependencies
# - Install all Python dependencies
# - Prompt for environment selection
# - Check service health
```

### 2. Start Development

```bash
npm run dev
# Starts in recommended mode:
# - Backend on port 8000
# - Public site on port 3000
# - Oversight hub on port 3001
```

### 3. Develop & Test

```bash
# In another terminal, run tests
npm run test:all

# Check code quality
npm run lint

# Format code
npm run format
```

### 4. Before Committing

```bash
npm run format          # Auto-format all code
npm run lint:fix        # Fix lint issues
npm run test:all        # Run full test suite
npm run health:check    # Verify services
```

---

## 🐛 Troubleshooting

### Port Already in Use

```bash
# Check what's using port 3000
lsof -i :3000

# Kill specific process
kill -9 <PID>

# Or just clean and restart
npm run clean
npm run dev
```

### Dependencies Not Installing

```bash
# Full reset
npm run clean:install

# Then re-run setup
npm run setup
```

### Python Tests Failing

```bash
# Check Python setup
python --version  # Should be 3.10+
pip --version

# Reinstall Python deps
npm run setup:python

# Run with more verbose output
npm run test:python -- -vv
```

### Services Not Starting

```bash
# Check health
npm run health:check

# Check individual service logs
npm run dev:cofounder      # Backend logs
npm run dev:public         # Frontend logs (in another terminal)
npm run dev:oversight      # Admin hub logs (in another terminal)
```

---

## 📝 Environment Configuration

Environments are managed in `.env.local` at project root. Switch between them:

```bash
npm run env:select
```

This will prompt you to choose:

- **development** - Local dev with all features
- **staging** - Simulated production environment
- **production** - Full prod config

---

## 🎯 Common Tasks

### Add a New Dependency

**JavaScript:**

```bash
npm install package-name --workspace=web/public-site
```

**Python:**

```bash
pip install package-name
echo "package-name>=version" >> src/cofounder_agent/requirements.txt
```

### Run a Single Test File

```bash
npm run test:python -- tests/test_e2e_fixed.py -v
```

### Generate Coverage Report

```bash
npm run test:python:coverage
# Opens coverage/htmlcov/index.html
```

### Type Check Python Code

```bash
cd src/cofounder_agent
npm run typecheck
```

### Security Audit

```bash
cd src/cofounder_agent
npm run security:audit
```

---

## ✅ Pre-Commit Checklist

Before pushing code:

- [ ] `npm run test:all` passes
- [ ] `npm run lint` passes (or `npm run lint:fix`)
- [ ] `npm run format:check` passes (or `npm run format`)
- [ ] `npm run health:check` shows all green
- [ ] No console errors in browser dev tools

---

## 📚 Documentation

- **Architecture**: See [docs/02-ARCHITECTURE_AND_DESIGN.md](docs/02-ARCHITECTURE_AND_DESIGN.md)
- **Development**: See [docs/04-DEVELOPMENT_WORKFLOW.md](docs/04-DEVELOPMENT_WORKFLOW.md)
- **Agents**: See [docs/05-AI_AGENTS_AND_INTEGRATION.md](docs/05-AI_AGENTS_AND_INTEGRATION.md)
- **Deployment**: See [docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md](docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)

---

## 🤝 Contributing

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make your changes and test: `npm run test:all && npm run lint:fix && npm run format`
3. Commit with clear messages
4. Push and create a pull request

---

**Last Updated:** December 23, 2025  
**Node Version Required:** 18+  
**Python Version Required:** 3.10+
