# Poetry Workflow Guide

## What is Poetry?

Poetry is a modern Python dependency management and packaging tool that works like npm for Python:

- **Single source of truth**: `pyproject.toml` (like package.json)
- **Deterministic installs**: `poetry.lock` (like package-lock.json)
- **Automatic venv**: No manual `python -m venv` needed
- **Easy scripts**: Define commands in pyproject.toml

---

## Installing Poetry

### Option 1: Official Installer (Recommended)

```bash
curl -sSL https://install.python-poetry.org | python3 -
# Then add to PATH:
export PATH="$HOME/.local/bin:$PATH"  # macOS/Linux
```

### Option 2: Homebrew (macOS)

```bash
brew install poetry
poetry --version
```

### Option 3: Scoop (Windows)

```bash
scoop install poetry
poetry --version
```

### Option 4: Pip

```bash
pip install poetry
poetry --version
```

---

## Quick Start with Poetry

### 1. Initialize a New Project

```bash
poetry new my-project
cd my-project
```

### 2. Add Dependencies

```bash
poetry add fastapi uvicorn            # Add production deps
poetry add --group dev pytest black   # Add dev deps
```

### 3. Install All Dependencies

```bash
poetry install
# Creates: .venv/, poetry.lock
```

### 4. Run Commands in Virtual Environment

```bash
poetry run python script.py
poetry run pytest
poetry run black src/
```

### 5. Activate Virtual Environment (Optional)

```bash
# Auto activation (recommended via poetry)
# Or manually:
source .venv/bin/activate      # macOS/Linux
.venv\Scripts\activate          # Windows
```

---

## Understanding pyproject.toml

### Metadata

```toml
[tool.poetry]
name = "glad-labs-backend"
version = "0.1.0"
description = "FastAPI backend with AI agents"
authors = [{ name = "Your Name", email = "you@example.com" }]
```

### Dependencies (Production)

```toml
[tool.poetry.dependencies]
python = ">=3.10,<3.13"           # Python version constraint
fastapi = "^0.104.0"              # Caret: allows 0.104.x, not 0.105.0
uvicorn = "~0.24.0"               # Tilde: allows 0.24.x, not 0.25.0
requests = ">=2.28.0"             # Greater than or equal
sqlalchemy = "2.0.0"              # Exact version
```

### Version Constraints Explained

```toml
fastapi = "^0.104.0"       # Caret: 0.104.0 <= v < 0.105.0
fastapi = "~0.104.0"       # Tilde: 0.104.0 <= v < 0.105.0
fastapi = ">=0.104.0,<1.0" # Range: 0.104.0 <= v < 1.0
fastapi = "0.104.0"        # Exact version
fastapi = "*"              # Any version
```

### Development Dependencies

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^7.4.0"
black = "^23.12.0"
mypy = "^1.7.0"
```

### Tool Configuration

```toml
[tool.black]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.mypy]
strict = true
python_version = "3.10"
```

---

## Managing Dependencies

### Add a New Package

```bash
# Production dependency
poetry add fastapi

# Development dependency
poetry add --group dev pytest

# Specific version
poetry add fastapi@^0.105.0
poetry add "fastapi>=0.104.0,<0.105.0"
```

### Remove a Package

```bash
poetry remove fastapi
poetry remove --group dev pytest
```

### Update Packages

```bash
poetry update                    # Update all, respecting constraints
poetry update fastapi            # Update only fastapi
poetry update --dry-run          # See what would change
```

### List Installed Packages

```bash
poetry show                      # All packages
poetry show --outdated           # Outdated packages
poetry show --tree               # Dependency tree
```

### Lock File Management

```bash
poetry lock                      # Regenerate lock file
poetry lock --no-update          # Refresh without updating versions
```

---

## Virtual Environment Management

### View Environment Info

```bash
poetry env info                  # Current environment
poetry env list                  # All environments
```

### Use Specific Python Version

```bash
poetry env use python3.12
poetry env use /usr/bin/python3.12
poetry env use python  # Latest in PATH
```

### Remove Environment

```bash
poetry env remove python3.10
poetry env remove python3.10-venv
```

### Inspect Virtual Environment

```bash
poetry run python --version
poetry run which python
poetry run pip list
```

---

## Running Commands

### Via Poetry (Recommended)

```bash
poetry run python script.py
poetry run pytest tests/
poetry run black src/
poetry run mypy src/
```

### Manual Activation

```bash
# Activate the virtual environment
source .venv/bin/activate      # macOS/Linux
.venv\Scripts\activate          # Windows

