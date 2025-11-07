#!/usr/bin/env powershell
# Quick restart script for testing the unified table fix

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  GLAD LABS - Task Management Unified Table Fix        ║" -ForegroundColor Cyan
Write-Host "║  Restart Backend & Test New Changes                   ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check if backend is already running
Write-Host "1️⃣ Checking for existing backend processes..." -ForegroundColor Yellow
$existingProcess = Get-Process | Where-Object { $_.ProcessName -eq "python" -and $_.CommandLine -like "*main.py*" } -ErrorAction SilentlyContinue

if ($existingProcess) {
    Write-Host "⚠️ Found existing Python process" -ForegroundColor Yellow
    Write-Host "   Please manually kill it or let VS Code restart it" -ForegroundColor Gray
    Write-Host ""
    Write-Host "   Or run this to kill it:" -ForegroundColor Yellow
    Write-Host "   Stop-Process -Name python -Force" -ForegroundColor DarkGray
    Write-Host ""
}

# Step 2: Check Ollama
Write-Host "2️⃣ Checking Ollama service..." -ForegroundColor Yellow
try {
    $ollamaCheck = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -ErrorAction Stop -TimeoutSec 3
    $models = $ollamaCheck.Content | ConvertFrom-Json
    Write-Host "✅ Ollama is running" -ForegroundColor Green
    if ($models.models.Count -gt 0) {
        Write-Host "   Models available: $($models.models.Count)" -ForegroundColor Green
        $models.models | ForEach-Object { Write-Host "     - $($_.name)" -ForegroundColor Gray }
    } else {
        Write-Host "   ⚠️ No models installed. Run: ollama pull mistral" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "⚠️ Ollama is NOT running" -ForegroundColor Yellow
    Write-Host "   Start it with: ollama serve" -ForegroundColor Gray
}

Write-Host ""
Write-Host "3️⃣ Backend startup instructions:" -ForegroundColor Yellow
Write-Host ""
Write-Host "   Open a NEW terminal and run:" -ForegroundColor Cyan
Write-Host ""
Write-Host "   cd c:\Users\mattm\glad-labs-website\src\cofounder_agent" -ForegroundColor DarkCyan
Write-Host "   python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "   Wait for: 'Application startup complete'" -ForegroundColor Gray
Write-Host ""

Write-Host "4️⃣ Then test in browser:" -ForegroundColor Yellow
Write-Host "   URL: http://localhost:3001/task-management" -ForegroundColor Cyan
Write-Host ""
Write-Host "5️⃣ What you should see:" -ForegroundColor Yellow
Write-Host "   ✅ ONE unified table (not 3 cards)" -ForegroundColor Green
Write-Host "   ✅ Summary stats at top" -ForegroundColor Green
Write-Host "   ✅ Refresh button and auto-refresh message" -ForegroundColor Green
Write-Host "   ✅ All tasks in one place" -ForegroundColor Green
Write-Host ""

Write-Host "6️⃣ To create a test task:" -ForegroundColor Yellow
Write-Host "   1. Go to http://localhost:3001 (Dashboard)" -ForegroundColor Gray
Write-Host "   2. Find 'Content Generator' or similar" -ForegroundColor Gray
Write-Host "   3. Create a task and watch it appear in the table" -ForegroundColor Gray
Write-Host ""

Write-Host "📋 Documentation:" -ForegroundColor Cyan
Write-Host "   Full details: docs/TASK_MANAGEMENT_UNIFIED_TABLE_FIX.md" -ForegroundColor Gray
Write-Host "   Quick summary: docs/QUICK_FIX_SUMMARY.md" -ForegroundColor Gray
Write-Host ""

Write-Host "🧪 Troubleshooting:" -ForegroundColor Cyan
Write-Host "   Check Ollama: .\scripts\fix-ollama-warmup.ps1" -ForegroundColor Gray
Write-Host ""

Write-Host "╔════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║  Ready to test! Open browser and visit URLs above ✨  ║" -ForegroundColor Green
Write-Host "╚════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
