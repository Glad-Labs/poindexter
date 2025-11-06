# 🚀 Co-Founder Agent - Startup Configuration Guide

**Last Updated:** December 2024  
**Status:** ✅ Optimized for Production + Development  
**Configuration Level:** Intermediate

---

## 📋 Overview

Your Co-Founder Agent has been configured to start optimally via VS Code tasks. This guide explains:

1. **Which startup script to use** and why
2. **How it's configured** in tasks.json
3. **How to launch it** manually if needed
4. **Troubleshooting** if something goes wrong

---

## 🎯 Quick Start

### **Via VS Code Tasks (Recommended)**

1. Open VS Code Command Palette: `Ctrl+Shift+P`
2. Type: `Tasks: Run Task`
3. Select: **"Start Co-founder Agent"**
4. Watch the logs → Should see: `[Backend] Starting uvicorn...` → `Uvicorn running on http://127.0.0.1:8000`

### **Via Command Line (PowerShell)**

```powershell
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python start_backend.py
```

### **Via All Services (Recommended)**

1. Command Palette: `Ctrl+Shift+P`
2. Type: `Tasks: Run Task`
3. Select: **"Start All Services"**
4. Watch all 4 services start in parallel:
   - Strapi CMS (port 1337)
   - Oversight Hub (port 3000)
   - Public Site (port 3001)
   - Co-founder Agent (port 8000)

---

## 🔍 Startup Script Analysis

### **`start_backend.py` (RECOMMENDED)**

**What it does:**

```python
1. Adds src/ to Python path for proper imports
2. Sets PYTHONPATH environment variable
3. Changes to project root directory
4. Runs: python -m uvicorn src.cofounder_agent.main:app --reload
5. Handles graceful shutdown (Ctrl+C)
```

**Key Features:**

- ✅ **Auto-reload**: Detects code changes automatically
- ✅ **Proper PYTHONPATH**: Ensures imports work correctly
- ✅ **Error handling**: Catches interrupts and exceptions
- ✅ **Port**: 8000 (matches requirements)
- ✅ **Module path**: Uses full import path `src.cofounder_agent.main:app`

**When to use:**

- Local development (auto-reload helpful)
- Running via VS Code tasks (reliable)
- Windows PowerShell (cross-platform safe)

**Output example:**

```
[Backend] Project root: c:\Users\mattm\glad-labs-website
[Backend] Python path includes: c:\Users\mattm\glad-labs-website\src
[Backend] Working directory: c:\Users\mattm\glad-labs-website
[Backend] Starting uvicorn...

INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started server process [12345]
INFO:     Application startup complete
```

---

### **`run.py` (Production Alternative)**

**What it does:**

```python
import uvicorn
uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=False, log_level="info")
```

**Key Features:**

- ✅ **Simple**: 20-line script
- ✅ **Production-ready**: reload=False (stable)
- ✅ **Port 8001**: Non-standard to avoid conflicts
- ❌ **No auto-reload**: Need to restart manually for code changes

**When to use:**

- Production deployments (Railway, Docker)
- Stable/released versions
- When you DON'T want auto-reload

---

### **`start_server.py` (Debug Alternative)**

**What it does:**

```python
1. 5-step initialization with logging
2. Environment variable checking
3. Path setup
4. ImportError handling
5. Verbose timestamped output
```

**Key Features:**

- ✅ **Verbose logging**: See every initialization step
- ✅ **Environment checking**: Shows DEBUG, ENVIRONMENT settings
- ✅ **Development-friendly**: Good for troubleshooting
- ⚠️ **Noisy output**: Too much logging for production

**When to use:**

- Debugging startup issues
- First-time setup verification
- Understanding initialization sequence

---

## ⚙️ Configuration in tasks.json

### **What Changed**

**Before:**

```json
{
  "label": "Start Cofounder Agent",
  "command": "python start_server.py", // Verbose script
  "options": { "cwd": "${workspaceFolder}/src/cofounder_agent" }
}
```

**After:**

```json
{
  "label": "Start Co-founder Agent",
  "command": "python start_backend.py", // Optimized script
  "options": { "cwd": "${workspaceFolder}/src/cofounder_agent" },
  "problemMatcher": ["$python"],
  "presentation": {
    "group": "services",
    "panel": "dedicated",
    "reveal": "always",
    "clear": false
  }
}
```

### **What Each Setting Does**

| Setting          | Value                                    | Purpose                     |
| ---------------- | ---------------------------------------- | --------------------------- |
| `label`          | "Start Co-founder Agent"                 | Task name in VS Code        |
| `type`           | "shell"                                  | Run in terminal             |
| `command`        | "python start_backend.py"                | Execute this file           |
| `cwd`            | `${workspaceFolder}/src/cofounder_agent` | Run from this directory     |
| `problemMatcher` | `["$python"]`                            | Parse Python errors         |
| `group`          | "services"                               | Group with other services   |
| `panel`          | "dedicated"                              | Use dedicated terminal      |
| `reveal`         | "always"                                 | Show terminal if hidden     |
| `clear`          | false                                    | Don't clear previous output |

