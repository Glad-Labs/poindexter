# WhatsApp Integration

The WhatsApp service enables the AI co-founder to communicate with clients through WhatsApp, providing real-time notifications, task updates, and human-in-the-loop approval workflows.

## Overview

WhatsApp integration is a key channel for the AI co-founder system, allowing:

- **Task notifications** - AI updates clients on task completion and status
- **Approval requests** - AI requests human approval for significant actions
- **Status checks** - Quick connection and health status monitoring
- **Human-in-the-loop** - Client can approve/reject AI suggestions via WhatsApp

## Features

### ✅ Core Capabilities

1. **Send Messages** - Broadcast task updates and notifications to clients
2. **Connection Status** - Check if WhatsApp connection is active
3. **Approval Requests** - Request human approval for AI-generated suggestions
4. **Phone Number Management** - Store and retrieve client phone numbers
5. **Message Caching** - Optimize repeated messages to same recipient

### 🎯 Use Cases

#### Client Notifications

When the AI completes a task, send a WhatsApp notification:

```bash
curl -X POST http://localhost:8000/api/whatsapp/send \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "recipient_phone": "+1234567890",
    "message": "✅ Task Complete: Blog Post Generated\n\nYour article about AI in business is ready. Check your dashboard for details."
  }'
```

#### Approval Workflows

Request human approval for significant decisions:

```bash
curl -X POST http://localhost:8000/api/whatsapp/request-approval \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "task_abc123",
    "suggestion": "I recommend running a $200 ad campaign on Facebook. This could generate significant ROI based on historical data. Approve?"
  }'
```

## Configuration

### Environment Variables

Set these in your `.env.local` file:

```env
# WhatsApp API Configuration
OPENCLAW_WHATSAPP_API_KEY=your_whatsapp_api_key
OPENCLAW_WHATSAPP_PHONE_NUMBER=your_phone_number

# Optional: API Base URL (defaults to localhost:8000)
OPENCLAW_API_URL=http://localhost:8000
```

### Phone Number Format

Always use international format with '+' prefix:

- ✅ `+1234567890`
- ✅ `+15551234567`
- ❌ `1234567890` (missing +)
- ❌ `(123) 456-7890` (wrong format)

## API Endpoints

### POST `/api/whatsapp/send`

Send a WhatsApp message to a client.

**Request:**

```json
{
  "recipient_phone": "+1234567890",
  "message": "Your task has been completed successfully."
}
```

**Response:**

```json
{
  "success": true,
  "recipient": "+1234567890",
  "message_id": "msg_abc123",
  "timestamp": "2026-03-21T01:30:00Z"
}
```

### GET `/api/whatsapp/status`

Check WhatsApp connection status.

**Response:**

```json
{
  "connected": true,
  "phone_number": "+1234567890",
  "last_check": "2026-03-21T01:30:00Z",
  "error": null
}
```

### POST `/api/whatsapp/request-approval`

Request human approval for a task.

**Request:**

```json
{
  "task_id": "task_abc123",
  "suggestion": "I recommend running a $200 ad campaign. Approve?"
}
```

**Response:**

```json
{
  "success": true,
  "recipient": "+1234567890",
  "message_id": "msg_xyz789",
  "task_id": "task_abc123",
  "timestamp": "2026-03-21T01:30:00Z"
}
```

## Integration Examples

### In Agent Workflow

```python
from services.whatsapp.whatsapp_client import WhatsAppClient
import asyncio

async def handle_agent_task(task_id, result):
    """Send completion notification via WhatsApp"""
    whatsapp = WhatsAppClient()

    # Get user's phone number
    phone_number = await whatsapp.get_user_phone(user_id)

    if phone_number:
        # Send completion notification
        message = f"""
✅ Task Complete: {task_id}

Result: {result}

What would you like me to do next?
        """
        await whatsapp.send_message(phone_number, message, user_id)

    # Request approval if needed
    if result.get("needs_approval"):
        await whatsapp.create_approval_request(
            user_id=user_id,
            task_id=task_id,
            suggestion=result.get("suggestion"),
            channel="whatsapp"
        )
```

### In Oversight Hub Dashboard

```javascript
const ChannelStatus = () => {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    // Check WhatsApp status
    fetch('/api/whatsapp/status')
      .then((res) => res.json())
      .then((data) => setStatus(data));
  }, []);

  return (
    <Card>
      <CardContent>
        <Typography variant="h6">WhatsApp Status</Typography>
        {status?.connected ? (
          <CheckCircleIcon color="success" />
        ) : (
          <ErrorIcon color="error" />
        )}
        <Typography>{status?.phone_number || 'Not connected'}</Typography>
      </CardContent>
    </Card>
  );
};
```

## Testing

### Manual Testing with curl

```bash
# Test send message
curl -X POST http://localhost:8000/api/whatsapp/send \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "recipient_phone": "+1234567890",
    "message": "Test message from AI co-founder"
  }'

# Test status
curl -X GET http://localhost:8000/api/whatsapp/status \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Test approval request
curl -X POST http://localhost:8000/api/whatsapp/request-approval \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "test_123",
    "suggestion": "I recommend running this test task. Approve?"
  }'
```

## Security Considerations

1. **API Key Protection** - Always store WhatsApp API key securely (use environment variables)
2. **Phone Number Validation** - Always validate phone numbers before sending
3. **Rate Limiting** - Implement rate limiting to prevent abuse
4. **Message Length** - Truncate messages to WhatsApp limits (4096 chars)
5. **Authentication** - Always require JWT token for all endpoints
6. **HTTPS** - Use HTTPS in production to protect API keys and messages

## Troubleshooting

### Message Not Sending

- Check WhatsApp API URL is correct
- Verify API key is set in environment variables
- Ensure phone number starts with '+'
- Check for network connectivity

### Connection Status Shows Disconnected

- Verify OpenClaw gateway is running
- Check API endpoint is accessible
- Review error messages in logs
- Verify API key is valid

### User Phone Not Found

- Ensure phone number is stored in database
- Check cache is enabled if configured
- Verify phone format in database (must include '+')

## Database Schema (Optional)

### WhatsApp Phone Numbers Table

```sql
CREATE TABLE IF NOT EXISTS whatsapp_phones (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) UNIQUE NOT NULL,
    phone_number VARCHAR(20) NOT NULL,
    is_verified BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Approval Requests Table

```sql
CREATE TABLE IF NOT EXISTS approval_requests (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    task_id VARCHAR(255) NOT NULL,
    suggestion TEXT NOT NULL,
    channel VARCHAR(50) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    approved BOOLEAN,
    rejection_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

## Next Steps

1. **Integrate with OpenClaw plugins** for full channel support
2. **Add real-time WebSocket notifications** for message status
3. **Implement message read receipts** and delivery confirmations
4. **Add analytics tracking** for message performance
5. **Create dashboard widgets** for channel management
6. **Implement multi-channel support** (WhatsApp, Telegram, Discord, iMessage)

## Related Documentation

- [Quick Start Guide](01-Getting-Started/Quick-Start-Guide.md) - Get started with WhatsApp integration
- [API Design](02-Architecture/API-Design.md) - Complete API endpoint reference
- [Environment Variables](01-Getting-Started/Environment-Variables.md) - Configuration details
- [WhatsApp Service Implementation](../../src/cofounder_agent/services/whatsapp/README.md) - Technical documentation

---

**Status:** ✅ Production Ready
**Version:** 1.0.81
**Last Updated:** March 21, 2026
