# 🚀 Quick Start: Coverage Measurement

**Last Updated:** December 6, 2025  
**Status:** Ready to Use ✅

---

## ⚡ 60-Second Setup

### 1. Install Coverage (One-Time)

```bash
# Windows (PowerShell)
pip install coverage

# macOS/Linux
pip install coverage

# Verify
python -m coverage --version
```

### 2. Run Measurement

```powershell
# Windows PowerShell - FASTEST WAY
cd c:\Users\mattm\glad-labs-website
.\scripts\measure-coverage.ps1 -ReportType all
```

```bash
# macOS/Linux
cd ~/glad-labs-website
./scripts/measure-coverage.sh all
```

### 3. View Reports

```powershell
# HTML Report (Opens in Browser)
Start-Process htmlcov/index.html

# Terminal Report (Already Displayed)
# Check console output for percentage and uncovered lines

# JSON Report Location
type coverage.json | more
```

---

## 📊 What You'll Get

✅ **Terminal Report** - Coverage % by file (displayed immediately)  
✅ **HTML Report** - Interactive visualization (auto-opens in browser)  
✅ **JSON Report** - Machine-readable format (for CI/CD)  
✅ **XML Report** - CI/CD integration format

---

## 🎯 Key Metrics to Look For

### Ideal Coverage Distribution

```
OVERALL COVERAGE TARGET: >80%

Ideal by Component:
  • Authentication Service:    95%+ ✅
  • Input Validation Service:  90%+ ✅
  • Core API Endpoints:        85%+ ⏳
  • Database Operations:       85%+ ⏳
  • Error Handling:            80%+ ⏳
  • Utilities/Helpers:         80%+ ⏳
```

---

## 🔍 Reading the HTML Report

1. **Open `htmlcov/index.html`** (auto-opens after script)
2. **Look at summary stats:**
   - Green: Good (>80% coverage)
   - Red: Needs work (<80% coverage)
3. **Click on a file to see:**
   - Green lines: Tested code
   - Red lines: Untested code
   - Numbers: How many times executed

---

## 📈 Coverage Goals

| Phase              | Target               | Status    |
| ------------------ | -------------------- | --------- |
| **Week 2.1** (Now) | Baseline measurement | ✅ Ready  |
| **Week 2.2**       | 85%+ overall         | ⏳ Coming |
| **Week 2.3**       | 88%+ overall         | ⏳ Coming |
| **Week 2.4**       | 90%+ critical paths  | ⏳ Coming |

---

## ⚙️ Command Reference

### Generate Specific Reports

```powershell
# HTML only (opens in browser)
.\scripts\measure-coverage.ps1 -ReportType html

# Terminal only (no browser)
.\scripts\measure-coverage.ps1 -ReportType term

# JSON only (for parsing)
.\scripts\measure-coverage.ps1 -ReportType json

# All reports
.\scripts\measure-coverage.ps1 -ReportType all
```

### Direct Commands

```bash
# Run tests with coverage measurement
cd src/cofounder_agent
coverage run -m pytest tests/ -v

# Display terminal report (fails if < 80%)
coverage report --fail-under=80

# Generate HTML (opens in explorer window)
coverage html

# Generate JSON (for scripting/parsing)
coverage json

# Generate XML (for CI/CD like Jenkins)
coverage xml
```

---

## ✅ Success Criteria

✅ Script runs without errors  
✅ All tests pass (0 failures)  
✅ Coverage report generated  
✅ Coverage ≥ 80% overall  
✅ Critical paths ≥ 90%

---

## 🐛 Troubleshooting

### "coverage: command not found"

```bash
pip install coverage
python -m coverage --version  # Verify
```

### "No data to report"

```bash
# Make sure tests are being discovered
pytest --collect-only src/cofounder_agent/tests/

# Run one test to verify
pytest src/cofounder_agent/tests/test_input_validation_webhooks.py::TestInputValidator::test_validate_string -v
```

### "Script cannot be loaded"

```powershell
# Allow script execution
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Then run again
.\scripts\measure-coverage.ps1
```

---

## 🎓 What's Next After Measurement?

1. **Review Results**
   - Open `htmlcov/index.html`
   - Look for red lines (uncovered code)
   - Note which modules need work

2. **Add Tests** (if coverage < 80%)
   - Find uncovered lines in HTML report
   - Write tests for those code paths
   - Re-run measurement

3. **Set Threshold** (>80%)
   - Already configured in `.coveragerc`
   - Builds fail if coverage drops below 80%
   - Commit and push changes

4. **Integrate with CI/CD** (GitHub Actions)
   - Coverage checks on every PR
   - Automatic coverage reports
   - Fail builds if threshold not met

---

## 📚 Full Documentation

For complete details, see:

- **[COVERAGE_CONFIGURATION.md](../docs/reference/COVERAGE_CONFIGURATION.md)** - 500+ line comprehensive guide
- **[TESTING.md](../docs/reference/TESTING.md)** - Testing best practices
- **[SECURITY_TESTING_DOCUMENTATION.md](../src/cofounder_agent/tests/SECURITY_TESTING_DOCUMENTATION.md)** - Security tests

---

## 🚀 Ready to Start?

**Copy and paste this command (Windows PowerShell):**

```powershell
cd c:\Users\mattm\glad-labs-website; .\scripts\measure-coverage.ps1 -ReportType all
```

**Or (Bash/macOS/Linux):**

```bash
cd ~/glad-labs-website && ./scripts/measure-coverage.sh all
```

---

**Status:** ✅ Configuration Complete | 🚀 Ready to Measure Coverage
