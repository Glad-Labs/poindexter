# Quick Reference: Just Commands

## 🚀 Essential Commands (Start Here)

```bash
just setup          # First time: install everything
just dev            # Daily: start all services
just test           # Verify everything works
just format         # Fix formatting issues
```

## 🎯 Development

```bash
just dev                # All 3 services (8000, 3000, 3001)
just dev-backend        # Backend only
just dev-frontend       # Frontend only
just dev-debug          # Backend with debug logs
```

## 🧪 Testing

```bash
just test                   # All tests
just test-python            # Python tests
just test-js                # JavaScript tests
just test-python-parallel   # Faster (multi-threaded)
just test-smoke             # Quick smoke tests
just test-watch             # Watch mode
```

## 🧹 Code Quality

```bash
just format             # Format code
just lint               # Check code
just lint-python-fix    # Fix Python issues
just lint-js-fix        # Fix JavaScript issues
just typecheck          # Type checking
just security-audit     # Security scan
```

## 📦 Dependencies

```bash
just deps           # Show what's installed
just deps-update    # Update everything
```

## 🗄️ Database

```bash
just db-migrate                # Apply migrations
just db-migrate-create NAME    # Create new migration
just db-rollback               # Undo last migration
just db-reset                  # Full reset
```

## 🏥 Health & Monitoring

```bash
just health-check       # Are all services running?
just services-status    # Detailed status
just info              # Environment info
just clean             # Clean cache
```

## 📚 See All Commands

```bash
just --list
just --help
just help
```

---

## Installation

```bash
# macOS
brew install just

# Windows (Scoop)
scoop install just

# Linux (Cargo)
cargo install just

# Or download: https://github.com/casey/just/releases
```

---

## For Python Tasks in Backend

Once in `src/cofounder_agent/`:

```bash
poetry install              # Install deps
poetry run pytest           # Run tests
poetry add package-name     # Add dependency
poetry show                 # List packages
poetry env info             # Virtual env info
```

Or use just from root:

```bash
just test-python            # Run from anywhere
just format-python
just lint-python
```
