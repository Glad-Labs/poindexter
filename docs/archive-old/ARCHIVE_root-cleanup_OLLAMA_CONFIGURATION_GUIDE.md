# Ollama Configuration Guide - Default LLM Provider Setup

**Last Updated:** Today  
**Status:** ✅ Configured and Ready  
**Default LLM Provider:** Ollama (Free, Local, Zero-Cost)

---

## 🎯 Quick Summary

The system now uses **Ollama as the default LLM provider**. This means:

✅ **Zero API costs** - Ollama runs locally on your machine  
✅ **No rate limits** - Unlimited requests  
✅ **Full privacy** - No data leaves your machine  
✅ **Fast inference** - GPU-accelerated (CUDA/Metal)  
✅ **Per-task customization** - Override provider/model per blog post task

---

## 🔧 Configuration Overview

### 1. Default Provider (Ollama)

**File:** `.env.local`

```bash
LLM_PROVIDER=ollama                    # Default provider
USE_OLLAMA=true                        # Enable Ollama
OLLAMA_HOST=http://localhost:11434     # Ollama server URL
```

**File:** `src/agents/content_agent/config.py`

```python
self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # Defaults to ollama
```

### 2. Model Selection per Task Stage

**File:** `.env.local`

```bash
MODEL_FOR_RESEARCH=ollama/mistral      # Research: balanced quality/speed
MODEL_FOR_CREATIVE=ollama/mistral      # Creative writing: excellent quality
MODEL_FOR_QA=ollama/mistral            # Quality assurance: analytical
MODEL_FOR_IMAGE=ollama/mistral         # Image selection: understanding
MODEL_FOR_PUBLISHING=ollama/phi        # Publishing: fast formatting (2.7B)
```

**Available Ollama Models:**

| Model            | Size   | Speed        | Quality              | Best For                        |
| ---------------- | ------ | ------------ | -------------------- | ------------------------------- |
| `ollama/phi`     | 2.7B   | ⚡ Very Fast | ⭐⭐ Good            | Fast formatting, quick analysis |
| `ollama/mistral` | 7B     | 🔄 Balanced  | ⭐⭐⭐ Excellent     | General purpose, all tasks      |
| `ollama/llama2`  | 7B-13B | 🔄 Balanced  | ⭐⭐⭐ Excellent     | Research, creative writing      |
| `ollama/mixtral` | 8x7B   | 🐢 Slower    | ⭐⭐⭐⭐ Outstanding | Complex reasoning, QA           |

---

## 📝 Creating Blog Posts with Model Selection

### Option 1: Use Default Ollama Configuration

**Request (using default Ollama/mistral):**

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Advanced Python Decorators",
    "style": "technical",
    "tone": "professional",
    "target_length": 2000,
    "tags": ["python", "advanced"],
    "generate_featured_image": true
  }'
```

**Result:**

- Uses `ollama/mistral` for all stages (configured in .env.local)
- No additional cost
- Runs entirely on your local machine

---

### Option 2: Override Provider/Model per Task

**Request (using specific model):**

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Advanced Python Decorators",
    "style": "technical",
    "tone": "professional",
    "target_length": 2000,
    "llm_provider": "ollama",
    "model": "ollama/mixtral",
    "tags": ["python", "advanced"],
    "generate_featured_image": true
  }'
```

**Result:**

- Uses `ollama/mixtral` (more powerful, slower) for this task
- Better reasoning quality for complex topics
- Still zero cost, runs locally

---

### Option 3: Use Alternative Provider (OpenAI, Claude, etc.)

**If you have an OpenAI API key:**

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Advanced Python Decorators",
    "llm_provider": "openai",
    "model": "gpt-4",
    "style": "technical",
    "tone": "professional",
    "target_length": 2000,
    "tags": ["python", "advanced"],
    "generate_featured_image": true
  }'
```

**Result:**

- Uses OpenAI GPT-4 instead of local Ollama
- Requires OPENAI_API_KEY in environment
- Higher quality but costs money (~$0.03-$0.05 per request)

---

## 🚀 Ollama Setup (If Not Already Running)

### Prerequisites

1. **Install Ollama:** https://ollama.ai/
2. **Pull a model:**
   ```bash
   ollama pull mistral      # Recommended (7B, balanced)
   ollama pull phi          # Fast (2.7B)
   ollama pull mixtral      # Powerful (8x7B)
   ```

### Start Ollama Server

**Windows/macOS/Linux:**

```bash
ollama serve
```

This starts Ollama on `http://localhost:11434`

