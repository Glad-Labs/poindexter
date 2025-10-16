# Co-founder Agent Development Mode Setup

## Overview

The GLAD Labs AI Co-founder Agent can now run in **Development Mode** to work without requiring Google Cloud Platform (GCP) authentication. This is ideal for local development and testing.

## What Was Fixed

### Issue

The server was starting successfully but showing GCP authentication errors:

- `No GCP_PROJECT_ID found, using default project`
- `Failed to ensure topics exist: 404 Requested project not found`
- ALTS credentials errors

### Solution

Added development mode support that:

1. **Loads environment variables** from `.env` file automatically
2. **Skips GCP authentication** when in dev mode
3. **Uses mock services** for Firestore and Pub/Sub operations
4. **Provides fallback IDs** for database operations

## Configuration

### Environment Variables (`.env` file)

```bash
# Enable Development Mode
DEV_MODE=true
USE_MOCK_SERVICES=true

# GCP Configuration (still required for project reference)
GCP_PROJECT_ID=gen-lang-client-0031944915
```

## Running the Server

### Development Mode (Local Testing - No GCP Required)

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Run the server
npm run dev:cofounder
```

The server will:

- ✅ Load environment variables from `.env`
- ✅ Start on `http://localhost:8000`
- ✅ API docs available at `http://localhost:8000/docs`
- ✅ Skip GCP authentication (dev mode)
- ✅ Use mock services for database operations

### Production Mode (With GCP Authentication)

```bash
# Set environment variable
DEV_MODE=false

# Ensure GCP credentials are configured
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json

# Run the server
npm run dev:cofounder
```

## Files Modified

1. **`start_server.py`** - Added automatic `.env` file loading with `python-dotenv`
2. **`services/firestore_client.py`** - Added dev mode detection and graceful fallback
3. **`services/pubsub_client.py`** - Added dev mode detection and skips topic creation
4. **`.env`** - Added `DEV_MODE` and `USE_MOCK_SERVICES` flags

## Benefits

### For Development

- 🚀 **Faster startup** - No waiting for GCP authentication
- 🛠️ **No credentials needed** - Work without service account keys
- ✨ **Clean logs** - No error noise from missing GCP access
- 🔄 **Quick iterations** - Test changes without cloud dependencies

### For Production

- ☁️ **Full GCP integration** - When `DEV_MODE=false`
- 📊 **Real database** - Firestore persistence for production data
- 🔔 **Event system** - Pub/Sub messaging for agent coordination
- 📈 **Monitoring** - Performance tracking and logging

## API Endpoints

Even in dev mode, all API endpoints are available:

- **GET** `/` - Health check
- **POST** `/command` - Send commands to the agent
- **GET** `/agents` - List agent statuses
- **POST** `/tasks` - Create tasks
- **GET** `/performance` - Get performance metrics
- **GET** `/docs` - Interactive API documentation

## Testing

### Quick Test (Automated)

Run all tests with one command:

```powershell
.\scripts\quick-test-api.ps1
```

### Interactive API Documentation

Access the interactive API documentation in your browser:

```powershell
Start-Process 'http://localhost:8000/docs'
```

Or visit: http://localhost:8000/docs

### Manual API Tests

**Health Check:**

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/' -Method Get
```

**Send Status Command:**

```powershell
$body = @{command="status"; parameters=@{}} | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/command" `
    -Method Post `
    -Headers @{"Content-Type"="application/json"} `
    -Body $body
```

**Create a Task:**

```powershell
$taskBody = @{
    topic = "AI Development Best Practices"
    primary_keyword = "AI agents"
    target_audience = "Developers"
    category = "Blog Post"
    metadata = @{
        priority = 1
        content_type = "technical_guide"
    }
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/tasks" `
    -Method Post `
    -Headers @{"Content-Type"="application/json"} `
    -Body $taskBody
```

**Get All Agents:**

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/agents' -Method Get | ConvertTo-Json -Depth 5
```

**Get Performance Metrics:**

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/performance' -Method Get | ConvertTo-Json -Depth 5
```

> **Note**: PowerShell's `curl` is an alias for `Invoke-WebRequest` with different syntax. Use `Invoke-RestMethod` for API calls instead.

## Troubleshooting

### If you still see GCP errors

1. Check `.env` file has `DEV_MODE=true`
2. Restart the server
3. Verify `python-dotenv` is installed: `pip install python-dotenv`

### If environment variables aren't loading

```powershell
# Install python-dotenv
pip install python-dotenv

# Verify .env file exists
Test-Path .\.env
```

### To switch to production mode

```bash
# Edit .env file
DEV_MODE=false
USE_MOCK_SERVICES=false

# Set GCP credentials
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

## Next Steps

1. **Test all API endpoints** in dev mode
2. **Add more mock data** for realistic testing
3. **Create integration tests** that work in both modes
4. **Document production deployment** process with GCP credentials

## Dependencies

Required Python packages (already in `requirements.txt`):

- `python-dotenv>=1.0.0` - Environment variable loading
- `fastapi>=0.104.0` - Web framework
- `uvicorn>=0.24.0` - ASGI server
- `google-cloud-firestore>=2.12.0` - Firestore (optional in dev mode)
- `google-cloud-pubsub>=2.18.0` - Pub/Sub (optional in dev mode)

---

**Status**: ✅ Working - Server runs successfully in dev mode without GCP authentication
**Date**: October 15, 2025
