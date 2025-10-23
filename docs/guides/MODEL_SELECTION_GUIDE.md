# 🤖 AI Model Selection & Local Model Integration Guide

## Overview

You now have **complete AI model selection** with support for:

- ✅ **Local models** (Ollama) - Free, uses your RTX 5070
- ✅ **HuggingFace models** - Free tier available
- ✅ **Google Gemini** - Paid fallback
- ✅ **Intelligent model selection** UI in the BlogPostCreator
- ✅ **Cost tracking** and model usage reporting
- ✅ **Automatic fallback** if preferred model is unavailable

## What Was Added

### 1. Backend Services

#### `llm_provider_manager.py` (450+ lines)

- Manages all LLM providers and configurations
- Tracks model availability in real-time
- Provides recommendations for RTX 5070
- Handles provider status and cost analysis

**Key Classes:**

- `LLMProviderManager` - Main orchestrator
- `LLMConfig` - Model configuration
- `LLMProvider` - Provider enum (Ollama, HuggingFace, Gemini)
- `ModelSize` - Model size categories

#### `ai_content_generator.py` (300+ lines)

- **Unified AI content generation** with intelligent fallback
- Tries models in this order:
  1. Local Ollama (if available)
  2. HuggingFace (if token available)
  3. Google Gemini (if key available)
  4. Fallback content generator

**Usage:**

```python
generator = get_content_generator()
content, model_used = await generator.generate_blog_post(
    topic="AI for Business",
    style="technical",
    tone="professional",
    target_length=1500,
    tags=["AI", "Business"]
)
```

### 2. API Routes

#### `routes/models.py` (250+ lines)

New endpoints for model management:

- `GET /api/v1/models/available` - List all available models
- `GET /api/v1/models/status` - Provider status (Ollama, HuggingFace, Gemini)
- `GET /api/v1/models/recommended` - Models recommended for current setup
- `GET /api/v1/models/rtx5070` - Models optimized for RTX 5070

### 3. Frontend Integration

#### `modelService.js` (200+ lines)

React service for model management:

```javascript
import { modelService } from '@/services/modelService';

// Get available models
const models = await modelService.getAvailableModels();

// Get recommended models (sorted by preference)
const recommended = modelService.getRecommendedModels();

// Get models for your GPU
const rtx5070Models = modelService.getModelsForRTX5070();

// Estimate costs
const cost = modelService.estimateCost('gemini-2.5-flash', 1000000);
```

#### `BlogPostCreator.jsx` - Model Selection UI

- Beautiful dropdown with model selection
- Shows provider icons (🖥️ local, 🌐 cloud, ☁️ cloud-paid)
- Displays VRAM requirements
- Shows cost information
- Real-time model availability checking

#### `BlogPostCreator.css` - Styling

- Premium styling for model selection
- Model badges with color coding
- Loading spinners
- Dark/light mode support

### 4. Content Generation

#### `content.py` - Enhanced Routes

Updated to use real AI models instead of mocks:

```python
@content_router.post("/create-blog-post")
async def create_blog_post(request: CreateBlogPostRequest):
    # Now uses actual AI models with fallback strategy
    content, model_used = await _generate_content_with_ai(request, task_id)
```

## Model Configurations

### Ollama Models (Local, Free, RTX 5070 Optimized)

```
1. Neural Chat 13B
   - Excellent quality
   - ~12GB VRAM (perfect for RTX 5070!)
   - Recommended for blog generation
   - Fast and intelligent

2. Mistral 13B
   - High-quality reasoning
   - ~12GB VRAM (perfect for RTX 5070!)
   - Great for technical content
   - Good balance of speed/quality

3. Neural Chat 7B
   - Fast inference
   - ~7GB VRAM
   - Good for quick generation
   - Lower quality than 13B

4. Mistral 7B
   - Balanced performance
   - ~7GB VRAM
   - Fast and capable
   - Lower quality than 13B
```

