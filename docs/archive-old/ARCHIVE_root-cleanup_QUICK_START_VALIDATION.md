# 🚀 Quick Start: Content Pipeline Validation

**Time to Run:** 2-20 minutes depending on mode  
**Goal:** Validate content pipeline edge cases and API client refactoring  
**Status:** Ready to execute

---

## ⚡ 2-Minute Quick Start

### Windows PowerShell

```powershell
# Navigate to project
cd c:\Users\mattm\glad-labs-website

# Run quick smoke test (2 minutes)
.\scripts\run-validation-suite.ps1 -Mode quick

# Expected result:
# ✅ Health Check test passed
# ✅ Basic task creation test passed
# Status: All quick tests passed
```

### Linux/macOS

```bash
# Navigate to project
cd ~/glad-labs-website

# Run quick smoke test (2 minutes)
./scripts/run-validation-suite.sh quick

# Expected result:
# ✅ Health Check test passed
# ✅ Basic task creation test passed
# Status: All quick tests passed
```

---

## 🔍 5-Minute Edge Case Test

**Tests:** Unicode, long strings, special characters, validation

### Windows PowerShell

```powershell
.\scripts\run-validation-suite.ps1 -Mode edge-cases
```

### Linux/macOS

```bash
./scripts/run-validation-suite.sh edge-cases
```

**Expected Results:**

