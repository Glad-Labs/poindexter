#!/usr/bin/env python
"""
PHASE 1 ACTION ITEMS - Execute in order

This script outlines exact commands to run for Phase 1 verification.
Run each section, validate output, then move to next section.
"""

# =============================================================================
# SECTION 1: ENVIRONMENT SETUP (5 minutes)
# =============================================================================
"""
ACTION 1.1: Create .env file with required variables

The backend needs these environment variables to work:
- STRAPI_API_URL: Where Strapi CMS is running
- STRAPI_API_TOKEN: Authentication token for Strapi
- PEXELS_API_KEY: API key for free image search (https://www.pexels.com/api/)

Steps:
1. If .env doesn't exist, create it in project root
2. Add these lines:
   STRAPI_API_URL=http://localhost:1337
   STRAPI_API_TOKEN=<your-token-from-strapi-admin>
   PEXELS_API_KEY=<your-key-from-pexels>
   USE_OLLAMA=true
   OLLAMA_HOST=http://localhost:11434

3. Save and reload terminal
"""

# How to get tokens:
# Strapi token: Go to http://localhost:1337/admin -> Settings -> API Tokens -> Create
# Pexels key: Go to https://www.pexels.com/api/ -> Get API Key
# Gemini key (optional fallback): https://makersuite.google.com/app/apikey

# =============================================================================
# SECTION 2: TEST AI CONTENT GENERATOR (15 minutes)
# =============================================================================

"""
ACTION 2.1: Test content generation directly

Run this Python script to verify the AI generator works:

cd src/cofounder_agent
python -c "
import asyncio
from services.ai_content_generator import get_content_generator

async def test():
    gen = get_content_generator()
    print(f'Ollama available: {gen.ollama_available}')
    print(f'HF token: {bool(gen.hf_token)}')
    print(f'Gemini key: {bool(gen.gemini_key)}')
    
    print('\\nGenerating blog post...')
    content, model, metrics = await gen.generate_blog_post(
        topic='Getting Started with Python',
        style='technical',
        tone='professional',
        target_length=1000,
        tags=['python', 'beginner']
    )
    
    print(f'✓ Generated {len(content)} characters')
    print(f'✓ Model: {model}')
    print(f'✓ Quality: {metrics[\"final_quality_score\"]}/10')
    print(f'✓ Time: {metrics[\"generation_time_seconds\"]:.1f}s')
    print(f'\\nFirst 300 chars:\\n{content[:300]}...')

asyncio.run(test())
"

EXPECTED OUTPUT:
- Ollama available: True
- Content: 800-1200 words
- Quality: 7-10/10
- Model: ollama:mistral (or other ollama model)
- Time: 30-90 seconds
"""

# =============================================================================
# SECTION 3: TEST API ENDPOINTS (20 minutes)
# =============================================================================

"""
ACTION 3.1: Create blog post via API

curl -X POST http://localhost:8000/api/content/create \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI in Business: A Practical Guide",
    "style": "technical",
    "tone": "professional",
    "target_length": 1200,
    "tags": ["AI", "Business", "Technology"],
    "generate_featured_image": true,
    "publish_mode": "draft"
  }'

EXPECTED RESPONSE:
{
  "task_id": "blog_20251102_abc12345",
  "status": "pending",
  "topic": "AI in Business: A Practical Guide",
  "polling_url": "/api/v1/content/tasks/blog_20251102_abc12345",
  "estimated_completion": "2025-11-02T15:30:45.123456"
}

SAVE THE task_id - you'll use it in next step!
"""

"""
ACTION 3.2: Poll for status

Every 3-5 seconds, run:

curl http://localhost:8000/api/content/tasks/blog_20251102_abc12345

(Replace task_id with one from 3.1)

This will show progress:
- Stage 0%: "queued"
- Stage 25%: "content_generation" 
- Stage 50%: "image_generation"
- Stage 75%: "publishing"
- Stage 100%: "complete"

WAIT until status is "completed" (takes 1-3 minutes)

When completed, you'll see result:
{
  "task_id": "blog_20251102_abc12345",
  "status": "completed",
  "result": {
    "title": "AI in Business: A Practical Guide",
    "content": "# AI in Business...",
    "featured_image_url": "https://...",
    "model_used": "ollama:mistral",
    "quality_score": 8.5,
    "word_count": 1250
  }
}
"""

