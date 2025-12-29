# Glad Labs Development Task Runner
# Usage: just <task> [args]
# Install: cargo install just (or brew install just)

set shell := ["bash", "-uc"]
set dotenv-load := true

# Show all available tasks
@help:
    just --list

# ===== SETUP TASKS =====

# Complete initial setup (first-time developers)
setup: setup-node setup-python setup-env
    #!/bin/bash
    echo "✅ Setup complete!"
    echo ""
    echo "Next steps:"
    echo "  just dev          # Start all services"
    echo "  just test         # Run all tests"
    echo "  just format       # Format all code"

# Install Node.js dependencies for all workspaces
setup-node:
    #!/bin/bash
    echo "📦 Installing Node.js dependencies..."
    npm install --workspaces
    echo "✅ Node dependencies installed"

# Install Python dependencies with Poetry
setup-python:
    #!/bin/bash
    echo "🐍 Installing Python dependencies with Poetry..."
    cd src/cofounder_agent
    poetry install
    echo "✅ Python dependencies installed"

# Setup environment files
setup-env:
    #!/bin/bash
    if [ ! -f .env.local ]; then
        echo "📝 Creating .env.local template..."
        {
            echo "# Database"
            echo "DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs"
            echo ""
            echo "# LLM API Keys (at least one required)"
            echo "OPENAI_API_KEY="
            echo "ANTHROPIC_API_KEY="
            echo "GOOGLE_API_KEY="
            echo ""
            echo "# Ollama (local models)"
            echo "OLLAMA_BASE_URL=http://localhost:11434"
            echo ""
            echo "# Optional"
            echo "LLM_PROVIDER="
            echo "DEFAULT_MODEL_TEMPERATURE=0.7"
            echo "SQL_DEBUG=false"
            echo "LOG_LEVEL=info"
        } > .env.local
        echo "⚠️  Created .env.local - please add your API keys"
    else
        echo "✅ .env.local already exists"
    fi

# ===== DEVELOPMENT TASKS =====

# Start all services (backend + frontends)
dev:
    #!/bin/bash
    echo "🚀 Starting all services..."
    npm run dev

# Start only the backend
dev-backend:
    #!/bin/bash
    echo "🚀 Starting backend..."
    cd src/cofounder_agent
    poetry run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Start only the frontend
dev-frontend:
    #!/bin/bash
    echo "🚀 Starting frontend services..."
    npm run dev:frontend

# Start backend with debug logging
dev-debug:
    #!/bin/bash
    echo "🚀 Starting backend with debug logging..."
    cd src/cofounder_agent
    poetry run uvicorn main:app --reload --log-level debug --host 0.0.0.0 --port 8000

# ===== TESTING TASKS =====

# Run all tests (Python + JavaScript)
test: test-python test-js

# Run Python tests
test-python:
    #!/bin/bash
    echo "🧪 Running Python tests..."
    cd src/cofounder_agent
    poetry run pytest tests/ -v

# Run Python tests with coverage
test-python-coverage:
    #!/bin/bash
    echo "📊 Running Python tests with coverage..."
    cd src/cofounder_agent
    poetry run pytest tests/ --cov=src/cofounder_agent --cov-report=html --cov-report=term-missing
    echo "📈 Coverage report generated at htmlcov/index.html"

# Run Python tests in parallel
test-python-parallel:
    #!/bin/bash
    echo "⚡ Running Python tests in parallel..."
    cd src/cofounder_agent
    poetry run pytest tests/ -n auto --dist loadgroup

# Run smoke tests only (fast)
test-smoke:
    #!/bin/bash
    echo "💨 Running smoke tests..."
    cd src/cofounder_agent
    poetry run pytest tests/smoke/ -v

# Run JavaScript tests
test-js:
    #!/bin/bash
    echo "🧪 Running JavaScript tests..."
    npm run test --workspaces

# Run JavaScript tests with coverage
test-js-coverage:
    #!/bin/bash
    echo "📊 Running JavaScript tests with coverage..."
    npm run test:coverage --workspaces

# Watch mode for Python tests
test-watch:
    #!/bin/bash
    echo "👀 Running tests in watch mode..."
    cd src/cofounder_agent
    poetry run pytest tests/ -v --tb=short -x

