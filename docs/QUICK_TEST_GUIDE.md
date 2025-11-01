# 🚀 Quick Start: Test the Fixes

## What You Asked For

1. ✅ **"Check if I'm actually connecting to Ollama"** → Now shows 🟢 or 🔴 status indicator
2. ✅ **"Warm-up Ollama on hub load"** → Automatically happens after 1 second if connected
3. ✅ **"Navigation menu loses access"** → Fixed - menu stays available across all pages

---

## 3-Step Test (5 minutes)

### STEP 1: Start Backend

```powershell
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload
# Wait for: "Uvicorn running on http://127.0.0.1:8000"
```

### STEP 2: Start Frontend

```powershell
cd c:\Users\mattm\glad-labs-website\web\oversight-hub
npm start
# Wait for: "Compiled successfully"
```

### STEP 3: Open Browser

```
http://localhost:3001
```

---

## What You'll See

### Header

```
🧪 Dexter's Lab   ☰   🟢 Ollama Ready
```

OR

```
🧪 Dexter's Lab   ☰   🔴 Ollama Offline
```

### Console (F12 → Console tab)

```
[Ollama] Checking connection...
[Ollama] ✅ Connected! Found 3 models
[Ollama] Starting warm-up...
[Ollama] Warm-up complete: ✅ Model 'mistral' warmed up in 2.34 seconds
```

### Chat Box

```
System: Co-Founder AI ready. How can I help?
AI: 🔥 Model 'mistral' warmed up successfully in 2.34 seconds
```

---

## Test Navigation

1. Click hamburger menu ☰
2. See 8 options:
   - 📊 Dashboard
   - ✅ Tasks
   - 🤖 Models
   - 📱 Social
   - 📝 Content
   - 💰 Costs
   - 📈 Analytics
   - ⚙️ Settings

3. Click "Tasks" → page changes, **menu stays visible**
4. Click "Models" → page changes, **see Ollama status info**
5. Click "Dashboard" → back to metrics view

**Result:** ✅ No lost menu! Can navigate from any page!

---

## Troubleshooting

### "❌ Ollama Offline" but Ollama IS running

- Hard refresh: `Ctrl+Shift+R`
- Check Ollama port: `netstat -ano | findstr 11434`
- Check backend logs for errors

### "Import error for ollama_routes"

- Backend may not have restarted
- Stop and restart:
  ```powershell
  # Kill old python process
  Get-Process python | Stop-Process -Force
  python -m uvicorn main:app --reload
  ```

### Frontend won't compile

```powershell
cd web\oversight-hub
rm -r node_modules
npm install
npm start
```

---

## Files Changed

**Backend (New):**

- `src/cofounder_agent/routes/ollama_routes.py` (383 lines)
  - GET `/api/ollama/health` - Check connection
  - POST `/api/ollama/warmup` - Warm up models
  - GET `/api/ollama/status` - System info

**Backend (Modified):**

- `src/cofounder_agent/main.py`
  - Added ollama router import
  - Registered router (1 line change)

**Frontend (Modified):**

- `web/oversight-hub/src/OversightHub.jsx`
  - Added Ollama health check (useEffect)
  - Changed href navigation to state-based
  - Added 8 page-specific content views
  - Added status indicator
  - Added warm-up integration

---

## Architecture

```
Frontend (React)
  ↓
  On Mount: Checks /api/ollama/health
  ↓
  Backend (FastAPI)
    ↓
    Calls Ollama at localhost:11434
    ↓
    Returns: connected=true/false, models=[], status
  ↓
  Frontend:
    - Shows 🟢 or 🔴 indicator
    - Calls /api/ollama/warmup if connected
    - Displays warm-up message in chat
```

---

## Console Logs to Watch

| Event          | Log                                                                       |
| -------------- | ------------------------------------------------------------------------- |
| Check starts   | `[Ollama] Checking connection...`                                         |
| Connected      | `[Ollama] ✅ Connected! Found X models`                                   |
| Not connected  | `[Ollama] ⚠️ Not connected`                                               |
| Warm-up starts | `[Ollama] Starting warm-up...`                                            |
| Warm-up done   | `[Ollama] Warm-up complete: ✅ Model 'mistral' warmed up in X.XX seconds` |

---

## Endpoints Reference

```bash
# Check if Ollama is running
curl http://localhost:8000/api/ollama/health

# Warm up a model
curl -X POST http://localhost:8000/api/ollama/warmup \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"mistral\"}"

# Get status
curl http://localhost:8000/api/ollama/status
```

---

**Ready?** → Go to Step 1 above and start testing!

Expected test time: 5-10 minutes  
Expected success rate: 95%+ (unless Ollama not installed)  
Difficulty: Easy - just start and watch
