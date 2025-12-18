# Quick Reference: Ollama Configuration & Per-Task Model Selection

## ⚡ TL;DR

✅ **Ollama is now the default** (free, local, zero-cost)  
✅ **Per-task model override supported** (specify model when creating blog post)  
✅ **Configuration via environment variables** (.env.local)  
✅ **API fields added** (llm_provider, model)

---

## 🎯 Use Cases

### Case 1: Free Blog Posts (Recommended)

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Your Topic",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500
  }'
```

✅ Uses default Ollama/mistral (free, local)

---

### Case 2: Fast Blog Posts

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Your Topic",
    "model": "ollama/phi",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500
  }'
```

✅ Uses fastest model (2.7B, still free)

---

### Case 3: High-Quality Blog Posts

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Your Topic",
    "model": "ollama/mixtral",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500
  }'
```

✅ Uses most powerful model (8x7B, still free but slower)

---

### Case 4: Premium Content (Using GPT-4)

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Your Topic",
    "llm_provider": "openai",
    "model": "gpt-4",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500
  }'
```

✅ Uses GPT-4 (requires OPENAI_API_KEY, costs ~$0.05)

---

## 📊 Available Models

| Model          | Size | Speed  | Quality    | Cost   | Where         |
| -------------- | ---- | ------ | ---------- | ------ | ------------- |
| ollama/phi     | 2.7B | ⚡⚡⚡ | ⭐⭐       | FREE   | Local         |
| ollama/mistral | 7B   | ⚡⚡   | ⭐⭐⭐     | FREE   | Local         |
| ollama/mixtral | 8x7B | ⚡     | ⭐⭐⭐⭐   | FREE   | Local         |
| gpt-4          | -    | ⚡⚡   | ⭐⭐⭐⭐⭐ | ~$0.05 | OpenAI API    |
| claude-opus    | -    | ⚡⚡   | ⭐⭐⭐⭐⭐ | ~$0.03 | Anthropic API |
| gemini-pro     | -    | ⚡⚡   | ⭐⭐⭐⭐   | FREE+  | Google API    |

---

## 🔧 Configuration Files

### .env.local

```bash
# LLM Provider
LLM_PROVIDER=ollama

# Per-Stage Models
MODEL_FOR_RESEARCH=ollama/mistral
MODEL_FOR_CREATIVE=ollama/mistral
MODEL_FOR_QA=ollama/mistral
MODEL_FOR_IMAGE=ollama/mistral
MODEL_FOR_PUBLISHING=ollama/phi
```

### API Request (CreateBlogPostRequest)

```python
# Optional fields - leave blank to use defaults
llm_provider: Optional[str] = None  # Override provider
model: Optional[str] = None         # Override model
```

---

## 💡 Recommended Configurations

### Configuration A: Speed (Default - Mistral)

```bash
MODEL_FOR_RESEARCH=ollama/mistral      # Balanced
MODEL_FOR_CREATIVE=ollama/mistral      # Good writing
MODEL_FOR_QA=ollama/mistral            # Good analysis
MODEL_FOR_IMAGE=ollama/mistral         # Understanding
MODEL_FOR_PUBLISHING=ollama/phi        # Fast formatting
```

**Time:** 5-8 minutes per post | **Cost:** FREE

---

### Configuration B: Quality (Mixtral)

```bash
MODEL_FOR_RESEARCH=ollama/mixtral      # Excellent research
MODEL_FOR_CREATIVE=ollama/mixtral      # Outstanding writing
MODEL_FOR_QA=ollama/mixtral            # Deep analysis
MODEL_FOR_IMAGE=ollama/mistral         # Fast image selection
MODEL_FOR_PUBLISHING=ollama/phi        # Fast formatting
```

**Time:** 10-15 minutes per post | **Cost:** FREE

---

### Configuration C: Cost-Optimized (Phi + Mistral)

```bash
MODEL_FOR_RESEARCH=ollama/phi          # Fast but ok
MODEL_FOR_CREATIVE=ollama/mistral      # Good writing
MODEL_FOR_QA=ollama/phi                # Quick check
MODEL_FOR_IMAGE=ollama/phi             # Fast selection
MODEL_FOR_PUBLISHING=ollama/phi        # Super fast
```

**Time:** 3-5 minutes per post | **Cost:** FREE

---

## 🚀 Getting Started

### Step 1: Start Ollama (if not already running)

```bash
ollama serve
```

### Step 2: Pull Required Models

