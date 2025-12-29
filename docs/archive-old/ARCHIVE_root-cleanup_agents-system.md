# 🧠 Agent System Integration Guide

**Last Updated:** October 26, 2025  
**Status:** ✅ Production Ready | Fully Integrated  
**Architecture:** Self-Critiquing Multi-Agent Pipeline

---

## 📋 Overview

The Glad Labs agent system is an **active, production-ready** component orchestrating sophisticated AI-powered business operations through a self-critiquing pipeline architecture. This guide explains how agents integrate with the Co-Founder orchestrator and Strapi CMS.

**Key Points:**

- ✅ **Active System:** Not legacy code - fully integrated and in active use
- ✅ **Self-Critiquing:** Agents evaluate each other's work and provide feedback
- ✅ **Modular Design:** Use entire pipeline OR individual agents independently
- ✅ **Multi-Provider:** Automatic model fallback (Ollama → Claude → GPT → Gemini)
- ✅ **Production Ready:** 50+ tests passing, comprehensive error handling

---

## 🏗️ Architecture Overview

### Six-Agent Content Creation Pipeline

```text
┌─────────────────────────────────────────────────┐
│         Content Generation Request               │
│      (topic, style, length, parameters)         │
└──────────────────┬──────────────────────────────┘
                   │
        ┌──────────▼────────────┐
        │  1️⃣ RESEARCH AGENT    │
        │  ─────────────────    │
        │ Gathers information   │
        │ Identifies key points │
        │ Collects sources      │
        └──────────┬────────────┘
                   │
        ┌──────────▼─────────────────┐
        │  2️⃣ CREATIVE AGENT        │
        │  ──────────────────────    │
        │ Generates initial draft    │
        │ Applies brand voice        │
        │ Creates outline + body     │
        └──────────┬─────────────────┘
                   │
        ┌──────────▼──────────────────────┐
        │  3️⃣ QA AGENT (CRITIQUE LOOP)  │
        │  ─────────────────────────────  │
        │ Evaluates quality               │
        │ Provides specific feedback      │
        │ Suggests improvements           │
        └──────────┬──────────────────────┘
                   │
        ┌──────────▼──────────────────────┐
        │  🔄 CREATIVE AGENT (REFINEMENT) │
        │  ────────────────────────────   │
        │ Incorporates QA feedback        │
        │ Improves weak sections          │
        │ Maintains voice consistency     │
        └──────────┬──────────────────────┘
                   │
        ┌──────────▼───────────────┐
        │  4️⃣ IMAGE AGENT          │
        │  ─────────────────────   │
        │ Selects visual assets     │
        │ Optimizes for web         │
        │ Adds alt text & metadata  │
        └──────────┬───────────────┘
                   │
        ┌──────────▼──────────────────┐
        │  5️⃣ PUBLISHING AGENT       │
        │  ──────────────────────    │
        │ Formats for Strapi CMS     │
        │ Adds SEO metadata          │
        │ Creates structured data    │
        └──────────┬──────────────────┘
                   │
        ┌──────────▼──────────────────┐
        │  📤 PUBLISH TO STRAPI CMS   │
        │  ────────────────────────   │
        │ Content live & accessible  │
        │ Stored in database         │
        │ Ready for frontend display │
        └──────────────────────────────┘
```

### Model Router with Ollama Prioritization

