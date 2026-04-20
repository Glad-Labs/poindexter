# Contributing to Poindexter

Thanks for your interest in contributing. Poindexter is built by Glad Labs LLC and welcomes outside contributions.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/poindexter.git`
3. Run the bootstrap: `poindexter setup` — this creates `bootstrap.toml` with auto-generated secrets, starts the local Postgres + Grafana, installs dependencies, and pulls the minimum required Ollama models.
4. Bring up the full stack: `bash scripts/start-stack.sh`
5. Run tests: `cd src/cofounder_agent && python -m pytest tests/unit/ -q`

## Development Workflow

1. Create a branch from `main`: `git checkout -b feat/your-feature`
2. Install the pre-commit hooks once per clone: `pip install pre-commit && pre-commit install` — this wires up `gitleaks` (secret scan) and a few formatting checks so commits are blocked before anything sensitive lands in history.
3. Make your changes
4. Run tests and ensure they pass: `python -m pytest tests/unit/ -q`
5. Run linting: `npm run lint`
6. Commit with a clear message describing what and why
7. Open a pull request against `main`

## CI security gates

Every PR and every push to `main` runs three automated security gates (see `.github/workflows/security.yml` + `.gitea/workflows/security.yml`):

- **gitleaks** — secret scan across the diff + full history, blocks on any finding
- **Trivy** — filesystem CVE scan, fails the build on HIGH / CRITICAL fixed vulnerabilities
- **syft + grype** — generates a CycloneDX SBOM, scans it for HIGH / CRITICAL CVEs, and uploads the SBOM as a workflow artifact

Dependencies are kept current by Dependabot (`.github/dependabot.yml`) with a weekly PR batch. Patch and minor bumps auto-rebase onto `dev`; majors wait on human review.

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

Report security vulnerabilities to **security@gladlabs.io** — never in public GitHub issues. See [SECURITY.md](SECURITY.md) for the full policy.

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 license.
