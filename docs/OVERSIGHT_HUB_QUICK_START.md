# Oversight Hub Quick Start Guide

## 🚀 Start the Enhanced Platform

### 1. Start All Services

Open 4 separate terminal windows:

**Terminal 1 - Strapi CMS**

```powershell
cd cms/strapi-v5-backend
npm run develop
```

Server: http://localhost:1337

**Terminal 2 - AI Co-Founder Backend**

```powershell
cd src/cofounder_agent
python start_server.py
```

Server: http://localhost:8000

**Terminal 3 - Public Site**

```powershell
cd web/public-site
npm run dev
```

Server: http://localhost:3000

**Terminal 4 - Oversight Hub** ⭐ **(Enhanced!)**

```powershell
cd web/oversight-hub
npm start
```

Server: http://localhost:3001

---

## 📋 New Features Tour

### 🏠 Dashboard (Route: `/`)

**What's New:** Complete system health monitoring

**Try This:**

1. Visit http://localhost:3001
2. See real-time service health (AI Co-Founder, Strapi, Public Site)
3. View model configuration status (Ollama, OpenAI, Anthropic)
4. Check system metrics (API calls, costs, cache hit rate)
5. Click "Refresh" to update all data
6. Click "Start New Task" for quick task creation

**What to Look For:**

- ✅ Green "Healthy" badges on all services
- ✅ Response times under 100ms
- ✅ Model configuration showing your available models
- ✅ System alerts (budget warnings if spending is high)

---

### ✅ Task Management (Route: `/tasks`)

**What's New:** Full task CRUD + bulk operations

**Try This:**

1. Click "Tasks" in the sidebar
2. Click "Create Task" button
   - Title: "Test blog post"
   - Description: "Generate article about AI"
   - Agent: Content Agent
   - Priority: High
3. Click "Create"
4. Select multiple tasks (checkboxes)
5. Try bulk actions: Pause, Resume, Cancel
6. Use filters to find specific tasks
7. Switch between tabs: Active, Completed, Failed

**What to Look For:**

- ✅ Tasks appear in table immediately
- ✅ Status chips show colors (blue=in_progress, green=completed, red=failed)
- ✅ Bulk action banner appears when tasks selected
- ✅ Filters update the table in real-time
- ✅ Auto-refresh every 10 seconds

---

### 🤖 Model Management (Route: `/models`)

**What's New:** AI model testing and configuration

**Try This:**

1. Click "Models" in the sidebar
2. View all three provider cards (Ollama, OpenAI, Anthropic)
3. Toggle a provider on/off with the switch
4. Click the play button (▶️) next to a model
5. Enter a test prompt: "What is 2+2?"
6. Click "Test Model"
7. View the response with metrics (time, tokens, cost)

**What to Look For:**

- ✅ Ollama shows "$0.00/request" (free local models)
- ✅ Models list shows available models for each provider
- ✅ Usage statistics show request counts and costs
- ✅ Test dialog shows real AI responses
- ✅ Response time and cost metrics displayed
- ✅ Success indicators (green check) or errors (red X)

---

### 💰 Financials (Route: `/cost-metrics`)

**Existing Feature:** Cost metrics dashboard

**Check This:**

- Budget status with progress bar
- AI cache performance (hit rate, savings)
- Model router efficiency
- Interventions triggered
- Total optimization impact

---

### 📝 Content (Route: `/content`)

**Status:** Placeholder (Phase 2)

---

### 📈 Analytics (Route: `/analytics`)

**Status:** Placeholder (Phase 2)

---

### ⚙️ Settings (Route: `/settings`)

**Status:** Basic settings page (Phase 2 enhancement planned)

---

## 🧪 Testing Checklist

### Basic Functionality

- [ ] All 4 services start without errors
- [ ] Dashboard loads at http://localhost:3001
- [ ] Sidebar navigation works (click each menu item)
- [ ] All pages load without console errors
- [ ] Refresh button updates data

