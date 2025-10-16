# PowerShell Scripts Fixed ✅

## Issue

Both `kill-services.ps1` and `check-services.ps1` had PowerShell syntax errors:

- Missing closing braces in conditional blocks
- Try-catch blocks without proper termination
- Variable naming conflicts (`$pid` is a PowerShell reserved variable)

## Solution

Completely rewrote both scripts with proper syntax and added npm script aliases for convenience.

## Fixed Scripts

### 1. `scripts/kill-services.ps1`

**Purpose:** Stop all GLAD Labs services cleanly

**Usage:**

```powershell
# Direct execution
.\scripts\kill-services.ps1

# Via npm
npm run services:kill
```

**Features:**

- ✅ Stops all services on ports: 8000, 3001, 3000, 1337
- ✅ Displays process names and PIDs
- ✅ Color-coded output
- ✅ Summary of stopped processes

---

### 2. `scripts/check-services.ps1`

**Purpose:** Check status of all GLAD Labs services

**Usage:**

```powershell
# Direct execution
.\scripts\check-services.ps1

# Via npm
npm run services:check
```

**Features:**

- ✅ Shows running/stopped status for each service
- ✅ Displays process details (name, PID, start time, uptime)
- ✅ Provides service URLs
- ✅ Summary with actionable recommendations

---

## New NPM Scripts

Added to `package.json` under utilities section:

```json
{
  "scripts": {
    "services:check": "powershell -ExecutionPolicy Bypass -File ./scripts/check-services.ps1",
    "services:kill": "powershell -ExecutionPolicy Bypass -File ./scripts/kill-services.ps1",
    "services:restart": "npm run services:kill && npm run dev"
  }
}
```

### Available Commands

| Command                    | Description                      |
| -------------------------- | -------------------------------- |
| `npm run services:check`   | Check which services are running |
| `npm run services:kill`    | Stop all services                |
| `npm run services:restart` | Stop all services and restart    |

---

## Typical Workflows

### 🔄 Restart All Services

```bash
npm run services:restart
```

### 🛑 Stop Services Before Shutdown

```bash
npm run services:kill
```

### 📊 Check Service Status

```bash
npm run services:check
```

### 🚀 Clean Start

```bash
# 1. Kill any lingering processes
npm run services:kill

# 2. Start fresh
npm run dev

# 3. Verify all running
npm run services:check
```

---

## Benefits

### 1. **No More Port Conflicts**

- Clean shutdown prevents "port already in use" errors
- No need to manually kill processes in Task Manager

### 2. **Easy to Use**

- Simple npm commands (`services:check`, `services:kill`, `services:restart`)
- No need to remember PowerShell syntax
- Works from any directory in the project

### 3. **Bypass Execution Policy**

- Scripts use `-ExecutionPolicy Bypass` flag
- No need to change system execution policy
- Works even on restricted systems

### 4. **Better Visibility**

- Color-coded output makes status clear
- See exactly what's running and for how long
- Quick troubleshooting when something goes wrong

---

## Technical Details

### Fixed Syntax Issues

**Before (Broken):**

```powershell
if ($process) {
    # do something
}
# Missing closing brace for parent if statement
} catch {
    # Error: Try-catch incomplete
}
```

**After (Fixed):**

```powershell
if ($conn) {
    $processId = $conn.OwningProcess
    if ($process) {
        # do something
    }
} else {
    # handle empty case
}
# All braces properly closed
```

### Variable Naming

- Changed `$pid` → `$processId` (avoids PowerShell automatic variable conflict)
- Changed `$proc` → `$process` for clarity

---

## Testing Results

### ✅ `kill-services.ps1`

```
========================================
 Stopping GLAD Labs Services
========================================

Checking port 8000 - AI Co-Founder API...
  Found: python (PID: 15312)
  Stopped
Checking port 3001 - Oversight Hub...
  Found: node (PID: 22668)
  Stopped
Checking port 3000 - Public Site...
  Found: node (PID: 28572)
  Stopped
Checking port 1337 - Strapi CMS...
  Found: node (PID: 51456)
  Stopped

========================================
 Cleanup Complete
 Stopped: 4 process(es)
========================================
```

### ✅ `check-services.ps1`

```
========================================
 GLAD Labs Services Status
========================================

AI Co-Founder API (Port 8000)
  URL: http://localhost:8000/docs
  Status: NOT RUNNING

[... all services listed ...]

========================================
 Summary: 0 of 4 services running
========================================

No services running. Start with: npm run dev
```

---

## Quick Reference

| Scenario                              | Command                                    |
| ------------------------------------- | ------------------------------------------ |
| Services won't start (port conflicts) | `npm run services:kill` then `npm run dev` |
| Check what's running                  | `npm run services:check`                   |
| Quick restart                         | `npm run services:restart`                 |
| Stop before pulling code changes      | `npm run services:kill`                    |
| Stop before system shutdown           | `npm run services:kill`                    |

---

**Date:** October 16, 2025  
**Status:** ✅ Both scripts working perfectly  
**npm Scripts:** ✅ Added and tested