---

## 🔗 How It Integrates

### **Component Architecture**

```
Start All Services (Composite Task)
├── Start Strapi CMS (port 1337)
├── Start Oversight Hub (port 3000)
├── Start Public Site (port 3001)
└── Start Co-founder Agent (port 8000)
    └── Runs: python start_backend.py
        └── Executes: uvicorn src.cofounder_agent.main:app
            └── Loads: FastAPI app with TaskExecutor
                └── Background tasks: Poll every 5 seconds
```

### **Port Configuration**

| Service          | Port | Path                  | Status     |
| ---------------- | ---- | --------------------- | ---------- |
| Strapi CMS       | 1337 | `cms/strapi-main`     | ✅ Running |
| Oversight Hub    | 3000 | `web/oversight-hub`   | ✅ Running |
| Public Site      | 3001 | `web/public-site`     | ✅ Running |
| Co-founder Agent | 8000 | `src/cofounder_agent` | ✅ Running |

---

## ✅ Prerequisites

Before starting, verify these are installed:

### **1. Python 3.12+**

```powershell
python --version
# Expected: Python 3.12.x or higher
```

### **2. Required Python Packages**

```powershell
# From src/cofounder_agent directory:
pip install -r requirements.txt

# Key packages:
# - fastapi >= 0.104.0
# - uvicorn >= 0.24.0
# - sqlalchemy[asyncio] >= 2.0.0
# - asyncpg >= 0.29.0
# - (and 20+ more - see requirements.txt)
```

### **3. Environment Variables**

Check that `.env` or `.env.local` exists:

```bash
cd src/cofounder_agent
cat .env
```

Should contain:

```bash
DATABASE_URL=sqlite:///.tmp/data.db  # Local dev
# OR
DATABASE_URL=postgresql://user:pass@localhost/dbname  # Production

ENVIRONMENT=development
DEBUG=True
LOG_LEVEL=DEBUG
```

### **4. PostgreSQL Database** (if using production DB)

```powershell
psql -U postgres -c "CREATE DATABASE glad_labs;"
```

Or use SQLite (automatic):

```powershell
# Already configured in .env for local development
```

### **5. Ollama Service** (for AI models)

```powershell
# Check if Ollama is running
curl http://localhost:11434/api/tags

# If not, start Ollama:
ollama serve
```

---

## 🚀 How to Start

### **Method 1: Via VS Code UI (Easiest)**

1. **Press**: `Ctrl+Shift+P`
2. **Type**: `Tasks: Run Task`
3. **Select**: `Start Co-founder Agent`
4. **Wait**: Until you see `Application startup complete`

### **Method 2: Via Command Palette (Keyboard)**

1. **Press**: `Ctrl+Shift+P`
2. **Type**: `task` (auto-completes to "Tasks: Run Task")
3. **Press**: `Enter`
4. **Select**: `Start Co-founder Agent` (or `Start All Services`)
5. **Press**: `Enter`

### **Method 3: Via PowerShell (Manual)**

```powershell
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python start_backend.py
```

Expected output:

```
[Backend] Project root: c:\Users\mattm\glad-labs-website
[Backend] Python path includes: c:\Users\mattm\glad-labs-website\src
[Backend] Working directory: c:\Users\mattm\glad-labs-website
[Backend] Starting uvicorn...

INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started server process [12345]
INFO:     Application startup complete
```

---

## ✨ What Happens When It Starts

### **1. Initialization Sequence (3-5 seconds)**

```
✓ Python path configured
✓ Working directory changed to project root
✓ Uvicorn started
✓ FastAPI app loaded (main.py)
✓ TaskExecutor initialized
✓ Task polling started (every 5 seconds)
```

### **2. Services Active**

```
✓ RESTful API: http://localhost:8000/api/*
✓ Interactive docs: http://localhost:8000/docs
✓ Swagger UI: http://localhost:8000/redoc
✓ Background task executor: Processing every 5 seconds
✓ WebSocket ready: ws://localhost:8000/ws
```

### **3. Task Processing Enabled**

Tasks created via API will:

1. Be stored in database
2. Show status: "pending"
3. Be picked up by executor (within 5 seconds)
4. Execute asynchronously
5. Update status: "completed" or "failed"

---

## 🧪 Verify It's Working

### **Quick Health Check**

```powershell
# Test 1: Health endpoint
curl http://localhost:8000/api/health

# Expected response:
# {
#   "status": "healthy",
#   "timestamp": "2024-12-19T10:30:00Z",
#   "agents": {...}
# }
```

### **Test 2: Create a Task**

```powershell
$body = @{
    title = "Test Task"
    description = "Test task creation"
    type = "content_generation"
} | ConvertTo-Json

curl -X POST `
  -Headers @{'Content-Type'='application/json'} `
  -Body $body `
  http://localhost:8000/api/tasks

# Expected response:
# {
#   "id": "123e4567-e89b-12d3-a456-426614174000",
#   "title": "Test Task",
#   "status": "pending",
#   "created_at": "2024-12-19T10:30:00Z"
# }
```

### **Test 3: Run Full Test Suite**

```powershell
cd c:\Users\mattm\glad-labs-website
python test_task_pipeline.py