### Dashboard Tests

- [ ] Service health cards show correct status
- [ ] Model configuration displays your setup
- [ ] Metrics show reasonable numbers
- [ ] Quick action buttons are clickable
- [ ] Auto-refresh updates timestamp

### Task Management Tests

- [ ] Create new task works
- [ ] Task appears in table
- [ ] Edit task dialog opens
- [ ] Delete task prompts confirmation
- [ ] Bulk select works (checkboxes)
- [ ] Bulk actions bar appears
- [ ] Filters change table content
- [ ] Tabs switch views (Active/Completed/Failed)

### Model Management Tests

- [ ] Provider cards display
- [ ] Toggle switches work
- [ ] Test model dialog opens
- [ ] Test runs and shows response
- [ ] Usage statistics display
- [ ] Metrics show request counts

### Error Handling

- [ ] Stop AI Co-Founder backend
- [ ] Dashboard shows "Unreachable" status
- [ ] Error messages are clear
- [ ] Restart backend → status returns to "Healthy"

---

## 🎯 Key Metrics to Monitor

### Service Health

- **Response Time:** Should be < 200ms for healthy services
- **Status:** All should show "Healthy" (green)
- **Unavailable:** If any service is down, red "Unreachable" appears

### Model Usage (24h)

- **API Calls:** Number of requests sent to models
- **Total Cost:** $0.00 if using Ollama, $X.XX if using OpenAI/Anthropic
- **Cache Hit Rate:** Higher is better (saves money and time)

### System Alerts

- **Info (Blue):** All systems operational
- **Warning (Orange):** Budget at 75%+ or service degraded
- **Error (Red):** Budget at 90%+ or service unreachable

---

## 🐛 Troubleshooting

### "Cannot connect to backend"

**Fix:** Ensure AI Co-Founder is running on port 8000

```powershell
cd src/cofounder_agent
python start_server.py
```

### "Service Unreachable" on Dashboard

**Fix:** Check that service is actually running

- Strapi: http://localhost:1337/\_health
- AI Co-Founder: http://localhost:8000/metrics/health
- Public Site: http://localhost:3000

### "Model test failed"

**Fix:**

- For Ollama: Run `ollama list` to verify models installed
- For OpenAI/Anthropic: Check API keys in environment variables

### Tasks not showing

**Fix:** Backend may be in development mode (returns mock data)

- This is normal for local testing
- Real tasks require Firestore connection

### Compilation errors

**Fix:** Clear cache and reinstall

```powershell
cd web/oversight-hub
rm -rf node_modules
npm install
npm start
```

---

## 📊 What Success Looks Like

After following this guide, you should see:

✅ **Dashboard:** All services healthy, models configured, metrics displaying  
✅ **Tasks:** Can create, edit, filter, and bulk manage tasks  
✅ **Models:** Can test connectivity, see usage stats, toggle providers  
✅ **Navigation:** Smooth transitions between all pages  
✅ **Performance:** Pages load in < 2 seconds  
✅ **Auto-refresh:** Data updates automatically every 10-30 seconds

---

## 🎉 Next Steps

1. ✅ Verify all features work
2. ✅ Familiarize yourself with the new UI
3. ✅ Create some test tasks
4. ✅ Test model connectivity
5. ✅ Monitor system health
6. 📋 Review Phase 2 priorities (Content, Financial Controls, Settings)
7. 🚀 Provide feedback for improvements

---

## 📞 Support

If you encounter issues:

1. **Check Logs:** Look in terminal windows for errors
2. **Browser Console:** Press F12, check Console tab
3. **Network Tab:** Press F12, check Network tab for failed requests
4. **Documentation:** Read `docs/OVERSIGHT_HUB_ENHANCEMENTS.md`
5. **Code Review:** See `docs/CODE_REVIEW_SUMMARY_OCT_15.md`

---

**Happy Monitoring! 🎊**

_The Oversight Hub is now your command center for all GLAD Labs AI operations._
