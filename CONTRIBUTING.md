# Contributing to Poindexter

Thanks for your interest in contributing. Poindexter is built by Glad Labs LLC and welcomes outside contributions.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/poindexter.git`
3. Copy `.env.example` to `.env` and configure
4. Start services: `docker compose up -d`
5. Run tests: `cd src/cofounder_agent && python -m pytest tests/unit/ -q`

## Development Workflow

1. Create a branch from `main`: `git checkout -b feat/your-feature`
2. Make your changes
3. Run tests and ensure they pass: `python -m pytest tests/unit/ -q`
4. Run linting: `npm run lint`
5. Commit with a clear message describing what and why
6. Open a pull request against `main`

## What We're Looking For

- Bug fixes with tests
- New data source connectors (Singer tap format preferred)
- Quality scoring improvements
- Documentation improvements
- Performance optimizations with benchmarks

## Code Style

- Python: Follow existing patterns. Use `get_logger(__name__)` for logging.
- JavaScript/TypeScript: Prettier + ESLint (runs automatically via lint-staged)
- SQL: Parameterized queries only (`$1, $2, ...`). Never use f-strings in SQL.
- Tests: Unit tests for all new services. Use pytest with async support.

## Reporting Bugs

Open an issue with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Your environment (OS, Python version, Docker version)

## Security

Report security vulnerabilities to security@gladlabs.io — not in public issues. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 license.
