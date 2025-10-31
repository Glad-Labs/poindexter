# Test Fixes Needed - Quick Reference

**Status:** Production code is now clean and original  
**Task:** Update tests to match production code

## Test Endpoint Updates Required

### Content Pipeline Tests

**Old expectation:**

```python
POST /api/content/create  # ❌ This endpoint no longer exists
```

**New expectation:**

```python
POST /api/content/generate  # ✅ This is the actual endpoint
```

**Tests to update:**

- `tests/test_content_pipeline.py::TestContentPipelineIntegration::test_create_content_endpoint_exists`
- `tests/test_content_pipeline.py::TestContentPipelineIntegration::test_create_content_requires_topic`
- `tests/test_content_pipeline.py::TestContentPipelineIntegration::test_create_content_dev_mode`
- And all other tests using `/api/content/create`

### Webhook Tests

**Old expectation:**

```python
POST /api/webhooks/content-created  # ❌ Route not registered in app
```

**New expectation:**

```python
# Mock webhook functionality in tests instead of making HTTP calls
from unittest.mock import AsyncMock, patch
# Mock firestore_client.log_webhook_event if needed
```

**Tests to update:**

- `tests/test_content_pipeline.py::TestContentPipelineIntegration::test_webhook_endpoint_exists`
- All webhook-related tests in `test_content_pipeline.py`

## Test Mocking Pattern

### For Settings Tests

```python
# ✅ CORRECT - Mock get_current_user
from unittest.mock import patch, MagicMock

mock_user = MagicMock(id=1, email="test@example.com")
with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
    # Your test here
    pass
```

### For Firestore Tests

```python
# ✅ CORRECT - Mock firestore_client if needed
from unittest.mock import AsyncMock, patch

mock_fs = AsyncMock()
mock_fs.log_webhook_event = AsyncMock(return_value="webhook-123")
with patch('main.firestore_client', mock_fs):
    # Your test here
    pass
```

### For Webhook Tests

```python
# ✅ CORRECT - Don't call real webhook routes
# Instead, test the webhook handler function directly or mock it
from unittest.mock import AsyncMock, patch

# Don't test: POST /api/webhooks/content-created
# Instead test: the handler function directly with mocked dependencies
```

## Files Affected by Changes

| Test File                      | Issue                         | Fix                               |
| ------------------------------ | ----------------------------- | --------------------------------- |
| `test_content_pipeline.py`     | Uses `/api/content/create`    | Change to `/api/content/generate` |
| `test_content_pipeline.py`     | Tests webhook endpoints       | Mock webhook functionality        |
| `test_integration_settings.py` | Patches `get_current_user`    | Already works (stub added)        |
| `test_unit_settings_api.py`    | Patches `get_current_user`    | Already works (stub added)        |
| Any Firestore webhook tests    | Expects `log_webhook_event()` | Remove or mock method call        |

## Key Production Endpoints (Actual)

### Content Generation

- `POST /api/content/generate` - Generate blog post
- `GET /api/content/status/{task_id}` - Get generation status
- `POST /api/content/save-to-strapi` - Save to Strapi CMS

### Settings Management

- `GET /api/settings` - Get user settings
- `POST /api/settings` - Create settings
- `PUT /api/settings` - Update settings
- `DELETE /api/settings` - Delete settings

### Note: No Webhook Routes

- Webhook functionality is NOT exposed as HTTP endpoints
- Tests should mock webhook event handling
- Firestore integration is optional (test without it)

## Testing Strategy Going Forward

1. **Test the actual endpoints** that exist in production
2. **Mock external dependencies** (Firestore, webhooks) instead of making real calls
3. **Don't add endpoints to code just to pass tests**
4. **Tests adapt to production, not vice versa**

## Files Already Fixed ✅

These have legitimate improvements and should stay:

- `settings_routes.py` - Has `get_current_user()` stub for test mocking
- `settings_service.py` - Has validation logic
- Settings endpoints - Fully functional with proper response models

---

**Remember:** Production code is clean now. Tests should match it, not the other way around!