### Verify Ollama is Running

```bash
# Test Ollama connectivity
curl http://localhost:11434/api/tags

# Expected response (list of installed models):
# {"models":[{"name":"mistral:latest",...},{"name":"phi:latest",...}]}
```

---

## 🔄 How It Works: Task Processing with Model Selection

```
┌─────────────────────────────────────┐
│  Blog Post Request with Model Info  │
│  - topic: "Python Decorators"       │
│  - model: "ollama/mixtral" (optional)
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Create Task in Database            │
│  - Store model preference in metadata
│  - Store llm_provider override      │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Background Processing Starts       │
│  - ResearchAgent: model_for_research│
│  - CreativeAgent: model_for_creative│
│  - QAAgent: model_for_qa            │
│  - ImageAgent: model_for_image      │
│  - PublishingAgent: model_for_...   │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  LLM Client Routes to Model         │
│  1. Check task metadata for override│
│  2. If not specified, use config def│
│  3. Route to Ollama (local) or API  │
│  4. Call model.generate()           │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Agent Executes Stage               │
│  - Research: gather data            │
│  - Creative: write content          │
│  - QA: critique & feedback          │
│  - Image: select visual             │
│  - Publishing: format for CMS       │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Task Complete                      │
│  - Result stored in database        │
│  - Ready for review/publishing      │
└─────────────────────────────────────┘
```

---

## 📊 Configuration Files Changed

### 1. ✅ `src/agents/content_agent/config.py`

Changed default from `"gemini"` to `"ollama"`:

```python
# OLD:
self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

# NEW:
self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# ADDED: Per-task model configuration
self.MODEL_FOR_RESEARCH = os.getenv("MODEL_FOR_RESEARCH", "ollama/mistral")
self.MODEL_FOR_CREATIVE = os.getenv("MODEL_FOR_CREATIVE", "ollama/mistral")
self.MODEL_FOR_QA = os.getenv("MODEL_FOR_QA", "ollama/mistral")
self.MODEL_FOR_IMAGE = os.getenv("MODEL_FOR_IMAGE", "ollama/mistral")
self.MODEL_FOR_PUBLISHING = os.getenv("MODEL_FOR_PUBLISHING", "ollama/phi")
```

### 2. ✅ `.env.local`

Added explicit LLM configuration:

```bash
# NEW: Explicit provider selection
LLM_PROVIDER=ollama

# NEW: Per-stage model selection
MODEL_FOR_RESEARCH=ollama/mistral
MODEL_FOR_CREATIVE=ollama/mistral
MODEL_FOR_QA=ollama/mistral
MODEL_FOR_IMAGE=ollama/mistral
MODEL_FOR_PUBLISHING=ollama/phi
```

### 3. ✅ `src/cofounder_agent/routes/content_routes.py`

Added optional fields to `CreateBlogPostRequest`:

```python
# NEW: Optional LLM provider override per task
llm_provider: Optional[str] = Field(
    None,
    description="Optional: LLM provider override (ollama, openai, anthropic, gemini)",
    examples=["ollama", "openai", "anthropic"]
)

# NEW: Optional model override per task
model: Optional[str] = Field(
    None,
    description="Optional: Specific model to use (e.g., 'ollama/mistral', 'gpt-4')",
    examples=["ollama/mistral", "ollama/phi", "gpt-4", "claude-opus"]
)
```

**Updated task creation to store model preferences:**

```python
# Store in task metadata
"metadata": {
    "categories": request.categories or [],
    "publish_mode": request.publish_mode.value,
    "target_environment": request.target_environment,
    "llm_provider": request.llm_provider,      # NEW
    "model": request.model,                     # NEW
}
```

---

## ✅ Testing the Configuration

### Test 1: Create Blog Post with Default Ollama

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Testing Ollama Configuration",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500,
    "tags": ["testing", "ollama"]
  }'