```text
Request to LLM
     │
     ▼
┌─────────────────────────────────────────────┐
│     MODEL ROUTER - Fallback Chain            │
├─────────────────────────────────────────────┤
│  🥇 Try: Ollama (Local)                     │
│     ├─ Zero cost ✅                         │
│     ├─ Instant response ✅                  │
│     ├─ No rate limits ✅                    │
│     ├─ Full privacy ✅                      │
│     └─ GPU acceleration ✅                  │
│         ❌ Not available?                   │
│              ↓                              │
│  🥈 Try: Claude 3 Opus (Anthropic)         │
│     ├─ Best for creative writing ✅        │
│     ├─ Superior reasoning ✅                │
│     └─ Full context awareness ✅           │
│         ❌ Quota exceeded?                  │
│              ↓                              │
│  🥉 Try: GPT-4 (OpenAI)                    │
│     ├─ Fast & reliable ✅                  │
│     ├─ Great for analysis ✅               │
│     └─ Good cost/quality balance ✅        │
│         ❌ Rate limited?                    │
│              ↓                              │
│  4️⃣ Try: Gemini Pro (Google)               │
│     ├─ Lower cost ✅                       │
│     ├─ Good availability ✅                │
│     └─ Fast responses ✅                   │
│         ❌ Still failing?                   │
│              ↓                              │
│  ⚠️ Use: Fallback Model                    │
│     └─ Ensures system availability ✅      │
└─────────────────────────────────────────────┘
```

---

## 🚀 Using the Agent System

### Option 1: End-to-End Blog Post Generation

Use the complete 6-agent pipeline for publication-ready content:

```bash
POST /api/content/generate-blog-post
{
  "topic": "AI in Healthcare",
  "style": "professional",
  "length": "2000 words",
  "include_images": true,
  "seo_keywords": ["AI", "healthcare", "medical technology"]
}
```

**Flow:**

1. Research Agent gathers industry data
2. Creative Agent writes initial draft
3. QA Agent evaluates and provides feedback
4. Creative Agent refines based on QA feedback
5. Image Agent selects relevant medical imagery
6. Publishing Agent formats for Strapi
7. Content automatically published to CMS

**Response:**

```json
{
  "status": "published",
  "post_id": "abc123",
  "title": "The Future of AI in Healthcare",
  "url": "https://strapi.example.com/posts/ai-healthcare",
  "execution_time": "45 seconds",
  "agents_used": ["research", "creative", "qa", "image", "publishing"],
  "feedback_loops": 2
}
```

### Option 2: Individual Agent Access

Use specific agents for targeted tasks:

```bash
# Research only
POST /api/agents/research/execute
{
  "task": "research",
  "topic": "Latest AI trends 2025",
  "depth": "comprehensive"
}

# Creative writing only
POST /api/agents/creative/execute
{
  "task": "write",
  "topic": "AI trends",
  "style": "technical",
  "word_count": 2000
}

# QA & Critique only
POST /api/agents/qa/execute
{
  "task": "critique",
  "content": "Blog post content...",
  "criteria": ["clarity", "accuracy", "engagement"]
}
```

### Option 3: Custom Workflows

Combine agents in any order:

```python
# Custom workflow in Python
from src.cofounder_agent.multi_agent_orchestrator import AgentOrchestrator

orchestrator = AgentOrchestrator()

# Step 1: Research
research_data = await orchestrator.agents['research'].execute({
    "topic": "Market analysis",
    "scope": "comprehensive"
})

# Step 2: Custom processing
processed_data = process_research(research_data)

# Step 3: Multiple agents in parallel
results = await asyncio.gather(
    orchestrator.agents['creative'].execute({
        "research": processed_data,
        "topic": "Market analysis"
    }),
    orchestrator.agents['financial'].execute({
        "data": processed_data,
        "calculate": "ROI"
    })
)

# Step 4: Publish
await orchestrator.agents['publishing'].execute({
    "content": results[0],
    "metadata": results[1]
})
```

---

## 🔗 Integration Points

### With Co-Founder Orchestrator (src/cofounder_agent/main.py)

The Co-Founder Agent acts as the central coordinator:

```python
# main.py - FastAPI routes
@app.post("/api/content/generate-blog-post")
async def generate_blog_post(request: BlogPostRequest):
    # Orchestrator handles routing to agents
    return await orchestrator.execute_agent_pipeline(request)

# orchestrator_logic.py - Agent coordination
class Orchestrator:
    async def execute_agent_pipeline(self, request):
        # 1. Route to content pipeline
        # 2. Execute agents in sequence with feedback loops
        # 3. Aggregate results
        # 4. Return to API
```

### With Strapi CMS (cms/strapi-main/)

