# Testing Execution Guide

**Updated:** February 21, 2026  
**Status:** Complete Testing Infrastructure Ready for Production

---

## Quick Start: Run All Tests

```bash
# From repository root, run everything
npm run test:unified

# Or run specific test suites
npm run test:python              # Python backend tests only
npm run test:playwright          # Browser/UI tests only
npm run test:api                 # API integration tests only
```

## Test Execution Patterns

### By Testing Phase (Recommended)

#### Phase 1: Smoke Tests (30 seconds)
Quick validation that system is working:
```bash
npm run test:python:integration -- -m smoke
```

Validates:
- Service is alive
- Database is connected
- Basic APIs respond
- Infrastructure is ready

#### Phase 2: Integration Tests (2-5 minutes)
Core functionality testing:
```bash
npm run test:python:integration
npm run test:api
```

Validates:
- All CRUD operations work
- Workflows execute
- Data flows through system
- Error handling works

#### Phase 3: Full Suite (10-15 minutes)
Comprehensive testing:
```bash
npm run test:unified
```

Validates:
- All 170+ tests pass
- Error scenarios covered
- End-to-end workflows
- Full API coverage
- Performance acceptable

#### Phase 4: Slow Tests (30+ minutes)
Extended testing for thorough validation:
```bash
npm run test:unified -- -m slow
```

Validates:
- Long-running operations
- Stress testing
- Performance baselines
- Resource cleanup

### By Test Type

#### Error Handling Tests
```bash
poetry run pytest tests/integration/test_error_scenarios.py -v
```

#### Workflow Tests
```bash
poetry run pytest tests/integration/test_full_stack_workflows.py -v
```

#### Endpoint Tests
```bash
poetry run pytest tests/integration/test_api_endpoint_coverage.py -v
```

#### Fixture Validation
```bash
poetry run pytest tests/fixtures_validation.py -v
npm run test:playwright -- fixtures-validation.spec.ts
```

### By Development Stage

#### During Development (Fast Feedback)
```bash
# Watch mode with fast tests only
poetry run pytest tests/integration/test_api_integration.py -v --tb=short -x
```

Runs only essential tests, stops on first failure for rapid iteration.

#### Before Commit
```bash
npm run test:unified:coverage --fast
node scripts/test-runner-validation.js
```

Runs fast tests plus infrastructure checks.

#### Before Merge to Main
```bash
npm run test:unified:coverage
npm run test:python:performance -- --baseline
```

Full coverage report and performance benchmarks.

#### Post-Deployment (Production Verification)
```bash
npm run test:python:integration -- -m smoke
npm run test:playwright -- --suite=smoke
```

Smoke tests to verify deployed system.

## Test Filtering & Selection

### Filter by Marker
```bash
# Run only async integration tests
poetry run pytest -m integration

# Run only performance tests
poetry run pytest -m performance

# Run only auth/security tests
poetry run pytest -m auth

# Skip slow tests
poetry run pytest -m "not slow"

# Concurrent tests only
poetry run pytest -m concurrent
```

### Filter by Filename
```bash
# Run error scenario tests
poetry run pytest tests/integration/test_error_scenarios.py

# Run single test file
poetry run pytest tests/integration/test_error_scenarios.py::test_invalid_json_payload

# Run tests matching pattern
poetry run pytest tests/integration/ -k "workflow"
```

### Run in Parallel
```bash
# Run with pytest-xdist (install if needed)
poetry run pytest tests/integration/ -n auto  # Use all CPUs

# Run with limited workers
poetry run pytest tests/integration/ -n 4     # Use 4 workers
```

## Test Reports & Coverage

### Generate Coverage Report
```bash
npm run test:unified:coverage
# Report available in: coverage/index.html
```

### View Coverage by File
```bash
poetry run pytest tests/ --cov=src/cofounder_agent --cov-report=term-missing
```

### Generate HTML Report
```bash
poetry run pytest tests/ --cov=src/cofounder_agent --cov-report=html:coverage
# Open: coverage/index.html
```

### Generate JUnit XML (for CI/CD)
```bash
npm run test:unified -- --junit-xml=test-results.xml
```

## Debugging Tests

### Run Single Test with Verbose Output
```bash
poetry run pytest tests/integration/test_error_scenarios.py::test_invalid_json_payload -vv
```

### Run with Full Stack Trace
```bash
poetry run pytest tests/integration/ --tb=long
```

### Run with Print Statements Visible
```bash
poetry run pytest tests/integration/ -s
```

### Run with Python Debugger
```bash
poetry run pytest tests/integration/test_api_integration.py -vv --pdb

# At breakpoint:
# (Pdb) continue     # Resume
# (Pdb) next         # Step
# (Pdb) p variable   # Print variable
# (Pdb) q            # Quit
```

### Run with Pytest Breakpoint
Add to test:
```python
def test_something():
    breakpoint()  # Drops into pdb
    # ... test code ...
```

Then run normally - will pause at breakpoint.

## Playwright Test Execution

### Run UI Tests Only
```bash
npm run test:playwright
```

### Run Single Playwright Test File
```bash
npm run test:playwright -- web/public-site/e2e/fixtures-validation.spec.ts
```

### Run in UI Mode (Interactive)
```bash
npx playwright test --ui
```

### Run with Test Generator
```bash
npx playwright codegen http://localhost:3000
```

Records clicks/inputs, generates test code.

### Run Tests Against Specific Browser
```bash
npx playwright test --project chromium
npx playwright test --project firefox
npx playwright test --project webkit
```

