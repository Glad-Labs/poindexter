# Complete E2E Workflow Implementation Guide

**Goal:** Get a working end-to-end workflow:

1. ✅ Access Oversight Hub
2. ✅ Generate blog post with local Ollama
3. ✅ Save to Strapi CMS
4. ✅ Display on Public Site

**Timeline:** 30-45 minutes to get working demo  
**Difficulty:** Medium  
**Status:** Starting implementation

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ Oversight Hub (React)                                       │
│ - User clicks "Generate Post"                               │
│ - Inputs: Topic, Style, Tone, Length                        │
└──────────────────┬──────────────────────────────────────────┘
                   │ HTTP POST /api/content/generate
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI Backend (Python)                                    │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 1. Receive request with POST body                       │ │
│ │ 2. Create Task (in-memory) with status "pending"        │ │
│ │ 3. Call Ollama model (http://localhost:11434)           │ │
│ │ 4. Generate blog post content                           │ │
│ │ 5. Update task status to "completed"                    │ │
│ │ 6. Return task_id to client                             │ │
│ └─────────────────────────────────────────────────────────┘ │
└──────────────────┬──────────────────────────────────────────┘
                   │ HTTP POST /api/posts (with token)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ Strapi CMS (Node.js)                                        │
│ - Save blog post with generated content                     │
│ - Store title, slug, content, metadata                      │
│ - Return post ID                                            │
└──────────────────┬──────────────────────────────────────────┘
                   │ ISR Revalidation
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ Public Site (Next.js)                                       │
│ - Fetch updated posts from Strapi                           │
│ - Display new post on homepage                              │
│ - SEO optimized rendering                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 1: Ensure Ollama is Running Locally

### Check Ollama Status

```bash
# Test Ollama is responding
curl http://localhost:11434/api/tags

# Expected response:
# {"models":[{"name":"mistral:latest",...},{"name":"neural-chat:latest",...}]}

# If error, start Ollama:
ollama serve
```

### Pull a Model (if needed)

```bash
# Quick model (3B, fast)
ollama pull mistral

# Or use existing model
ollama list
```

---

## Step 2: Implement Content Generation Endpoint

**Location:** `src/cofounder_agent/routes/content_generation.py` (CREATE NEW FILE)

This endpoint will:

1. Accept blog post request
2. Generate content using Ollama
3. Return task tracking info

Create this file:

````python
"""
Content Generation Routes

Endpoints for AI-powered blog post generation using Ollama or other models.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import logging
import httpx
import os

logger = logging.getLogger(__name__)

# Router for content endpoints
content_router = APIRouter(prefix="/api/content", tags=["content"])

# In-memory task storage (in production, use database)
task_store: Dict[str, Dict[str, Any]] = {}


class GenerateBlogPostRequest(BaseModel):
    """Request to generate a blog post"""
    topic: str = Field(..., min_length=5, max_length=300, description="Blog post topic")
    style: str = Field("technical", description="Writing style: technical, narrative, listicle")
    tone: str = Field("professional", description="Tone: professional, casual, academic")
    target_length: int = Field(1500, ge=300, le=5000, description="Target word count")
    tags: Optional[List[str]] = Field(None, description="Tags for categorization")


class GenerateBlogPostResponse(BaseModel):
    """Response with task tracking info"""
    task_id: str
    status: str  # "pending" or "completed"
    message: str


class TaskStatus(BaseModel):
    """Task status info"""
    task_id: str
    status: str
    created_at: str
    result: Optional[Dict[str, Any]] = None


# Ollama Configuration
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")


async def call_ollama(prompt: str) -> str:
    """Call Ollama API to generate content"""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:  # 5 minute timeout
            response = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,  # Get full response at once
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")
    except Exception as e:
        logger.error(f"Ollama API error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate content: {str(e)}")


def generate_blog_post_prompt(topic: str, style: str, tone: str, target_length: int) -> str:
    """Generate prompt for blog post creation"""
    return f"""
Write a {target_length}-word blog post about: {topic}

Style: {style}
Tone: {tone}

Requirements:
1. Start with a compelling headline
2. Include an engaging introduction
3. Break content into clear sections with headers
4. Use bullet points where appropriate
5. Include a strong conclusion
6. Write in markdown format

Generate the blog post now:
"""


async def generate_post_background(task_id: str, request: GenerateBlogPostRequest):
    """Generate blog post in background"""
    try:
        # Update status to processing
        task_store[task_id]["status"] = "processing"

        # Generate prompt
        prompt = generate_blog_post_prompt(
            request.topic,
            request.style,
            request.tone,
            request.target_length
        )

        # Call Ollama
        logger.info(f"Calling Ollama for task {task_id}")
        content = await call_ollama(prompt)

        # Parse title from content
        lines = content.split("\n")
        title = next((line.replace("# ", "").strip() for line in lines if line.startswith("# ")), request.topic)

        # Create slug
        slug = title.lower().replace(" ", "-").replace(",", "").replace(".", "")[:50]

        # Store result
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {
            "title": title,
            "slug": slug,
            "content": content,
            "topic": request.topic,
            "style": request.style,
            "tone": request.tone,
            "tags": request.tags or [request.topic.lower()],
            "generated_at": datetime.utcnow().isoformat()
        }

        logger.info(f"Task {task_id} completed successfully")

    except Exception as e:
        task_store[task_id]["status"] = "error"
        task_store[task_id]["error"] = str(e)
        logger.error(f"Task {task_id} failed: {e}")


@content_router.post("/generate", response_model=GenerateBlogPostResponse)
async def generate_blog_post(
    request: GenerateBlogPostRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate a blog post using Ollama

    Returns immediately with task_id. Check /status/{task_id} for progress.

    Example:
    ```
    POST /api/content/generate
    {
        "topic": "Getting Started with FastAPI",
        "style": "technical",
        "tone": "professional",
        "target_length": 2000,
        "tags": ["fastapi", "python", "api"]
    }
    ```
    """
    # Create task
    task_id = str(uuid.uuid4())
    task_store[task_id] = {
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "request": request.dict()
    }

    # Start background generation
    background_tasks.add_task(generate_post_background, task_id, request)

    return GenerateBlogPostResponse(
        task_id=task_id,
        status="pending",
        message="Post generation started. Check /api/content/status/{task_id} for progress."
    )


@content_router.get("/status/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str):
    """Get the status of a content generation task"""
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail="Task not found")

    task = task_store[task_id]
    return TaskStatus(
        task_id=task_id,
        status=task["status"],
        created_at=task["created_at"],
        result=task.get("result")
    )
````

---

## Step 3: Register the Content Router in main.py

**File:** `src/cofounder_agent/main.py`

Add this after the existing router imports (around line 24):

```python
# Add to imports section
from routes.content_generation import content_router

# Then add to the app setup section (around line 150-160)
# Find where other routers are included:
app.include_router(content_router)
```

---

## Step 4: Implement Strapi Integration

**Location:** `src/cofounder_agent/services/strapi_client.py` (CREATE NEW FILE)

```python
"""
Strapi CMS Client

Handles all interactions with Strapi CMS including:
- Creating blog posts
- Updating posts
- Publishing posts
"""

import httpx
import logging
from typing import Optional, Dict, Any
import os

logger = logging.getLogger(__name__)

STRAPI_URL = os.getenv("STRAPI_URL", "http://localhost:1337")
STRAPI_API_TOKEN = os.getenv("STRAPI_API_TOKEN", "")


class StrapiClient:
    """Client for Strapi CMS operations"""

    def __init__(self, base_url: str = STRAPI_URL, api_token: str = STRAPI_API_TOKEN):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

    async def create_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new blog post in Strapi

        Args:
            post_data: {
                "title": "Post Title",
                "slug": "post-slug",
                "content": "Markdown content...",
                "excerpt": "Short excerpt",
                "tags": ["tag1", "tag2"],
                "status": "draft" or "published"
            }

        Returns:
            Response from Strapi with created post
        """
        try:
            # Prepare post data for Strapi
            strapi_payload = {
                "data": {
                    "title": post_data.get("title"),
                    "slug": post_data.get("slug"),
                    "content": post_data.get("content"),
                    "excerpt": post_data.get("excerpt", post_data.get("content", "")[:200]),
                    "status": post_data.get("status", "draft"),
                }
            }

            # Add optional fields
            if post_data.get("tags"):
                strapi_payload["data"]["tags"] = post_data["tags"]

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/posts",
                    json=strapi_payload,
                    headers=self.headers
                )
                response.raise_for_status()
                result = response.json()
                logger.info(f"Post created in Strapi: {result.get('data', {}).get('id')}")
                return result

        except httpx.HTTPError as e:
            logger.error(f"Strapi API error: {e}")
            raise

    async def publish_post(self, post_id: int) -> Dict[str, Any]:
        """Publish a draft post"""
        try:
            strapi_payload = {
                "data": {
                    "status": "published"
                }
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{self.base_url}/api/posts/{post_id}",
                    json=strapi_payload,
                    headers=self.headers
                )
                response.raise_for_status()
                logger.info(f"Post {post_id} published")
                return response.json()

        except httpx.HTTPError as e:
            logger.error(f"Failed to publish post: {e}")
            raise


# Singleton instance
_strapi_client: Optional[StrapiClient] = None


def get_strapi_client() -> StrapiClient:
    """Get or create Strapi client instance"""
    global _strapi_client
    if _strapi_client is None:
        _strapi_client = StrapiClient()
    return _strapi_client
```

---

## Step 5: Add Save-to-Strapi Endpoint

**File:** `src/cofounder_agent/routes/content_generation.py`

Add this endpoint to the file created in Step 2 (add at the end):

````python
from services.strapi_client import get_strapi_client


class SavePostRequest(BaseModel):
    """Request to save generated post to Strapi"""
    task_id: str = Field(..., description="Task ID from generate endpoint")
    publish: bool = Field(False, description="Auto-publish post")


class SavePostResponse(BaseModel):
    """Response with saved post info"""
    strapi_post_id: int
    title: str
    slug: str
    status: str
    url: str


@content_router.post("/save-to-strapi", response_model=SavePostResponse)
async def save_post_to_strapi(request: SavePostRequest):
    """
    Save generated blog post to Strapi CMS

    Example:
    ```
    POST /api/content/save-to-strapi
    {
        "task_id": "uuid-from-generate",
        "publish": true
    }
    ```
    """
    # Get task
    if request.task_id not in task_store:
        raise HTTPException(status_code=404, detail="Task not found")

    task = task_store[request.task_id]

    if task["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Task not ready. Current status: {task['status']}"
        )

    result = task.get("result")
    if not result:
        raise HTTPException(status_code=400, detail="No result found for task")

    # Save to Strapi
    try:
        strapi = get_strapi_client()
        strapi_response = await strapi.create_post({
            "title": result["title"],
            "slug": result["slug"],
            "content": result["content"],
            "tags": result.get("tags", []),
            "status": "published" if request.publish else "draft"
        })

        post_id = strapi_response.get("data", {}).get("id")

        return SavePostResponse(
            strapi_post_id=post_id,
            title=result["title"],
            slug=result["slug"],
            status="published" if request.publish else "draft",
            url=f"/posts/{result['slug']}"
        )

    except Exception as e:
        logger.error(f"Failed to save to Strapi: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save to Strapi: {str(e)}")
````

---

## Step 6: Update Oversight Hub UI

**File:** `web/oversight-hub/src/components/ContentGenerator.jsx` (CREATE NEW FILE)

```jsx
import React, { useState } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  LinearProgress,
  Alert,
  Typography,
} from '@mui/material';

const ContentGenerator = () => {
  const [topic, setTopic] = useState('');
  const [style, setStyle] = useState('technical');
  const [tone, setTone] = useState('professional');
  const [targetLength, setTargetLength] = useState(1500);
  const [tags, setTags] = useState('');

  const [loading, setLoading] = useState(false);
  const [taskId, setTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const generatePost = async () => {
    if (!topic.trim()) {
      setError('Please enter a topic');
      return;
    }

    setError(null);
    setLoading(true);

    try {
      // Generate post
      const response = await fetch(
        'http://localhost:8000/api/content/generate',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            topic,
            style,
            tone,
            target_length: targetLength,
            tags: tags ? tags.split(',').map((t) => t.trim()) : [],
          }),
        }
      );

      if (!response.ok) throw new Error('Failed to generate post');

      const data = await response.json();
      setTaskId(data.task_id);

      // Poll for status
      pollStatus(data.task_id);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  const pollStatus = async (id) => {
    const maxAttempts = 60; // 5 minutes max
    let attempts = 0;

    const poll = async () => {
      try {
        const response = await fetch(
          `http://localhost:8000/api/content/status/${id}`
        );
        if (!response.ok) throw new Error('Failed to get status');

        const data = await response.json();
        setTaskStatus(data.status);

        if (data.status === 'completed') {
          setResult(data.result);
          setLoading(false);
        } else if (data.status === 'error') {
          setError(data.error || 'Generation failed');
          setLoading(false);
        } else if (attempts < maxAttempts) {
          attempts++;
          setTimeout(poll, 5000); // Check every 5 seconds
        } else {
          setError('Generation timeout');
          setLoading(false);
        }
      } catch (err) {
        setError(err.message);
        setLoading(false);
      }
    };

    poll();
  };

  const saveToStrapi = async () => {
    if (!taskId) return;

    try {
      setLoading(true);
      const response = await fetch(
        'http://localhost:8000/api/content/save-to-strapi',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task_id: taskId,
            publish: true,
          }),
        }
      );

      if (!response.ok) throw new Error('Failed to save to Strapi');

      const data = await response.json();
      setError(null);
      alert(`Post saved to Strapi! ID: ${data.strapi_post_id}`);

      // Reset form
      setTopic('');
      setTaskId(null);
      setResult(null);
      setTaskStatus(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardContent>
        <Typography variant="h5" gutterBottom>
          Generate Blog Post with Ollama
        </Typography>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField
            label="Topic"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="e.g., Getting Started with FastAPI"
            fullWidth
            disabled={loading}
          />

          <FormControl fullWidth disabled={loading}>
            <InputLabel>Style</InputLabel>
            <Select
              value={style}
              onChange={(e) => setStyle(e.target.value)}
              label="Style"
            >
              <MenuItem value="technical">Technical</MenuItem>
              <MenuItem value="narrative">Narrative</MenuItem>
              <MenuItem value="listicle">Listicle</MenuItem>
            </Select>
          </FormControl>

          <FormControl fullWidth disabled={loading}>
            <InputLabel>Tone</InputLabel>
            <Select
              value={tone}
              onChange={(e) => setTone(e.target.value)}
              label="Tone"
            >
              <MenuItem value="professional">Professional</MenuItem>
              <MenuItem value="casual">Casual</MenuItem>
              <MenuItem value="academic">Academic</MenuItem>
            </Select>
          </FormControl>

          <TextField
            label="Target Length (words)"
            type="number"
            value={targetLength}
            onChange={(e) => setTargetLength(parseInt(e.target.value))}
            disabled={loading}
          />

          <TextField
            label="Tags (comma-separated)"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="fastapi, python, api"
            disabled={loading}
          />

          {loading && <LinearProgress />}

          {taskStatus && !result && (
            <Alert severity="info">Generation in progress: {taskStatus}</Alert>
          )}

          {result && (
            <Box sx={{ mt: 2, p: 2, bgcolor: '#f5f5f5', borderRadius: 1 }}>
              <Typography variant="h6">{result.title}</Typography>
              <Typography color="textSecondary" sx={{ mb: 1 }}>
                Slug: {result.slug}
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  whiteSpace: 'pre-wrap',
                  maxHeight: 300,
                  overflow: 'auto',
                }}
              >
                {result.content}
              </Typography>
            </Box>
          )}

          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button
              variant="contained"
              onClick={generatePost}
              disabled={loading}
            >
              {loading ? 'Generating...' : 'Generate Post'}
            </Button>

            {result && (
              <Button
                variant="contained"
                color="success"
                onClick={saveToStrapi}
                disabled={loading}
              >
                Save to Strapi & Publish
              </Button>
            )}
          </Box>
        </Box>
      </CardContent>
    </Card>
  );
};

