# 🧪 Dexter's Lab - Ollama & Navigation Fix Guide

## ✅ What Was Fixed

### 1. **Ollama Connection Detection**

- ✅ Added `/api/ollama/health` endpoint to check if Ollama is running
- ✅ Added `/api/ollama/warmup` endpoint to pre-load models into memory
- ✅ Frontend automatically checks Ollama on app load
- ✅ Visual status indicator (🟢 green when connected, 🔴 red when offline)
- ✅ Warm-up happens automatically after 1 second if Ollama is detected

### 2. **Navigation Menu Fix**

- ✅ Changed from `<a href>` links (full page reload) to React state-based navigation
- ✅ Menu now uses buttons with `handleNavigate()` instead of links
- ✅ Each page has content placeholder
- ✅ Active page is highlighted in navigation menu
- ✅ Menu stays accessible while navigating (doesn't get "lost")

### 3. **Page-Specific Content**

- Dashboard: Shows metrics and task queue (original view)
- Tasks: Task management interface placeholder
- Models: Model config + Ollama status info
- Social: Social media management placeholder
- Content: Content generation placeholder
- Costs: Cost tracking placeholder
- Analytics: Analytics dashboard placeholder
- Settings: Settings configuration placeholder

---

## 🚀 How to Test

### Step 1: Start Ollama (if you want to test connection)

**Option A: If you have Ollama installed**

```powershell
# Open a new terminal and start Ollama
ollama serve
```

**Option B: Skip for now**

- The app will show "🔴 Ollama Offline" warning
- Chat will still work in demo mode
- You can start Ollama anytime and refresh the page

### Step 2: Start the Backend

```powershell
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload
```

### Step 3: Start the Frontend

```powershell
cd c:\Users\mattm\glad-labs-website\web\oversight-hub
npm start
```

### Step 4: Open the App

```
http://localhost:3001
```

---

## 🧪 Testing Checklist

### Test Ollama Connection

- [ ] App loads
- [ ] Check browser console (F12 → Console tab)
- [ ] Look for `[Ollama] Checking connection...` message
- [ ] If Ollama is running:
  - [ ] You should see `[Ollama] ✅ Connected! Found X models`
  - [ ] Green indicator shows "🟢 Ollama Ready"
  - [ ] A warm-up message appears in chat: "🔥 Model 'mistral' warmed up successfully in X.XX seconds"
- [ ] If Ollama is not running:
  - [ ] You should see `[Ollama] ⚠️ Not connected:`
  - [ ] Red indicator shows "🔴 Ollama Offline"
  - [ ] Yellow warning box appears: "⚠️ Ollama Connection Issue"

### Test Navigation Menu

- [ ] Click hamburger menu (☰) in top left
- [ ] You should see 8 menu items:
  - 📊 Dashboard
  - ✅ Tasks
  - 🤖 Models
  - 📱 Social
  - 📝 Content
  - 💰 Costs
  - 📈 Analytics
  - ⚙️ Settings
- [ ] Click "Tasks" - page changes, menu stays open
- [ ] Click "Models" - page changes, models info shows
- [ ] Click "Dashboard" - back to original view with metrics
- [ ] Click "Settings" - page changes to settings view
- [ ] Menu still works at any page
- [ ] Active page is highlighted (left border is cyan)

### Test Chat

- [ ] Type a message and click Send
- [ ] Backend should respond with demo response
- [ ] Select different models from dropdown
- [ ] Chat messages appear correctly

### Test Ollama Status Display (on Models page)

- If Ollama is connected:
  - [ ] Shows "Ollama Status: running"
  - [ ] Shows "Connected: ✅ Yes"
  - [ ] Lists all available models
- If Ollama is offline:
  - [ ] Shows "Ollama Status: unreachable"
  - [ ] Shows "Connected: ❌ No"
  - [ ] Shows error message with instructions

---

## 🔍 Console Logs to Look For

### Successful Ollama Connection

```
[Ollama] Checking connection...
[Ollama] Health check response: {connected: true, status: "running", models: Array(3), ...}
[Ollama] ✅ Connected! Found 3 models
[Ollama] Starting warm-up...
[Ollama] Warm-up complete: ✅ Model 'mistral' warmed up successfully in 2.34 seconds
```

### Ollama Not Running

```
[Ollama] Checking connection...
[Ollama] Connection check error: fetch failed (connection refused)
[Ollama] ❌ Not connected
```

### Navigation

```
[Navigation] Page changed to: tasks
[Navigation] Page changed to: models
[Navigation] Page changed to: dashboard
```

---

## 📝 Backend API Endpoints

### Ollama Health Check

```bash
GET http://localhost:8000/api/ollama/health

# Response (Connected)
{
  "connected": true,
  "status": "running",
  "models": ["mistral", "llama2", "neural-chat"],
  "message": "✅ Ollama is running with 3 model(s)",
  "timestamp": "2025-11-01T12:00:00.000Z"
}

# Response (Not Connected)
{
  "connected": false,
  "status": "unreachable",
  "models": null,
  "message": "❌ Cannot connect to Ollama at http://localhost:11434. Is Ollama running?",
  "timestamp": "2025-11-01T12:00:00.000Z"
}
```

### Ollama Warm-up

```bash
POST http://localhost:8000/api/ollama/warmup
Content-Type: application/json

{
  "model": "mistral"
}

# Response (Success)
{
  "status": "success",
  "model": "mistral",
  "message": "✅ Model 'mistral' warmed up successfully in 2.34 seconds",
  "generation_time": 2.34,
  "timestamp": "2025-11-01T12:00:00.000Z"
}
```

### Ollama System Status

```bash
GET http://localhost:8000/api/ollama/status

# Response
{
  "running": true,
  "host": "http://localhost:11434",
  "models_available": 3,
  "models": ["mistral", "llama2", "neural-chat"],
  "last_check": "2025-11-01T12:00:00.000Z"
}
```

---

## 📂 Files Changed

### Frontend

- `web/oversight-hub/src/OversightHub.jsx` - Updated with:
  - Ollama health check on component mount
  - State-based navigation (no href links)
  - Page-specific content rendering
  - Ollama status indicator
  - Warm-up integration

### Backend

- `src/cofounder_agent/routes/ollama_routes.py` - **NEW** file with:
  - Health check endpoint
  - Warm-up endpoint
  - Status endpoint
  - Async HTTP client for Ollama
- `src/cofounder_agent/main.py` - Updated to:
  - Import ollama_routes
  - Register ollama router

---

## 🐛 Troubleshooting

### "Cannot connect to Ollama"

**Solution:** Ollama is not running or not on localhost:11434

```powershell
# Start Ollama
ollama serve

# Or verify it's running
netstat -ano | findstr 11434
```

### Navigation menu closes on page change

**Solution:** Menu should stay open. If it closes:

1. Hard refresh: `Ctrl+Shift+R`
2. Clear browser cache
3. Check console for errors: F12 → Console

### Warm-up never completes

**Solution:** Model is loading. This is normal - first warm-up can take 30+ seconds

- Check browser console for status updates
- Wait for message: "🔥 Model warmed up successfully"

### Ollama shows but then disappears

**Solution:** Verify Ollama is still running

```powershell
# Check if ollama process exists
Get-Process ollama -ErrorAction SilentlyContinue
```

---

## ✨ Next Steps

### When Ready for Real Ollama Integration

1. Modify `/api/chat` endpoint to actually call Ollama API instead of demo response
2. Use the model selector to route requests to correct Ollama model
3. Implement streaming responses for better UX
4. Add error handling for model timeouts

### When Ready for Other Models

1. Implement OpenAI integration (use existing OPENAI_API_KEY)
2. Implement Claude integration (use existing ANTHROPIC_API_KEY)
3. Implement Gemini integration (use existing GOOGLE_API_KEY)
4. Add model fallback chain (try Ollama → OpenAI → Claude → Gemini)

---

## 📞 Quick Test Commands

**Test Ollama health endpoint:**

```powershell
$response = Invoke-WebRequest -Uri "http://localhost:8000/api/ollama/health"
$response | ConvertTo-Json
```

**Test Ollama warm-up:**

```powershell
$payload = @{ model = "mistral" } | ConvertTo-Json
$response = Invoke-WebRequest -Uri "http://localhost:8000/api/ollama/warmup" -Method Post -ContentType "application/json" -Body $payload
$response | ConvertTo-Json
```

**Test navigation in browser console:**

```javascript
// These should be visible in the console
[Ollama] Checking connection...
[Ollama] Starting warm-up...
```

---

**Status:** ✅ Ready for testing  
**Last Updated:** November 1, 2025  
**Version:** 1.0
