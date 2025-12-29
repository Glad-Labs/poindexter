# Ollama Warmup Troubleshooting Script
# This script helps diagnose and fix Ollama warmup issues

Write-Host "🔍 Ollama Warmup Diagnostics" -ForegroundColor Cyan
Write-Host "=============================" -ForegroundColor Cyan
Write-Host ""

# Check if Ollama is running
Write-Host "1️⃣ Checking if Ollama is running..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -ErrorAction Stop
    Write-Host "✅ Ollama is running" -ForegroundColor Green
    
    # Parse and display available models
    $models = $response.Content | ConvertFrom-Json
    Write-Host ""
    Write-Host "Available models:" -ForegroundColor Cyan
    if ($models.models) {
        $models.models | ForEach-Object {
            Write-Host "  - $($_.name)" -ForegroundColor Green
        }
    } else {
        Write-Host "  ⚠️ No models found. Please pull a model:" -ForegroundColor Yellow
        Write-Host "     ollama pull mistral" -ForegroundColor Gray
        Write-Host "     ollama pull llama2" -ForegroundColor Gray
    }
}
catch {
    Write-Host "❌ Ollama is NOT running" -ForegroundColor Red
    Write-Host ""
    Write-Host "To fix:" -ForegroundColor Yellow
    Write-Host "  1. Start Ollama service"
    Write-Host "  2. Run: ollama serve" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "2️⃣ Testing warmup for available models..." -ForegroundColor Yellow
Write-Host ""

# Try to warm up each model
if ($models.models) {
    $models.models | ForEach-Object {
        $modelName = $_.name
        Write-Host "Testing warmup for model: $modelName" -ForegroundColor Cyan
        
        try {
            $warmupPayload = @{
                model = $modelName
                prompt = "Hi"
                stream = $false
            } | ConvertTo-Json
            
            $warmupResponse = Invoke-WebRequest -Uri "http://localhost:11434/api/generate" `
                -Method POST `
                -Body $warmupPayload `
                -ContentType "application/json" `
                -ErrorAction Stop `
                -TimeoutSec 60
            
            if ($warmupResponse.StatusCode -eq 200) {
                $data = $warmupResponse.Content | ConvertFrom-Json
                $genTime = $data.total_duration / 1e9  # Convert nanoseconds to seconds
                Write-Host "  ✅ Warmup successful in $([Math]::Round($genTime, 2))s" -ForegroundColor Green
            }
        }
        catch {
            Write-Host "  ⚠️ Warmup failed or timed out" -ForegroundColor Yellow
            Write-Host "     Error: $($_.Exception.Message)" -ForegroundColor Gray
        }
        Write-Host ""
    }
}

Write-Host "=============================" -ForegroundColor Cyan
Write-Host "✅ Diagnostics complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. If models show up, restart the backend:" -ForegroundColor Gray
Write-Host "     python -m uvicorn main:app --reload" -ForegroundColor DarkGray
Write-Host "  2. Check that the model name matches backend configuration" -ForegroundColor Gray
