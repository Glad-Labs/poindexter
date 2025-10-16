# NPM Run Dev Troubleshooting Guide

> **Issue:** Strange terminal behavior when running `npm run dev`  
> **Date:** October 16, 2025  
> **Status:** ✅ RESOLVED

---

## 🐛 The Problem

When running `npm run dev`, you encounter:

1. **Port Binding Errors**

   ```
   ERROR: [Errno 10048] error while attempting to bind on address ('0.0.0.0', 8000):
   only one usage of each socket address is normally permitted
   ```

2. **Process Exit Errors**

   ```
   ERROR: "dev:cofounder" exited with 1.
   ```

3. **Services Already Running**
   - Port 8000 (AI Co-Founder API) - Already in use
   - Port 3000 (Public Site) - Already in use
   - Port 3001 (Oversight Hub) - Already in use
   - Port 1337 (Strapi CMS) - Already in use

---

## 🔍 Root Cause

The issue occurs because **services are already running** from a previous session. When you run `npm run dev`, it attempts to start all services in parallel using `npm-run-all --parallel dev:*`, but the ports are already occupied.

**Why this happens:**

1. You previously started services individually or via VS Code tasks
2. Services didn't shut down properly when closing terminals
3. Background processes continued running
4. New `npm run dev` command tries to bind to occupied ports

---

## ✅ Solution: Kill Existing Services First

### Quick Fix (Recommended)

Use the new kill script to stop all services:

```powershell
# From project root
.\scripts\kill-services.ps1
```

This will:

- ✅ Stop all processes on ports 8000, 3001, 3000, 1337
- ✅ Kill lingering Python/Node processes
- ✅ Free up all ports
- ✅ Display status of each service stopped

### Then Start Fresh

```powershell
npm run dev
```

---

## 🔧 Manual Solutions

### Option 1: Kill by Port (Specific Service)

Kill a specific service by port:

```powershell
# Find process using port 8000
$pid = (Get-NetTCPConnection -LocalPort 8000).OwningProcess
Stop-Process -Id $pid -Force

# Or all at once
@(8000, 3001, 3000, 1337) | ForEach-Object {
    $pid = (Get-NetTCPConnection -LocalPort $_ -ErrorAction SilentlyContinue).OwningProcess
    if ($pid) { Stop-Process -Id $pid -Force }
}
```

### Option 2: Kill All Python/Node Processes

**⚠️ WARNING: This kills ALL Python and Node processes**

```powershell
# Kill all Python processes
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

# Kill all Node processes
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force
```

### Option 3: Restart Your Computer

If processes are stubborn, a system restart will clear everything.

---

## 🔍 Diagnostic Commands

### Check Service Status

Use the status check script:

```powershell
.\scripts\check-services.ps1
```

Output shows:

- ✅ Which services are running
- 📍 Port numbers and URLs
- 🕐 How long each service has been running
- 🔍 Other processes on development ports

### Manual Checks

```powershell
# Check all development ports
Get-NetTCPConnection -State Listen | Where-Object {
    $_.LocalPort -in @(3000, 3001, 1337, 8000)
} | Select-Object LocalPort, OwningProcess, State

# Get process details
Get-Process -Id <PID> | Select-Object Id, ProcessName, Path, StartTime

# Check what's using a specific port
Get-NetTCPConnection -LocalPort 8000 | Select-Object LocalPort, OwningProcess
```

---

## 📝 Best Practices to Avoid This Issue

### 1. Always Stop Services Cleanly

**When done developing:**

```powershell
# Stop all services
.\scripts\kill-services.ps1

# Or use Ctrl+C in the terminal running npm run dev
```

### 2. Check Status Before Starting

```powershell
# Check what's running
.\scripts\check-services.ps1

# If anything is running, kill it first
.\scripts\kill-services.ps1

# Then start fresh
npm run dev
```

### 3. Use Individual Service Scripts

If you don't need all services:

```powershell
# Start only what you need
npm run dev:strapi      # CMS only
npm run dev:oversight   # Admin dashboard only
npm run dev:public      # Public website only
npm run dev:cofounder   # AI API only
```

### 4. Use VS Code Tasks Instead

VS Code tasks are better managed:

1. Open Command Palette (`Ctrl+Shift+P`)
2. Type "Tasks: Run Task"
3. Select individual tasks like "Start Strapi CMS"

**Benefits:**

- ✅ Better process management
- ✅ Easier to stop (click X in terminal)
- ✅ Status visible in VS Code
- ✅ Can run multiple tasks separately

---

## 🚀 Improved Workflow

### Recommended Daily Workflow

1. **Morning Startup:**

   ```powershell
   # Check status
   .\scripts\check-services.ps1

   # Kill any lingering processes
   .\scripts\kill-services.ps1

   # Start fresh
   npm run dev
   ```

