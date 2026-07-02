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
3. Turn on commit signing: `bash scripts/setup-git-signing.sh` — see [Signed commits](#signed-commits) below.
4. Make your changes
5. Run tests and ensure they pass: `python -m pytest tests/unit/ -q`
6. Run linting: `npm run lint`
7. Commit with a clear message describing what and why
8. Open a pull request against `main`

## How your PR lands

The public `Glad-Labs/poindexter` repository is a build artifact: it is
force-rebuilt from a private source-of-truth monorepo on every upstream push,
so pull requests opened against it are **reviewed but never merged directly**
(you'll notice Dependabot's PRs close unmerged the same way). The happy path
for an accepted PR is:

1. A maintainer reviews your PR here.
2. Accepted changes are ported into the source-of-truth repo with credit to
   you (`Co-authored-by` on the ported commit).
3. The change appears in this repository on the next mirror sync (usually
   within a minute of the upstream merge).
4. Your PR is closed with a pointer to the landed commit.

A closed-unmerged PR whose changes are live on `main` is the success case
here, not a rejection.

## Signed commits

Commits on `main` and all release tags are expected to be GPG-signed so they
show a **Verified** badge on GitHub. Signed commits prove the work
actually came from the author's key, not a compromised account.

Quick setup (full walkthrough in [`docs/operations/commit-signing.md`](docs/operations/commit-signing.md)):

1. Generate a GPG key if you don't have one: `gpg --full-generate-key`
2. Tell git which key to use: `git config --global user.signingkey <KEYID>`
3. From the repo root: `bash scripts/setup-git-signing.sh`
   — sets `commit.gpgsign=true` and `tag.gpgsign=true` at the repo scope,
   then verifies signing works by creating a throwaway signed commit.
   **Fails loud** if anything is misconfigured, so you'll know immediately.
4. Upload the public key (`gpg --armor --export <KEYID>`) to your
   GitHub account settings so the Verified badge actually renders.

The pre-commit hook warns (but does not block) when a commit would be
unsigned — treat the warning as a signal to run the setup script.

## CI security gates

Every PR and every push to `main` runs three automated security gates (see `.github/workflows/security.yml`):

- **gitleaks** — secret scan across the diff + full history, blocks on any finding
- **Trivy** — filesystem CVE scan, fails the build on HIGH / CRITICAL fixed vulnerabilities
- **syft + grype** — generates a CycloneDX SBOM, scans it for HIGH / CRITICAL CVEs, and uploads the SBOM as a workflow artifact

Dependencies are kept current by Dependabot (`.github/dependabot.yml`) with a weekly PR batch — every ecosystem targets `main`. Patch and minor bumps auto-rebase against `main`; majors wait on human review.

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

## Testing conventions

A test only has value if it can FAIL when the code drifts. A test that mocks
both the input and the boundary it's checking validates nothing — it stays
green forever, even after the behavior it claims to cover is broken. Avoid that
trap:

- **Mock exactly one boundary per test.** Either drive the code with a real
  resource and assert on the real output, or stub the boundary and pass a bare
  sentinel as input — never both. Stubbing the input row _and_ patching the
  converter that reads it means neither is actually exercised.
- **Fake DB rows must be strict.** When a test hand-builds an `asyncpg.Record`,
  make `__getitem__` raise `KeyError` on a missing column
  (`row.__getitem__ = lambda self, k, _d=data: _d[k]`) and include only the
  columns the code under test reads. A loose faker that returns `None` for
  unknown keys lets a renamed/dropped column pass silently. Prefer the real
  `db_pool` fixture (runs migrations against a disposable Postgres) when you're
  testing column-level behavior.
- **For scalar queries, assert the query — not just the mocked return.** If a
  function runs `SELECT ... FROM some_table` and you mock the return value, the
  test won't notice a wrong table/column name. Capture the executed SQL and
  assert it targets the real schema (e.g. the column actually exists). A scalar
  mock that returns a plausible value hides a query that errors against the real
  database.
- **Don't delete coverage to make a test pass.** Test-collection count must not
  drop when you refactor a test.

## Reporting Bugs

Open an issue with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Your environment (OS, Python version, Docker version)

## Security

Report security vulnerabilities via GitHub Security Advisories: **<https://github.com/Glad-Labs/poindexter/security/advisories/new>** — never in public GitHub issues. See [SECURITY.md](SECURITY.md) for the full policy, including the email fallback if you can't use GitHub.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