```bash
ollama pull phi      # Fast model (2.7B)
ollama pull mistral  # Balanced model (7B)
ollama pull mixtral  # Powerful model (8x7B)
```

### Step 3: Create Your First Blog Post

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Getting Started with Machine Learning",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500
  }'
```

### Step 4: Check Status

```bash
# Replace {task_id} with the ID from Step 3
curl http://localhost:8000/api/content/tasks/{task_id}
```

---

## 🎨 JSON Request Examples

### Minimal (Uses All Defaults)

```json
{
  "task_type": "blog_post",
  "topic": "Minimal Example"
}
```

### Standard (Recommended)

```json
{
  "task_type": "blog_post",
  "topic": "Python Best Practices",
  "style": "technical",
  "tone": "professional",
  "target_length": 1500,
  "tags": ["python", "best-practices"],
  "generate_featured_image": true
}
```

### With Model Override

```json
{
  "task_type": "blog_post",
  "topic": "Advanced Topic",
  "style": "technical",
  "tone": "professional",
  "target_length": 2000,
  "tags": ["advanced"],
  "generate_featured_image": true,
  "llm_provider": "ollama",
  "model": "ollama/mixtral"
}
```

### With Premium Provider

```json
{
  "task_type": "blog_post",
  "topic": "Critical Content",
  "style": "technical",
  "tone": "professional",
  "target_length": 2000,
  "llm_provider": "openai",
  "model": "gpt-4"
}
```

---

## 🔍 PowerShell/Bash Examples

### Create Task (PowerShell)

```powershell
$body = @{
    task_type = "blog_post"
    topic = "PowerShell Automation"
    style = "technical"
    tone = "professional"
    target_length = 1500
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/content/tasks" `
  -Method Post `
  -Headers @{"Content-Type"="application/json"} `
  -Body $body
```

### Create Task (Bash)

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{"task_type":"blog_post","topic":"Bash Automation","style":"technical"}'
```

### Check Status (PowerShell)

```powershell
$taskId = "your-task-id-here"
Invoke-RestMethod -Uri "http://localhost:8000/api/content/tasks/$taskId"
```

### Check Status (Bash)

```bash
curl http://localhost:8000/api/content/tasks/your-task-id-here
```

---

## 📈 Performance Guide

| Want        | Model          | Expected Time | Cost    |
| ----------- | -------------- | ------------- | ------- |
| **Speed**   | ollama/phi     | 3-5 min       | FREE ✅ |
| **Balance** | ollama/mistral | 5-8 min       | FREE ✅ |
| **Quality** | ollama/mixtral | 10-15 min     | FREE ✅ |
| **Premium** | gpt-4          | 2-4 min       | ~$0.05  |
| **Best**    | claude-opus    | 2-4 min       | ~$0.03  |

---

## ✅ Verification

### Check Configuration

```bash
# View current LLM_PROVIDER
grep LLM_PROVIDER .env.local

# View all model settings
grep MODEL_FOR .env.local

# Test Ollama connectivity
curl http://localhost:11434/api/tags
```

### Create Test Task

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{"task_type":"blog_post","topic":"Test"}'
```

### Expected Response

```json
{
  "task_id": "uuid-here",
  "task_type": "blog_post",
  "status": "pending",
  "topic": "Test",
  "created_at": "2025-12-05T...",
  "polling_url": "/api/content/tasks/uuid-here"
}
```

---

## 🆘 Troubleshooting

### "Ollama not responding"

```bash
# Start Ollama
ollama serve

# Verify
curl http://localhost:11434/api/tags
```

### "Model not found"

```bash
# Pull the model
ollama pull mistral

# List available
ollama list
```

### "Task failed"

- Check logs: `curl http://localhost:8000/api/content/tasks/{id}`
- Verify environment: `grep LLM_PROVIDER .env.local`
- Verify backend: `curl http://localhost:8000/api/health`

---

## 📚 Full Documentation

- **Detailed Guide:** `OLLAMA_CONFIGURATION_GUIDE.md`
- **Implementation Details:** `OLLAMA_IMPLEMENTATION_COMPLETE.md`
- **Setup:** `docs/01-SETUP_AND_OVERVIEW.md`
- **Architecture:** `docs/02-ARCHITECTURE_AND_DESIGN.md`

---

**Status:** ✅ Ready to Use | Default: Ollama | Per-Task: Supported | Cost: FREE (Local) or Custom (API)