### Run Tests with Headed Browser (See Browser)
```bash
npx playwright test --headed
```

### Run Tests in Debug Mode
```bash
npx playwright test --debug
```

Interactive mode - step through, inspect elements, modify during run.

## CI/CD Test Execution

### GitHub Actions (from .github/workflows/)
```yaml
- name: Run Tests
  run: npm run test:unified -- --ci --coverage
```

### Local CI Simulation
```bash
npm run test:ci
```

### Test Results Upload
```bash
npm run test:unified -- --junit-xml=test-results.xml
npm run test:unified -- --coverage --coverage-report=cobertura
```

## Common Testing Workflows

### "I changed something, did I break anything?"
```bash
npm run test:python:integration -k "api" -x
```

Tests API integration only, stops on first failure.

### "This test is flaky, when does it fail?"
```bash
poetry run pytest tests/integration/test_api_integration.py::test_name -v --count=10
```

Runs test 10 times to catch flakiness.

### "Let me see what the test does"
```bash
npx playwright test --debug --headed
```

Debug mode with browser visible, step through test.

### "Performance is degraded, which tests are slow?"
```bash
npm run test:unified -- --durations=20
```

Shows slowest 20 tests.

### "I need coverage for specific module"
```bash
poetry run pytest tests/ --cov=src/cofounder_agent/agents --cov-report=html
```

Coverage for specific package only.

### "Let's validate this new workflow works"
```bash
poetry run pytest tests/integration/test_full_stack_workflows.py -v -k "workflow_name"
```

Run specific workflow tests.

## Test Environment Setup

### Set Python Path
```bash
export PYTHONPATH=.:src:src/cofounder_agent
```

### Load Environment Variables
```bash
# Tests automatically load .env.local
# Ensure you have:
DATABASE_URL=...
OPENAI_API_KEY=...
# etc.
```

### Use Different Environment
```bash
# Copy environment
cp .env.staging .env.local

# Run tests
npm run test:unified
```

### Reset Test Database
```bash
# Cleanup test data
poetry run pytest tests/ --cleanup

# Or manually
psql $DATABASE_URL -c "DELETE FROM tasks WHERE id LIKE 'test-%';"
```

## Continuous Integration Setup

### Pre-Commit Hook (Git)
```bash
#!/bin/bash
npm run test:python:integration -- -x
```

### GitHub Actions Example
```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
      - run: npm install
      - run: npm run test:unified --ci
```

### GitLab CI Example
```yaml
test:
  image: python:3.12
  script:
    - npm install
    - npm run test:unified --ci
```

### Railway Deployment Tests
```bash
# Tests run automatically on deployment
# Defined in Procfile release phase
release: npm run test:unified -- --ci
```

## Test Performance Optimization

### Run Tests in Parallel
```bash
# Playwright runs 4 browsers in parallel by default
npm run test:playwright

# Pytest with multiple workers
poetry run pytest -n auto tests/integration/
```

### Skip Slow Tests During Development
```bash
poetry run pytest -m "not slow" tests/integration/
```

### Run Only Recently Changed Tests
```bash
# Using pytest-git integration
poetry run pytest --co -m integration
```

## Troubleshooting Common Issues

### "Tests fail with 'database connection refused'"
```bash
# Ensure PostgreSQL is running
psql -U postgres

# Or check connection string
echo $DATABASE_URL

# Restart PostgreSQL
# macOS:  brew services restart postgresql
# Ubuntu: sudo systemctl restart postgresql
```

### "Import errors in tests"
```bash
# Ensure Python path includes src/
export PYTHONPATH=.:src:src/cofounder_agent

# Or reinstall with pytest
npm run test:python
```

### "Flaky test that sometimes passes"
```bash
# Run test multiple times
poetry run pytest tests/integration/test_name.py::test_func -v --count=20

# If > 5% failure rate, test is flaky
# Add retries to flaky test:
@pytest.mark.flaky(reruns=3)
def test_something():
    ...
```

### "Playwright browsers not installed"
```bash
npx playwright install

# Or with specific browser
npx playwright install chromium
```

### "Tests run slowly"
```bash
# Check slowest tests
npm run test:unified -- --durations=20

# Run parallel tests
poetry run pytest -n auto tests/integration/

# Skip slow tests for development
poetry run pytest -m "not slow"
```

## Test Maintenance Schedule

### Daily
- Run integration tests during development
- Fix broken tests same day

### Before Release
- Run full test suite with coverage
- Address any coverage gaps
- Review flaky test patterns

### Weekly
- Run performance benchmarks
- Review test execution times
- Archive new obsolete tests

### Monthly
- Full audit of test coverage
- Identify new gap areas
- Refactor slow/brittle tests

---

## Summary: Common Commands

| Command | Purpose | Duration |
|---------|---------|----------|
| `npm run test:unified` | Run everything | 10-15 min |
| `npm run test:python:integration` | Backend tests | 2-5 min |
| `npm run test:playwright` | UI/browser tests | 3-8 min |
| `npm run test:unified:coverage` | With coverage report | 15-20 min |
| `npm run test:python:performance` | Performance benchmarks | 5-10 min |
| `npm run test:api` | API endpoints only | 1-2 min |
| `node scripts/test-runner-validation.js` | Infrastructure check | 30 sec |

**Next:** See [TESTING_MAINTENANCE_SCHEDULE.md](TESTING_MAINTENANCE_SCHEDULE.md) for ongoing maintenance procedures.
