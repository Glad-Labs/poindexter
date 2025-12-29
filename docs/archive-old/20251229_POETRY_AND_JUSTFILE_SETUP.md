# 🚀 Just + Poetry Development Setup

Your project now supports two complementary task runners for seamless Python + JavaScript development.

## What's New

### ✨ Justfile - Cross-platform Task Runner

A modern, simple task runner that works on Windows, macOS, and Linux.

**Install just:**

```bash
# macOS
brew install just

# Windows (with Scoop)
scoop install just

# Linux (with cargo)
cargo install just

# Or from: https://github.com/casey/just/releases
```

**Why Justfile instead of Make?**

- ✅ Simpler syntax than Makefiles
- ✅ Works natively on Windows (no GNU Make needed)
- ✅ Comments and documentation built-in
- ✅ Variable interpolation and error handling
- ✅ Cross-platform guaranteed

---

## 📦 Poetry - Python Dependency Management

Poetry replaces pip/requirements.txt with a modern, deterministic approach.

**Install Poetry:**

```bash
# Official installer (recommended)
curl -sSL https://install.python-poetry.org | python3 -

# Or with pip
pip install poetry

# Or with Homebrew (macOS)
brew install poetry
```

**Why Poetry instead of pip?**

- ✅ Deterministic `poetry.lock` file (like npm's package-lock.json)
- ✅ Automatic virtual environment management
- ✅ Dependency resolution is faster and more reliable
- ✅ Scripts defined in `pyproject.toml` (single source of truth)
- ✅ Easy version pinning and ranges
- ✅ Better security with lock file verification

---

## 🎯 Quick Start

### First Time Setup

```bash
just setup
# This runs:
# 1. npm install --workspaces (Node deps)
# 2. poetry install (Python deps + venv)
# 3. Creates .env.local template

# Add your API keys to .env.local
nano .env.local
```

### Daily Development

```bash
just dev
# Starts backend (8000) + public site (3000) + oversight hub (3001)
```

### Common Workflows

**Code Quality**

```bash
just format      # Auto-format Python + JavaScript
just lint        # Check for issues
just lint-*-fix  # Auto-fix issues
```

**Testing**

```bash
just test              # All tests
just test-python       # Python tests only
just test-python-parallel  # Fast parallel execution
just test-watch        # Watch mode for Python
```

**Database**

```bash
just db-migrate           # Apply migrations
just db-migrate-create my-migration  # Create new migration
just db-reset            # Reset to initial state
```

---

## 📋 All Available Commands

### Setup & Installation

```bash
just setup              # Complete setup (node, python, env)
just setup-node         # npm install only
just setup-python       # poetry install only
just setup-env          # Create .env.local
```

### Development

```bash
just dev                # All services
just dev-backend        # Backend only
just dev-frontend       # Frontend only
just dev-debug          # Backend with debug logging
```

### Testing (Pick your style)

```bash
# Standard
just test-python        # Standard test run
just test-js            # JavaScript tests

# Advanced
just test-python-parallel   # Faster execution
just test-python-coverage   # With coverage report
just test-watch             # Auto-rerun on changes
just test-smoke             # Quick smoke tests
```

### Code Quality

```bash
just format             # Format all code
just lint               # Check all code
just lint-python-fix    # Fix Python issues
just lint-js-fix        # Fix JavaScript issues
just typecheck          # Python type checking
just security-audit     # Security scan
just pre-commit-run     # Run pre-commit hooks
```

### Database

```bash
just db-migrate             # Apply migrations
just db-migrate-create name # Create new migration
just db-rollback            # Undo one migration
just db-reset               # Full reset
```

### Monitoring

```bash
just health-check       # Check all services
just services-status    # Detailed service info
just info              # Environment information
just deps              # Show dependencies
```

### Cleanup

```bash
just clean             # Remove cache/artifacts
just deps-update       # Update all dependencies
```

---

## 🐍 Python Virtual Environment

Poetry automatically creates a virtual environment at `.venv/` in the Python directory:

```bash
cd src/cofounder_agent

# View venv info
poetry env info

# List all environments
poetry env list

# Use a specific Python version
poetry env use python3.12
```

**Running commands in the venv:**

```bash
# Via poetry (recommended)
poetry run python script.py
poetry run pytest

# Or activate the venv manually
source .venv/bin/activate  # macOS/Linux
.venv\Scripts\activate     # Windows
python script.py
```

---

## 📝 PyProject.toml Structure

Your `src/cofounder_agent/pyproject.toml` defines:

### Dependencies Section

```toml
[tool.poetry.dependencies]
python = ">=3.10,<3.13"
fastapi = "^0.104.0"      # Exact version: 0.104.0 or newer, <0.105.0
openai = "^1.3.0"         # Caret: allows 1.3.0, 1.4.0, etc. (< 2.0.0)
```

### Dev Dependencies Section

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^7.4.0"         # Test framework
black = "^23.12.0"        # Code formatter
mypy = "^1.7.0"          # Type checker
```

### Tool Configuration

- `[tool.black]` - Formatting rules
- `[tool.isort]` - Import sorting
- `[tool.mypy]` - Type checking rules
- `[tool.pytest.ini_options]` - Testing configuration
- `[tool.ruff]` - Fast linting

---

## ⚡ Migration from Old Setup

### What Changed

- ❌ `requirements.txt` → ✅ `pyproject.toml` + `poetry.lock`
- ❌ `npm scripts` for Python → ✅ `poetry run` + `just`
- ❌ Manual venv management → ✅ Automatic via Poetry

### Old vs New

**Old (pip + npm):**

```bash
# Backend setup
pip install -r requirements.txt
npm run dev:backend

# Testing
npm run test:python
```

**New (Poetry + just):**

```bash
# Backend setup
just setup-python
just dev-backend

# Testing
just test-python
```

---

## 🔧 Troubleshooting

### Poetry not found

```bash
# Ensure it's in PATH
poetry --version

# If not found, reinstall
curl -sSL https://install.python-poetry.org | python3 -
export PATH="$HOME/.local/bin:$PATH"
```

### Virtual environment issues

```bash
# Remove and recreate venv
cd src/cofounder_agent
poetry env remove python3.10  # (use your version)
poetry install
```

### Just command not found

```bash
just --version
# If missing, see install instructions above
```

### Dependencies won't install

```bash
# Clear Poetry cache
poetry cache clear . --all
poetry install
```

---

## 📚 Next Steps

1. **Install just and Poetry** (see above)
2. **Run initial setup**: `just setup`
3. **Start development**: `just dev`
4. **Check health**: `just health-check`
5. **Read DEVELOPMENT_GUIDE.md** for detailed workflows

---

## 🎓 Learning Resources

- **Justfile docs**: https://github.com/casey/just
- **Poetry docs**: https://python-poetry.org/docs/
- **FastAPI**: https://fastapi.tiangolo.com/
- **PyTest**: https://docs.pytest.org/

---

## 💡 Pro Tips

### Add a new Python dependency

```bash
cd src/cofounder_agent
poetry add package-name
poetry add --group dev package-name    # Dev only
```

### Update all dependencies safely

```bash
just deps-update
# This updates poetry.lock while respecting version constraints
```

### Run scripts directly

```bash
cd src/cofounder_agent
poetry run python -c "import cofounder_agent; print('OK')"
```

### Check what's in your venv

```bash
cd src/cofounder_agent
poetry show              # List all packages
poetry show --outdated   # Show outdated packages
```

---

**Questions?** Check the full DEVELOPMENT_GUIDE.md or see the justfile comments for detailed task descriptions.
