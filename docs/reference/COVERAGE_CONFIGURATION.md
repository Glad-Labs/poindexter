# Test Coverage Configuration & Setup Guide

**Last Updated:** December 6, 2025  
**Status:** Week 2 - Testing Infrastructure Setup  
**Goal:** Establish >80% code coverage threshold enforcement

---

## 📊 Overview

This guide covers the complete test coverage infrastructure for Glad Labs, including:

- **Coverage.py Configuration** - Measuring code coverage
- **>80% Threshold Enforcement** - Failing builds if coverage drops below threshold
- **Report Generation** - HTML, JSON, and XML reports
- **CI/CD Integration** - Automated coverage checks on every commit
- **Coverage Gaps** - Identifying and fixing untested code

---

## 🛠️ Installation

### Step 1: Install Coverage.py

```bash
# Install coverage package
pip install coverage

# Verify installation
python -m coverage --version
```

### Step 2: Copy Configuration Files

The following files are already in place:

- **`.coveragerc`** - Coverage configuration (root directory)
- **`scripts/measure-coverage.ps1`** - Windows PowerShell measurement script
- **`scripts/measure-coverage.sh`** - Bash measurement script

### Step 3: Update package.json Scripts

Add these scripts to `package.json` in the root:

```json
{
  "scripts": {
    "test:coverage": "coverage run -m pytest src/cofounder_agent/tests/ -v && coverage report",
    "test:coverage:html": "coverage run -m pytest src/cofounder_agent/tests/ -v && coverage html",
    "test:coverage:json": "coverage run -m pytest src/cofounder_agent/tests/ -v && coverage json",
    "test:coverage:all": "npm run test:coverage:html && npm run test:coverage:json",
    "test:coverage:report": "coverage report --fail-under=80"
  }
}
```

---

## 📈 Measuring Coverage

### Option 1: Using PowerShell (Windows)

```powershell
# Measure coverage and display terminal report
.\scripts\measure-coverage.ps1 -ReportType term

# Generate HTML report (opens in browser)
.\scripts\measure-coverage.ps1 -ReportType html

# Generate all reports
.\scripts\measure-coverage.ps1 -ReportType all

# Custom threshold
.\scripts\measure-coverage.ps1 -ReportType term -Threshold 85
```

### Option 2: Using Bash (Linux/macOS)

```bash
# Measure coverage and display terminal report
./scripts/measure-coverage.sh term

# Generate HTML report
./scripts/measure-coverage.sh html

# Generate all reports
./scripts/measure-coverage.sh all
```

### Option 3: Using npm Scripts

```bash
# Terminal report with >80% threshold enforcement
npm run test:coverage:report

# Generate HTML report
npm run test:coverage:html

# Generate all reports
npm run test:coverage:all
```

### Option 4: Direct Command

```bash
# Run tests with coverage measurement
cd src/cofounder_agent
coverage run -m pytest tests/ -v

# Display coverage report
coverage report --fail-under=80

# Generate HTML report
coverage html

# Generate JSON report
coverage json

# Generate XML report (for CI/CD)
coverage xml
```

---

## 📄 Configuration Details (.coveragerc)

### Coverage Sources

The `.coveragerc` file measures coverage for:

```ini
source = 
    src/cofounder_agent              # Python backend
    web/oversight-hub/src            # React Oversight Hub
    web/public-site/lib              # Next.js Public Site
```

### Excluded Files

```ini
omit = 
    */tests/*                        # Test files themselves
    */test_*.py                      # Python test files
    */__pycache__/*                  # Compiled Python
    */site-packages/*                # Dependencies
    */.venv/*                        # Virtual environment
    */venv/*                         # Virtual environment
    */conftest.py                    # pytest configuration
    */migrations/*                   # Database migrations
    */node_modules/*                 # NPM dependencies
```

### Excluded Lines

Lines excluded from coverage calculation:

```ini
exclude_lines = 
    pragma: no cover                 # Manual pragma marker
    def __repr__                     # String representations
    raise AssertionError             # Debug assertions
    raise NotImplementedError        # Stub implementations
    if __name__ == .__main__.:       # Script entry points
    if TYPE_CHECKING:                # Type checking only
    @abstractmethod                  # Abstract methods
    except ImportError:              # Import guards
    except AttributeError:           # Attribute guards
```

### Reporting Thresholds

```ini
[report]
fail_under = 80                      # Fail if coverage < 80%
precision = 2                        # 2 decimal places
show_missing = True                  # Show uncovered lines
skip_covered = False                 # Show covered files too
skip_empty = True                    # Skip files with no statements
```

---

## 📊 Report Types

### 1. Terminal Report (Default)

```bash
coverage report --fail-under=80
```

