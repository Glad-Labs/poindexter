# 04 - Model Router & MCP

**Last Updated:** February 10, 2026  
**Version:** 1.0.0  
**Status:** ✅ Active | Multi-Provider | Cost-Optimized

---

## 🏗️ Model Routing Architecture

Glad Labs uses an intelligent fallback routing system to ensure high availability and cost efficiency. All LLM calls pass through the `ModelRouter` service.

**Location:** `src/cofounder_agent/services/model_router.py`

### Provider Priority

The system attempts providers in the following order (if keys are available in `.env.local`):

1. **Ollama** (Local, Zero-Cost, Highest Speed)
2. **Anthropic** (Claude 3.5 Sonnet / Opus)
3. **OpenAI** (GPT-4o / GPT-4 Turbo)
4. **Google** (Gemini 1.5 Pro)

### Cost-Tier Execution Logic

Instead of hardcoding model names, the system uses **Cost Tiers** defined in `MCPContentOrchestrator`:

| Tier | Primary Model | Usage Case |
| :--- | :--- | :--- |
| **Ultra Cheap** | Ollama (Llama 3) | Drafting, Initial Research |
| **Cheap** | Gemini 1.5 Flash | Classification, Tagging |
| **Balanced** | Claude 3.5 Sonnet | Writing, QA (Standard) |
| **Premium** | Claude 3 Opus / GPT-4o | Complex Reasoning, Final Review |

---

## 🛠️ Model Context Protocol (MCP)

**Location:** `src/mcp/`

The MCP provides a standardized interface for agents to interact with:

- **Tools:** Search (Serper), Data Retrieval (Postgres), Media (Pexels).
- **Context:** Dynamic memory and RAG-based context injection.

### Implementing a New Tool

To add a tool to the MCP:

1. Define the tool logic in `src/mcp_server/`.
2. Register the tool in `MCPContentOrchestrator`.
3. Update the `unified_orchestrator` to include the tool in the agent prompt.

---

## 📊 Monitoring & Costs

Usage is tracked per-task and per-user using the `UsageTracker` service.

- **Metrics:** `src/cofounder_agent/services/usage_tracker.py`
- **Dashboard:** View real-time costs in the **Oversight Hub** (Port 3001).
