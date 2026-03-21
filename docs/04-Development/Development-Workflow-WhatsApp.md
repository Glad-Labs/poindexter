# Development Workflow - WhatsApp Integration

## Adding WhatsApp to Your Development Workflow

### Prerequisites

1. **WhatsApp API Key** - Obtain from OpenClaw or your messaging provider
2. **Test Phone Number** - WhatsApp sandbox number for development
3. **JWT Token** - For API authentication (see [Authentication](../02-Architecture/API-Design.md#%F0%9F%9C%93-Authentication))

### Step 1: Configure Environment Variables

Add to your `.env.local`:

```env
OPENCLAW_WHATSAPP_API_KEY=your_api_key_here
OPENCLAW_WHATSAPP_PHONE_NUMBER=your_phone_number_here
OPENCLAW_API_URL=http://localhost:8000
```

### Step 2: Start Backend with WhatsApp

```bash
cd src/cofounder_agent
poetry install
poetry run uvicorn main:app --reload --port 8000
```

The WhatsApp router will be automatically registered if the WhatsApp dependencies are installed.

### Step 3: Test WhatsApp Endpoints

```bash
# Test connection status
curl http://localhost:8000/api/whatsapp/status \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Send a test message
curl -X POST http://localhost:8000/api/whatsapp/send \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "recipient_phone": "+1234567890",
    "message": "Test message from development environment"
  }'
```

### Step 4: Integrate in Your Workflow

#### For Python Agents

```python
from services.whatsapp.whatsapp_client import WhatsAppClient

async def my_agent_task(task_id):
    # ... do your work ...

    # Send completion notification
    whatsapp = WhatsAppClient()
    await whatsapp.send_message(
        user_id=user_id,
        message=f"Task {task_id} completed!",
        channel="whatsapp"
    )

    # Request approval if needed
    if needs_approval:
        await whatsapp.create_approval_request(
            user_id=user_id,
            task_id=task_id,
            suggestion="I recommend action X",
            channel="whatsapp"
        )
```

#### For JavaScript/TypeScript

```javascript
import { WhatsAppClient } from './services/whatsappClient';

const whatsapp = new WhatsAppClient();

// Send message
await whatsapp.sendMessage({
  recipient_phone: '+1234567890',
  message: 'Task completed successfully!',
});

// Request approval
await whatsapp.requestApproval({
  task_id: 'task_abc123',
  suggestion: 'I recommend running this campaign',
});
```

### Step 5: Debugging

Check backend logs for WhatsApp errors:

```bash
# Logs will show:
# - Connection attempts
# - Message send results
# - Error messages (if any)
tail -f logs/backend.log
```

Common issues:

- API key not set → Check `.env.local`
- Phone number format → Must start with '+'
- Connection failed → Verify backend is running

### Step 6: Testing on Staging

When deploying to staging:

1. Update environment variables in Railway
2. Use a staging WhatsApp API key (if available)
3. Test with staging phone number
4. Verify webhook/notifications work

### Step 7: Testing on Production

When deploying to production:

1. Use production WhatsApp API key
2. Use production phone number
3. Verify authentication works
4. Test approval workflows
5. Monitor message delivery rates

---

## Development Guidelines

### Code Style

- Follow existing service patterns in `src/cofounder_agent/services/`
- Use async/await for all async operations
- Implement error handling with try/catch
- Log all WhatsApp operations with appropriate levels

### Testing

```python
# Test whatsapp_service.py
import pytest
from services.whatsapp.whatsapp_service import send_whatsapp_message

def test_send_message():
    response = send_whatsapp_message(
        phone="+1234567890",
        message="Test"
    )
    assert response["success"] == True
```

### Security

- Never commit API keys to git
- Use environment variables for all credentials
- Validate all inputs before sending messages
- Implement rate limiting

---

## Related Documentation

- [Quick Start Guide](../01-Getting-Started/Quick-Start-Guide.md) - Quick setup
- [API Design](../02-Architecture/API-Design.md) - Complete API reference
- [WhatsApp Feature Docs](../03-Features/WhatsApp.md) - Feature overview
- [Environment Variables](../01-Getting-Started/Environment-Variables.md) - Configuration
- [Testing Guide](../04-Development/Testing-Guide.md) - Testing strategies

---

**Status:** ✅ Production Ready
**Last Updated:** March 21, 2026
