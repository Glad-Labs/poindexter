# GitHub Actions CI/CD Configuration for FastAPI Testing

# Save as: .github/workflows/tests.yml

name: FastAPI Tests

on:
push:
branches: [ main, develop, feat/* ]
pull_request:
branches: [ main, develop ]
schedule: # Run tests daily at 2 AM UTC - cron: '0 2 \* \* \*'

jobs:
test:
runs-on: ubuntu-latest

    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']
      fail-fast: false

    steps:
      # Checkout code
      - uses: actions/checkout@v3
        with:
          fetch-depth: 0  # Full history for better analysis

      # Setup Python
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}
          cache: 'pip'

      # Install dependencies
      - name: Install dependencies
        run: |
          cd src/cofounder_agent
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov pytest-xdist

      # Run linting
      - name: Lint with pylint
        continue-on-error: true
        run: |
          cd src/cofounder_agent
          pip install pylint
          pylint tests/ --disable=all --enable=E,F || true

      # Run unit tests
      - name: Run unit tests
        run: |
          cd src/cofounder_agent
          pytest tests/ -m unit -v --tb=short --junitxml=unit-results.xml

      # Run integration tests
      - name: Run integration tests
        run: |
          cd src/cofounder_agent
          pytest tests/ -m integration -v --tb=short --junitxml=integration-results.xml
        continue-on-error: true

      # Run all tests with coverage
      - name: Run all tests with coverage
        run: |
          cd src/cofounder_agent
          pytest tests/ -v \
            --tb=short \
            --cov=. \
            --cov-report=xml \
            --cov-report=html \
            --cov-report=term-missing \
            --junitxml=test-results.xml \
            -n auto

      # Upload test results
      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: test-results-${{ matrix.python-version }}
          path: |
            src/cofounder_agent/test-results.xml
            src/cofounder_agent/unit-results.xml
            src/cofounder_agent/integration-results.xml

      # Upload coverage report
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
        with:
          file: ./src/cofounder_agent/coverage.xml
          flags: unittests
          name: codecov-umbrella
          fail_ci_if_error: false
          verbose: true

      # Comment PR with coverage
      - name: Comment PR with coverage
        if: github.event_name == 'pull_request'
        uses: py-cov-action/python-coverage-comment-action@v3
        with:
          GITHUB_TOKEN: ${{ github.token }}
          MINIMUM_GREEN: 80
          MINIMUM_ORANGE: 70

security-check:
runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          cd src/cofounder_agent
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install bandit safety

      # Run security checks
      - name: Run bandit security check
        continue-on-error: true
        run: |
          cd src/cofounder_agent
          bandit -r . -ll --skip B101,B601 || true

      - name: Check for vulnerable dependencies
        continue-on-error: true
        run: |
          cd src/cofounder_agent
          safety check --json || true

performance-check:
runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          cd src/cofounder_agent
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-benchmark

      - name: Run performance tests
        run: |
          cd src/cofounder_agent
          pytest tests/ -m performance -v --benchmark-only || true
        continue-on-error: true

type-check:
runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          cd src/cofounder_agent
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install mypy types-requests types-aiofiles

      - name: Run type checking
        continue-on-error: true
        run: |
          cd src/cofounder_agent
          mypy . --ignore-missing-imports || true

results:
name: Test Results
needs: [test, security-check]
runs-on: ubuntu-latest
if: always()

    steps:
      - name: Download test results
        uses: actions/download-artifact@v3
        with:
          path: test-artifacts

      - name: Publish test results
        uses: EnricoMi/publish-unit-test-result-action@v2
        if: always()
        with:
          files: test-artifacts/**/test-results.xml
          check_name: Test Results
          comment_mode: always

---

# Local Test Configuration

# Save as: Makefile (or setup similar in your project)

.PHONY: test test-unit test-integration test-e2e test-coverage test-watch test-debug

# Run all tests

test:
cd src/cofounder_agent && pytest -v

# Run only unit tests

test-unit:
cd src/cofounder_agent && pytest -m unit -v

# Run only integration tests

test-integration:
cd src/cofounder_agent && pytest -m integration -v

# Run only E2E tests

test-e2e:
cd src/cofounder_agent && pytest -m e2e -v

# Run tests with coverage

test-coverage:
cd src/cofounder_agent && pytest --cov=. --cov-report=html --cov-report=term-missing

# Run tests in watch mode

test-watch:
cd src/cofounder_agent && pytest-watch -- -v

# Debug specific test

test-debug:
cd src/cofounder_agent && pytest --pdb -v

# Run performance tests

test-performance:
cd src/cofounder_agent && pytest -m performance -v

# Run security tests

test-security:
cd src/cofounder_agent && pytest -m security -v

# Run all tests excluding slow

test-fast:
cd src/cofounder_agent && pytest -m "not slow" -v

# Generate test report

test-report:
cd src/cofounder_agent && pytest --html=report.html --self-contained-html

---

# Pre-commit Hook Configuration

# Save as: .pre-commit-config.yaml

repos:

- repo: local
  hooks:
  - id: pytest
    name: pytest
    entry: bash -c 'cd src/cofounder_agent && pytest -q'
    language: system
    types: [python]
    pass_filenames: false
    stages: [commit]
    fail_fast: true
  - id: pytest-coverage
    name: pytest-coverage
    entry: bash -c 'cd src/cofounder_agent && pytest --cov=. --cov-fail-under=80 -q'
    language: system
    types: [python]
    pass_filenames: false
    stages: [push]

---

# VSCode Settings for Testing

# Save as: .vscode/settings.json (add to existing)

{
"python.testing.pytestEnabled": true,
"python.testing.pytestArgs": [
"src/cofounder_agent/tests",
"-v"
],
"python.testing.unittestEnabled": false,
"python.linting.enabled": true,
"python.linting.pylintEnabled": true,
"python.linting.pylintArgs": [
"--load-plugins=pylint_django"
],
"[python]": {
"editor.formatOnSave": true,
"editor.defaultFormatter": "ms-python.python"
},
"python.formatting.provider": "black",
"python.formatting.blackArgs": [
"--line-length=100"
]
}

---

# Test Report Template

# Use when submitting test results

## Test Execution Report

**Date:** $(date)
**Python Version:** $(python --version)
**Test Count:** $(pytest --collect-only -q | tail -1)

### Test Results Summary

```
Unit Tests:      [PASS/FAIL] (X/Y)
Integration:     [PASS/FAIL] (X/Y)
E2E Tests:       [PASS/FAIL] (X/Y)
Performance:     [PASS/FAIL] (X/Y)
Security:        [PASS/FAIL] (X/Y)
```

### Coverage Summary

```
Overall Coverage: XX%
- Critical Paths: XX%
- Business Logic: XX%
- Error Handling: XX%
```

### Performance

- Total Test Time: X.XXs
- Fastest Test: X.XXs
- Slowest Test: X.XXs
- Average: X.XXs

### Issues Found

- [ ] Failing tests
- [ ] Coverage drops
- [ ] Performance regressions
- [ ] Security warnings

---

# Quick Setup Commands

# 1. Create GitHub Actions workflow

mkdir -p .github/workflows

# Copy tests.yml content above

# 2. Setup local Makefile

# Copy Makefile content above

# 3. Setup pre-commit

pip install pre-commit

# Copy .pre-commit-config.yaml content above

pre-commit install

# 4. Run first test with GitHub Actions

git add .github/workflows/tests.yml
git commit -m "Add GitHub Actions CI/CD for tests"
git push

# Monitor at: https://github.com/YOUR_REPO/actions