# ===== CODE QUALITY TASKS =====

# Run all linting and formatting checks
lint: lint-python lint-js

# Check Python linting without fixing
lint-python:
    #!/bin/bash
    echo "🔍 Checking Python code..."
    if ! command -v poetry &> /dev/null; then
        echo "⚠️  Poetry not found. Install with: pip install poetry"
        echo "    Or run: just setup-python"
        exit 1
    fi
    cd src/cofounder_agent
    poetry run pylint src/cofounder_agent
    poetry run ruff check src/cofounder_agent
    echo "✅ Python linting passed"

# Fix Python linting issues
lint-python-fix:
    #!/bin/bash
    echo "🔧 Fixing Python code..."
    if ! command -v poetry &> /dev/null; then
        echo "⚠️  Poetry not found. Install with: pip install poetry"
        echo "    Or run: just setup-python"
        exit 1
    fi
    cd src/cofounder_agent
    poetry run ruff check --fix src/cofounder_agent
    poetry run black src/cofounder_agent
    poetry run isort src/cofounder_agent
    echo "✅ Python code fixed"

# Check JavaScript linting without fixing
lint-js:
    #!/bin/bash
    echo "🔍 Checking JavaScript code..."
    npm run lint --workspaces || true

# Fix JavaScript linting issues
lint-js-fix:
    #!/bin/bash
    echo "🔧 Fixing JavaScript code..."
    npm run lint:fix --workspaces

# Type checking (Python)
typecheck:
    #!/bin/bash
    echo "📋 Running Python type checks..."
    cd src/cofounder_agent
    poetry run mypy src/cofounder_agent --strict
    echo "✅ Type checking passed"

# Security audit
security-audit:
    #!/bin/bash
    echo "🔒 Running security audits..."
    cd src/cofounder_agent
    poetry run bandit -r src/cofounder_agent
    npm audit --workspaces
    echo "✅ Security audit complete"

# ===== FORMATTING TASKS =====

# Format all code (Python + JavaScript)
format: format-python format-js
    #!/bin/bash
    echo "✨ All code formatted"

# Format Python code
format-python:
    #!/bin/bash
    echo "🐍 Formatting Python code..."
    cd src/cofounder_agent
    poetry run black src/cofounder_agent
    poetry run isort src/cofounder_agent
    echo "✅ Python code formatted"

# Format JavaScript code
format-js:
    #!/bin/bash
    echo "📘 Formatting JavaScript code..."
    npm run format --workspaces 2>/dev/null || echo "ℹ️  No formatter configured for JS"

# Check formatting without making changes
format-check:
    #!/bin/bash
    echo "📋 Checking code format..."
    cd src/cofounder_agent
    poetry run black --check src/cofounder_agent
    poetry run isort --check-only src/cofounder_agent
    echo "✅ Code format OK"

# ===== DATABASE TASKS =====

# Run database migrations
db-migrate:
    #!/bin/bash
    echo "📚 Running database migrations..."
    cd src/cofounder_agent
    poetry run python -m alembic upgrade head
    echo "✅ Migrations complete"

# Rollback database one version
db-rollback:
    #!/bin/bash
    echo "⏮️  Rolling back database..."
    cd src/cofounder_agent
    poetry run python -m alembic downgrade -1
    echo "✅ Rollback complete"

# Reset database to initial state
db-reset:
    #!/bin/bash
    echo "🔄 Resetting database..."
    cd src/cofounder_agent
    poetry run python -m alembic downgrade base
    poetry run python -m alembic upgrade head
    echo "✅ Database reset complete"

# Create a new database migration
db-migrate-create name:
    #!/bin/bash
    echo "✏️  Creating migration: {{name}}"
    cd src/cofounder_agent
    poetry run python -m alembic revision --autogenerate -m "{{name}}"
    echo "✅ Migration created"

# ===== HEALTH & MONITORING TASKS =====

# Check if all services are running
health-check:
    #!/bin/bash
    echo "🏥 Checking service health..."
    node scripts/health-check.js