Publishing Agent formats content for Strapi:

```python
# Publishing Agent formats for Strapi
class PublishingAgent(BaseAgent):
    async def format_for_strapi(self, content):
        """Convert to Strapi collection format"""
        return {
            "title": content["title"],
            "slug": generate_slug(content["title"]),
            "content": content["body"],
            "excerpt": content["summary"],
            "category": content["category_id"],
            "status": "published",
            "seo_title": content["seo_title"],
            "seo_description": content["seo_description"],
            "featured_image": content["image_id"],
            "published_at": datetime.now().isoformat()
        }
```

Then published to Strapi:

```python
# Publish to Strapi CMS
strapi_response = await strapi_client.create_post(
    formatted_content
)
# Content now live on public site
```

### With Oversight Hub (web/oversight-hub/)

Monitor agent execution in real-time:

```javascript
// Dashboard shows agent status
const { tasks, setTasks } = useStore();

// Poll agent status
useEffect(() => {
  const interval = setInterval(async () => {
    const status = await fetch('/api/agents/status').then((r) => r.json());
    setAgentStatus(status);
  }, 2000);

  return () => clearInterval(interval);
}, []);

// Display in UI
<AgentMonitorPanel agents={agentStatus} />;
```

---

## 📊 Agent Capabilities Matrix

| Agent          | Blog Posts | Research | QA/Critique | Image Selection | Publishing | Financial | Market Analysis |
| -------------- | ---------- | -------- | ----------- | --------------- | ---------- | --------- | --------------- |
| **Research**   | ⚠️         | ✅       | ❌          | ❌              | ❌         | ❌        | ✅              |
| **Creative**   | ✅         | ❌       | ❌          | ❌              | ❌         | ❌        | ⚠️              |
| **QA**         | ❌         | ❌       | ✅          | ❌              | ❌         | ❌        | ❌              |
| **Image**      | ❌         | ❌       | ❌          | ✅              | ❌         | ❌        | ❌              |
| **Publishing** | ❌         | ❌       | ❌          | ❌              | ✅         | ❌        | ❌              |
| **Financial**  | ❌         | ⚠️       | ❌          | ❌              | ⚠️         | ✅        | ⚠️              |
| **Market**     | ⚠️         | ✅       | ⚠️          | ❌              | ❌         | ⚠️        | ✅              |
| **Compliance** | ⚠️         | ✅       | ⚠️          | ❌              | ⚠️         | ❌        | ❌              |

**Legend:**

- ✅ Primary capability
- ⚠️ Secondary or supporting capability
- ❌ Not applicable

---

## ⚙️ Configuration

### Per-Agent Model Selection

```python
# src/cofounder_agent/services/model_router.py
AGENT_MODEL_CONFIG = {
    "research": {
        "primary": "ollama:mistral",  # Fast for research
        "fallback": ["gpt-4", "claude-opus"],
        "temperature": 0.3,  # Factual
        "max_tokens": 2000
    },
    "creative": {
        "primary": "ollama:llama3.2",  # Good writer
        "fallback": ["claude-opus", "gpt-4"],
        "temperature": 0.7,  # Creative
        "max_tokens": 3000
    },
    "qa": {
        "primary": "ollama:mistral",  # Good evaluator
        "fallback": ["gpt-4", "claude-opus"],
        "temperature": 0.2,  # Precise
        "max_tokens": 1000
    },
}
```

### Environment Variables

```bash
# Required - choose at least ONE
OPENAI_API_KEY=sk-...              # OpenAI models
ANTHROPIC_API_KEY=sk-ant-...       # Claude models
GOOGLE_API_KEY=AIza-...            # Gemini models

# Optional - prioritized if set
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODELS=mistral,llama3.2,phi  # Available models

# Database
DATABASE_URL=postgresql://user:pass@localhost/dbname

# Logging
LOG_LEVEL=INFO
DEBUG=False
```

---

## 🧪 Testing Agent System

### Run Agent Tests