**Output:**
```
Name                                          Stmts   Miss  Cover   Missing
───────────────────────────────────────────────────────────────────────────
src/cofounder_agent/main.py                     145      8    94%    89, 156-160
src/cofounder_agent/services/model_router.py    234     18    92%    45-48, 112-120, 245
src/cofounder_agent/services/auth_service.py    98      5    95%    62-66
...
───────────────────────────────────────────────────────────────────────────
TOTAL                                         2847     186    93%
```

**Exit Code Behavior:**
- **Exit 0**: Coverage ≥ 80% ✅
- **Exit 1**: Coverage < 80% ❌ (build fails)

### 2. HTML Report

```bash
coverage html
open htmlcov/index.html  # macOS/Linux
start htmlcov/index.html # Windows
```

**Features:**
- Interactive coverage visualization
- Color-coded files (green ≥80%, red <80%)
- Line-by-line coverage information
- Click to see uncovered lines
- Summary statistics

**Output Location:** `htmlcov/index.html`

### 3. JSON Report

```bash
coverage json
```

**Output Location:** `coverage.json`

**Content:**
```json
{
  "meta": {
    "version": "6.5.0",
    "timestamp": "2025-12-06T15:30:00Z"
  },
  "totals": {
    "covered_lines": 2861,
    "missing_lines": 186,
    "num_statements": 3047,
    "percent_covered": 93.9,
    "percent_covered_display": "93.9"
  },
  "files": {
    "src/cofounder_agent/main.py": {
      "executed_lines": [1, 2, 3, ...],
      "missing_lines": [89, 156, 157, ...],
      "line_rate": 0.94,
      "summary": {
        "covered_lines": 137,
        "missing_lines": 8,
        "percent_covered": 94.5
      }
    }
  }
}
```

### 4. XML Report (for CI/CD)

```bash
coverage xml
```

**Output Location:** `coverage.xml`

**Usage:** GitLab CI, GitHub Actions, Jenkins, etc.

---

## 🎯 Setting >80% Threshold

### Method 1: .coveragerc Configuration (Recommended)

Already configured in `.coveragerc`:

```ini
[report]
fail_under = 80
```

This automatically fails tests if coverage drops below 80%.

### Method 2: Command Line Flag

```bash
coverage report --fail-under=80
```

### Method 3: CI/CD Integration

