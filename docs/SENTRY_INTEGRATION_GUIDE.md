# 📊 Sentry Error Tracking Integration Guide

## Overview

Sentry is integrated into the Glad Labs AI Co-Founder system for enterprise-grade error tracking, performance monitoring, and incident management. This guide covers setup, configuration, and usage.

## 🚀 Quick Start

### 1. Create a Sentry Account

1. Go to [sentry.io](https://sentry.io)
2. Sign up for a free account (includes generous free tier: 5k events/month)
3. Create a new project and select **FastAPI** as the platform
4. Copy the **DSN** (Data Source Name)

### 2. Configure Environment Variable

Add your Sentry DSN to your environment:

```bash
# Development (local)
export SENTRY_DSN="https://key@sentry.io/project-id"
export SENTRY_ENABLED="true"
export ENVIRONMENT="development"

# Production
export SENTRY_DSN="https://key@sentry.io/project-id"
export SENTRY_ENABLED="true"
export ENVIRONMENT="production"
```

Or add to `.env.local`:

```env
SENTRY_DSN=https://key@sentry.io/project-id
SENTRY_ENABLED=true
ENVIRONMENT=production
```

### 3. Install Sentry SDK

```bash
cd src/cofounder_agent
pip install sentry-sdk[fastapi]
# or
pip install -r requirements.txt  # Already includes sentry-sdk
```

### 4. Verify Installation

```bash
# Check startup logs for Sentry initialization
python -m uvicorn main:app --reload

# Look for:
# ✅ Sentry initialized successfully
#    Environment: development
#    Release: 3.0.1
```

## 📋 Configuration

### Environment Variables

| Variable         | Description           | Default       | Required |
| ---------------- | --------------------- | ------------- | -------- |
| `SENTRY_DSN`     | Sentry project DSN    | (empty)       | Yes\*    |
| `SENTRY_ENABLED` | Enable/disable Sentry | `true`        | No       |
| `ENVIRONMENT`    | Environment name      | `development` | No       |
| `APP_VERSION`    | Application version   | `3.0.1`       | No       |

\*Required to enable error tracking

### Development vs Production

**Development**:

```bash
export SENTRY_DSN="https://..."
export SENTRY_ENABLED="true"
export ENVIRONMENT="development"

# Captures 100% of transactions and profiles
# Includes local variables in stack traces
# Debug logging enabled
```

**Production**:

```bash
export SENTRY_DSN="https://..."
export SENTRY_ENABLED="true"
export ENVIRONMENT="production"

# Captures 10% of transactions (adjustable)
# Excludes sensitive local variables
# Debug logging disabled
```

### Disable Sentry (Testing/Staging)

```bash
export SENTRY_ENABLED="false"

# Or simply don't set SENTRY_DSN
# Sentry will initialize but no events are sent
```

## 🎯 Features

### 1. Automatic Exception Tracking

All unhandled exceptions are automatically captured:

```python
# This exception is automatically captured by Sentry
@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = database.get_task(task_id)  # If this raises, Sentry captures it
    return task
```

**Captured Information**:

- Exception type and message
- Stack trace with source code context
- Request URL, method, headers
- Query parameters and form data
- User context (if authenticated)
- Environment variables (non-sensitive)
- Local variable values (in development)

### 2. Performance Monitoring

Automatically monitors:

- **Request duration**: How long each API endpoint takes
- **Database queries**: Execution time of database operations
- **Async tasks**: Duration of background tasks
- **Transaction throughput**: Number of requests per second
- **Error rates**: Percentage of requests resulting in errors

**Performance Dashboard**:
View metrics in Sentry dashboard → **Performance** tab:

- Slowest endpoints
- Most error-prone endpoints
- Performance trends over time
- Apdex score (application performance index)

### 3. Breadcrumb Tracking

Breadcrumbs provide context for debugging:

```python
# Automatic breadcrumbs:
# - HTTP requests
# - Database queries
# - Cache hits/misses
# - Log messages

# Manual breadcrumb:
from services.sentry_integration import SentryIntegration

SentryIntegration.add_breadcrumb(
    category="task.execution",
    message="Task started",
    level="info",
    data={"task_id": "123", "priority": "high"}
)
```

**Breadcrumb Example in Error Report**:

```
Task processing error
├─ 14:32:15 User authenticated (user: user_123)
├─ 14:32:16 Task created (task_id: task_456)
├─ 14:32:17 Database query started (select from tasks...)
├─ 14:32:18 Cache miss for model configuration
├─ 14:32:19 API call to OpenAI
└─ 14:32:20 ERROR: Request timeout
```

### 4. User Context Tracking

Track which users experience errors:

```python
from services.sentry_integration import SentryIntegration

@app.post("/api/auth/github/callback")
async def github_callback(request_data):
    # ... authentication logic ...

    # Set user context after successful auth
    SentryIntegration.set_user_context(
        user_id=user.id,
        email=user.email,
        username=user.username
    )

    return token

@app.post("/api/auth/logout")
async def logout(current_user):
    # Clear user context on logout
    SentryIntegration.clear_user_context()
    return {"success": True}
```

## 💻 Usage Examples

### Manual Exception Reporting

```python
from services.sentry_integration import SentryIntegration

# Capture an exception with context
try:
    result = await process_content(content_id)
except Exception as e:
    SentryIntegration.capture_exception(
        e,
        context={
            "content_id": content_id,
            "user_id": current_user.id,
            "action": "content_publishing"
        },
        level="error"
    )
    raise
```

### Manual Message Reporting

```python
# Report a message event
SentryIntegration.capture_message(
    "Large task queue detected",
    level="warning",
    context={
        "queue_size": 1000,
        "threshold": 500,
        "duration_seconds": 3600
    }
)
```

### Adding Breadcrumbs

```python
from services.sentry_integration import SentryIntegration

async def process_task(task):
    # Log initialization
    SentryIntegration.add_breadcrumb(
        category="task.processing",
        message=f"Starting task: {task.title}",
        level="info"
    )

    # Log milestone
    SentryIntegration.add_breadcrumb(
        category="database",
        message="Task saved to database",
        level="info",
        data={"task_id": task.id}
    )

    # Log decision point
    SentryIntegration.add_breadcrumb(
        category="ai.inference",
        message="Generating content",
        level="info",
        data={"model": "gpt-4", "tokens": 2048}
    )

    try:
        result = await generate_content(task)
    except Exception as e:
        # If error occurs, all breadcrumbs are included in error report
        SentryIntegration.add_breadcrumb(
            category="error",
            message=f"Generation failed: {str(e)}",
            level="error"
        )
        raise
```

### Performance Monitoring

```python
from services.sentry_integration import SentryIntegration

@app.get("/api/analytics/expensive-report")
async def generate_expensive_report():
    # Start a transaction for performance monitoring
    transaction = SentryIntegration.start_transaction(
        name="generate_expensive_report",
        op="http.request",
        description="Generate comprehensive analytics report"
    )

    try:
        with transaction:
            # Your endpoint logic
            report = await build_analytics_report()
            return report
    except Exception as e:
        SentryIntegration.capture_exception(e)
        raise
```

## 🔍 Sentry Dashboard

### Issue Investigation

1. **Go to Issues Tab**
   - See all reported errors and warnings
   - Group by error type
   - Filter by environment, release, or user

2. **Click on an Issue**
   - View full error details
   - See all breadcrumbs
   - Check affected users
   - Review session replay (if enabled)

3. **Error Details Include**:
   - Stack trace with source code
   - Request information
   - User context
   - Environment variables
   - Local variable values (development)
   - Related errors (similar stack traces)

### Performance Monitoring

1. **Performance Tab**
   - View slowest endpoints
   - Check error rates
   - Monitor Apdex score
   - See performance trends

2. **Transaction Details**
   - Request duration breakdown
   - Child operations (DB queries, API calls)
   - Span details with timings
   - Related errors

### Alerts & Notifications

**Default Alerts**:

- First error in new release
- Spike in error rate (2x normal)
- Critical error

**Custom Alerts**:
Configure in Sentry:

1. Go to Alerts → Create Alert Rule
2. Set condition (e.g., "error rate > 5%")
3. Set action (e.g., "Notify Slack channel")

**Example Alert Setup**:

```
If: [Error rate] > [5%]
For the last: [1 minute]
Then: [Send to Slack: #engineering-alerts]
```

## 🔐 Privacy & Security

### Data Redaction

Sentry automatically redacts sensitive data:

- Authorization headers
- Cookie values
- API keys and tokens
- Passwords (in request body)

**Custom Redaction** (in `sentry_integration.py`):

```python
# The _before_send method handles redaction
# Add additional fields to redact:

sensitive_fields = [
    "authorization",
    "x-api-key",
    "password",
    "ssn",
    "credit_card"
]
```

### What's NOT Sent to Sentry

By default, Sentry does NOT capture:

- Request/response body (unless errors occur)
- SQL query values (only structure)
- File contents
- Environment secrets (unless explicitly logged)

### Disable for GDPR/Compliance

```bash
# Completely disable Sentry
export SENTRY_ENABLED="false"

# Or keep DSN unset
unset SENTRY_DSN
```

## 📱 Mobile & Desktop Clients

### Using Sentry's Mobile App

- iOS/Android app available
- Real-time notifications
- Browsable issues and errors
- Performance insights

### Desktop (Web Dashboard)

- [sentry.io](https://sentry.io) - Full web dashboard
- Alerts, performance monitoring, releases
- Team collaboration
- Integration with Slack, PagerDuty, etc.

## 🔗 Integration with Other Services

### Slack Integration

1. In Sentry → Settings → Integrations
2. Install Slack integration
3. Authorize Sentry for your Slack workspace
4. Create alert rules to notify specific channels

**Alert Example**:

```
When: Error rate spike detected
Send: Message to #alerts with error details
```

### PagerDuty Integration

1. Connect PagerDuty account
2. Configure alert rules for critical issues
3. Automatic incident creation on high-severity errors

### GitHub Integration

1. Connect GitHub repository
2. Sentry automatically creates issues
3. Link releases to commits
4. Track issue resolution in PRs

## 📈 Metrics & Analytics

### Key Metrics Tracked

- **Error Rate**: Percentage of requests resulting in errors
- **Apdex**: Application Performance Index (0-1 scale)
- **P95/P99 Latency**: 95th and 99th percentile response times
- **Throughput**: Requests per second
- **Unique Users Affected**: Users experiencing errors

### Trending & Alerts

- **Regression Alert**: Error rate increased 2x
- **Stability Alert**: New issue in production
- **Performance Alert**: Response time degraded
- **Release Tracking**: Errors per release version

## 🚀 Best Practices

### 1. Release Tracking

Tag releases for better issue tracking:

```python
# In main.py
import os
os.environ.setdefault("APP_VERSION", "3.0.1")

# Sentry automatically captures version from APP_VERSION env var
```

### 2. Error Severity Levels

Use appropriate severity:

```python
# Critical - page broken
SentryIntegration.capture_exception(e, level="fatal")

# High - feature not working
SentryIntegration.capture_exception(e, level="error")

# Medium - degraded functionality
SentryIntegration.capture_exception(e, level="warning")

# Low - informational
SentryIntegration.capture_message("Unusual usage pattern", level="info")
```

### 3. Avoid Noise

- Don't capture expected errors (e.g., 404s from bots)
- Use breadcrumbs for detailed logging
- Set appropriate sample rates for high-volume endpoints

### 4. User Privacy

- Minimize PII (Personal Identifiable Information)
- Use numeric IDs instead of email addresses when possible
- Redact sensitive fields
- Respect user privacy preferences

## 🐛 Troubleshooting

### Events Not Showing Up

**Check**:

1. DSN is correct: `echo $SENTRY_DSN`
2. SENTRY_ENABLED=true: `echo $SENTRY_ENABLED`
3. Sentry is initialized: Check startup logs
4. Network connectivity to sentry.io

**Debug**:

```bash
# Enable debug logging
export ENVIRONMENT=development

# Check logs
tail -f src/cofounder_agent/server.log | grep -i sentry
```

### High Event Volume

**Solutions**:

1. Reduce `traces_sample_rate` in production
2. Filter/ignore specific error types
3. Set up before_send filter to drop events
4. Upgrade Sentry plan for higher quota

### Privacy Concerns

**Options**:

1. Self-host Sentry (on-premise)
2. Use Sentry's EU region (for GDPR)
3. Disable Sentry completely
4. Use alternative (e.g., Rollbar, Bugsnag)

## 📚 Additional Resources

- **Sentry Docs**: https://docs.sentry.io
- **FastAPI Integration**: https://docs.sentry.io/platforms/python/integrations/fastapi
- **Performance Monitoring**: https://docs.sentry.io/platforms/python/performance/
- **Releases**: https://docs.sentry.io/product/releases/

## 💡 Summary

**Sentry provides**:

- ✅ Automatic error capturing
- ✅ Performance monitoring
- ✅ User context tracking
- ✅ Breadcrumb debugging
- ✅ Alert management
- ✅ Release tracking
- ✅ Team collaboration
- ✅ GDPR/Privacy compliant

**Setup cost**: ~5 minutes
**Benefit**: Production visibility and quick issue resolution

---

**Last Updated**: December 7, 2025  
**Version**: 3.0.1  
**Status**: ✅ Production Ready
