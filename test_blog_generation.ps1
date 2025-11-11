#!/usr/bin/env powershell
# Test Blog Generation with Enhanced Logging

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "🚀 BLOG GENERATION TEST" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$apiUrl = "http://127.0.0.1:8000"

$blogPostData = @{
    topic = "Artificial Intelligence Best Practices for 2025"
    style = "technical"
    tone = "professional"
    target_length = 1500
    tags = @("AI", "Machine Learning", "Best Practices")
    categories = @("Technology")
    generate_featured_image = $true
    enhanced = $true
    publish_mode = "draft"
} | ConvertTo-Json

Write-Host "📝 Creating blog post:" -ForegroundColor Yellow
Write-Host "   Topic: Artificial Intelligence Best Practices for 2025" -ForegroundColor Gray
Write-Host "   Style: technical | Tone: professional" -ForegroundColor Gray
Write-Host "   Featured Image: Enabled" -ForegroundColor Gray
Write-Host ""

try {
    $ProgressPreference = 'SilentlyContinue'
    $response = Invoke-WebRequest `
        -Uri "$apiUrl/api/content/blog-posts" `
        -Method Post `
        -ContentType "application/json" `
        -Body $blogPostData `
        -TimeoutSec 30

    $responseData = $response.Content | ConvertFrom-Json

    Write-Host "✅ Blog post creation ACCEPTED" -ForegroundColor Green
    Write-Host ""
    Write-Host "📊 Response:" -ForegroundColor Yellow
    Write-Host "   Task ID: $($responseData.task_id)" -ForegroundColor Cyan
    Write-Host "   Status: $($responseData.status)" -ForegroundColor Cyan
    Write-Host ""

    Write-Host "================================" -ForegroundColor Cyan
    Write-Host "⏳ MONITORING TASK PROGRESS" -ForegroundColor Cyan
    Write-Host "================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Watch backend console for detailed logs:" -ForegroundColor Yellow
    Write-Host "  🎬 BLOG GENERATION STARTED" -ForegroundColor Gray
    Write-Host "  🔄 [ATTEMPT 1/3] Trying Ollama..." -ForegroundColor Gray
    Write-Host "  📊 Quality Score: X.X/7.0" -ForegroundColor Gray
    Write-Host "  📝 [STAGE 1/4] Generating content" -ForegroundColor Gray
    Write-Host "  🖼️  [STAGE 2/4] Searching for image" -ForegroundColor Gray
    Write-Host "  💾 [STAGE 3/4] Publishing to Strapi" -ForegroundColor Gray
    Write-Host "  ✨ [STAGE 4/4] Finalizing task" -ForegroundColor Gray
    Write-Host ""

    $taskId = $responseData.task_id
    $attempt = 0
    $maxAttempts = 150

    while ($attempt -lt $maxAttempts) {
        $attempt++
        Start-Sleep -Seconds 2

        try {
            $statusResponse = Invoke-WebRequest `
                -Uri "$apiUrl/api/content/blog-posts/tasks/$taskId" `
                -Method Get `
                -TimeoutSec 10

            $statusData = $statusResponse.Content | ConvertFrom-Json

            if ($statusData.progress) {
                $pct = $statusData.progress.percentage
                $stage = $statusData.progress.stage
                Write-Host "   [$attempt] Status: $($statusData.status) | Progress: $pct% | Stage: $stage" -ForegroundColor Cyan
            }

            if ($statusData.status -eq "completed") {
                Write-Host ""
                Write-Host "✅ GENERATION COMPLETED!" -ForegroundColor Green
                Write-Host "   Final Progress: $($statusData.progress.percentage)%" -ForegroundColor Cyan
                break
            }
            elseif ($statusData.status -eq "failed") {
                Write-Host ""
                Write-Host "❌ GENERATION FAILED: $($statusData.error)" -ForegroundColor Red
                break
            }
        }
        catch {
            # Silently continue polling
        }
    }

    Write-Host ""
    Write-Host "================================" -ForegroundColor Cyan
    Write-Host "📋 TEST COMPLETE" -ForegroundColor Cyan
    Write-Host "================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "✅ Check the backend console for full pipeline logs!" -ForegroundColor Green
    Write-Host ""
}
catch {
    Write-Host ""
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
}