```

**Expected Response:**

```json
{
  "task_id": "...",
  "task_type": "blog_post",
  "status": "pending",
  "topic": "Testing Ollama Configuration",
  "created_at": "...",
  "polling_url": "/api/content/tasks/..."
}
```

**Check Status:**

```bash
curl http://localhost:8000/api/content/tasks/{task_id}
```

Expected: Task runs through all stages (research → creative → qa → image → publishing)

---

### Test 2: Override Model per Task

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Advanced Topic for Mixtral",
    "style": "technical",
    "tone": "professional",
    "target_length": 2000,
    "llm_provider": "ollama",
    "model": "ollama/mixtral"
  }'
```

**Expected:** Uses mixtral (8x7B) for this task, all other tasks use default mistral

---

## 🎛️ Customization Options

### Change Default Model

**In `.env.local`:**

```bash
MODEL_FOR_RESEARCH=ollama/mixtral      # Use more powerful model by default
```

**Or programmatically:**

```bash
# At runtime
export MODEL_FOR_RESEARCH=ollama/mixtral
```

### Switch Entire System to OpenAI

**In `.env.local`:**

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
MODEL_FOR_RESEARCH=gpt-4
MODEL_FOR_CREATIVE=gpt-4
# etc.
```

### Mix Providers (Ollama for speed, GPT-4 for quality)

**In task request:**

```bash
# Use fast Ollama for most tasks
# But use GPT-4 for critical content

curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "Critical Topic",
    "llm_provider": "openai",
    "model": "gpt-4"
  }'
```

---

## 📋 Troubleshooting

### Problem: "Ollama not responding"

**Solution:**

```bash
# Start Ollama server
ollama serve

# Verify it's running
curl http://localhost:11434/api/tags
```

### Problem: "Model not found: ollama/mistral"

**Solution:**

```bash
# Pull the model
ollama pull mistral

# Verify it's installed
ollama list
```

### Problem: Task using wrong model

**Solution:** Check task metadata:

```bash
# View task details
curl http://localhost:8000/api/content/tasks/{task_id}

# Look for "model_used" or "llm_provider_used" in response
# Should show which model actually executed the task
```

### Problem: Slow task execution

**Solution:** Use faster model:

```bash
# Use phi (2.7B) for faster, lower-quality responses
curl -X POST http://localhost:8000/api/content/tasks \
  -d '{"model": "ollama/phi", ...}'

# Or use mistral for balanced speed/quality
# Or use mixtral for best quality (slowest)
```

---

## 📈 Performance Expectations

### Task Execution Times (on typical hardware)

| Model          | Size | Task Duration | Quality    | Cost   |
| -------------- | ---- | ------------- | ---------- | ------ |
| ollama/phi     | 2.7B | 3-5 min       | ⭐⭐       | FREE   |
| ollama/mistral | 7B   | 5-8 min       | ⭐⭐⭐     | FREE   |
| ollama/mixtral | 8x7B | 10-15 min     | ⭐⭐⭐⭐   | FREE   |
| GPT-4          | -    | 2-4 min       | ⭐⭐⭐⭐⭐ | ~$0.05 |
| Claude 3 Opus  | -    | 2-4 min       | ⭐⭐⭐⭐⭐ | ~$0.03 |

---

## 🔗 Next Steps

1. **Verify Ollama is running:** `ollama serve` in terminal
2. **Verify models installed:** `ollama list`
3. **Create first blog post:** Use curl request above
4. **Monitor task:** Poll `/api/content/tasks/{task_id}` for status
5. **Adjust models:** Override per-task with `model` and `llm_provider` fields

---

## 📚 Related Documentation

- **Main Setup Guide:** `docs/01-SETUP_AND_OVERVIEW.md`
- **Architecture:** `docs/02-ARCHITECTURE_AND_DESIGN.md`
- **AI Agents:** `docs/05-AI_AGENTS_AND_INTEGRATION.md`
- **Ollama:** https://ollama.ai/

---

**Status:** ✅ **Fully Configured and Ready to Use**

You now have:

- ✅ Ollama as the default LLM provider (zero cost)
- ✅ Per-task model selection capability
- ✅ Flexible provider switching (Ollama, OpenAI, Claude, Gemini)
- ✅ Environment-based configuration
- ✅ Self-critiquing content generation pipeline

**Start creating amazing content for free!** 🚀