# =============================================================================
# SECTION 4: VERIFY STRAPI PUBLISHING (10 minutes)
# =============================================================================

"""
ACTION 4.1: Check Strapi is accessible

curl http://localhost:1337/admin

Should return HTML (admin panel page)
"""

"""
ACTION 4.2: Verify API token works

curl -H "Authorization: Bearer YOUR_STRAPI_TOKEN" \
  http://localhost:1337/api/articles

Should return JSON with articles list (may be empty)
"""

"""
ACTION 4.3: Test creating post in Strapi directly

curl -X POST http://localhost:1337/api/articles \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "title": "Test Post",
      "content": "# Test Content",
      "summary": "This is a test"
    }
  }'

Should return created post with ID
"""

# =============================================================================
# SECTION 5: COMPLETE WORKFLOW TEST (30 minutes)
# =============================================================================

"""
ACTION 5.1: Full end-to-end test

Run this PowerShell script:

$response = Invoke-WebRequest -Uri 'http://localhost:8000/api/content/create' `
  -Method Post `
  -Headers @{ "Content-Type" = "application/json" } `
  -Body '{
    "topic": "E2E Test: Python Data Science",
    "style": "educational",
    "tone": "casual",
    "target_length": 1500,
    "tags": ["Python", "DataScience"],
    "generate_featured_image": true,
    "publish_mode": "draft"
  }'

$taskId = ($response.Content | ConvertFrom-Json).task_id
Write-Host "Task ID: $taskId"

# Poll until complete
while ($true) {
  $status = Invoke-WebRequest -Uri "http://localhost:8000/api/content/tasks/$taskId" | ConvertFrom-Json
  Write-Host "Status: $($status.status) - Progress: $($status.progress.percentage)%"
  
  if ($status.status -eq "completed") {
    Write-Host "✓ Generation complete!"
    Write-Host "  Title: $($status.result.title)"
    Write-Host "  Words: $($status.result.word_count)"
    Write-Host "  Quality: $($status.result.quality_score)/10"
    Write-Host "  Model: $($status.result.model_used)"
    break
  }
  
  Start-Sleep -Seconds 3
}

EXPECTED OUTCOME:
- Task completes in 2-3 minutes
- Content quality 7-10/10
- Generated 1400-1600 words
- Model used: ollama:mistral
- Featured image URL populated
"""

# =============================================================================
# TROUBLESHOOTING
# =============================================================================

"""
PROBLEM: "Ollama not available"
SOLUTION: Start Ollama in another terminal: ollama serve

PROBLEM: "STRAPI_API_TOKEN invalid"  
SOLUTION: 
1. Go to http://localhost:1337/admin
2. Login
3. Settings (gear icon) -> API Tokens
4. Create new token or copy existing

PROBLEM: "Content too short" (quality <7)
SOLUTION: Increase target_length to 1500+

PROBLEM: "Pexels image not found"
SOLUTION:
1. Check PEXELS_API_KEY is set correctly
2. Try with different keywords
3. Can run without images (featured_image: false)

PROBLEM: "Strapi endpoint returns 401"
SOLUTION:
1. Verify token is correct
2. Check format: "Authorization: Bearer TOKEN"
3. Regenerate token in Strapi admin

PROBLEM: Server crashes with "Port 8000 in use"
SOLUTION: Kill existing process and restart
  lsof -ti:8000 | xargs kill -9
  python -m uvicorn main:app --reload --port 8000
"""

# =============================================================================
# SUCCESS CRITERIA - Phase 1 Complete When:
# =============================================================================

"""
✓ Content generator produces 1000+ word articles
✓ AI quality validation works (scores 7-10)
✓ Ollama model selection works
✓ API endpoints respond correctly
✓ Task status polling works
✓ Full workflow completes in < 3 minutes
✓ Strapi publishing integration verified
✓ Image search (Pexels) working
✓ No crashes or unhandled errors
✓ Progress tracking accurate
"""

print(__doc__)
