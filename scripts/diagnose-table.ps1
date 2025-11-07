# Diagnostic Script for Task Table Issues
# This helps identify why the table isn't showing

Write-Host "===== TASK TABLE DIAGNOSTICS =====" -ForegroundColor Green
Write-Host ""

# Step 1: Check if backend is running
Write-Host "1. Checking Backend API..." -ForegroundColor Yellow
$backendCheck = $null
try {
    $backendCheck = curl -s http://localhost:8000/api/health -TimeoutSec 5
    if ($backendCheck) {
        Write-Host "   ✅ Backend is running on http://localhost:8000" -ForegroundColor Green
        $backendData = $backendCheck | ConvertFrom-Json
        Write-Host "   Status: $($backendData.status)" -ForegroundColor Green
    }
} catch {
    Write-Host "   ❌ Backend is NOT running" -ForegroundColor Red
    Write-Host "   Start it with: cd c:\Users\mattm\glad-labs-website\src\cofounder_agent" -ForegroundColor Cyan
    Write-Host "                 python -m uvicorn main:app --reload" -ForegroundColor Cyan
}

Write-Host ""

# Step 2: Check if frontend is running
Write-Host "2. Checking Frontend..." -ForegroundColor Yellow
$frontendCheck = $null
try {
    $frontendCheck = curl -s http://localhost:3001 -TimeoutSec 5
    if ($frontendCheck -match "html") {
        Write-Host "   ✅ Frontend is running on http://localhost:3001" -ForegroundColor Green
    }
} catch {
    Write-Host "   ❌ Frontend is NOT running" -ForegroundColor Red
    Write-Host "   Restart it with npm start in web/oversight-hub/" -ForegroundColor Cyan
}

Write-Host ""

# Step 3: Check if there are tasks in the database
Write-Host "3. Checking for Tasks in Database..." -ForegroundColor Yellow
try {
    $tasksApi = curl -s http://localhost:8000/api/tasks -TimeoutSec 5
    if ($tasksApi) {
        $tasksData = $tasksApi | ConvertFrom-Json
        $taskCount = if ($tasksData.tasks) { $tasksData.tasks.Count } else { 0 }
        
        if ($taskCount -gt 0) {
            Write-Host "   ✅ Found $taskCount tasks in database" -ForegroundColor Green
            Write-Host "   First task: $($tasksData.tasks[0].task_name)" -ForegroundColor Cyan
        } else {
            Write-Host "   ⚠️  No tasks in database (this is normal if you haven't created any)" -ForegroundColor Yellow
            Write-Host "   Create a task to test: Go to Dashboard → Content Generator" -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "   ❌ Error fetching tasks from API" -ForegroundColor Red
    Write-Host "   Error: $_" -ForegroundColor Red
}

Write-Host ""

# Step 4: Browser console check instructions
Write-Host "4. Checking Browser Console (Manual Steps)..." -ForegroundColor Yellow
Write-Host "   1. Go to http://localhost:3001/task-management" -ForegroundColor Cyan
Write-Host "   2. Press F12 to open Developer Tools" -ForegroundColor Cyan
Write-Host "   3. Go to Console tab" -ForegroundColor Cyan
Write-Host "   4. Look for any RED errors" -ForegroundColor Cyan
Write-Host "   5. Run this command in console:" -ForegroundColor Cyan
Write-Host "      console.log(document.querySelector('.tasks-table'))" -ForegroundColor Magenta
Write-Host "   6. If it returns null, the table element is not found" -ForegroundColor Cyan
Write-Host "   7. If it returns an element, the table exists but may be hidden by CSS" -ForegroundColor Cyan

Write-Host ""

# Step 5: CSS Check
Write-Host "5. Checking Component Files..." -ForegroundColor Yellow
$taskMgmtExists = Test-Path "c:\Users\mattm\glad-labs-website\web\oversight-hub\src\routes\TaskManagement.jsx"
$cssExists = Test-Path "c:\Users\mattm\glad-labs-website\web\oversight-hub\src\routes\TaskManagement.css"

if ($taskMgmtExists) {
    Write-Host "   ✅ TaskManagement.jsx exists" -ForegroundColor Green
} else {
    Write-Host "   ❌ TaskManagement.jsx NOT found" -ForegroundColor Red
}

if ($cssExists) {
    Write-Host "   ✅ TaskManagement.css exists" -ForegroundColor Green
} else {
    Write-Host "   ❌ TaskManagement.css NOT found" -ForegroundColor Red
}

# Check for '.tasks-table' in CSS
$cssContent = Get-Content "c:\Users\mattm\glad-labs-website\web\oversight-hub\src\routes\TaskManagement.css" -Raw
if ($cssContent -match '\.tasks-table\s*\{') {
    Write-Host "   ✅ .tasks-table CSS class defined" -ForegroundColor Green
} else {
    Write-Host "   ⚠️  .tasks-table CSS class NOT found in CSS file" -ForegroundColor Yellow
}

# Check for '.summary-stats' in CSS
if ($cssContent -match '\.summary-stats\s*\{') {
    Write-Host "   ✅ .summary-stats CSS class defined" -ForegroundColor Green
} else {
    Write-Host "   ⚠️  .summary-stats CSS class NOT found in CSS file" -ForegroundColor Yellow
}

Write-Host ""

# Step 6: Browser refresh instructions
Write-Host "6. If You Still Don't See the Table..." -ForegroundColor Yellow
Write-Host "   Try these fixes in order:" -ForegroundColor Cyan
Write-Host "   1. Hard refresh: Ctrl+Shift+Delete → Clear browser cache" -ForegroundColor Cyan
Write-Host "   2. Then reload: F5 or Ctrl+R" -ForegroundColor Cyan
Write-Host "   3. If still not working, restart frontend:" -ForegroundColor Cyan
Write-Host "      - Kill Oversight Hub (Ctrl+C in terminal)" -ForegroundColor Cyan
Write-Host "      - Run: cd web/oversight-hub && npm start" -ForegroundColor Cyan
Write-Host "   4. If STILL not working, restart backend too:" -ForegroundColor Cyan
Write-Host "      - Kill backend (Ctrl+C)" -ForegroundColor Cyan
Write-Host "      - Run: cd src/cofounder_agent && python -m uvicorn main:app --reload" -ForegroundColor Cyan

Write-Host ""
Write-Host "===== END DIAGNOSTICS =====" -ForegroundColor Green
