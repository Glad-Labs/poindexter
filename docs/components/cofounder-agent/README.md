# 🤖 Co-Founder Agent (FastAPI + Python)

> Intelligent multi-agent orchestrator for business AI operations

## 📍 Location

- **Source**: `src/cofounder_agent/`
- **Main Entry**: `src/cofounder_agent/README.md` (component-level)
- **Component Docs**: This folder (`docs/components/cofounder-agent/`)

---

## 📚 Documentation

### Quick Links

- **Parent Documentation**: See [05-AI_AGENTS_AND_INTEGRATION.md](../../05-AI_AGENTS_AND_INTEGRATION.md) for overall agent architecture
- **Troubleshooting**: See [troubleshooting/](./troubleshooting/) for common issues
- **Source Code**: See `src/cofounder_agent/README.md` for detailed implementation

---

## 🎯 Key Features

- **Multi-Agent Orchestration** - Coordinates multiple specialized AI agents
- **FastAPI Server** - RESTful API for frontend integration
- **Model Routing** - Intelligent cost-optimized model selection
- **Memory System** - Persistent conversation context and learning
- **Business Intelligence** - Collects and analyzes operational metrics
- **MCP Integration** - Model Context Protocol for advanced capabilities

---

## 🏗️ Agent Architecture

### Core Components

1. **Co-Founder Agent** (Main Orchestrator)
   - Route requests to specialized agents
   - Maintain business context
   - Provide strategic insights

2. **Specialized Agents**
   - **Content Agent** - Content generation and refinement
   - **Compliance Agent** - Regulatory and legal compliance checks
   - **Financial Agent** - Financial analysis and forecasting
   - **Market Insight Agent** - Market analysis and trends

### Communication Pattern

```
Frontend Request
    ↓
FastAPI Server (main.py)
    ↓
Orchestrator (orchestrator_logic.py)
    ↓
Model Router (services/model_router.py)
    ↓
Selected AI Provider (OpenAI, Gemini, Ollama)
    ↓
Response → Frontend
```

---

## 📂 Folder Structure

```
src/cofounder_agent/
├── README.md                    ← Component README
├── main.py                      ← FastAPI application
├── orchestrator_logic.py        ← Agent routing logic
├── intelligent_cofounder.py     ← Main co-founder implementation
├── memory_system.py             ← Conversation memory
├── business_intelligence.py     ← BI data collection
├── multi_agent_orchestrator.py ← Multi-agent coordination
├── mcp_integration.py           ← MCP server integration
├── requirements.txt             ← Python dependencies
├── services/
│   ├── model_router.py         ← Model selection logic
│   ├── ollama_client.py        ← Local Ollama integration
│   ├── gemini_client.py        ← Google Gemini integration
│   ├── firestore_client.py     ← Firestore integration
│   ├── performance_monitor.py  ← Performance tracking
│   └── intervention_handler.py ← Intervention responses
├── ai_memory_system/            ← Memory storage
├── business_intelligence_data/  ← BI data cache
└── tests/
    ├── test_main_endpoints.py  ← API endpoint tests
    ├── conftest.py             ← Test fixtures
    └── [other test files]
```

---

## 🔗 Integration Points

### AI Model Providers

Configured in `.env`:

```
OPENAI_API_KEY=<key>           # OpenAI GPT models
ANTHROPIC_API_KEY=<key>        # Anthropic Claude
GOOGLE_AI_API_KEY=<key>        # Google Gemini
OLLAMA_BASE_URL=http://localhost:11434  # Local Ollama
```

### Firebase Integration

- **Firestore**: Store conversation history and context
- **Pub/Sub**: Real-time event messaging
- **Cloud Functions**: Trigger interventions

### Firestore Collections

```
cofounder_context/
  - user_context (business info, preferences)
  - conversation_history (chats and interactions)
  - business_metrics (revenue, costs, KPIs)
  - agent_state (current agent modes and settings)
```

---

## 🧪 Testing

```bash
# Run all tests
cd src/cofounder_agent
pytest tests/

# Run specific test file
pytest tests/test_main_endpoints.py

# Run with coverage
pytest tests/ --cov=.

# Run with output
pytest tests/ -v
```

**Test Files:**

- `test_main_endpoints.py` - 60+ API endpoint tests
- `test_unit_comprehensive.py` - Unit tests for services
- `test_e2e_comprehensive.py` - End-to-end integration tests

---

## 🚀 Development Workflow

### Local Development

```bash
# Install dependencies
cd src/cofounder_agent
pip install -r requirements.txt

# Start the server
python -m uvicorn main:app --reload

# API Documentation
# Open: http://localhost:8000/docs
```

### API Endpoints

**Base URL**: `http://localhost:8000`

Available endpoints:

- `GET /docs` - API documentation (Swagger UI)
- `POST /process` - Process user input
- `POST /chat` - Chat with co-founder
- `GET /context` - Get current business context
- `POST /context` - Update business context
- `GET /memory` - Get conversation history
- See Swagger UI for complete API spec

---

## 🔑 Environment Variables

Required in `.env`:

```bash
# API Configuration
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_AI_API_KEY=...

# Local LLM
OLLAMA_BASE_URL=http://localhost:11434

# Firebase
FIREBASE_PROJECT_ID=glad-labs-xxx
FIREBASE_CREDENTIALS_PATH=./credentials.json

# Pub/Sub
PUBSUB_TOPIC=cofounder-events

# Agent Configuration
DEFAULT_AGENT=cofounder
MODEL_COST_LIMIT=0.10
ENABLE_MEMORY_SYSTEM=true
```

---

## 📊 Business Intelligence

The BI system automatically collects:

- **CMS Metrics** - Content views, engagement
- **AI Usage** - Model calls, costs, token usage
- **System Health** - Uptime, error rates, performance
- **Financial Data** - Revenue, costs, profitability
- **User Behavior** - Tasks completed, interactions

Data stored in `business_intelligence_data/` and Firestore.

---

## 🔄 Docker Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0"]
```

---

## 📋 Related Documentation

**In this component docs:**

- Intelligence features: See `INTELLIGENT_COFOUNDER.md` (this folder)
- Setup: See `README.md` in `src/cofounder_agent/`

**In main docs hub:**

- Agent Architecture: `docs/02-Architecture/Multi-Agent-Pipeline.md`
- Model Selection: `docs/guides/MODEL_SELECTION_GUIDE.md` (if exists)
- Testing: `docs/guides/PYTHON_TESTS_SETUP.md`
- Deployment: `docs/05-Operations/Operations-Maintenance.md`

---

## ✅ Quick Links

- **Development**: Local setup in `src/cofounder_agent/README.md`
- **Intelligence**: `INTELLIGENT_COFOUNDER.md`
- **Architecture**: `docs/02-Architecture/Multi-Agent-Pipeline.md`
- **Testing**: `docs/guides/PYTHON_TESTS_SETUP.md`
- **Deployment**: `docs/05-Operations/Operations-Maintenance.md`