- ✅ Unicode characters (测试, Über, 🚀) work
- ✅ Long strings (200+ chars) accepted
- ✅ Special characters (!@#$%^&\*) handled
- ✅ Empty fields rejected with 422 error
- ✅ Invalid status rejected with 422 error

---

## 📊 15-20 Minute Full Validation

**Tests:** All 32 edge cases, performance, integration, error handling

### Windows PowerShell

```powershell
.\scripts\run-validation-suite.ps1 -Mode full
```

### Linux/macOS

```bash
./scripts/run-validation-suite.sh full
```

**What It Tests:**

- ✅ System health (3 tests)
- ✅ Basic functionality (4 tests)
- ✅ Edge cases (9 tests)
- ✅ Pipeline workflow (5 tests)
- ✅ Post creation (6 tests)
- ✅ Error handling (4 tests)
- ✅ Performance (3 tests)
- ✅ Integration (2 tests)

**Expected Output:**

```
32/32 tests passed ✅
Coverage: ~85% ✅
Duration: 15-20 seconds ✅
```

---

## ⚙️ Performance Test (10 minutes)

**Tests:** Concurrent requests, response times, large datasets

### Windows PowerShell

```powershell
.\scripts\run-validation-suite.ps1 -Mode performance
```

### Linux/macOS

```bash
./scripts/run-validation-suite.sh performance
```

**Generates:**

- Task creation response time baseline
- List tasks response time baseline
- Health check response time
- Concurrent request success rate

---

## 🛠️ Manual Testing (Advanced)

### Test Single Test Class

```bash
cd src/cofounder_agent
python -m pytest tests/test_content_pipeline_comprehensive.py::TestEdgeCases -v
```

### Test Single Test Method

```bash
python -m pytest tests/test_content_pipeline_comprehensive.py::TestEdgeCases::test_task_with_unicode_characters -v
```

### Test With Coverage Report

```bash
python -m pytest tests/test_content_pipeline_comprehensive.py -v --cov=. --cov-report=html
# Opens: htmlcov/index.html
```

### Watch Mode (auto-rerun on file changes)

```bash
pip install pytest-watch
ptw tests/ -v
```

---

## 📋 What Each Mode Tests

### Quick Mode (2 min)

```
TestSystemHealth
├─ test_health_endpoint (health check)
├─ test_metrics_endpoint (system metrics)
└─ test_root_endpoint (/)

TestBasicTaskCreation::test_create_task_with_minimal_fields
└─ Creates task with just required fields
```

### Edge Cases Mode (5 min)

```
TestEdgeCases
├─ test_task_with_unicode_characters (测试 🚀)
├─ test_task_with_very_long_string (200+ chars)
├─ test_task_with_special_characters (!@#$%^&*)
├─ test_task_with_null_optional_fields (None values)
├─ test_task_with_empty_required_field (empty string)
├─ test_task_with_missing_required_field (422)
├─ test_task_with_invalid_status (422)
├─ test_list_with_extreme_pagination (skip=999999)
└─ test_malformed_json (422)
```

### Full Mode (15-20 min)

```
All 32 tests + coverage report
```

### Performance Mode (10 min)

```
TestPerformance
├─ test_handle_large_result_set (1000+ items)
├─ test_create_many_tasks_concurrently (10 concurrent)
└─ test_execute_concurrent_api_calls (5 concurrent)

Plus manual response time testing
```

---

## ✅ Success Criteria

### All Modes Should Show

```
✅ All tests passed
✅ No errors or exceptions
✅ Expected HTTP status codes (200, 201, 422, etc.)
✅ Valid JSON responses
✅ Proper error messages for failures
```

### Specific Criteria

**Quick Mode:** 2 tests pass in < 5 seconds  
**Edge Cases:** 9 tests pass, all edge cases handled  
**Full Mode:** 32 tests pass, coverage > 80%, duration < 30 seconds  
**Performance:** Response times < 2 seconds

---

## 🔧 If Tests Fail

### Check Test Output

```
The script shows detailed error messages:
FAILED test_name - AssertionError: expected 201, got 500
```

### Common Issues

1. **Backend Not Running**

   ```
   Error: Cannot connect to http://localhost:8000

   Solution: Start backend first
   python -m uvicorn src.cofounder_agent.main:app --reload
   ```

2. **Database Connection Error**

   ```
   Error: Cannot connect to database

   Solution: Ensure PostgreSQL or SQLite is available
   ```

3. **Import Error**

   ```
   Error: No module named 'src.cofounder_agent'

   Solution: Run from project root, not from subdirectory
   ```

4. **Port Already In Use**

   ```
   Error: Port 8000 already in use

   Solution: Stop other processes or use different port
   ```

---

## 📚 Next Steps After Validation

### If All Tests Pass ✅

1. **Review Test Results**

   ```
   Check output for any warnings or slow tests
   Review coverage report if generated
   ```

2. **Update Components**

   ```
   Follow OVERSIGHT_HUB_MIGRATION_GUIDE.md
   Migrate each component to use new API client
   ```

3. **Deploy to Staging**

   ```
   Run tests on staging environment
   Verify with real data
   Gather team feedback
   ```

4. **Production Deployment**
   ```
   Create release tag
   Deploy to production
   Monitor for 24 hours
   ```

### If Tests Fail ❌

1. **Check Error Output** - Detailed error messages in console
2. **Review Test Code** - See test_content_pipeline_comprehensive.py for what's being tested
3. **Check Backend** - Verify endpoints match API client
4. **Check Database** - Verify tables and data exist
5. **Run Specific Test** - Isolate failing test for debugging

---

## 📖 Documentation

| Document                                                                                                      | Purpose                          |
| ------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| [CONTENT_PIPELINE_VALIDATION_GUIDE.md](../CONTENT_PIPELINE_VALIDATION_GUIDE.md)                               | Detailed test documentation      |
| [OVERSIGHT_HUB_MIGRATION_GUIDE.md](../OVERSIGHT_HUB_MIGRATION_GUIDE.md)                                       | Component migration instructions |
| [test_content_pipeline_comprehensive.py](../src/cofounder_agent/tests/test_content_pipeline_comprehensive.py) | Test source code                 |
| [apiClient.js](../web/oversight-hub/src/lib/apiClient.js)                                                     | API client source code           |

---

## 🎯 TL;DR - Fastest Path

```bash
# 1. Run quick test (2 min)
.\scripts\run-validation-suite.ps1 -Mode quick

# 2. If it passes, run full test (15 min)
.\scripts\run-validation-suite.ps1 -Mode full

# 3. If it passes, you're done validating! ✅
```

---

**Status: Ready to run! 🚀**

Pick your test mode above and run the command. All tests should pass.