export default ContentGenerator;
```

Add this component to your Oversight Hub dashboard.

---

## Step 7: Quick Testing Checklist

### 1. Start All Services

```bash
# Terminal 1: Ollama
ollama serve

# Terminal 2: Strapi
cd cms/strapi-v5-backend
npm run develop

# Terminal 3: Backend API
cd src/cofounder_agent
python -m uvicorn main:app --reload

# Terminal 4: Frontend
cd web/oversight-hub
npm start
```

### 2. Check Ollama is Ready

```bash
curl http://localhost:11434/api/tags
# Should return model list
```

### 3. Check Strapi is Ready

Visit `http://localhost:1337/admin` and verify logged in.

### 4. Create Strapi API Token

1. Go to `http://localhost:1337/admin`
2. Settings → API Tokens → Create new token
3. Name: "Test Token"
4. Type: "Full access"
5. Copy token

### 5. Set Environment Variables

```bash
# In terminal where backend runs:
$env:STRAPI_URL = "http://localhost:1337"
$env:STRAPI_API_TOKEN = "your-token-here"
$env:OLLAMA_HOST = "http://localhost:11434"
$env:OLLAMA_MODEL = "mistral"
```

### 6. Test in Oversight Hub

1. Open `http://localhost:3001`
2. Navigate to Content Generator component
3. Enter topic: "How to use Ollama with Python"
4. Select style and tone
5. Click "Generate Post"
6. Wait 1-2 minutes for Ollama to generate
7. See content preview
8. Click "Save to Strapi & Publish"
9. Visit `http://localhost:3000` to see published post

