# ============================================================================
# GLAD Labs E2E Workflow Test Script
# ============================================================================
# This script tests the complete workflow:
# 1. Ollama generates blog post
# 2. Backend receives and processes
# 3. Post saves to Strapi
# 4. Post should appear on public site
# ============================================================================

# Configuration
$OLLAMA_HOST = "http://localhost:11434"
$STRAPI_URL = "http://localhost:1337"
$BACKEND_URL = "http://localhost:8000"
$STRAPI_API_TOKEN = $env:STRAPI_API_TOKEN  # Must be set before running

# Colors for output
$ColorSuccess = "Green"
$ColorError = "Red"
$ColorInfo = "Cyan"
$ColorWarning = "Yellow"

function Write-Success {
    param([string]$Message)
    Write-Host "✅ $Message" -ForegroundColor $ColorSuccess
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "❌ $Message" -ForegroundColor $ColorError
}

function Write-Info {
    param([string]$Message)
    Write-Host "ℹ️  $Message" -ForegroundColor $ColorInfo
}

function Write-Warning-Custom {
    param([string]$Message)
    Write-Host "⚠️  $Message" -ForegroundColor $ColorWarning
}

# Header
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  GLAD Labs E2E Workflow Test" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Verify Strapi token is set
Write-Info "Step 1: Checking configuration..."

if ([string]::IsNullOrEmpty($STRAPI_API_TOKEN)) {
    Write-Error-Custom "STRAPI_API_TOKEN environment variable not set!"
    Write-Host ""
    Write-Host "To set it, run:"
    Write-Host '  $env:STRAPI_API_TOKEN = "your-token-here"'
    Write-Host ""
    Write-Host "To get your token:"
    Write-Host "  1. Go to http://localhost:1337/admin"
    Write-Host "  2. Settings > API Tokens > Create new API token"
    Write-Host "  3. Copy the token"
    Write-Host ""
    exit 1
}

Write-Success "STRAPI_API_TOKEN is configured"

# Step 2: Verify services are running
Write-Info "Step 2: Verifying services are running..."

try {
    $ollamaCheck = curl -s "$OLLAMA_HOST/api/tags" -ErrorAction Stop
    if ($null -eq $ollamaCheck -or $ollamaCheck.Length -eq 0) {
        throw "Ollama returned empty response"
    }
    Write-Success "Ollama is responding at $OLLAMA_HOST"
} catch {
    Write-Error-Custom "Ollama is not responding at $OLLAMA_HOST"
    Write-Host "  Run: ollama serve" -ForegroundColor Yellow
    exit 1
}

try {
    $strapiCheck = curl -s "$STRAPI_URL/admin" -ErrorAction Stop
    if ($null -eq $strapiCheck -or $strapiCheck.Length -eq 0) {
        throw "Strapi returned empty response"
    }
    Write-Success "Strapi is responding at $STRAPI_URL"
} catch {
    Write-Error-Custom "Strapi is not responding at $STRAPI_URL"
    Write-Host "  Run: cd cms/strapi-main && npm run develop" -ForegroundColor Yellow
    exit 1
}

try {
    $backendCheck = curl -s "$BACKEND_URL/docs" -ErrorAction Stop
    if ($null -eq $backendCheck -or $backendCheck.Length -eq 0) {
        throw "Backend returned empty response"
    }
    Write-Success "Backend API is responding at $BACKEND_URL"
} catch {
    Write-Error-Custom "Backend API is not responding at $BACKEND_URL"
    Write-Host "  Run: cd src/cofounder_agent && python -m uvicorn main:app --reload" -ForegroundColor Yellow
    exit 1
}

Write-Host ""

# Step 3: Generate blog post
Write-Info "Step 3: Generating blog post with Ollama..."
Write-Host "  (This takes 1-3 minutes depending on model speed)" -ForegroundColor Yellow

$generateBody = @{
    topic = "The Future of AI in Business"
    style = "technical"
    tone = "professional"
    target_length = 1000
    tags = @("ai", "business", "technology")
} | ConvertTo-Json

try {
    $generateResponse = curl -s -X POST "$BACKEND_URL/api/content/generate" `
        -H "Content-Type: application/json" `
        -d $generateBody -ErrorAction Stop | ConvertFrom-Json
    
    $taskId = $generateResponse.task_id
    
    Write-Success "Generation started"
    Write-Host "  Task ID: $taskId"
    Write-Host "  Status: $($generateResponse.status)"
} catch {
    Write-Error-Custom "Failed to start generation: $_"
    exit 1
}

Write-Host ""

# Step 4: Poll for completion
Write-Info "Step 4: Waiting for generation to complete..."

$maxAttempts = 60
$attempt = 0
$completed = $false
$result = $null

for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    Start-Sleep -Seconds 5
    
    try {
        $statusResponse = curl -s "$BACKEND_URL/api/content/status/$taskId" -ErrorAction Stop | ConvertFrom-Json
        $status = $statusResponse.status
        
        Write-Host "  [$attempt/$maxAttempts] Status: $status" -ForegroundColor Cyan
        
        if ($status -eq "completed") {
            $completed = $true
            $result = $statusResponse.result
            break
        }
        elseif ($status -eq "error") {
            Write-Error-Custom "Generation failed: $($statusResponse.error)"
            exit 1
        }
    } catch {
        Write-Error-Custom "Failed to check status: $_"
        exit 1
    }
}

if (-not $completed) {
    Write-Error-Custom "Generation timeout after 5 minutes"
    exit 1
}

Write-Success "Generation completed!"
Write-Host "  Title: $($result.title)"
Write-Host "  Slug: $($result.slug)"
Write-Host "  Generated: $($result.generated_at)"

Write-Host ""

# Step 5: Save to Strapi
Write-Info "Step 5: Saving post to Strapi..."

$saveBody = @{
    task_id = $taskId
    publish = $true
} | ConvertTo-Json

try {
    $saveResponse = curl -s -X POST "$BACKEND_URL/api/content/save-to-strapi" `
        -H "Content-Type: application/json" `
        -d $saveBody -ErrorAction Stop | ConvertFrom-Json
    
    if ($null -eq $saveResponse.strapi_post_id) {
        Write-Error-Custom "Failed to save post: $($saveResponse.message)"
        exit 1
    }
    
    Write-Success "Post saved to Strapi!"
    Write-Host "  Post ID: $($saveResponse.strapi_post_id)"
    Write-Host "  Title: $($saveResponse.title)"
    Write-Host "  Status: $($saveResponse.status)"
} catch {
    Write-Error-Custom "Failed to save to Strapi: $_"
    exit 1
}

Write-Host ""

# Step 6: Success summary
Write-Host "================================================" -ForegroundColor Green
Write-Host "  ✅ E2E WORKFLOW TEST PASSED!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green

Write-Host ""
Write-Host "📝 Next Steps:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Check Strapi Content Manager:"
Write-Host "   http://localhost:1337/admin -> Content Manager -> Posts"
Write-Host ""
Write-Host "2. Check Public Site:"
Write-Host "   http://localhost:3000 -> Your post should appear on homepage"
Write-Host ""
Write-Host "3. Build React UI Component:"
Write-Host "   Add ContentGenerator.jsx to web/oversight-hub/src/components/"
Write-Host ""
Write-Host "4. Celebrate! 🎉"
Write-Host ""