2. **During Development:**
   - Keep `npm run dev` running in one terminal
   - Don't start duplicate services
   - Use Ctrl+C to stop when needed

3. **End of Day:**

   ```powershell
   # Stop all services
   Ctrl+C  # in the npm run dev terminal

   # Or use the kill script
   .\scripts\kill-services.ps1
   ```

---

## 🛠️ New Helper Scripts

Two new scripts have been added to help manage services:

### 1. `scripts/kill-services.ps1`

Stops all GLAD Labs services cleanly.

**Usage:**

```powershell
.\scripts\kill-services.ps1
```

**Features:**

- ✅ Stops services on ports 8000, 3001, 3000, 1337
- ✅ Kills lingering Python/Node processes
- ✅ Shows what was stopped
- ✅ Verifies ports are freed

### 2. `scripts/check-services.ps1`

Checks status of all services.

**Usage:**

```powershell
.\scripts\check-services.ps1
```

**Features:**

- ✅ Shows which services are running
- ✅ Displays process details (PID, name, uptime)
- ✅ Lists URLs for each service
- ✅ Warns about other processes on dev ports

---

## ⚠️ Common Errors & Fixes

### Error: Port 8000 Already in Use

**Symptoms:**

```
ERROR: [Errno 10048] error while attempting to bind on address ('0.0.0.0', 8000)
```

**Fix:**

```powershell
.\scripts\kill-services.ps1
npm run dev
```

---

### Error: Cannot Find Module

**Symptoms:**

```
Error: Cannot find module '@strapi/strapi'
```

**Fix:**

```powershell
npm run install:all
npm run dev
```

---

### Error: Python Module Not Found

**Symptoms:**

```
ModuleNotFoundError: No module named 'fastapi'
```

**Fix:**

```powershell
pip install -r requirements.txt
npm run dev
```

---

### Error: EADDRINUSE on Port 3000/3001/1337

**Symptoms:**

```
Error: listen EADDRINUSE: address already in use :::3000
```

**Fix:**

```powershell
.\scripts\kill-services.ps1
npm run dev
```

---

## 🔐 Firestore/Pub/Sub Warnings (Normal)

You may see these warnings - **they are normal in development**:

```
Failed to initialize Firestore client
Continuing in dev mode without Firestore functionality
```

**Why:** These services require Google Cloud credentials which aren't needed for local development.

**To fix (optional):**

1. Set up Google Cloud project
2. Download service account key
3. Set environment variable:
   ```powershell
   $env:GOOGLE_APPLICATION_CREDENTIALS = "path/to/service-account-key.json"
   ```

**For now:** You can safely ignore these warnings in development.

---

## 📊 Service Port Reference

| Service               | Port | URL                         | Command                 |
| --------------------- | ---- | --------------------------- | ----------------------- |
| **AI Co-Founder API** | 8000 | http://localhost:8000       | `npm run dev:cofounder` |
| **Oversight Hub**     | 3001 | http://localhost:3001       | `npm run dev:oversight` |
| **Public Site**       | 3000 | http://localhost:3000       | `npm run dev:public`    |
| **Strapi CMS**        | 1337 | http://localhost:1337/admin | `npm run dev:strapi`    |

---

## ✅ Verification Steps

After running `npm run dev`, verify all services are working:

1. **Check Terminal Output:**
   - ✅ No ERROR messages
   - ✅ All services say "ready" or "compiled"
   - ✅ No port binding errors

2. **Test Each Service:**

   ```powershell
   # Test API
   curl http://localhost:8000/health

   # Test Public Site (open browser)
   start http://localhost:3000

   # Test Oversight Hub (open browser)
   start http://localhost:3001

   # Test Strapi (open browser)
   start http://localhost:1337/admin
   ```

3. **Check Status:**
   ```powershell
   .\scripts\check-services.ps1
   ```
   Should show all 4 services running.

---

## 📚 Related Documentation

- **[Setup Guide](./01-SETUP_GUIDE.md)** - Initial installation
- **[NPM Scripts Health Check](./NPM_SCRIPTS_HEALTH_CHECK.md)** - All available scripts
- **[Technical Design](./03-TECHNICAL_DESIGN.md)** - Architecture overview

---

## 🆘 Still Having Issues?

If you're still experiencing problems:

1. **Full Reset:**

   ```powershell
   # Kill all services
   .\scripts\kill-services.ps1

   # Clean install
   npm run clean:install

   # Reinstall Python deps
   pip install -r requirements.txt

   # Start fresh
   npm run dev
   ```

2. **Check Logs:**
   - Look in `logs/` directory for error details
   - Check browser console (F12) for frontend errors
   - Review terminal output carefully

3. **Restart Computer:**
   - Sometimes processes get stuck
   - A fresh restart clears everything
   - Then run: `npm run dev`

---

**Last Updated:** October 16, 2025  
**Status:** ✅ Issue Resolved - Helper scripts added
