# ⚡ QUICK START - Co-Founder Agent

## 🚀 Start Now (30 seconds)

**Option 1: All Services Together (Easiest)**
```
Ctrl+Shift+P → Tasks: Run Task → Start All Services
```

**Option 2: Just Co-founder Agent**
```
Ctrl+Shift+P → Tasks: Run Task → Start Co-founder Agent
```

**Option 3: Manual (PowerShell)**
```powershell
cd src\cofounder_agent
python start_backend.py
```

## ✅ Verify It Works

```powershell
# Test endpoint (should return 200)
curl http://localhost:8000/api/health

# Access interactive docs
http://localhost:8000/docs
```

## 🎯 What Was Changed

| Item | Before | After | Why |
|------|--------|-------|-----|
| **Startup Script** | `start_server.py` (verbose) | `start_backend.py` (optimized) | Better PYTHONPATH handling, auto-reload enabled |
| **Port** | 8001 (implicit) | 8000 (explicit) | Matches requirements.txt |
| **Problem Matcher** | None | `$python` | Better error detection |
| **Panel** | Shared | Dedicated | Cleaner output |

## 📁 Key Files

- **Startup**: `src/cofounder_agent/start_backend.py` ← Used by tasks.json
- **Config**: `.vscode/tasks.json` ← Updated ✅
- **App**: `src/cofounder_agent/main.py` ← Has TaskExecutor
- **Tests**: `test_task_pipeline.py` ← Verify it works

## 🔍 Available Startup Scripts

| Script | Best For | Port | Reload |
|--------|----------|------|--------|
| `start_backend.py` | **Tasks.json ✅** | 8000 | ✅ Yes |
| `run.py` | Production | 8001 | ❌ No |
| `start_server.py` | Debugging | 8000 | ? |

## ✨ What Happens When Running

1. **Initialization** (3-5 seconds)
   - Python path configured
   - Working directory set
   - FastAPI app loads
   - TaskExecutor starts

2. **Services Available**
   - REST API: `http://localhost:8000/api/*`
   - Docs: `http://localhost:8000/docs`
   - WebSocket: `ws://localhost:8000/ws`
   - Background task polling: Every 5 seconds ✅

3. **Ready for**
   - Creating tasks via API
   - Processing automatically in background
   - Viewing results via endpoint

## 🚨 Troubleshooting

| Problem | Fix |
|---------|-----|
| "Module not found" | `pip install -r requirements.txt` |
| "Port 8000 in use" | `netstat -ano \| findstr :8000` then `taskkill /PID XXXX /F` |
| "Database error" | Check `.env` has `DATABASE_URL` set |
| "Ollama not found" | Run `ollama serve` in separate terminal (optional) |

## 📊 Full Service Architecture

```
Start All Services (Composite)
├── Strapi CMS (port 1337) - npm run develop
├── Oversight Hub (port 3000) - npm start
├── Public Site (port 3001) - npm run dev
└── Co-founder Agent (port 8000) - python start_backend.py ✅
    └── TaskExecutor: Background processing
```

## ✅ Checklist

- [ ] Python 3.12+ installed: `python --version`
- [ ] Packages installed: `pip install -r requirements.txt`
- [ ] `.env` exists with DATABASE_URL
- [ ] `.vscode/tasks.json` updated (✅ done)
- [ ] Port 8000 available
- [ ] Ready to start!

## 📝 Next Steps

1. **Start**: `Ctrl+Shift+P` → Tasks: Run Task → Start All Services
2. **Verify**: `curl http://localhost:8000/api/health`
3. **Test**: `python test_task_pipeline.py`
4. **Monitor**: Watch task processing in real-time

---

**Your Co-Founder Agent is now optimized and ready to run! 🎉**
