# 📝 Content Generation Guide

Complete guide for generating SEO-optimized content using GLAD Labs

> This guide consolidates content generation documentation including quick start, quick reference, and implementation details.

---

## 🎯 Quick Start (5 minutes)

### Minimum Required Setup

```bash
# 1. Ensure Python environment is configured
python --version  # Should be 3.10+

# 2. Install content generation dependencies
pip install -r src/cofounder_agent/requirements.txt

# 3. Start the content generation service
python src/cofounder_agent/main.py
```

### Your First Content Generation

```bash
# Create SEO-optimized blog post
curl -X POST http://localhost:8000/api/v1/content/enhanced/blog-posts/create-seo-optimized \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI in Market Analysis",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500
  }'
```

Expected response:

---

## 📚 Understanding Content Generation

### What Gets Generated

✅ **SEO-Optimized Title** (50-60 characters)

- Keyword-rich, compelling, descriptive
- Example: "AI-Powered Market Intelligence Guide"

✅ **Meta Description** (155-160 characters)

- Google SERP snippet
- Clear value proposition

✅ **URL Slug** (URL-friendly)

- Lowercase, hyphens, 60 chars max
- Example: "ai-powered-market-intelligence"

✅ **Keywords** (5-8 relevant terms)

- Extracted from content
- Sorted by frequency
- 4+ character minimum

✅ **Featured Image Prompt**

- Professional, high-quality
- 1200x630px optimal ratio
- Ready for image generation APIs

✅ **JSON-LD Structured Data**

- Rich snippet support
- Schema.org BlogPosting format
- Improves SERP display

✅ **Social Media Metadata**

- Open Graph (Facebook)
- Twitter Cards
- Platform-specific optimization

✅ **Category & Tags**

- Auto-detected from content
- Categories: AI, Business, Compliance, Strategy, Operations
- 5-8 tags for organization

✅ **Reading Time**

- Calculated at 200 words/minute
- Minimum 1 minute
- Displayed in blog metadata

---

## 🔧 Configuration Options

### Content Generation Parameters

| Parameter         | Type   | Default        | Options                                                         | Notes                    |
| ----------------- | ------ | -------------- | --------------------------------------------------------------- | ------------------------ |
| `topic`           | string | required       | any                                                             | Main topic/subject       |
| `style`           | enum   | "technical"    | technical, narrative, listicle, educational, thought-leadership | Writing style            |
| `tone`            | enum   | "professional" | professional, casual, academic, inspirational                   | Voice/tone               |
| `target_length`   | int    | 1500           | 500-5000                                                        | Target word count        |
| `tags`            | array  | []             | any                                                             | Initial tags to consider |
| `generate_images` | bool   | true           | true/false                                                      | Generate image prompts   |

### Style Guide

#### Technical

- Suitable for: Product docs, How-tos, Technical tutorials
- Characteristics: Detail-oriented, structured, precise

#### Narrative

- Suitable for: Stories, case studies, industry insights
- Characteristics: Engaging, flowing, contextual

#### Listicle

- Suitable for: Tips, Top 10s, Checklists
- Characteristics: Scannable, organized, actionable

#### Educational

- Suitable for: Guides, Courses, Training
- Characteristics: Clear, comprehensive, step-by-step

#### Thought-Leadership

- Suitable for: Opinion pieces, Strategy, Vision
- Characteristics: Authoritative, forward-thinking, inspiring

---

## 📋 API Reference

### Create SEO-Optimized Blog Post

**Endpoint:** `POST /api/v1/content/enhanced/blog-posts/create-seo-optimized`

**Request Body:**

```json
{
  "topic": "AI in Market Analysis",
  "style": "technical",
  "tone": "professional",
  "target_length": 1500,
  "tags": ["AI", "market-intelligence"],
  "generate_images": true
}
```

**Response (202 Accepted):**

```json
{
  "task_id": "blog_seo_20251022_abc123",
  "status": "processing",
  "topic": "AI in Market Analysis",
  "created_at": "2025-10-22T18:35:00Z",
  "message": "Content generation started in background"
}
```

### Get Task Status

**Endpoint:** `GET /api/v1/content/enhanced/blog-posts/tasks/{task_id}`

**Response:**

```json
{
  "task_id": "blog_seo_20251022_abc123",
  "status": "completed",
  "result": {
    "title": "AI-Powered Market Intelligence Guide",
    "content": "# AI in Market Analysis\n\n...",
    "excerpt": "Learn how AI analyzes market trends...",
    "metadata": {
      "seo_title": "AI-Powered Market Intelligence Guide",
      "meta_description": "Comprehensive guide to AI applications in market analysis...",
      "slug": "ai-powered-market-intelligence",
      "meta_keywords": ["AI", "market", "analysis", ...],
      "reading_time": 8,
      "word_count": 1582,
      "featured_image_prompt": "Professional featured image for AI market analysis...",
      "json_ld_schema": {...},
      "social_metadata": {...},
      "category": "AI & Technology",
      "tags": ["ai", "market-intelligence", ...]
    },
    "model_used": "neural-chat:13b",
    "quality_score": 8.5,
    "generation_time_seconds": 45.2
  }
}
```

### Get Available Models

**Endpoint:** `GET /api/v1/content/enhanced/blog-posts/available-models`

**Response:**

```json
{
  "models": [
    {
      "name": "Ollama - neural-chat:13b",
      "provider": "Ollama",
      "available": true
    },
    {
      "name": "Ollama - mistral:13b",
      "provider": "Ollama",
      "available": true
    }
  ]
}
```

---

## 🚀 Advanced Usage

### Generate Multiple Blog Posts

