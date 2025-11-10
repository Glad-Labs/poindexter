# 🧪 Glad Labs Comprehensive Testing Guide

**Last Updated:** October 24, 2025  
**Status:** ✅ Production Ready | Phase 3.4 Testing Implementation  
**Test Coverage:** 93+ tests across backend and frontend

---

## 📋 Quick Navigation

- **[Quick Start](#-quick-start)** - Run tests in 2 minutes
- **[Test Structure](#-test-structure)** - How tests are organized
- **[Running Tests](#-running-tests)** - Different test commands
- **[Writing Tests](#-writing-tests)** - How to add new tests
- **[Coverage Reports](#-coverage-reports)** - Analyze coverage

---

## 🚀 Quick Start

### Run All Tests (Recommended)

```bash
# Run everything: backend unit, backend integration, frontend unit, frontend integration
npm test

# Run with coverage report
npm run test:coverage
```

### Run Specific Test Suites

```bash
# Backend only
npm run test:python

# Frontend only
npm run test:frontend

# Quick backend smoke tests
npm run test:python:smoke

# Frontend with coverage
npm run test:frontend:ci
```

### Using the Test Runner Script

```bash
# Run all tests with Python runner
python scripts/run_tests.py

# Run specific suite
python scripts/run_tests.py --backend --unit

# Generate coverage
python scripts/run_tests.py --coverage

# Save results to JSON
python scripts/run_tests.py --save-results
```

---

## 🏗️ Test Structure

### File Organization

```
Backend Tests:
├── src/cofounder_agent/tests/
│   ├── conftest.py                          # pytest fixtures
│   ├── test_unit_settings_api.py            # 27 unit tests
│   ├── test_integration_settings.py         # 14 integration tests
│   ├── test_e2e_fixed.py                    # Existing E2E tests
│   ├── test_unit_comprehensive.py           # Existing comprehensive tests
│   └── test_api_integration.py              # Existing API tests

Frontend Tests:
├── web/oversight-hub/__tests__/
│   ├── components/
│   │   └── SettingsManager.test.jsx         # 33 unit tests
│   └── integration/
│       └── SettingsManager.integration.test.jsx  # 19 integration tests
```

### Test Categories

| Category                 | Purpose                    | Count  | Framework |
| ------------------------ | -------------------------- | ------ | --------- |
| **Backend Unit**         | Isolated endpoint testing  | 27     | pytest    |
| **Backend Integration**  | Full workflow testing      | 14     | pytest    |
| **Frontend Unit**        | Component behavior testing | 33     | Jest      |
| **Frontend Integration** | API integration testing    | 19     | Jest      |
| **Total**                | -                          | **93** | -         |

---

## 🧬 Backend Tests

### Test Unit Tests (27 tests)

**File:** `src/cofounder_agent/tests/test_unit_settings_api.py`

**Coverage:**

```
✅ GET Endpoints (4 tests)
   - Get all user settings
   - Get specific setting
   - Unauthorized access
   - Invalid token handling

✅ POST Endpoints (4 tests)
   - Create settings (success)
   - Missing required fields
   - Invalid data types
   - Duplicate settings

✅ PUT Endpoints (4 tests)
   - Update all settings
   - Update single setting
   - Update nonexistent user
   - Partial validation

✅ DELETE Endpoints (3 tests)
   - Delete all settings
   - Delete specific setting
   - Delete nonexistent setting

✅ Validation (4 tests)
   - Theme enum validation
   - Email frequency validation
   - Timezone validation
   - Boolean field validation

✅ Permissions (2 tests)
   - User cannot access other user settings
   - Admin can access any user settings

✅ Audit Logging (2 tests)
   - Settings change creates audit log
   - Audit log contains user information
```

**Run Unit Tests:**

```bash
npm run test:python  # All backend tests
cd src/cofounder_agent && python -m pytest tests/test_unit_settings_api.py -v
```

### Integration Tests (14 tests)

**File:** `src/cofounder_agent/tests/test_integration_settings.py`

**Coverage:**

```
✅ CRUD Workflow (1 test)
   - Create → Read → Update → Delete cycle

✅ Authentication (2 tests)
   - Token validation
   - Multi-user isolation

✅ Batch Operations (2 tests)
   - Bulk update multiple settings
   - Partial bulk update

✅ Error Handling (3 tests)
   - Malformed JSON requests
   - Null value handling
   - Extra field handling

✅ Concurrency (2 tests)
   - Concurrent reads
   - Concurrent writes

✅ Response Format (2 tests)
   - Response schema compliance
   - Error response format

✅ Defaults (1 test)
   - Default settings on first access

✅ Audit Integration (1 test)
   - All settings changes logged
```

**Run Integration Tests:**

```bash
cd src/cofounder_agent && python -m pytest tests/test_integration_settings.py -v
```

### Key Testing Patterns

**1. Using FastAPI TestClient**

```python
from fastapi.testclient import TestClient
from cofounder_agent.main import app

client = TestClient(app)

def test_get_settings():
    response = client.get(
        "/api/settings",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
```

**2. Mocking Authentication**

```python
from unittest.mock import patch

@patch('cofounder_agent.routes.settings.get_current_user')
def test_create_settings(mock_auth):
    mock_auth.return_value = UserModel(
        user_id="test-user",
        email="test@example.com",
        role="user"
    )

    response = client.post("/api/settings", json={...})
    assert response.status_code == 201
```

**3. Database Testing (with mocked DB)**

```python
@pytest.fixture
def mock_db():
    """Simulate database operations"""
    db = {}

    async def get_settings(user_id: str):
        return db.get(user_id)

    return get_settings
```

---

## 🎨 Frontend Tests

### Unit Tests (33 tests)

**File:** `web/oversight-hub/__tests__/components/SettingsManager.test.jsx`

**Coverage:**

```
✅ Rendering (3 tests)
   - Component renders without errors
   - All tabs display
   - Save/cancel buttons present

✅ Theme Settings (4 tests)
   - Theme dropdown renders
   - Theme selection changes value
   - Theme preview displays
   - Language options available

✅ Notification Settings (4 tests)
   - Notification toggles render
   - Toggle on/off functionality
   - Email frequency selector
   - Notification type checkboxes

✅ Security Settings (4 tests)
   - 2FA section renders
   - Enable 2FA button present
   - Password change form fields
   - Active sessions list

✅ Form Interactions (4 tests)
   - Form marks dirty on changes
   - Save button disabled when clean
   - Cancel resets form
   - Form state management

✅ Form Validation (3 tests)
   - Required field validation
   - Password strength validation
   - Email format validation

✅ API Integration (4 tests - Mocked)
   - Save settings calls API
   - Loading state during save
   - Success message displays
   - Error message displays

✅ Edge Cases (3 tests)
   - Rapid tab switching
   - Component unmount during save
   - Missing default settings

✅ Accessibility (3 tests)
   - Heading hierarchy correct
   - All inputs have labels
   - Keyboard navigable
```

**Run Unit Tests:**

```bash
npm run test:frontend  # Watch mode
npm run test:frontend:ci  # CI mode with coverage
cd web/oversight-hub && npm test -- SettingsManager.test.jsx --watchAll=false
```

### Integration Tests (19 tests)

**File:** `web/oversight-hub/__tests__/integration/SettingsManager.integration.test.jsx`

**Coverage:**

```
✅ Load Settings on Mount (4 tests)
   - Loads settings when mounted
   - Displays loaded settings
   - Handles error loading
   - Shows loading spinner

✅ Save Settings (5 tests)
   - Saves settings on button click
   - Shows success message
   - Shows error message
   - Displays saving spinner
   - API called with correct data

✅ Cancel Changes (2 tests)
   - Cancels unsaved changes
   - API not called on cancel

✅ Multiple Settings Tabs (2 tests)
   - Switches tabs without losing changes
   - Saves changes from multiple tabs

✅ Real-time Updates (1 test)
   - Handles external settings changes

✅ Settings Validation Integration (1 test)
   - Validates before sending to API

✅ Network Errors (2 tests)
   - Handles network timeout
   - Retries on transient error

✅ Concurrent Operations (1 test)
   - Prevents duplicate saves on rapid clicks

✅ Data Persistence (1 test)
   - Maintains changes during API request
```

**Run Integration Tests:**

```bash
cd web/oversight-hub && npm test -- SettingsManager.integration.test.jsx --watchAll=false
```

### Key Testing Patterns

**1. Rendering Components**

```javascript
import { render, screen } from '@testing-library/react';
import SettingsManager from '../SettingsManager';

test('renders settings manager', () => {
  render(<SettingsManager />);

  expect(screen.getByText('Settings')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
});
```

**2. User Interactions**

```javascript
import userEvent from '@testing-library/user-event';

test('saves settings on button click', async () => {
  const user = userEvent.setup();
  render(<SettingsManager />);

  const themeSelect = screen.getByDisplayValue('light');
  await user.selectOption(themeSelect, 'dark');

  const saveButton = screen.getByRole('button', { name: /save/i });
  await user.click(saveButton);

  expect(screen.getByText(/settings saved/i)).toBeInTheDocument();
});
```

**3. Mocking API Calls**

```javascript
jest.mock('../api', () => ({
  getSettings: jest.fn(),
  saveSettings: jest.fn(),
}));

test('loads settings on mount', async () => {
  const mockSettings = { theme: 'light', notifications: true };
  const { getSettings } = require('../api');

  getSettings.mockResolvedValue(mockSettings);

  render(<SettingsManager />);

  await waitFor(() => {
    expect(screen.getByDisplayValue('light')).toBeInTheDocument();
  });

  expect(getSettings).toHaveBeenCalled();
});
```

**4. Testing Async Operations**

```javascript
import { waitFor } from '@testing-library/react';

test('shows loading spinner during save', async () => {
  const user = userEvent.setup();
  render(<SettingsManager />);

  const saveButton = screen.getByRole('button', { name: /save/i });
  await user.click(saveButton);

  expect(screen.getByRole('progressbar')).toBeInTheDocument();

  await waitFor(() => {
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });
});
```

---

## 🎯 Running Tests

### Standard Commands

```bash
# Run all tests
npm test

# Run with coverage
npm run test:coverage

# Backend only
npm run test:python

# Backend smoke tests
npm run test:python:smoke

# Frontend only
npm run test:frontend

# Frontend with coverage
npm run test:frontend:ci
```

### Advanced Commands

```bash
# Run specific test file
cd src/cofounder_agent && python -m pytest tests/test_unit_settings_api.py -v

# Run tests matching pattern
python -m pytest tests/ -k "settings" -v

# Run tests with detailed output
python -m pytest tests/ -vv --tb=long

# Run with coverage report
python -m pytest tests/ --cov=src/cofounder_agent --cov-report=html

# Run frontend tests in watch mode
cd web/oversight-hub && npm test -- --watch

# Run frontend tests with coverage
cd web/oversight-hub && npm test -- --coverage --watchAll=false
```

### Using the Test Runner Script

```bash
# Run all tests
python scripts/run_tests.py

# Backend tests only
python scripts/run_tests.py --backend

# Frontend tests only
python scripts/run_tests.py --frontend

# Unit tests only
python scripts/run_tests.py --unit

# Integration tests only
python scripts/run_tests.py --integration

# With coverage
python scripts/run_tests.py --coverage

# Save results to JSON
python scripts/run_tests.py --save-results

# Backend unit tests with coverage and save
python scripts/run_tests.py --backend --unit --coverage --save-results
```

---

## ✍️ Writing Tests

### Adding Backend Unit Tests

**File:** `src/cofounder_agent/tests/test_unit_settings_api.py`

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from cofounder_agent.main import app

client = TestClient(app)

class TestMyFeature:
    """Tests for my feature"""

    @pytest.fixture
    def mock_auth(self):
        """Mock authentication"""
        with patch('cofounder_agent.routes.settings.get_current_user') as mock:
            mock.return_value = {
                "user_id": "test-user",
                "email": "test@example.com"
            }
            yield mock

    def test_my_feature_success(self, mock_auth):
        """Test successful feature operation"""
        response = client.get(
            "/api/my-endpoint",
            headers={"Authorization": "Bearer test-token"}
        )

        assert response.status_code == 200
        assert response.json()["data"] == "expected"

    def test_my_feature_error(self, mock_auth):
        """Test feature error handling"""
        response = client.get(
            "/api/my-endpoint",
            headers={"Authorization": "Bearer invalid"}
        )

        assert response.status_code == 401
        assert "error" in response.json()
```

### Adding Frontend Unit Tests

**File:** `web/oversight-hub/__tests__/components/MyComponent.test.jsx`

```javascript
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MyComponent from '../MyComponent';

describe('MyComponent', () => {
  test('renders component', () => {
    render(<MyComponent />);

    expect(screen.getByText('My Component')).toBeInTheDocument();
  });

  test('handles user interaction', async () => {
    const user = userEvent.setup();
    render(<MyComponent />);

    const button = screen.getByRole('button', { name: /click me/i });
    await user.click(button);

    expect(screen.getByText('Clicked!')).toBeInTheDocument();
  });

  test('validates input', async () => {
    const user = userEvent.setup();
    render(<MyComponent />);

    const input = screen.getByRole('textbox');
    await user.type(input, 'invalid');

    expect(screen.getByText(/invalid input/i)).toBeInTheDocument();
  });
});
```

### Adding Integration Tests

**File:** `web/oversight-hub/__tests__/integration/MyFeature.integration.test.jsx`

```javascript
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MyFeature from '../MyFeature';

jest.mock('../api', () => ({
  saveFeature: jest.fn(),
  loadFeature: jest.fn(),
}));

describe('MyFeature Integration', () => {
  test('loads and saves feature', async () => {
    const { loadFeature, saveFeature } = require('../api');

    const mockData = { id: 1, name: 'test' };
    loadFeature.mockResolvedValue(mockData);
    saveFeature.mockResolvedValue({ success: true });

    const user = userEvent.setup();
    render(<MyFeature />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('test')).toBeInTheDocument();
    });

    const saveButton = screen.getByRole('button', { name: /save/i });
    await user.click(saveButton);

    await waitFor(() => {
      expect(saveFeature).toHaveBeenCalled();
      expect(screen.getByText(/saved/i)).toBeInTheDocument();
    });
  });
});
```

---

## 📊 Coverage Reports

### Generating Coverage

```bash
# Generate Python coverage
cd src/cofounder_agent
python -m pytest tests/ --cov=. --cov-report=html --cov-report=term

# Generate JavaScript coverage
cd web/oversight-hub
npm test -- --coverage --watchAll=false

# Using test runner
python scripts/run_tests.py --coverage
```

### Viewing Coverage Reports

```bash
# Open Python coverage HTML
open src/cofounder_agent/htmlcov/index.html  # macOS
start src/cofounder_agent/htmlcov/index.html # Windows
xdg-open src/cofounder_agent/htmlcov/index.html # Linux

# Open JavaScript coverage HTML
open web/oversight-hub/coverage/lcov-report/index.html  # macOS
start web/oversight-hub/coverage/lcov-report/index.html # Windows
xdg-open web/oversight-hub/coverage/lcov-report/index.html # Linux
```

### Coverage Targets

| Area                       | Target | Status |
| -------------------------- | ------ | ------ |
| Settings API (Backend)     | >80%   | ✅     |
| SettingsManager (Frontend) | >80%   | ✅     |
| Integration flows          | >70%   | ✅     |
| Error handling             | >90%   | ✅     |

---

## 🐛 Troubleshooting Tests

### Backend Tests Won't Run

**Problem:** `ModuleNotFoundError: No module named 'pytest'`

**Solution:**

```bash
pip install pytest pytest-asyncio pytest-cov httpx
# Or from project root:
npm run setup:python
```

### Frontend Tests Won't Run

**Problem:** `Cannot find module '@testing-library/react'`

**Solution:**

```bash
cd web/oversight-hub
npm install
npm test
```

### Test Timeouts

**Problem:** `Timeout waiting for async operation`

**Solution:** Increase timeout in test:

```python
# Backend
@pytest.mark.timeout(30)  # 30 second timeout
def test_long_operation():
    ...

# Frontend
test('async operation', async () => {
    ...
}, 10000);  // 10 second timeout
```

### Mock Data Issues

**Problem:** `TypeError: Cannot read property 'xyz' of undefined`

**Solution:** Verify mock data matches component expectations:

```javascript
// Check what component expects
console.log(component.propTypes);

// Provide complete mock
const mockData = {
    id: '123',
    name: 'Test',
    settings: { ... }  // Include all properties
};
```

---

## 📚 Test Naming Conventions

### Backend Tests

```python
# Unit tests
test_get_settings_success
test_get_settings_unauthorized
test_create_settings_invalid_data
test_update_settings_nonexistent_user
test_delete_settings_permission_denied

# Integration tests
test_create_read_update_delete_workflow
test_settings_multi_user_isolation
test_concurrent_settings_writes
```

### Frontend Tests

```javascript
// Unit tests
test('renders settings component');
test('theme dropdown changes value');
test('save button disabled when clean');
test('validation error displays');
test('keyboard navigation works');

// Integration tests
test('loads and displays settings on mount');
test('saves settings to API');
test('handles network errors gracefully');
test('prevents duplicate API calls');
```

---

## ✅ Pre-Commit Testing

Add to `.git/hooks/pre-commit`:

```bash
#!/bin/bash
echo "🧪 Running pre-commit tests..."

# Run quick smoke tests
npm run test:python:smoke
if [ $? -ne 0 ]; then
    echo "❌ Tests failed. Commit aborted."
    exit 1
fi

echo "✅ Pre-commit tests passed!"
exit 0
```

---

## 🔗 Related Documentation

- **[Architecture](./02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[Development Workflow](./04-DEVELOPMENT_WORKFLOW.md)** - Git and testing strategy
- **[Setup Guide](./01-SETUP_AND_OVERVIEW.md)** - Initial setup

---

## 📞 Common Test Commands Quick Reference

```bash
# Run all tests
npm test

# Run with coverage
npm run test:coverage

# Backend only
npm run test:python

# Frontend only
npm run test:frontend

# Specific test file
cd src/cofounder_agent && python -m pytest tests/test_unit_settings_api.py -v

# Watch mode (re-run on changes)
npm run test:frontend  # Automatically in watch mode

# Debug mode (verbose output)
python -m pytest tests/ -vv --tb=long

# Generate HTML coverage report
python -m pytest tests/ --cov=. --cov-report=html

# Run matching tests
python -m pytest tests/ -k "settings" -v
```

---

**[← Back to Documentation Hub](./00-README.md)**