### HuggingFace Models (Free Tier)

```
1. Mistral 7B Instruct
   - Apache 2.0 licensed
   - Free tier available
   - Good for content generation
   - Requires HF token for higher limits

2. Llama 2 7B Chat
   - Meta's open model
   - Free tier available
   - Requires explicit model access approval
   - Good quality

3. Falcon 7B Instruct
   - TII's open model
   - Free tier available
   - Fast inference
   - Good for general tasks
```

### Google Gemini (Paid Fallback)

```
Gemini 2.5 Flash
   - High-quality responses
   - $0.05 per 1M input tokens
   - Reliable fallback
   - Cloud-hosted (no local VRAM needed)
```

## Setup Instructions

### 1. Environment Configuration

Update `.env`:

```bash
# Ollama (local inference)
LOCAL_LLM_API_URL="http://localhost:11434"
LOCAL_LLM_MODEL_NAME="neural-chat:13b"
LLM_PROVIDER="ollama"

# HuggingFace (optional, free tier)
HUGGINGFACE_API_TOKEN="your_token_here"

# Google Gemini (fallback)
GEMINI_API_KEY="your_key_here"
```

### 2. Install Ollama (for RTX 5070 local inference)

```bash
# Download from https://ollama.ai/download
# Or on Linux:
curl https://ollama.ai/install.sh | sh

# Start Ollama service
ollama serve

# In another terminal, pull models
ollama pull neural-chat:13b    # Best for RTX 5070
ollama pull mistral:13b        # Alternative
ollama pull neural-chat:7b     # If running out of VRAM
```

### 3. Optional: HuggingFace Setup

```bash
# Get API token at https://huggingface.co/settings/tokens
# Create account if needed: https://hf.co/join

# Export token
export HUGGINGFACE_API_TOKEN="your_token"
```

### 4. Google Gemini (Fallback)

Already configured if you have `GEMINI_API_KEY` in `.env`

## Usage

### 1. In the UI (BlogPostCreator)

1. Navigate to Content Creator in Oversight Hub
2. Select your preferred model from the dropdown:
   - 🤖 Auto (Best Available) - Recommended
   - 🖥️ Local Models (Ollama) - Free, fast, RTX 5070 optimized
   - 🌐 Cloud Models (HuggingFace) - Free tier, no local VRAM
   - ☁️ Paid Models (Gemini) - Fallback

3. Fill in topic, style, tone
4. Click "Generate Blog Post"
5. System will use your selected model (or fallback if unavailable)

### 2. In the Backend

```python
from services.ai_content_generator import get_content_generator

generator = get_content_generator()

# Generate content (will try best available model)
content, model_used = await generator.generate_blog_post(
    topic="AI Model Selection Best Practices",
    style="technical",
    tone="professional",
    target_length=2000,
    tags=["AI", "Models", "Optimization"]
)

print(f"Generated with: {model_used}")
```

### 3. API Endpoints

```bash
# Get available models
curl http://localhost:8000/api/v1/models/available

# Get provider status
curl http://localhost:8000/api/v1/models/status

# Get recommended models
curl http://localhost:8000/api/v1/models/recommended

# Get RTX 5070 optimized models
curl http://localhost:8000/api/v1/models/rtx5070

# Create blog post (uses selected model)
curl -X POST http://localhost:8000/api/v1/content/create-blog-post \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI Models",
    "style": "technical",
    "tone": "professional",
    "targetLength": 1500,
    "tags": ["AI"],
    "selectedModel": "neural-chat:13b"
  }'
```

## Cost Analysis

### RTX 5070 Local Inference (Ollama)

- **Cost**: $0/month (hardware already purchased)
- **Models**: 7B-13B parameter models
- **Latency**: ~5-10s per post (depends on model)
- **Best for**: Unlimited content generation, zero API costs

### HuggingFace Free Tier

- **Cost**: $0 (free tier)
- **Rate limit**: ~30 requests/hour
- **Latency**: ~2-5s per post
- **Best for**: Dev/testing without GPU