```bash
#!/bin/bash

TOPICS=("AI in Healthcare" "Market Analysis Tools" "Blockchain Security")
STYLES=("technical" "narrative" "thought-leadership")

for topic in "${TOPICS[@]}"; do
  for style in "${STYLES[@]}"; do
    curl -X POST http://localhost:8000/api/v1/content/enhanced/blog-posts/create-seo-optimized \
      -H "Content-Type: application/json" \
      -d "{\"topic\": \"$topic\", \"style\": \"$style\", \"tone\": \"professional\", \"target_length\": 1500}"
    echo "Queued: $topic ($style)"
    sleep 2
  done
done
```

### Batch Processing with Status Tracking

```python
import requests
import json
import time

BASE_URL = "http://localhost:8000/api/v1/content/enhanced"

# Queue multiple tasks
topics = [
    {"topic": "AI in Market Analysis", "style": "technical"},
    {"topic": "Future of Blockchain", "style": "thought-leadership"},
    {"topic": "Content Strategy Tips", "style": "listicle"}
]

tasks = []
for item in topics:
    resp = requests.post(
        f"{BASE_URL}/blog-posts/create-seo-optimized",
        json={**item, "tone": "professional", "target_length": 1500}
    )
    task_id = resp.json()["task_id"]
    tasks.append(task_id)
    print(f"Queued: {item['topic']} (Task: {task_id})")

# Monitor progress
completed = set()
while len(completed) < len(tasks):
    for task_id in tasks:
        if task_id in completed:
            continue
        resp = requests.get(f"{BASE_URL}/blog-posts/tasks/{task_id}")
        status = resp.json()["status"]
        if status == "completed":
            completed.add(task_id)
            print(f"✅ Completed: {task_id}")
        elif status == "failed":
            completed.add(task_id)
            print(f"❌ Failed: {task_id}")
    time.sleep(5)

print(f"\n✅ All tasks completed!")
```

---

## 🎓 Best Practices

### 📝 Content Quality

✅ **Do:**

- Use specific, descriptive topics (not generic)
- Match style/tone to audience
- Set realistic target length (1000-3000 words)
- Review generated content for accuracy
- Test SEO metadata in SERP preview tools

❌ **Don't:**

- Generate extremely short content (<500 words)
- Use conflicting style/tone combinations
- Rely solely on AI-generated metadata
- Skip fact-checking important claims
- Use AI content for sensitive topics without review

### ⚡ Performance

✅ **Do:**

- Queue multiple tasks at once
- Use background processing
- Check task status periodically
- Implement retry logic
- Cache successful generations

❌ **Don't:**

- Wait synchronously for generation
- Poll status too frequently (>every 5 sec)
- Generate identical content repeatedly
- Overwhelm system with 100+ concurrent tasks
- Ignore timeouts

### 🔒 Security & Ethics

✅ **Do:**

- Validate all user inputs
- Sanitize generated content
- Include proper attribution
- Verify factual accuracy
- Disclose AI usage

❌ **Don't:**

- Generate misleading content
- Plagiarize from other sources
- Use copyrighted material without permission
- Generate content for illegal purposes
- Misrepresent AI content as human-written

---

## 🧪 Testing Content Generation

### Unit Tests

```bash
pytest src/cofounder_agent/tests/test_seo_content_generator.py -v
```

### API Integration Tests

```bash
pytest src/cofounder_agent/tests/test_enhanced_content_routes.py -v
```

### Full System Test

```bash
# Start services
npm run dev:cofounder &

# Wait for startup
sleep 5

# Test content generation
curl -X POST http://localhost:8000/api/v1/content/enhanced/blog-posts/create-seo-optimized \
  -H "Content-Type: application/json" \
  -d '{"topic": "Test Topic", "style": "technical", "tone": "professional", "target_length": 1500}'

# Monitor task
curl http://localhost:8000/api/v1/content/enhanced/blog-posts/tasks/{task_id}
```

---

## 🔍 Troubleshooting

### Generation Takes Too Long

**Symptom:** Task stuck in "processing" for >2 minutes

**Solutions:**

1. Check Ollama is running: `ollama list`
2. Check model is loaded: `ollama ps`
3. Restart co-founder service: `npm run dev:cofounder`
4. Reduce target_length: try 800-1000 words

### Generated Content Quality Issues

**Symptom:** Poor SEO metadata or weak content

**Solutions:**

1. Try different style/tone combination
2. Provide more specific topic
3. Check model selection
4. Review implementation: [Model Selection Guide](./MODEL_SELECTION_GUIDE.md)

### API Returns 422 Validation Error

**Symptom:** `"Argument missing for parameter 'target_length'"`

**Solutions:**

1. Ensure all required fields: topic, style, tone, target_length
2. Check target_length is integer: 500-5000
3. Check style is valid: technical, narrative, listicle, educational, thought-leadership
4. Check tone is valid: professional, casual, academic, inspirational

### Featured Image Generation Fails

**Symptom:** `featured_image_url` is null/empty

**Solutions:**

1. Set `generate_images: true` in request
2. Verify image generation service is available
3. Check image model quota/limits
4. Review image generation logs

---

## 📚 See Also

- [Model Selection Guide](./MODEL_SELECTION_GUIDE.md) - Choose the right AI model
- [API Contract](../reference/API_CONTRACT_CONTENT_CREATION.md) - Full API specs
- [Architecture Overview](../02-ARCHITECTURE_AND_DESIGN.md) - System design
- [Testing Guide](./TESTING.md) - Test execution

---

## 🔄 Version History

| Date         | Version | Changes                               |
| ------------ | ------- | ------------------------------------- |
| Oct 22, 2025 | 1.0     | Initial consolidated guide            |
| Oct 22, 2025 | 1.0     | Merged 4 guides into single reference |

---

**Need more help?** See [Troubleshooting](./troubleshooting/) for common issues.