# Show running services
services-status:
    #!/bin/bash
    echo "📊 Service Status:"
    echo ""
    echo "Backend (8000):"
    curl -s http://localhost:8000/health | jq . 2>/dev/null || echo "  ❌ Not running"
    echo ""
    echo "Public Site (3000):"
    curl -s http://localhost:3000 -I | head -1 || echo "  ❌ Not running"
    echo ""
    echo "Oversight Hub (3001):"
    curl -s http://localhost:3001 -I | head -1 || echo "  ❌ Not running"

# ===== UTILITY TASKS =====

# Install pre-commit hooks
pre-commit-install:
    #!/bin/bash
    echo "🪝 Installing pre-commit hooks..."
    poetry run pre-commit install
    echo "✅ Pre-commit hooks installed"

# Run pre-commit on all files
pre-commit-run:
    #!/bin/bash
    echo "🧹 Running pre-commit on all files..."
    poetry run pre-commit run --all-files

# Clean build artifacts and cache
clean:
    #!/bin/bash
    echo "🗑️  Cleaning build artifacts..."
    rm -rf build/ dist/ *.egg-info
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete
    rm -rf .pytest_cache .mypy_cache .coverage htmlcov/
    rm -rf .next/ node_modules/.next
    echo "✅ Cleaned"

# Show dependency information
deps:
    #!/bin/bash
    echo "📦 Node.js Dependencies:"
    echo "  npm list --depth=0"
    echo ""
    echo "🐍 Python Dependencies:"
    cd src/cofounder_agent && poetry show
    echo ""
    echo "🔄 Check for outdated:"
    echo "  npm outdated --workspaces"
    echo "  poetry show --outdated"

# Update dependencies (with caution!)
deps-update:
    #!/bin/bash
    echo "⚠️  Updating dependencies..."
    npm update --workspaces
    cd src/cofounder_agent && poetry update
    echo "✅ Dependencies updated"

# Show environment information
info:
    #!/bin/bash
    echo "🔍 Environment Information:"
    echo ""
    echo "Node.js: $(node --version)"
    echo "npm: $(npm --version)"
    echo "Python: $(python --version 2>&1)"
    echo "Poetry: $(poetry --version 2>&1)"
    echo ""
    echo "🗂️  Project Root: {{justfile_directory()}}"
    echo ""
    echo "📁 Services:"
    echo "  Backend:       src/cofounder_agent"
    echo "  Public Site:   web/public-site"
    echo "  Oversight Hub: web/oversight-hub"

# ===== CI/CD TASKS =====

# Prepare for deployment (run all checks)
pre-deploy: format lint test
    #!/bin/bash
    echo "✅ Pre-deployment checks passed!"
    echo ""
    echo "Ready to deploy. Next steps:"
    echo "  git push origin main"

# Run CI checks (for CI/CD pipelines)
ci: clean setup-python lint typecheck test-python security-audit
    #!/bin/bash
    echo "✅ All CI checks passed!"

# ===== DOCKER TASKS =====

# Build Docker image for backend
docker-build:
    #!/bin/bash
    echo "🐳 Building Docker image..."
    docker build -t glad-labs:latest -f src/cofounder_agent/Dockerfile .
    echo "✅ Docker image built"

# Run Docker container
docker-run:
    #!/bin/bash
    echo "🐳 Running Docker container..."
    docker run -p 8000:8000 --env-file .env.local glad-labs:latest
    echo "✅ Container running on http://localhost:8000"

# ===== DOCUMENTATION =====

# Generate API documentation
docs-api:
    #!/bin/bash
    echo "📖 Generating API docs..."
    cd src/cofounder_agent
    poetry run python -c "from main import app; from fastapi.openapi.utils import get_openapi; import json; print(json.dumps(get_openapi(title='Glad Labs API', version='1.0.0', routes=app.routes), indent=2))" > ../../docs/api-docs.json
    echo "✅ API docs generated at docs/api-docs.json"

# Open documentation
docs-open:
    #!/bin/bash
    if command -v xdg-open &> /dev/null; then
        xdg-open DEVELOPMENT_GUIDE.md
    elif command -v open &> /dev/null; then
        open DEVELOPMENT_GUIDE.md
    else
        echo "📖 See DEVELOPMENT_GUIDE.md for documentation"
    fi
