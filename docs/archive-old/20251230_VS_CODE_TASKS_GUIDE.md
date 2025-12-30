# VS Code Tasks Guide - Sequential Service Startup

## 📋 Quick Start

### How to Run All Services

#### Method 1: Keyboard Shortcut

`Ctrl+Shift+B`

This runs the default build task: **"🚀 Start All Services (Sequential)"**

#### Method 2: Command Palette

`Ctrl+Shift+P` → Tasks: Run Task → Select **"🚀 Start All Services (Sequential)"**

#### Method 3: Terminal Menu

Terminal → Run Task... → Select **"🚀 Start All Services (Sequential)"**

---

## 🚀 Available Tasks

### Primary Task (Runs All Services)

**🚀 Start All Services (Sequential)**

- Starts all 4 services in order
- Each service waits for the previous one to start
- All output goes to shared panel
- Press `Ctrl+Shift+B` to run

### Individual Service Tasks

If you want to start services manually (not recommended):

1. **1️⃣ Start Strapi CMS (Port 1337)** - Backend content management system - Start this first
2. **2️⃣ Start Public Site (Port 3000)** - Marketing/public website - Depends on Strapi
3. **3️⃣ Start Oversight Hub (Port 3001)** - Admin dashboard - Depends on Public Site
4. **4️⃣ Start Co-founder Agent (Port 8000)** - AI backend service - Depends on Oversight Hub

### Helper Tasks

**🛑 Kill All Services**

- Stops all running Node.js and Python processes
- Use this when services hang or won't stop gracefully

**✅ Check All Services Status**

- Displays which services are running (✅) or stopped (❌)
- Shows ports: 1337, 3000, 3001, 8000
- Helpful for debugging

**🔄 Restart All Services**

- Kills all services and prepares for fresh start
- Then run "Start All Services (Sequential)" again

---

## 📊 Service Startup Order

```
Step 1: Start Strapi CMS (Port 1337)
        ↓ [waits ~5-10 seconds]
Step 2: Start Public Site (Port 3000)
        ↓ [waits ~5-10 seconds]
Step 3: Start Oversight Hub (Port 3001)
        ↓ [waits ~3-5 seconds]
Step 4: Start Co-founder Agent (Port 8000)
        ↓
✅ All services running!
```

**Total startup time:** ~30-45 seconds (depending on your machine)

---

## 🔍 Checking Services

### Via VS Code Tasks

Run: **✅ Check All Services Status**

Output looks like:

```
=== SERVICE STATUS ===
Strapi CMS (1337): ✅ Running
Public Site (3000): ✅ Running
Oversight Hub (3001): ✅ Running
Co-founder Agent (8000): ✅ Running
```

### Via Browser

| Service              | URL                         | Purpose                  |
| -------------------- | --------------------------- | ------------------------ |
| Strapi CMS           | http://localhost:1337/admin | Content management       |
| Public Site          | http://localhost:3000       | Marketing website        |
| Oversight Hub        | http://localhost:3001       | Admin dashboard          |
| Co-founder Agent API | http://localhost:8000/docs  | AI backend documentation |

### Via Terminal

```powershell
netstat -ano | findstr :1337
netstat -ano | findstr :3000
netstat -ano | findstr :3001
netstat -ano | findstr :8000
```

---

## ⛔ Stopping Services

### Clean Stop (Recommended)

In VS Code Terminal panel: `Ctrl+C`

Each service will gracefully shut down.

### Force Stop (If Stuck)

Run task: **🛑 Kill All Services**

Or manually in PowerShell:

```powershell
Get-Process node | Stop-Process -Force
Get-Process python | Stop-Process -Force
```

---

## 🔧 Troubleshooting

### Services Not Starting

1. **Check ports aren't in use:**
   - `netstat -ano | findstr :1337`
   - `netstat -ano | findstr :3000`
   - `netstat -ano | findstr :3001`
   - `netstat -ano | findstr :8000`

2. **Kill any existing processes:**
   - Run task: **🛑 Kill All Services**
   - Wait 2 seconds
   - Try again

3. **Check dependencies:**
   - Is Node.js 18+ installed? `node --version`
   - Is Python 3.12+ installed? `python --version`
   - Are npm packages installed? `npm install --workspaces`

### Service Starts But Gets Stuck

- **Strapi CMS:** May take 10-15 seconds first time
- **Public Site:** May rebuild on first run (30 seconds)
- **Oversight Hub:** Should start quickly (5-10 seconds)
- **Co-founder Agent:** Depends on Python (5-10 seconds)

Wait longer or check for build errors in output panel.

### Can't Find Task in Command Palette

1. Reload VS Code: `Ctrl+Shift+P` → Developer: Reload Window
2. Make sure `.vscode/tasks.json` is in project root
3. Try again: `Ctrl+Shift+P` → Tasks: Run Task

---

## 📌 Key Shortcuts

| Shortcut       | Action                                  |
| -------------- | --------------------------------------- |
| `Ctrl+Shift+B` | Start All Services (default build task) |
| `Ctrl+Shift+P` | Open Command Palette (search tasks)     |
| `Ctrl+J`       | Toggle Terminal panel                   |
| `Ctrl+C`       | Stop current service (in terminal)      |

---

## 🎯 Common Workflows

### 🚀 Start Development Session

```
1. Press Ctrl+Shift+B
2. Wait 45 seconds for all services to start
3. Open browser: http://localhost:3000
4. Start coding!
```

### 🔄 Restart Services

```
1. Run task: "🔄 Restart All Services"
2. Wait 2 seconds
3. Press Ctrl+Shift+B to start again
```

### 🔍 Check What's Running

```
1. Run task: "✅ Check All Services Status"
2. See which services are running/stopped
3. Decide if you need to restart
```

### 🛑 Stop Before Sleep

```
1. Run task: "🛑 Kill All Services"
2. Close VS Code
3. Done! All ports cleaned up
```

---

## 📝 Configuration Details

**File Location:** `.vscode/tasks.json` (177 lines)

**Task Configuration:**

- All service tasks run in background
- Shared output panel for centralized logging
- Sequential execution via `dependsOn` chains
- Automatic startup detection via problem matchers
- Default task set to "🚀 Start All Services (Sequential)"

**Problem Matchers:**
Each service has pattern matching to detect when startup is complete:

- Strapi: Watches for "Server is running" or "listening on"
- Public Site: Watches for "ready" or "compiled"
- Oversight Hub: Watches for "Compiled" or "started"
- Co-founder Agent: Watches for "Application startup complete" or "Uvicorn running on"

---

## 🆘 Need Help?

1. **Check terminal output:** Most errors appear in the VS Code terminal
2. **View full logs:** Click on service name in output panel
3. **Restart everything:** Run task "🔄 Restart All Services"
4. **Check services individually:** Run task "✅ Check All Services Status"

---

**Last Updated:** 2025-11-05  
**Status:** ✅ Ready to Use
