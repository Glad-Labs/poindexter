#Requires -Version 7.0
<#
.DESCRIPTION
Test the complete blog post pipeline from task creation through content generation
#>

param(
    [string]$Topic = "Artificial Intelligence in Healthcare",
    [string]$Keyword = "AI healthcare",
    [switch]$FullDebug = $false
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://localhost:8000"

Write-Host "`n" + "="*80
Write-Host "🚀 BLOG POST PIPELINE TEST"
Write-Host "="*80 + "`n"

# Step 1: Create task
Write-Host "📋 Step 1: Creating blog post task..."
Write-Host "   Topic: $Topic"
Write-Host "   Keyword: $Keyword`n"

$createTaskPayload = @{
    topic = $Topic
    primary_keyword = $Keyword
    target_audience = "healthcare professionals"
    style = "professional"
    tone = "informative"
    target_length = 1500
} | ConvertTo-Json

$taskResponse = Invoke-WebRequest -Uri "$baseUrl/api/content/tasks" `
    -Method POST `
    -Headers @{ "Content-Type" = "application/json" } `
    -Body $createTaskPayload -UseBasicParsing

$taskData = $taskResponse.Content | ConvertFrom-Json
$taskId = $taskData.task_id

Write-Host "✅ Task created!"
Write-Host "   Task ID: $taskId"
Write-Host "   Status: $($taskData.status)"
Write-Host "   Created: $($taskData.created_at)`n"

# Step 2: Monitor task progress
Write-Host "⏳ Step 2: Waiting for content generation (monitoring every 2 seconds)...`n"

$maxWait = 60  # 60 seconds max
$elapsed = 0
$lastStatus = $taskData.status

while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds 2
    $elapsed += 2
    
    $getResponse = Invoke-WebRequest -Uri "$baseUrl/api/content/tasks/$taskId" `
        -Method GET `
        -UseBasicParsing
    
    $currentTask = $getResponse.Content | ConvertFrom-Json
    $currentStatus = $currentTask.status
    
    if ($currentStatus -ne $lastStatus) {
        Write-Host "[${elapsed}s] Status changed: $lastStatus → $currentStatus"
        $lastStatus = $currentStatus
    }
    
    if ($currentStatus -eq "awaiting_approval") {
        Write-Host "`n✅ Task reached 'awaiting_approval' status!`n"
        break
    }
    
    if ($currentStatus -eq "failed") {
        Write-Host "`n❌ Task failed!`n"
        Write-Host "Error: $($currentTask.error_message)"
        exit 1
    }
}

# Step 3: Check generated content
Write-Host "🔍 Step 3: Verifying generated content...`n"

$contentLength = if ($currentTask.content) { $currentTask.content.Length } else { 0 }

Write-Host "Content Status:"
Write-Host "   - Content length: $contentLength chars"
Write-Host "   - Excerpt: $(if($currentTask.excerpt) { $currentTask.excerpt.Substring(0, [Math]::Min(50, $currentTask.excerpt.Length)) + '...' } else { '[NULL]' })"
Write-Host "   - Featured image: $(if($currentTask.featured_image_url) { '✅' } else { '❌ NULL' })"
Write-Host "   - SEO title: $(if($currentTask.seo_title) { '✅ ' + $currentTask.seo_title.Substring(0, 40) + '...' } else { '❌ NULL' })"
Write-Host "   - Quality score: $($currentTask.quality_score)"
Write-Host ""

if ($contentLength -eq 0) {
    Write-Host "⚠️  WARNING: No content generated!"
    Write-Host "`n📋 Full task data:"
    $currentTask | ConvertTo-Json -Depth 5 | Write-Host
    exit 1
}

# Step 4: Approve and publish
Write-Host "✅ Step 4: Approving task for publication...`n"

$approvalPayload = @{
    approved = $true
    human_feedback = "Content looks good. Auto-approved by test script."
    reviewer_id = "test-script"
} | ConvertTo-Json

$approvalResponse = Invoke-WebRequest -Uri "$baseUrl/api/content/tasks/$taskId/approve" `
    -Method POST `
    -Headers @{ "Content-Type" = "application/json" } `
    -Body $approvalPayload -UseBasicParsing

$approvalData = $approvalResponse.Content | ConvertFrom-Json

Write-Host "Approval Result:"
Write-Host "   - Status: $($approvalData.approval_status)"
Write-Host "   - Published URL: $($approvalData.published_url)"
Write-Host "   - Post ID: $($approvalData.strapi_post_id)`n"

# Step 5: Verify blog post in database
Write-Host "📚 Step 5: Verifying blog post in posts table...`n"

$postsResponse = Invoke-WebRequest -Uri "$baseUrl/api/posts" `
    -Method GET `
    -UseBasicParsing

$postsData = $postsResponse.Content | ConvertFrom-Json

$newPost = $postsData.data | Where-Object { $_.id -eq $approvalData.strapi_post_id } | Select-Object -First 1

if ($newPost) {
    Write-Host "✅ Blog post found in database!"
    Write-Host "   - ID: $($newPost.id)"
    Write-Host "   - Title: $($newPost.title)"
    Write-Host "   - Content length: $(if($newPost.content) { $newPost.content.Length } else { 0 }) chars"
    Write-Host "   - Status: $($newPost.status)"
    Write-Host "   - Created: $($newPost.created_at)`n"
} else {
    Write-Host "❌ Blog post NOT found in database!"
    Write-Host "   Total posts in DB: $($postsData.data.Count)"
    Write-Host "   Looking for ID: $($approvalData.strapi_post_id)`n"
    exit 1
}

Write-Host "="*80
Write-Host "✅ PIPELINE TEST COMPLETE - All steps successful!"
Write-Host "="*80 + "`n"
