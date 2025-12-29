# Phase 3 Quick Start Guide - Target 50%+ Coverage

**Current Status:** Phase 2 Complete - 40.21% coverage (111 tests)  
**Phase 3 Goal:** 50%+ coverage (180+ tests)  
**Estimated Effort:** 6-8 hours  
**Start Date:** Ready to begin

---

## 🎯 Phase 3 Overview

Phase 3 focuses on expanding test coverage to key routes that have significant opportunities:

- ✅ CMS routes (16.80% → 40%+)
- ✅ Content routes (33.23% → 50%+)
- ✅ Database service (13.59% → 25%+)

---

## 📋 Files to Create

### 1. test_cms_routes.py (15-20 tests, ~300-400 lines)

**Scope:** Create, Read, Update, Delete for CMS content (posts, categories, tags)

**Test Classes to Create:**

```python
class TestCMSPostsCreate:
    # Test POST /api/cms/posts endpoint
    # 4-5 tests covering:
    # - Valid post creation
    # - Missing required fields
    # - Duplicate slug handling
    # - Authorization checks

class TestCMSPostsList:
    # Test GET /api/cms/posts endpoint
    # 3-4 tests covering:
    # - List pagination
    # - Category filtering
    # - Tag filtering
    # - Sorting

class TestCMSPostsUpdate:
    # Test PUT /api/cms/posts/{id} endpoint
    # 3-4 tests covering:
    # - Update existing post
    # - Publish/unpublish logic
    # - Category reassignment
    # - 404 for non-existent post

class TestCMSPostsDelete:
    # Test DELETE /api/cms/posts/{id} endpoint
    # 2-3 tests covering:
    # - Delete existing post
    # - 404 for non-existent
    # - Authorization checks

class TestCMSCategories:
    # Test category CRUD
    # 2-3 tests for create/list/update/delete

class TestCMSTags:
    # Test tag CRUD
    # 2-3 tests for create/list/update/delete
```

**Expected Coverage:** `cms_routes.py` from 16.80% → 40%+ (+23pp)  
**Files Affected:** cms_routes.py (125 statements)

---

### 2. test_content_routes.py (20-25 tests, ~400-500 lines)

**Scope:** Content generation endpoints, validation, error handling

**Test Classes to Create:**

```python
class TestContentGenerate:
    # Test POST /api/content/generate endpoint
    # 5-6 tests covering:
    # - Valid generation request
    # - Ollama integration
    # - Missing topic field
    # - Too-long content request
    # - Async task creation
    # - Response format validation

class TestContentValidation:
    # Test validation logic
    # 3-4 tests covering:
    # - SEO validation
    # - Content length limits
    # - Keyword validation
    # - Image count limits

class TestContentGeneration:
    # Test content generation pipeline
    # 4-5 tests covering:
    # - Multi-format output
    # - SEO metadata generation
    # - Image selection
    # - Markdown formatting
    # - Database persistence

class TestContentList:
    # Test GET /api/content endpoint
    # 2-3 tests covering:
    # - List all content
    # - Filter by status
    # - Pagination

class TestContentRetrieve:
    # Test GET /api/content/{id} endpoint
    # 3-4 tests covering:
    # - Get single content
    # - 404 handling
    # - Response format
    # - Secret field redaction

class TestContentDelete:
    # Test DELETE /api/content/{id} endpoint
    # 2-3 tests covering:
    # - Delete existing content
    # - 404 handling
    # - Cascade delete related data
```

**Expected Coverage:** `content_routes.py` from 33.23% → 50%+ (+17pp)  
**Files Affected:** content_routes.py (316 statements), SEO generator, content validation

---

### 3. test_database_service.py (10-15 tests, ~200-300 lines)

**Scope:** Database operations with proper mocking

**Test Classes to Create:**

```python
class TestDatabaseConnections:
    # Test database connection management
    # 3-4 tests covering:
    # - Connection pool initialization
    # - Connection error handling
    # - Retry logic
    # - Cleanup on shutdown

class TestDatabaseCRUD:
    # Test basic CRUD operations
    # 4-5 tests covering:
    # - Create operations
    # - Read/retrieve operations
    # - Update operations
    # - Delete operations
    # - Transaction handling

class TestDatabaseQueries:
    # Test complex queries
    # 2-3 tests covering:
    # - Filtered queries
    # - Joined queries
    # - Pagination
    # - Sorting

class TestDatabaseErrors:
    # Test error handling
    # 2-3 tests covering:
    # - Constraint violations
    # - Invalid data types
    # - Missing required fields
    # - Concurrent access
```

**Expected Coverage:** `database_service.py` from 13.59% → 25%+ (+11pp)  
**Files Affected:** database_service.py (309 statements)

---

