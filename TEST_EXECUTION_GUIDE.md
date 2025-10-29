# Quick Start: Run Tests & Identify Issues

**Status:** Ready to Execute  
**Time Required:** 15-30 minutes  
**Commands to Run:** 5

---

## Step 1: Run Backend Tests

### Command 1A: Run All Backend Tests with Verbose Output

```bash
cd src/cofounder_agent
python -m pytest tests/ -v --tb=short
```

**Expected Output:**

- If passing: ✅ 41+ tests pass
- If failing: ❌ Shows which tests fail and why

### Command 1B: Run Backend Tests with Coverage

```bash
cd src/cofounder_agent
python -m pytest tests/ -v --cov=. --cov-report=html --cov-report=term
```

**Expected Output:**

- Coverage report in `htmlcov/index.html`
- Terminal shows: Lines covered, missing, etc.

### Command 1C: Run Specific Test File

If you want to test one module:

```bash
cd src/cofounder_agent
python -m pytest tests/test_unit_comprehensive.py -v
# OR
python -m pytest tests/test_api_integration.py -v
# OR
python -m pytest tests/test_e2e_comprehensive.py -v
```

---

## Step 2: Run Frontend Tests

### Command 2A: Run All Frontend Tests (npm)

```bash
npm run test:frontend:ci
```

**Expected Output:**

- Jest runs all tests in `web/public-site/__tests__/` and `web/oversight-hub/__tests__/`
- Shows: Pass/fail, coverage %

### Command 2B: Run Frontend Tests in Watch Mode (Better for Development)

```bash
npm test
# Then press 'a' to run all tests, or 'p' for pattern matching
```

### Command 2C: Run Tests for Specific Component

```bash
npm test SettingsManager  # Tests only files with SettingsManager in name
npm test Header           # Tests only Header components
```

---

## Step 3: Full Test Suite (Both Frontend & Backend)

### Command 3: Run Everything

```bash
npm test
```

**This runs:**

- ✅ All frontend tests (Jest)
- ✅ All backend tests (pytest)
- ✅ Both in parallel

**Expected Output:**

- Shows overall pass/fail
- Lists any failures with details

---

## Step 4: Smoke Tests (Quick Validation)

### Command 4A: Backend Smoke Tests (5-10 min)

```bash
npm run test:python:smoke
```

### Command 4B: Quick Frontend Test

```bash
npm test -- PostCard
```

---

## Step 5: Check Test Coverage

### Command 5A: Generate Full Coverage Report

```bash
# Backend coverage
cd src/cofounder_agent
python -m pytest tests/ --cov=. --cov-report=html

# Frontend coverage
npm test -- --coverage

# View reports
# Backend: htmlcov/index.html
# Frontend: coverage/lcov-report/index.html
```

### Command 5B: Show Coverage by File

```bash
cd src/cofounder_agent
python -m pytest tests/ --cov=. --cov-report=term-missing
```

---

## 📊 What to Look For

### ✅ Success Indicators

If you see:

```
======================== 41 passed in 2.34s ========================
```

or for frontend:

```
PASS  web/public-site/__tests__/...
PASS  web/oversight-hub/__tests__/...

Test Suites: 8 passed, 8 total
Tests:       52 passed, 52 total
```

### ❌ Failure Indicators

Look for:

```
FAILED tests/test_main_endpoints.py::TestSomething::test_something_fails
```

Note:

- The test file name: `test_main_endpoints.py`
- The test class: `TestSomething`
- The specific test: `test_something_fails`
- The reason: (shown on next lines)

---

## 🔧 Common Issues & Solutions

### Issue 1: `ModuleNotFoundError: No module named 'pytest'`

**Solution:**

```bash
pip install pytest pytest-asyncio pytest-cov
```

### Issue 2: `ModuleNotFoundError` in test imports

**Solution:**

```bash
cd src/cofounder_agent
# Make sure you're in the right directory
```

### Issue 3: Database connection errors in tests

**Solution:**

- Tests should use mock database (check conftest.py)
- If not mocked, they're integration tests that need real DB
- See "Fix Database Mocking" section below

### Issue 4: Jest can't find components

**Solution:**

```bash
# Clear Jest cache
npm test -- --clearCache

# Then run again
npm test
```

### Issue 5: Timeout errors in tests

**Solution:**

- Async tests taking too long
- Check for infinite loops or slow operations
- May need to increase timeout in pytest.ini or jest.config.js

---

## 🛠️ Fix Database Mocking (If Tests Fail)

If backend tests are trying to connect to real database, check `conftest.py`:

```python
# Should have this:
@pytest.fixture
def mock_database():
    """Mock database for testing"""
    # Create in-memory SQLite or mock
    return MockDatabase()

# All tests should use:
def test_something(mock_database):
    # Use mock_database instead of real one
```

If missing, we'll add this in Phase 3.

---

## 📝 Record Results

After running tests, save this info:

```markdown
## Test Results - [DATE]

### Backend Tests

- Command: npm run test:python
- Result: [# passed] / [# total]
- Coverage: [%]
- Issues: [List any failures]

### Frontend Tests

- Command: npm test
- Result: [# passed] / [# total]
- Coverage: [%]
- Issues: [List any failures]

### Failures to Fix

1. [List each failure]
2. [With details]
```

---

## 🎯 Next Steps After Running Tests

### If All Tests Pass ✅

- Great! Move to Phase 3: Implement missing tests
- Can skip Phase 2: Fixing failures

### If Some Tests Fail ❌

- Document which tests fail (copy from terminal)
- Note the error messages
- We'll fix them one by one

### If Tests Can't Run 🔴

- Check prerequisites (Python 3.11+, Node 18+)
- Verify dependencies installed:
  ```bash
  npm install
  pip install -r src/cofounder_agent/requirements.txt
  ```

---

## 📞 Command Reference (Copy-Paste Ready)

```bash
# Run all tests (backend + frontend)
npm test

# Backend only
cd src/cofounder_agent && python -m pytest tests/ -v

# Frontend only
npm run test:frontend:ci

# With coverage
npm run test:python:smoke && npm test -- --coverage

# Quick validation
npm run test:python:smoke

# Specific test
cd src/cofounder_agent && python -m pytest tests/test_unit_comprehensive.py -v

# Clear caches
npm test -- --clearCache
rm -rf .pytest_cache
```

---

**Ready? Pick a command above and run it. Report results in the next message!**

---

## Expected Timeline

```
Task 1: Run backend tests          - 2-3 min
Task 2: Run frontend tests         - 3-5 min
Task 3: Check coverage             - 2-3 min
Task 4: Document failures          - 3-5 min
Task 5: Analyze results            - 5 min

Total:                             - ~15-25 min
```

**Let's go! 🚀**