---

## Step 8: Environment Setup

Create `.env` file in `src/cofounder_agent/`:

```bash
# Strapi
STRAPI_URL=http://localhost:1337
STRAPI_API_TOKEN=your-strapi-token-here

# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral

# FastAPI
DEBUG=True
LOG_LEVEL=INFO
```

---

## Troubleshooting

### Issue: "Connection refused" to Ollama

**Solution:**

```bash
# Start Ollama
ollama serve

# Verify it's running
curl http://localhost:11434/api/tags
```

### Issue: Strapi API returns 401

**Solution:**

- Check API token is correct
- Generate new token in Strapi admin
- Update `STRAPI_API_TOKEN` env var

### Issue: "Task not found" error

**Solution:**

- Ensure backend API is running
- Check task_id is correct
- Wait at least 5 seconds after generating before checking status

### Issue: Generation takes too long

**Solution:**

- Ollama models can take 1-3 minutes on first run
- Smaller models (phi, neural-chat) are faster
- Use: `ollama pull mistral` for faster model

---

## What You'll Have After This

✅ **Working End-to-End Workflow:**

1. **Oversight Hub** - Beautiful admin dashboard with content generator
2. **Generate Posts** - One-click blog post generation using Ollama (free local AI)
3. **Save to Strapi** - Automatically publish to Strapi CMS
4. **Display on Public Site** - See posts appear on homepage instantly
5. **Track Progress** - Monitor generation status in real-time

**No API costs** - Everything runs locally with Ollama!

---

## Next Steps After Getting This Working

1. Add featured image generation prompt
2. Add SEO metadata generation
3. Add category/tag selection UI
4. Add schedule publish for future dates
5. Add edit/update existing posts functionality