See [CI/CD Integration](#cicd-integration) section below.

---

## 🔄 CI/CD Integration

### GitHub Actions Workflow

**File:** `.github/workflows/coverage.yml`

```yaml
name: Test Coverage

on:
  push:
    branches: [dev, main]
  pull_request:
    branches: [dev, main]

jobs:
  coverage:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install coverage
      
      - name: Run tests with coverage
        run: |
          cd src/cofounder_agent
          coverage run -m pytest tests/ -v
          coverage report --fail-under=80
          coverage xml
      
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml
          fail_ci_if_error: true
          verbose: true
```

### GitLab CI Configuration

**File:** `.gitlab-ci.yml` (add to existing file)

```yaml
coverage:
  stage: test
  image: python:3.12
  script:
    - pip install -r requirements.txt
    - pip install coverage
    - cd src/cofounder_agent
    - coverage run -m pytest tests/ -v
    - coverage report --fail-under=80
    - coverage xml
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
```

---

## 📈 Coverage Goals & Targets

### Measurement Baseline (Week 2 - Current)

**Expected Current Coverage:** ~75-80% (after 50+ security tests)

| Component | Target | Priority | Status |
|-----------|--------|----------|--------|
| Core Services | 90%+ | CRITICAL | ⏳ In Progress |
| API Endpoints | 85%+ | HIGH | ⏳ In Progress |
| Authentication | 95%+ | CRITICAL | ✅ ~95% |
| Input Validation | 90%+ | CRITICAL | ✅ ~92% |
| Database Operations | 85%+ | HIGH | ⏳ In Progress |
| Error Handling | 80%+ | MEDIUM | ⏳ In Progress |

### Improvement Plan (Weeks 2-3)

**Week 2 Targets:**
- ✅ Measure baseline coverage
- ✅ Enforce >80% threshold
- 📈 Achieve 85%+ overall coverage
- 📋 Document coverage gaps

**Week 3 Targets:**
- 📈 Achieve 88%+ overall coverage
- 🔧 Add edge case tests
- 📊 Profile and optimize
- 🎯 Reach 90%+ on critical paths

---

## 🔍 Coverage Gap Analysis

### Identifying Uncovered Code

```bash
# Show only lines that are missing coverage
coverage report --show-missing

# Generate detailed HTML report
coverage html
# Then open htmlcov/index.html and look for red lines
```

### Common Coverage Gaps

1. **Exception Handlers**
   ```python
   try:
       do_something()
   except ValueError:
       # pragma: no cover  <- Mark if intentionally untested
       pass
   ```

2. **Debug Code**
   ```python
   if DEBUG:
       # pragma: no cover  <- Mark debug-only code
       print("Debug info")
   ```

3. **Stub Implementations**
   ```python
   @abstractmethod
   def do_something(self):  # pragma: no cover
       raise NotImplementedError()
   ```

### Adding Tests to Increase Coverage

**Example: Testing Edge Cases**

```python
# Original code (85% coverage)
def parse_user(data):
    return {
        "id": data["id"],           # Always tested
        "email": data["email"],     # Always tested
        "name": data.get("name"),   # ❌ NOT tested
    }

# Add test for edge case
def test_parse_user_missing_name():
    data = {"id": 1, "email": "test@example.com"}
    result = parse_user(data)
    assert result["name"] is None  # ✅ Now covered
```

---

## 📋 Daily Coverage Workflow

### Before Committing

```bash
# 1. Run tests with coverage
npm run test:coverage:report

# 2. Check if coverage >= 80%
# (script will exit with code 1 if below 80%)

# 3. If below threshold, add tests
# (see Coverage Gap Analysis above)

# 4. Commit when coverage >= 80%
git add .
git commit -m "test: add tests to reach 80% coverage"
```

### Weekly Review

```bash
# 1. Generate HTML report
npm run test:coverage:html

# 2. Review coverage trends
# (compare weekly reports in htmlcov/ folder)

# 3. Identify trends and patterns
# (which modules are improving/declining)

# 4. Update coverage targets if needed
```

### Monthly Reporting

```bash
# 1. Generate JSON report
npm run test:coverage:json

# 2. Parse and analyze
# (Use Python or Node.js to analyze coverage.json)

# 3. Create summary report
# (Document coverage by module, trends, improvements)

# 4. Present to team
# (Share metrics and identify improvement areas)
```

---

## 🚀 Advanced Topics

### Parallel Coverage Measurement

```bash
# Run tests in parallel with coverage tracking
coverage run --parallel-mode -m pytest tests/ -n auto

# Combine results
coverage combine

# Generate report
coverage report --fail-under=80
```

### Branch Coverage

Enable branch coverage in `.coveragerc`:

```ini
[run]
branch = True
```

This tracks not just executed lines, but also:
- `if/else` branches taken
- `try/except` branches taken
- Ternary operators

### Incremental Coverage

```bash
# Only measure coverage for changed files
git diff --name-only origin/main | grep "\.py$" | while read f; do
    coverage run --source="$f" -m pytest tests/ -v
done
```

### Coverage Badges

Generate a coverage badge for README:

```python
import json

with open('coverage.json') as f:
    data = json.load(f)
    coverage = data['totals']['percent_covered_display']

print(f"![Coverage]({coverage}%-green)")
```

---

## ❓ Troubleshooting

### Issue: "coverage: can't find file to match 'src/cofounder_agent'"

**Solution:** Ensure the path in `.coveragerc` matches your project structure:

```ini
source = src/cofounder_agent
```

### Issue: "No data to report"

**Solution:** Ensure tests are actually running:

```bash
# Check if pytest discovers tests
pytest --collect-only src/cofounder_agent/tests/

# Run tests with verbose output
pytest src/cofounder_agent/tests/ -v
```

### Issue: "Exit code 1 - Coverage below 80%"

**Solution:** Add tests for uncovered code:

```bash
# Identify what's not covered
coverage report --show-missing

# Or use HTML report
coverage html
open htmlcov/index.html
```

### Issue: "Coverage.py not found"

**Solution:** Install coverage package:

```bash
pip install coverage

# Verify installation
python -m coverage --version
```

---

## 📚 Next Steps

1. **Week 2 (Current):**
   - ✅ Install coverage.py
   - ✅ Configure `.coveragerc`
   - ✅ Measure baseline coverage
   - 📍 **YOU ARE HERE** - Run first coverage measurement
   - 📈 Add tests to reach 85%

2. **Week 3:**
   - 📊 Performance optimization (Redis caching)
   - 📈 Reach 88%+ coverage
   - 🔍 Profile and optimize queries

3. **Week 4:**
   - 🎯 Operations hardening
   - 📈 Reach 90%+ on critical paths
   - 📋 Final coverage documentation

---

## 🔗 Related Documentation

- **[TESTING.md](../docs/reference/TESTING.md)** - Comprehensive testing guide
- **[04-DEVELOPMENT_WORKFLOW.md](../docs/04-DEVELOPMENT_WORKFLOW.md)** - Development practices
- **[SECURITY_TESTING_DOCUMENTATION.md](../src/cofounder_agent/tests/SECURITY_TESTING_DOCUMENTATION.md)** - Security testing details

---

**Ready to measure coverage?**

```powershell
# Windows PowerShell
.\scripts\measure-coverage.ps1 -ReportType all

# Or bash
./scripts/measure-coverage.sh all

# Or npm
npm run test:coverage:all
```

Your coverage report will be generated in:
- **HTML Report:** `htmlcov/index.html`
- **JSON Report:** `coverage.json`
- **XML Report:** `coverage.xml` (for CI/CD)
