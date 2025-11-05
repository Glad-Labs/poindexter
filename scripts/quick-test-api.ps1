#!/usr/bin/env pwsh
# Glad Labs Quick API Test Script
# Run with: .\scripts\quick-test-api.ps1

$baseUrl = "http://localhost:8000"

Write-Host "`n🧪 Testing Glad Labs Co-founder Agent API...`n" -ForegroundColor Cyan

# Test 1: Health Check
Write-Host "1️⃣  Health Check..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "$baseUrl/" -Method Get
    Write-Host "   ✅ Server is running!" -ForegroundColor Green
    Write-Host "   Response: $($health | ConvertTo-Json -Compress)" -ForegroundColor Gray
} catch {
    Write-Host "   ❌ Failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Test 2: Send Status Command
Write-Host "2️⃣  Sending status command..." -ForegroundColor Yellow
try {
    $body = @{
        command = "status"
        parameters = @{}
    } | ConvertTo-Json
    
    $response = Invoke-RestMethod -Uri "$baseUrl/command" `
        -Method Post `
        -Headers @{"Content-Type"="application/json"} `
        -Body $body
    
    Write-Host "   ✅ Command sent successfully!" -ForegroundColor Green
    Write-Host "   Response:" -ForegroundColor Gray
    Write-Host "   $($response | ConvertTo-Json -Depth 5)" -ForegroundColor Gray
} catch {
    Write-Host "   ❌ Failed: $_" -ForegroundColor Red
}

Write-Host ""

# Test 3: Get Agents
Write-Host "3️⃣  Getting agent list..." -ForegroundColor Yellow
try {
    $agents = Invoke-RestMethod -Uri "$baseUrl/agents" -Method Get
    Write-Host "   ✅ Retrieved agents!" -ForegroundColor Green
    Write-Host "   Response:" -ForegroundColor Gray
    Write-Host "   $($agents | ConvertTo-Json -Depth 5)" -ForegroundColor Gray
} catch {
    Write-Host "   ❌ Failed: $_" -ForegroundColor Red
}

Write-Host ""

# Test 4: Get Performance Metrics
Write-Host "4️⃣  Getting performance metrics..." -ForegroundColor Yellow
try {
    $perf = Invoke-RestMethod -Uri "$baseUrl/performance" -Method Get
    Write-Host "   ✅ Retrieved performance data!" -ForegroundColor Green
    Write-Host "   Response:" -ForegroundColor Gray
    Write-Host "   $($perf | ConvertTo-Json -Depth 5)" -ForegroundColor Gray
} catch {
    Write-Host "   ❌ Failed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "✨ All tests complete!`n" -ForegroundColor Cyan
Write-Host "📖 Visit http://localhost:8000/docs for interactive API documentation`n" -ForegroundColor White