```bash
cd src/cofounder_agent

# All agent tests
pytest tests/test_orchestrator.py -v

# Content pipeline tests
pytest tests/test_e2e_comprehensive.py -v

# Quick smoke tests
pytest tests/test_e2e_fixed.py -v

# With coverage
pytest tests/ --cov=. --cov-report=html
```

### Manual Testing

```bash
# Start system
npm run dev:cofounder

# Test health
curl http://localhost:8000/api/health

# Test agents
curl http://localhost:8000/api/agents/status

# Test model router
curl http://localhost:8000/api/models/test-all

# Generate sample content
curl -X POST http://localhost:8000/api/content/generate-blog-post \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Test Topic",
    "style": "professional",
    "length": "500 words"
  }'
```

---

## 📈 Performance & Monitoring

### Key Metrics

| Metric             | Target | Monitor           |
| ------------------ | ------ | ----------------- |
| Research Time      | <30s   | Agent logs        |
| Creative Writing   | <60s   | Agent logs        |
| QA Evaluation      | <15s   | Agent logs        |
| Image Selection    | <10s   | Agent logs        |
| Publishing         | <5s    | Agent logs        |
| **Total Pipeline** | <2 min | `/api/tasks/{id}` |

### Monitoring Commands

```bash
# Agent status
curl http://localhost:8000/api/agents/status

# View agent logs
curl http://localhost:8000/api/agents/logs?agent=research&level=error

# Memory usage
curl http://localhost:8000/api/memory/stats

# Model router status
curl http://localhost:8000/api/models/status
```

---

## 🐛 Troubleshooting

### Agent Not Responding

**Symptom:** "Agent timeout" or "Connection refused"

**Solution:**

```bash
# 1. Check agent is running
curl http://localhost:8000/api/agents/status

# 2. Check logs
curl http://localhost:8000/api/agents/logs?level=error&limit=20

# 3. Restart orchestrator
npm run dev:cofounder
```

### Model Router Exhausted

**Symptom:** "All models in fallback chain failed"

**Solution:**

```bash
# 1. Check which models are available
curl http://localhost:8000/api/models/test-all

# 2. Check Ollama specifically
curl http://localhost:11434/api/tags

# 3. If no local model, start Ollama
ollama serve

# 4. Pull a model
ollama pull mistral
```

### QA Agent Stuck in Loop

**Symptom:** "QA rejection rate >30%" or feedback loops >3

**Solution:**

```bash
# 1. Check agent logs
curl http://localhost:8000/api/agents/logs?agent=qa&limit=50

# 2. Monitor feedback loops
curl http://localhost:8000/api/tasks/{task_id}

# 3. Adjust QA criteria or thresholds
# Edit MODEL_CONFIG in src/cofounder_agent/services/model_router.py
```

---

## 📚 Related Documentation

- **[Architecture & Design](../02-ARCHITECTURE_AND_DESIGN.md)** - Full system design
- **[AI Agents & Integration](../05-AI_AGENTS_AND_INTEGRATION.md)** - Agent details
- **[Co-Founder Agent README](../../src/cofounder_agent/README.md)** - API reference
- **[Content Agent README](../../src/agents/content_agent/README.md)** - Agent implementation
- **[Deployment Guide](../03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Production setup
- **[Testing Guide](../reference/TESTING.md)** - Comprehensive testing

---

## ✅ Integration Checklist

- [ ] Ollama installed and running (or API keys configured)
- [ ] PostgreSQL database connected
- [ ] Co-Founder Agent running (`npm run dev:cofounder`)
- [ ] Strapi CMS running and connected
- [ ] Oversight Hub dashboard accessible
- [ ] Agent health checks passing
- [ ] Model router fallback chain verified
- [ ] Content pipeline end-to-end test successful
- [ ] Monitoring alerts configured
- [ ] Logs accessible and configured

---

**Maintained by:** Glad Labs Development Team  
**Last Updated:** October 26, 2025  
**Status:** ✅ Production Ready | Fully Integrated | Self-Critiquing Pipeline