## 📊 Phase 3 Execution Plan

### Step 1: Create test_cms_routes.py (1-2 hours)

```bash
cd src/cofounder_agent/tests

# Create file with structure from above
# Add 15-20 test methods
# Run and verify: pytest test_cms_routes.py -v
```

### Step 2: Create test_content_routes.py (2-3 hours)

```bash
# Create file with structure from above
# Add 20-25 test methods
# Run and verify: pytest test_content_routes.py -v
```

### Step 3: Create test_database_service.py (1-2 hours)

```bash
# Create file with database mocking fixtures
# Add 10-15 test methods
# Run and verify: pytest test_database_service.py -v
```

### Step 4: Run Full Test Suite (30 minutes)

```bash
cd src/cofounder_agent

# Run all tests
python -m pytest tests/ -v --tb=short

# Measure coverage
python -m coverage run -m pytest tests/ -q
python -m coverage report --precision=2

# Generate HTML report
python -m coverage html
```

### Step 5: Document Results (30 minutes)

- Update coverage metrics
- Document new test patterns
- Create Phase 3 completion report
- Plan Phase 4 if needed

---

## 🔍 Testing Patterns to Use

### Pattern: Route Testing with Mocks

```python
def test_content_generate_with_ollama(self, admin_token, monkeypatch):
    """Generate content using mocked Ollama"""

    # Mock the Ollama call
    def mock_ollama_generate(prompt, model):
        return "Generated content based on prompt"

    monkeypatch.setattr("services.ollama_client.generate", mock_ollama_generate)

    # Call the endpoint
    response = client.post(
        "/api/content/generate",
        json={"topic": "AI", "length": "500", "keywords": ["AI", "ML"]},
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify
    assert response.status_code == 200
    assert "Generated content" in response.json()["content"]
```

### Pattern: Database Mocking

```python
def test_database_create_post(self, monkeypatch):
    """Test database create operation with mocking"""

    # Mock database session
    mock_db = MagicMock()
    mock_db.execute.return_value.inserted_primary_key = [123]

    monkeypatch.setattr("services.database_service.get_db", mock_db)

    # Call service
    result = database_service.create_post({
        "title": "Test",
        "slug": "test",
        "content": "Test content"
    })

    # Verify
    assert result["id"] == 123
    mock_db.commit.assert_called_once()
```

### Pattern: Authorization Testing

```python
def test_cms_post_create_unauthorized(self, user_token):
    """Test that users can't create posts"""

    response = client.post(
        "/api/cms/posts",
        json={"title": "Test", "slug": "test", "content": "Test"},
        headers={"Authorization": f"Bearer {user_token}"}
    )

    # User should get 403 (forbidden)
    assert response.status_code in [403, 401]
```

---

## 🎯 Success Criteria

- ✅ Create all 3 test files (~50-60 new tests)
- ✅ Achieve 180+ total tests passing
- ✅ Coverage reaches 50%+ (40.21% → 50%+)
- ✅ No regressions in Phase 1/2 tests
- ✅ All test files properly organized with clear naming
- ✅ Fixtures and patterns documented

---

## 📈 Expected Results

```
Current State (Phase 2):
- Total Tests: 111
- Coverage: 40.21%
- Pass Rate: 81%

After Phase 3:
- Total Tests: 180+
- Coverage: 50-55% (target: 50%)
- Pass Rate: 80%+ (maintain quality)

Coverage by Route After Phase 3:
- settings_routes.py: 83.25% (no change needed)
- cms_routes.py: 40%+ (from 16.80%)
- content_routes.py: 50%+ (from 33.23%)
- task_routes.py: 63.61% (no change needed)
- database_service.py: 25%+ (from 13.59%)
- Overall: 50-55% (from 40.21%)
```

---

## 🚀 Quick Commands

```bash
# CD to tests directory
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent\tests

# Run all Phase 3 tests
pytest test_cms_routes.py test_content_routes.py test_database_service.py -v

# Run with coverage
python -m coverage run -m pytest test_cms_routes.py test_content_routes.py test_database_service.py -q
python -m coverage report

# Run full test suite
cd ..
python -m pytest tests/ -v --tb=short

# Generate coverage HTML
python -m coverage html
```

---

## 📋 Checklist

- [ ] Create test_cms_routes.py (15-20 tests)
- [ ] Create test_content_routes.py (20-25 tests)
- [ ] Create test_database_service.py (10-15 tests)
- [ ] Run full test suite and verify passing
- [ ] Measure coverage (target: 50%+)
- [ ] Update documentation
- [ ] Create Phase 3 completion report
- [ ] Plan next steps (CI/CD integration)

---

**Ready to start Phase 3?** Begin with test_cms_routes.py - it has the most straightforward tests and highest coverage gain potential!
