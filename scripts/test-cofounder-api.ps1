# GLAD Labs Co-founder Agent - PowerShell API Test Commands
# Use these commands to test the API from PowerShell

Write-Host "`n🧪 GLAD Labs Co-founder Agent API Test Commands`n" -ForegroundColor Cyan

# Health Check
Write-Host "📍 Health Check:" -ForegroundColor Yellow
Write-Host "Invoke-RestMethod -Uri 'http://localhost:8000/' -Method Get`n" -ForegroundColor White

# API Documentation
Write-Host "📖 API Documentation (opens in browser):" -ForegroundColor Yellow
Write-Host "Start-Process 'http://localhost:8000/docs'`n" -ForegroundColor White

# Send Status Command
Write-Host "📊 Send Status Command:" -ForegroundColor Yellow
Write-Host @"
`$body = @{
    command = "status"
    parameters = @{}
} | ConvertTo-Json

Invoke-RestMethod -Uri 'http://localhost:8000/command' ``
    -Method Post ``
    -Headers @{"Content-Type"="application/json"} ``
    -Body `$body | ConvertTo-Json -Depth 10
"@ -ForegroundColor White
Write-Host ""

# Create a Task
Write-Host "📝 Create a Task:" -ForegroundColor Yellow
Write-Host @"
`$taskBody = @{
    topic = "AI Agent Development Best Practices"
    primary_keyword = "AI agents"
    target_audience = "Developers"
    category = "Blog Post"
    metadata = @{
        priority = 1
        content_type = "technical_guide"
        word_count_target = 2000
    }
} | ConvertTo-Json

Invoke-RestMethod -Uri 'http://localhost:8000/tasks' ``
    -Method Post ``
    -Headers @{"Content-Type"="application/json"} ``
    -Body `$taskBody | ConvertTo-Json -Depth 10
"@ -ForegroundColor White
Write-Host ""

# Get Agents Status
Write-Host "🤖 Get All Agents:" -ForegroundColor Yellow
Write-Host "Invoke-RestMethod -Uri 'http://localhost:8000/agents' -Method Get | ConvertTo-Json -Depth 10`n" -ForegroundColor White

# Get Performance Metrics
Write-Host "📈 Get Performance Metrics:" -ForegroundColor Yellow
Write-Host "Invoke-RestMethod -Uri 'http://localhost:8000/performance' -Method Get | ConvertTo-Json -Depth 10`n" -ForegroundColor White

Write-Host "💡 Tip: Copy and paste any command above to test the API`n" -ForegroundColor Green
