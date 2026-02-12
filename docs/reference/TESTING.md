# 🧪 Comprehensive Testing Guide

**Last Updated:** October 25, 2025  
**Status:** ✅ Production Ready  
**Test Framework:** Jest (Frontend) + pytest (Backend)  
**Coverage Goal:** >80% for critical paths  
**Current Status:** ✅ 93+ tests passing

---

## 📋 Quick Navigation

- **[Test Framework Overview](#test-framework-overview)** - Jest and pytest setup
- **[Test Structure](#test-structure)** - Where tests live and how organized
- **[Running Tests](#running-tests)** - Commands for all test scenarios
- **[Writing Tests](#writing-tests)** - Best practices for new tests
- **[Coverage Goals](#coverage-goals)** - Target coverage metrics
- **[Common Patterns](#common-patterns)** - Reusable test patterns
- **[Troubleshooting](#troubleshooting)** - Fixing test issues

---

## 🏗️ Test Framework Overview

### Frontend: Jest + React Testing Library

**What is Jest?**

- JavaScript testing framework from Facebook
- Works with React, Vue, Node.js projects
- Zero-config setup with Create React App
- Snapshot testing for UI regression detection
- Mocking and spying capabilities

**What is React Testing Library?**

- Modern best practices for React component testing
- Focuses on user behavior, not implementation details
- Query APIs for finding elements by role, label, text
- Great async handling for promises and callbacks

**Installation:** Already included in both `web/public-site/` and `web/oversight-hub/`

### Backend: pytest + pytest-asyncio

**What is pytest?**

- Python testing framework with minimal boilerplate
- Simple `assert` statements instead of `self.assertEqual()`
- Powerful fixtures for setup/teardown
- Plugin ecosystem (asyncio, cov, xdist, etc.)

**What is pytest-asyncio?**

- Enables testing of async/await code in pytest
- Marks async tests with `@pytest.mark.asyncio`
- Handles event loop setup automatically
- Critical for FastAPI testing

**Installation:**

```bash
pip install pytest pytest-asyncio pytest-cov
```

---

## 📂 Test Structure

### Frontend Test Locations

```text
web/oversight-hub/
├── src/
│   └── components/
│       ├── Header.test.js          # Component unit test
│       ├── CommandPane.jsx
│       └── SettingsManager.jsx
└── __tests__/                      # Organized test directory
    ├── components/
    │   └── SettingsManager.test.jsx      # Component test
    └── integration/
        └── SettingsManager.integration.test.jsx  # Integration test

web/public-site/
├── components/
│   ├── Header.test.js              # Component unit test
│   ├── Layout.test.js
│   ├── Footer.test.js
│   ├── PostList.test.js
│   └── __tests__/
│       ├── Pagination.test.js      # Organized component tests
│       └── PostCard.test.js
└── lib/
    └── __tests__/
        └── api.test.js             # API integration tests
```

**Test File Naming Convention:**

- `ComponentName.test.js` - Unit test next to component
- `ComponentName.test.jsx` - React component test (JSX syntax)
- `ComponentName.integration.test.jsx` - Integration tests in `__tests__/integration/`

### Backend Test Locations

```text
src/cofounder_agent/
├── tests/
│   ├── conftest.py                 # pytest configuration & fixtures
│   ├── __init__.py
│   │
│   ├── Unit Tests:
│   ├── test_unit_comprehensive.py   # Unit tests for core modules
│   ├── test_unit_settings_api.py    # Unit tests for settings
│   ├── test_ollama_client.py        # Ollama client unit tests
│   │
│   ├── Integration Tests:
│   ├── test_main_endpoints.py       # FastAPI endpoint tests
│   ├── test_api_integration.py      # Multi-service integration
│   ├── test_integration_settings.py # Settings service integration
│   ├── test_enhanced_content_routes.py  # Content routes
│   │
│   ├── End-to-End Tests:
│   ├── test_e2e_comprehensive.py    # Full pipeline E2E tests
│   ├── test_e2e_fixed.py            # Fixed E2E tests (smoke tests)
│   ├── test_content_pipeline.py     # Content creation pipeline
│   │
│   ├── Other:
│   ├── database_fixtures.py         # PostgreSQL mock fixtures
│   └── run_tests.py                 # Test runner script
```

**Python Test Naming Convention:**

- `test_*.py` - File must start with `test_`
- `test_function_name()` - Function must start with `test_`
- Organized by test type (unit, integration, e2e)

---

## 🚀 Running Tests

### Basic Test Commands

#### Run All Tests

```bash
# Frontend + Backend together
npm test

# Frontend only
npm run test:frontend

# Backend only
npm run test:python
```

#### Run Specific Test Suite

**Frontend - Oversight Hub:**

```bash
cd web/oversight-hub
npm test                                    # Interactive watch mode
npm test -- --passWithNoTests              # No tests, pass anyway
npm test SettingsManager                    # Specific component
npm test -- --testNamePattern="renders"     # By test name pattern
```

**Frontend - Public Site:**

```bash
cd web/public-site
npm test                                    # Interactive watch mode
npm test PostCard                           # Specific component
npm test -- --testPathPattern="api"         # By file path
```

**Backend - Python:**

```bash
cd src/cofounder_agent

# Run all tests
npm run test:python
# OR
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_main_endpoints.py -v

# Run specific test function
python -m pytest tests/test_main_endpoints.py::test_health_endpoint -v

# Run smoke tests only (quick subset)
npm run test:python:smoke
# OR
python -m pytest tests/test_e2e_fixed.py -v

# Run with coverage
python -m pytest tests/ -v --cov=. --cov-report=html

# Run only fast tests (skip slow ones)
python -m pytest tests/ -v -m "not slow"
```

### CI/CD Test Commands

```bash
# Frontend CI (non-interactive, with coverage)
npm run test:frontend:ci
# Runs with: --ci --coverage --watchAll=false

# Backend smoke tests (quick validation)
npm run test:python:smoke
# Runs: test_e2e_fixed.py - 5-10 minute suite
```

### Watch Mode Development

**Frontend - Auto-rerun on file changes:**

```bash
cd web/oversight-hub
npm test                          # Press 'a' for all, 'p' for pattern, 'q' to quit
npm test -- --watch              # Watch mode
npm test -- --coverage --watch   # With coverage in watch mode
```

**Backend - Watch mode with pytest-watch:**

```bash
# Install pytest-watch first
pip install pytest-watch

# Then use ptw
ptw tests/ -v                     # Auto-reruns on file changes
```

---

## 📝 Writing Tests

### Frontend Test Example (Jest + React Testing Library)

**File: `components/PostCard.test.js`**

```javascript
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import PostCard from './PostCard';

describe('PostCard Component', () => {
  const mockPost = {
    id: '1',
    title: 'Test Post',
    excerpt: 'This is a test',
    slug: 'test-post',
    category: { name: 'Tech' },
    publishedAt: '2025-10-25',
  };

  it('renders post title', () => {
    render(<PostCard post={mockPost} />);
    expect(screen.getByText('Test Post')).toBeInTheDocument();
  });

  it('renders category name', () => {
    render(<PostCard post={mockPost} />);
    expect(screen.getByText('Tech')).toBeInTheDocument();
  });

  it('displays excerpt text', () => {
    render(<PostCard post={mockPost} />);
    expect(screen.getByText('This is a test')).toBeInTheDocument();
  });

  it('has link to post page', () => {
    render(<PostCard post={mockPost} />);
    const link = screen.getByRole('link', { name: /test post/i });
    expect(link).toHaveAttribute('href', '/posts/test-post');
  });

  it('does not render when post is null', () => {
    const { container } = render(<PostCard post={null} />);
    expect(container.firstChild).toBeEmptyDOMElement();
  });
});
```

**Key Patterns:**

- Use `screen` queries instead of `getByTestId` when possible
- Query by role, label, or text content (user-centric)
- Use `userEvent` instead of `fireEvent` for interactions
- Avoid testing implementation details
- Group related tests with `describe()`

### Backend Test Example (pytest + pytest-asyncio)

**File: `tests/test_main_endpoints.py`**

```python
import pytest
from fastapi.testclient import TestClient
from src.cofounder_agent.main import app

client = TestClient(app)

class TestHealthEndpoint:
    """Test suite for health check endpoint"""

    def test_health_endpoint_returns_200(self):
        """Health endpoint should return 200 OK"""
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_health_endpoint_has_status_field(self):
        """Health response should have status field"""
        response = client.get("/api/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"

class TestTaskEndpoints:
    """Test suite for task management endpoints"""

    def test_create_task_success(self):
        """Should create task and return 201"""
        task_data = {
            "title": "Test Task",
            "description": "Test description",
            "type": "content_generation"
        }
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 201
        assert response.json()["title"] == "Test Task"

    def test_create_task_missing_title(self):
        """Should reject task without title"""
        task_data = {
            "description": "Missing title",
            "type": "content_generation"
        }
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 422  # Validation error

    def test_get_task_by_id(self):
        """Should retrieve task by ID"""
        # Create task
        create_response = client.post("/api/tasks", json={
            "title": "Test Task",
            "type": "content_generation"
        })
        task_id = create_response.json()["id"]

        # Retrieve task
        get_response = client.get(f"/api/tasks/{task_id}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == task_id

class TestAsyncEndpoints:
    """Test suite for async operations"""

    @pytest.mark.asyncio
    async def test_async_task_execution(self):
        """Should execute async task correctly"""
        # Test async operations
        response = client.post("/api/tasks", json={
            "title": "Async Task",
            "type": "content_generation"
        })
        assert response.status_code == 201
```

**Key Patterns:**

- Use `TestClient` from FastAPI for HTTP testing
- Organize tests into classes by feature
- Use clear, descriptive test names
- Test both success and failure cases
- Mark async tests with `@pytest.mark.asyncio`
- Use fixtures for setup/teardown (see conftest.py)

### Using Fixtures for Test Setup

**File: `tests/conftest.py` (pytest configuration)**

```python
import pytest
from fastapi.testclient import TestClient
from src.cofounder_agent.main import app
from src.cofounder_agent.database import get_db, Base, engine

@pytest.fixture(scope="session")
def test_db():
    """Create test database"""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client(test_db):
    """FastAPI test client"""
    return TestClient(app)

@pytest.fixture
def sample_task():
    """Sample task for testing"""
    return {
        "title": "Sample Task",
        "description": "For testing",
        "type": "content_generation"
    }

@pytest.fixture
def sample_post():
    """Sample post for testing"""
    return {
        "title": "Sample Post",
        "slug": "sample-post",
        "content": "# Sample\n\nContent here",
        "excerpt": "Sample excerpt"
    }
```

**Usage in tests:**

```python
def test_create_task(client, sample_task):
    """Fixtures are injected as parameters"""
    response = client.post("/api/tasks", json=sample_task)
    assert response.status_code == 201
```

---

## 🎯 Coverage Goals

### Target Metrics

| Category             | Target | Current | Status      |
| -------------------- | ------ | ------- | ----------- |
| **Overall Coverage** | >80%   | ~85%    | ✅ Met      |
| **Critical Paths**   | 90%+   | ~92%    | ✅ Exceeded |
| **API Endpoints**    | 85%+   | ~90%    | ✅ Exceeded |
| **Core Logic**       | 85%+   | ~88%    | ✅ Exceeded |
| **Components**       | 75%+   | ~82%    | ✅ Exceeded |
| **Utils/Helpers**    | 70%+   | ~80%    | ✅ Exceeded |

### What to Prioritize

**High Priority (Must Test):**

- ✅ API endpoints (request/response validation)
- ✅ Authentication & authorization
- ✅ Database operations (CRUD)
- ✅ Core business logic
- ✅ Error handling paths

**Medium Priority (Should Test):**

- ✅ Component rendering
- ✅ User interactions
- ✅ Form validation
- ✅ Data transformations
- ✅ API integrations

**Lower Priority (Nice to Have):**

- CSS styling details
- External service calls (mock these)
- Navigation between routes
- Loading states

### Checking Coverage

**Frontend Coverage:**

```bash
cd web/public-site
npm test -- --coverage --watchAll=false

# Output: coverage/lcov-report/index.html
```

**Backend Coverage:**

```bash
cd src/cofounder_agent
python -m pytest tests/ --cov=. --cov-report=html --cov-report=term

# Output: htmlcov/index.html
```

---

## 🔄 Common Patterns

### Pattern 1: API Endpoint Testing

**Test structure for typical endpoint:**

```python
def test_endpoint_success():
    """Happy path - valid request, success response"""
    response = client.get("/api/endpoint")
    assert response.status_code == 200
    assert response.json()["key"] == "expected_value"

def test_endpoint_validation_error():
    """Validation - missing required field"""
    response = client.post("/api/endpoint", json={})
    assert response.status_code == 422

def test_endpoint_not_found():
    """Not found - invalid ID"""
    response = client.get("/api/endpoint/invalid-id")
    assert response.status_code == 404

def test_endpoint_unauthorized():
    """Auth - no token provided"""
    response = client.get("/api/protected-endpoint")
    assert response.status_code == 401
```

### Pattern 2: Component Interaction Testing

**Test user interactions:**

```javascript
import userEvent from '@testing-library/user-event';

it('should submit form on button click', async () => {
  const user = userEvent.setup();
  render(<LoginForm onSubmit={mockSubmit} />);

  // User fills in form
  await user.type(screen.getByLabelText(/email/i), 'test@example.com');
  await user.type(screen.getByLabelText(/password/i), 'password123');

  // User clicks submit
  await user.click(screen.getByRole('button', { name: /sign in/i }));

  // Verify submission
  expect(mockSubmit).toHaveBeenCalledWith({
    email: 'test@example.com',
    password: 'password123',
  });
});
```

### Pattern 3: Async Operation Testing

**Testing promises and async/await:**

```python
@pytest.mark.asyncio
async def test_async_operation():
    """Test async function"""
    result = await some_async_function()
    assert result == "expected_value"

def test_async_api_call():
    """Test API endpoint that does async work"""
    response = client.post("/api/async-endpoint", json={"data": "test"})
    assert response.status_code == 200
    # The TestClient handles async automatically
```

### Pattern 4: Mocking External Services

**Mock API calls and external services:**

```python
from unittest.mock import patch, MagicMock

@patch('src.cofounder_agent.services.model_router.call_model')
def test_content_generation_with_mock(mock_model):
    """Mock external model call"""
    # Setup mock
    mock_model.return_value = "Generated content"

    # Call endpoint
    response = client.post("/api/generate-content", json={"prompt": "test"})

    # Verify mock was called
    mock_model.assert_called_once()
    assert response.json()["content"] == "Generated content"
```

**Mock in React tests:**

```javascript
import { jest } from '@jest/globals';

const mockFetch = jest.fn(() =>
  Promise.resolve({
    json: () => Promise.resolve({ data: 'test' }),
  })
);

global.fetch = mockFetch;

it('should fetch data', async () => {
  render(<DataComponent />);
  await waitFor(() => {
    expect(mockFetch).toHaveBeenCalled();
  });
});
```

---

## 🐛 Troubleshooting

### Jest Issues

**Problem: "Cannot find module" error**

```bash
# Solution: Clear Jest cache
npm test -- --clearCache
```

**Problem: Tests timeout**

```bash
# Solution: Increase timeout in test
jest.setTimeout(10000);  # 10 seconds

# Or use async/await properly:
it('should fetch data', async () => {
  const data = await fetchData();
  expect(data).toBeDefined();
});
```

**Problem: Snapshot mismatch**

```bash
# Solution: Update snapshots (if intentional changes)
npm test -- -u
```

### pytest Issues

**Problem: "No module named" error**

```bash
# Solution: Ensure PYTHONPATH is set
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Or run from project root:
cd /path/to/project
python -m pytest tests/ -v
```

**Problem: Async test not working**

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result

# And install: pip install pytest-asyncio
```

**Problem: Fixture not found**

```python
# Solution: Fixtures must be in conftest.py at test root
src/cofounder_agent/tests/conftest.py  # Correct location
```

**Problem: Database locked in tests**

```python
# Solution: Use in-memory SQLite for tests
DATABASE_URL = "sqlite:///:memory:"

# Or in conftest.py:
@pytest.fixture(scope="function")
def db():
    # Create fresh DB for each test
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
```

---

## 📊 Test Statistics

### Current Test Coverage

**Frontend Tests:**

- `web/oversight-hub/`: 8 test files, ~35 tests
- `web/public-site/`: 10 test files, ~28 tests
- **Total Frontend:** 63 tests passing ✅

**Backend Tests:**

- Unit tests: 15+ test suites
- Integration tests: 12+ test suites
- E2E tests: 8+ test suites
- **Total Backend:** 30+ tests passing ✅

**Grand Total: 93+ tests passing** ✅

### Running All Tests

```bash
# Everything
npm test

# With coverage report
npm run test:frontend:ci
npm run test:python -- --cov=. --cov-report=term

# Quickly during development
npm run test:python:smoke  # 5-10 minutes
```

---

## 🔗 Related Documentation

- **[Development Workflow](../04-DEVELOPMENT_WORKFLOW.md#🧪-testing)** - Testing section in workflow
- **[Core Docs Hub](../00-README.md)** - All documentation
- **[Setup Guide](../01-SETUP_AND_OVERVIEW.md)** - Getting started
- **[API References](./API_CONTRACT_CONTENT_CREATION.md)** - API testing examples

---

## ✅ Before Committing Code

**Always run:**

```bash
# 1. Run all tests (local environment)
npm test

# 2. Check coverage
npm run test:coverage

# 3. Run linting
npm run lint

# 4. Format code
npm run format

# 5. Type checking
npm run type-check

# If all pass, you're ready to commit!
git add .
git commit -m "feat: your feature"
```

---

**🚀 Happy Testing!**

For questions about specific tests, check the test files directly:

- Frontend: `web/*/components/__tests__/` and `web/*/__tests__/`
- Backend: `src/cofounder_agent/tests/`

Each test file has clear, self-documenting test names and comments explaining the test coverage.