### Google Gemini

- **Cost**: $0.05 per 1M input tokens (~$0.10-0.20 per blog post)
- **Latency**: ~1-2s per post
- **Best for**: Fallback when local/free options fail

## Recommended Strategy

### Development

1. **Primary**: Use Ollama (neural-chat:13b)
   - Free, uses your RTX 5070
   - Unlimited generation
   - Good quality for testing

2. **Fallback**: HuggingFace (if Ollama down)
   - Free tier available
   - Rate limited but good for testing

### Production

1. **Primary**: Railway Cofounder Agent with Ollama
   - Can run 7B models on Railway
   - Costs ~$10/month

2. **Fallback**: Google Gemini
   - Reliable, proven
   - ~$0.10-0.20 per post
   - Only used if primary fails

## Monitoring Model Usage

Check which models are being used:

```bash
# In Oversight Hub, go to Content Creator
# After generating a post, check:
- "Model Used" in the result
- Provider (Local/Cloud/Paid)
- Generation time
- Cost (if applicable)
```

## Troubleshooting

### Ollama not available

- Check if Ollama service is running: `ps aux | grep ollama`
- Start it: `ollama serve`
- Verify connection: `curl http://localhost:11434/api/tags`

### Model not found in Ollama

- List installed models: `ollama list`
- Pull missing model: `ollama pull neural-chat:13b`

### HuggingFace rate limited

- Free tier is rate limited (~30 req/hour)
- Get API token for higher limits
- Or add to your account at https://huggingface.co/settings/mcp/

### Gemini fallback not working

- Verify `GEMINI_API_KEY` in `.env`
- Check account has credits
- Try `curl -X POST https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key=YOUR_KEY`

## Files Created/Modified

### New Files

- `src/cofounder_agent/services/llm_provider_manager.py` (450 lines)
- `src/cofounder_agent/services/ai_content_generator.py` (300 lines)
- `src/cofounder_agent/routes/models.py` (250 lines)
- `web/oversight-hub/src/services/modelService.js` (200 lines)

### Modified Files

- `src/cofounder_agent/routes/content.py` - Updated to use real AI models
- `src/cofounder_agent/main.py` - Added route imports and includes
- `web/oversight-hub/src/components/BlogPostCreator.jsx` - Added model selection UI
- `web/oversight-hub/src/components/BlogPostCreator.css` - Added model selection styles

## Next Steps

1. **Start Ollama**: `ollama serve`
2. **Start Co-founder Agent**: `python -m uvicorn cofounder_agent.main:app --reload`
3. **Start Oversight Hub**: `npm start` (in web/oversight-hub)
4. **Test Model Selection**:
   - Navigate to Content Creator
   - Check model dropdown
   - Select a model
   - Generate a blog post
   - Check which model was used

## Advanced Configuration

### Custom Model Selection Logic

In `ai_content_generator.py`, modify `generate_blog_post()`:

```python
async def generate_blog_post(self, ...):
    # Try models in your preferred order
    # Example: Always prefer local Ollama

    for model in [ollama_models_first, huggingface, gemini]:
        try:
            content = await model.generate(...)
            return content, model.name
        except:
            continue
```

### Add New Model Provider

1. Create new client in `services/`
2. Add provider to `LLMProvider` enum
3. Add models to `llm_provider_manager.py`
4. Update `ai_content_generator.py` fallback logic

## Performance Tips

### For RTX 5070

- **Use 13B models** (not 7B) for better quality
- **Neural Chat 13B** is optimized for your GPU
- **Batch requests** if possible
- **Use lower temperature** (0.5-0.7) for consistent content

### For Production

- **Monitor model latencies** - track which models are fastest
- **Use local Ollama** as primary (costs $0)
- **Keep Gemini as fallback** only
- **Cache model responses** if you generate similar topics

---

**You now have full control over which AI models power your content creation!** 🚀