# Expected output:
# ✅ Task 1: Created (pending) → Processed → Result stored
# ✅ Task 2: Created (pending) → Processed → Result stored
# ✅ All 5 test tasks completed successfully
# ✅ Test pipeline verification complete
```

---

## 🐛 Troubleshooting

### **Issue 1: "ModuleNotFoundError: No module named 'fastapi'"**

**Cause**: Python packages not installed

**Fix**:

```powershell
cd src\cofounder_agent
pip install -r requirements.txt
```

---

### **Issue 2: "Port 8000 already in use"**

**Cause**: Another service using port 8000

**Fix** (PowerShell):

```powershell
# Find process using port 8000
netstat -ano | findstr :8000

# Kill process (replace XXXX with PID)
taskkill /PID XXXX /F

# Or modify start_backend.py to use different port:
# Change: "--port", "8000" → "--port", "8001"
```

---

### **Issue 3: "ERROR: could not translate host name 'localhost'"**

**Cause**: Database connection issue

**Fix**:

```powershell
# Check .env DATABASE_URL
cat .env | findstr DATABASE_URL

# For development, should be:
# DATABASE_URL=sqlite:///.tmp/data.db

# Verify .tmp directory exists:
dir .\.tmp\

# If not, create it:
mkdir .\.tmp
```

---

### **Issue 4: "Uvicorn running on... but won't load"**

**Cause**: Hanging import or initialization

**Fix**:

1. Press `Ctrl+C` to stop
2. Check logs for errors
3. Try verbose startup instead:
   ```powershell
   python start_server.py
   ```
4. Look for specific error messages

---

### **Issue 5: "ERROR: Application startup complete but tasks not processing"**

**Cause**: TaskExecutor not initialized

**Fix**:

1. Check main.py has TaskExecutor import (line 44)
2. Verify lifespan startup runs executor.start() (line 162)
3. Restart: `Ctrl+C` then run again
4. Check logs for "Task executor started" message

---

## 📊 Monitoring

### **Real-time Task Monitoring**

**Terminal 1**: Start Co-founder Agent

```powershell
# CoFounded Agent running, processing tasks
```

**Terminal 2**: Monitor tasks

```powershell
while ($true) {
    curl http://localhost:8000/api/tasks
    Start-Sleep -Seconds 5
}
```

### **Check Task Status**

```powershell
# Get all tasks
curl http://localhost:8000/api/tasks

# Get specific task
curl http://localhost:8000/api/tasks/{task-id}

# Expected progression:
# pending → in_progress → completed
# (or pending → in_progress → failed if error)
```

---

## 🔄 Switching Startup Scripts

If you need to switch startup methods:

### **Use `run.py` (Production)**

Edit tasks.json:

```json
"command": "python run.py"
```

Then restart.

### **Use `start_server.py` (Debug)**

Edit tasks.json:

```json
"command": "python start_server.py"
```

Then restart.

---

## 📝 Environment Variables Reference

All available environment variables in `.env`:

```bash
# Database
DATABASE_URL=sqlite:///.tmp/data.db
DATABASE_POOL_SIZE=5
DATABASE_ECHO=False

# Server
HOST=127.0.0.1
PORT=8000
ENVIRONMENT=development
DEBUG=True
LOG_LEVEL=DEBUG
RELOAD=True

# CORS
CORS_ORIGINS=http://localhost:3000,http://localhost:3001

# AI Models
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
USE_OLLAMA=True
OLLAMA_HOST=http://localhost:11434

# Task Executor
TASK_POLL_INTERVAL=5
TASK_TIMEOUT=300
TASK_RETRY_COUNT=3
```

---

## ✅ Checklist Before Running

- [ ] Python 3.12+ installed
- [ ] `pip install -r requirements.txt` completed
- [ ] `.env` file exists with DATABASE_URL
- [ ] `.tmp/` directory exists (or will be created)
- [ ] Ollama service running (optional, but recommended)
- [ ] Port 8000 is available
- [ ] VS Code has tasks.json updated ✅
- [ ] All 4 services can run in parallel

---

## 🎉 You're Ready!

Your Co-Founder Agent is now optimized and ready to run.

**Next steps:**

1. **Start all services**: `Ctrl+Shift+P` → `Tasks: Run Task` → `Start All Services`
2. **Access dashboard**: Open http://localhost:3001 (Oversight Hub)
3. **Create a task**: Use the API or dashboard to create tasks
4. **Watch executor**: Tasks will process automatically every 5 seconds
5. **View results**: Use `/api/tasks` endpoint to check status

---

## 📞 Support

**If you encounter issues:**

1. Check the **Troubleshooting** section above
2. Review startup script output for errors
3. Run `python start_server.py` for verbose debugging
4. Check `.env` configuration
5. Verify all prerequisites are installed

---

**Last Updated**: December 2024  
**Status**: ✅ Production Ready  
**Support Level**: Intermediate to Advanced