# Now run commands directly
python script.py
pytest
black src/
```

### One-off Commands

```bash
poetry run python -c "print('Hello')"
poetry run python -m http.server 8000
```

---

## Scripts in pyproject.toml

### Define Custom Scripts

```toml
[tool.poetry.scripts]
api = "cofounder_agent.main:app"  # Expose FastAPI app
```

### Run Custom Scripts

```bash
poetry run api  # Starts FastAPI without typing full command
```

### Environment Variables in Scripts

```bash
export ENV_VAR=value
poetry run script.py
```

---

## Building and Publishing

### Build Distributions

```bash
poetry build
# Creates: dist/my-package.tar.gz, dist/my-package.whl
```

### Publish to PyPI

```bash
poetry publish
# Or with custom registry
poetry publish --repository custom-repo
```

### Check Build

```bash
poetry check
```

---

## Troubleshooting

### Virtual Environment Issues

```bash
# Remove and recreate
poetry env remove 3.10
poetry install

# Or force recreate
poetry env remove --all
poetry install
```

### Dependency Conflicts

```bash
# Check what's wrong
poetry lock --no-update

# Try updating
poetry update

# Or check manually
poetry lock --verbose
```

### Cache Problems

```bash
poetry cache clear . --all
poetry install
```

### Python Version Not Found

```bash
# Check available
pyenv versions

# Or use direct path
poetry env use /path/to/python3.12
```

### Slow Installation

```bash
# Use pre-releases
poetry config pypi-token.pypi your-token

# Or try installing with --no-root
poetry install --no-root
```

---

## Integration with Just

Your `justfile` handles Poetry for you:

```bash
# In src/cofounder_agent directory
just setup-python      # Runs: poetry install
just test-python       # Runs: poetry run pytest
just format-python     # Runs: poetry run black + isort
just lint-python       # Runs: poetry run pylint + ruff
just typecheck         # Runs: poetry run mypy
just dev-backend       # Runs: poetry run uvicorn
```

---

## Comparison: Old vs New

| Task           | Old (pip)                           | New (Poetry)             |
| -------------- | ----------------------------------- | ------------------------ |
| Install deps   | `pip install -r requirements.txt`   | `poetry install`         |
| Add package    | `pip install package` + manual edit | `poetry add package`     |
| Run tests      | `python -m pytest`                  | `poetry run pytest`      |
| Lock versions  | Manual `pip freeze`                 | Automatic `poetry.lock`  |
| Manage venv    | Manual `python -m venv`             | Automatic `.venv/`       |
| Check updates  | Manual                              | `poetry show --outdated` |
| Version ranges | Error-prone                         | Declarative constraints  |

---

## Best Practices

1. **Commit poetry.lock to git** - Ensures reproducible builds
2. **Use version constraints** - `^` for most, `~` for critical
3. **Separate dev dependencies** - Use `--group dev` for tools
4. **Run via poetry run** - Don't activate venv manually
5. **Keep pyproject.toml clean** - Document your choices
6. **Use pre-commit** - Automate quality checks
7. **Check dependencies regularly** - `poetry show --outdated`

---

## Useful Commands Summary

```bash
# Setup
poetry install                 # Install all dependencies
poetry update                  # Update respecting constraints

# Dependencies
poetry add package-name        # Add production dependency
poetry add --group dev package # Add dev dependency
poetry remove package-name     # Remove package
poetry show                    # List all packages
poetry show --outdated         # Check for updates

# Environment
poetry env info               # Virtual environment info
poetry env list               # List environments
poetry env use python3.12     # Use specific Python version

# Running
poetry run pytest             # Run in venv
poetry run python script.py   # Run script in venv

# Locking
poetry lock                   # Generate/update lock file
poetry lock --no-update       # Refresh without changing versions

# Building
poetry build                  # Build wheel + source dist
poetry check                  # Validate pyproject.toml
```

---

## Resources

- **Official Docs**: https://python-poetry.org/docs/
- **GitHub**: https://github.com/python-poetry/poetry
- **Dependency Specification**: https://python-poetry.org/docs/dependency-specification/
- **Environment Variables**: https://python-poetry.org/docs/configuration/#environment-variables

---

**Next Steps**: Run `just setup` to install everything, then `just dev` to start!
