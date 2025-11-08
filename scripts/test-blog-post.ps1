# Test blog post creation and persistence
Write-Host "🟢 Testing Blog Post Creation..." -ForegroundColor Cyan

$uri = "http://127.0.0.1:8000/api/content/blog-posts"
$body = @{
    "topic" = "Test Persistence Logic"
    "style" = "technical"
    "tone" = "professional"
    "target_length" = 1500
} | ConvertTo-Json

Write-Host "📝 Sending POST request to $uri" -ForegroundColor Yellow
Write-Host "📋 Request body: $body" -ForegroundColor Gray

try {
    $response = Invoke-RestMethod -Uri $uri -Method POST -ContentType "application/json" -Body $body -TimeoutSec 10
    Write-Host "✅ Response received (HTTP 201)" -ForegroundColor Green
    
    $taskId = $response.task_id
    Write-Host "🆔 Task ID: $taskId" -ForegroundColor Green
    Write-Host "📊 Status: $($response.status)" -ForegroundColor Green
    Write-Host "" 
    Write-Host "📋 Full Response:" -ForegroundColor Cyan
    $response | ConvertTo-Json -Depth 10
    
    Write-Host ""
    Write-Host "🔍 Now testing retrieval of this task..." -ForegroundColor Cyan
    
    Start-Sleep -Seconds 2
    
    $getUri = "http://127.0.0.1:8000/api/content/blog-posts/tasks/$taskId"
    Write-Host "GET $getUri" -ForegroundColor Yellow
    
    $getResponse = Invoke-RestMethod -Uri $getUri -Method GET -TimeoutSec 10
    Write-Host "✅ Task retrieved successfully" -ForegroundColor Green
    Write-Host "📊 Current Status: $($getResponse.status)" -ForegroundColor Green
    $getResponse | ConvertTo-Json -Depth 10
    
} catch {
    Write-Host "❌ Error: $($_)" -ForegroundColor Red
    $_.Exception | Format-List -Force
}
