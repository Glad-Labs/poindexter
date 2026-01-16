# 🎯 Quick Reference: src/ Component Relationships

**Visual mapping of how src/ components interact**

---

## 1️⃣ REQUEST JOURNEY THROUGH SRC/

```
User clicks "Generate Content" in Oversight Hub
        ↓
        └─→ HTTP POST /api/generate-content
                ↓
        src/cofounder_agent/main.py
        (FastAPI app receives request)
                ↓
        src/cofounder_agent/routes/content_routes.py
        (Route handler processes request)
                ↓
        src/cofounder_agent/multi_agent_orchestrator.py
        (Orchestrator decides which agents needed)
                ↓
        src/agents/content_agent/orchestrator.py
        (ContentAgent executes pipeline)
                ↓
        src/agents/content_agent/agents/
        (ResearchAgent → CreativeAgent → QAAgent → ImageAgent → PublishingAgent)
                ↓
        src/cofounder_agent/services/model_router.py
        (Each agent selects best LLM: Ollama → Claude → GPT → Gemini)
                ↓
        src/cofounder_agent/services/database_service.py
        (Store results in PostgreSQL)
                ↓
        src/cofounder_agent/routes/content_routes.py
        (Format response as JSON)
                ↓
        HTTP Response 200 OK with content
        ↓
        Oversight Hub displays content to user ✓
```

---

## 2️⃣ AGENT INTERACTION MAP

```
                    ┌─────────────────────────────┐
                    │   MultiAgentOrchestrator    │
                    │   (Coordinates all agents)  │
                    └──────────┬──────────────────┘
                               │
                ┌──────────────┼──────────────┬────────────┐
                │              │              │            │
                ▼              ▼              ▼            ▼
        ┌────────────────┐ ┌──────────────┐ ┌─────────┐ ┌───────────┐
        │ ContentAgent   │ │ FinancialAgent   │ Market │ │Compliance │
        │ (src/agents/   │ │ (Tracks spend)   │Insight │ │  Agent    │
        │ content_agent/)│ │                  │ (Trends)│ │(Verifies) │
        │                │ │                  │         │ │           │
        │ 6-Phase Pipeline:                   │         │ │           │
        │ 1. Research    │ │ Accesses:        │         │ │           │
        │ 2. Creative    │ │ - Mercury API    │         │ │           │
        │ 3. QA/Critique │ │ - GCP Billing    │         │ │           │
        │ 4. Refine      │ │ - Accounting     │         │ │           │
        │ 5. Images      │ │                  │         │ │           │
        │ 6. Publishing  │ │                  │         │ │           │
        └──────┬─────────┘ └──────────────────┘ └─────────┘ └───────────┘
               │
               ▼ (Each agent inherits from:)
        ┌─────────────────────────────────┐
        │  BaseAgent (src/agents/base_agent.py)
        │                                  │
        │ Provides:                        │
        │ - MCP tool access               │
        │ - Memory management             │
        │ - Model selection               │
        │ - Error handling                │
        │ - Cost tracking                 │
        │ - Logging                       │
        └─────────────────────────────────┘
```

---

## 3️⃣ API ROUTE MAPPING

```
src/cofounder_agent/routes/

┌─────────────────────────────────────────────────────────┐
│  ALL ROUTES REGISTERED IN main.py                      │
└─────────────────────────────────────────────────────────┘
                          ↓
        ┌───────────────────┬────────────────┐
        │                   │                │
        ▼                   ▼                ▼
    content_routes.py  task_routes.py    auth_routes.py
    ────────────────    ──────────────    ──────────────

    POST /api/              POST /api/        POST /api/auth/
      generate-              tasks              login
      blog-post          GET /api/tasks    POST /api/auth/
    POST /api/            GET /api/tasks/    logout
      generate-             {id}
      content           PUT /api/tasks/
    POST /api/              {id}
      generate-         DELETE /api/
      images              tasks/{id}

    And 12+ more route files:
    • models.py              • agents_routes.py
    • webhooks.py            • social_routes.py
    • settings_routes.py     • metrics_routes.py
    • chat_routes.py         • command_queue_routes.py
    • financial_routes.py    • ollama_routes.py
    • etc.
```

---

## 4️⃣ SERVICE LAYER ARCHITECTURE

```
src/cofounder_agent/services/

┌──────────────────────────────────────────────────────────┐
│         SERVICES SUPPORTING AGENTS                       │
└──────────────────────────────────────────────────────────┘
         │
    ┌────┴─────────┬──────────────┬─────────────┐
    │              │              │             │
    ▼              ▼              ▼             ▼
┌────────────┐ ┌────────────┐ ┌─────────┐ ┌──────────┐
│ Database   │ │Model Router│ │Task     │ │ Memory   │
│Service     │ │            │ │Store    │ │System    │
│            │ │            │ │         │ │          │
│PostgreSQL  │ │Ollama      │ │Queue    │ │Short-term│
│Operations: │ │Claude 3    │ │Status   │ │Long-term │
│            │ │GPT-4       │ │History  │ │Semantic  │
│- Store     │ │Gemini      │ │         │ │Search    │
│  tasks     │ │Fallback    │ │         │ │          │
│- Fetch     │ │chain       │ │         │ │          │
│  tasks     │ │            │ │         │ │          │
│- Update    │ │COST:       │ │PERSISTENCE:    │Stores:
│  results   │ │FREE→CHEAP  │ │Survives    │
│            │ │            │ │restarts   │Context,
│            │ │PRIORITY:   │ │           │Learning
│            │ │Local First │ │           │
└────────────┘ └────────────┘ └─────────┘ └──────────┘
```

---

## 5️⃣ AGENT INHERITANCE HIERARCHY

```
                  ┌─────────────────────┐
                  │    BaseAgent        │
                  │  (src/agents/       │
                  │   base_agent.py)    │
                  └──────────┬──────────┘
                             │ PROVIDES:
                  ┌──────────┼──────────────┬───────────────┐
                  │          │              │               │
                  │     ┌─────────────┐     │               │
    ┌─────────────┴─────┤  MCP Tools  │─────────────┐       │
    │                   │  Access     │             │       │
    │            ┌──────┤  Memory     │─────┐       │       │
    │            │      │  Model      │     │       │       │
    ▼            ▼      │  Logging    │     ▼       ▼       ▼
┌──────────┐ ┌────────┐ │  Errors     │ ┌────────┐ ┌──────┐ ┌────────┐
│Content   │ │Financial  └─────────────┘ │ Market │ │Social│ │ Other  │
│Agent     │ │Agent                      │Insight │ │Media │ │ Agents │
│          │ │                           │Agent   │ │Agent │ │        │
│Research  │ │Accesses:                  │        │ │      │ │        │
│Creative  │ │                           │        │ │      │ │        │
│QA        │ │- Mercury API              │        │ │      │ │        │
│Image     │ │- GCP Billing              │        │ │      │ │        │
│Publish   │ │- Accounting               │        │ │      │ │        │
└──────────┘ └────────┘                  └────────┘ └──────┘ └────────┘
     6-step        2-agent                 1-step    1-step    1-step
   pipeline      integration              analysis  posting  tbd
```

---

## 6️⃣ MODEL SELECTION CASCADE

```
When any agent needs to call an AI model:

Agent calls: model_router.query(prompt)
                    ↓
    ┌───────────────────────────────────┐
    │ Model Selection Decision Tree      │
    └───────────────────────────────────┘
                    ↓
        "Is Ollama running locally?"
                ↙        ↖
              YES         NO
              ↓           ↓
        Use Ollama    "Try Claude 3 Opus"
        (FREE)              ↙        ↖
        Cost: $0         OK          FAIL
                         ↓           ↓
                      Use Claude   "Try GPT-4"
                      Cost: $0.02      ↙        ↖
                                     OK          FAIL
                                     ↓           ↓
                                  Use GPT     "Try Gemini"
                                  Cost: $0.03    ↙        ↖
                                                OK          FAIL
                                                ↓           ↓
                                           Use Gemini   Use Fallback
                                           Cost: $0.01  (Emergency)

RESULT: Always use cheapest available option first!
```

---

## 7️⃣ DATABASE SCHEMA (PostgreSQL)

```
PostgreSQL (Replaced Firestore)

┌─────────────────────────────┐
│  tasks                      │
├─────────────────────────────┤
│ id (uuid)                   │
│ type (content_generation)   │
│ status (pending→completed)  │
│ input_data (json)           │
│ output_data (json)          │
│ assigned_agent (content)    │
│ created_at                  │
│ updated_at                  │
│ completed_at                │
└─────────────────────────────┘
           ↓
┌─────────────────────────────┐
│  agents_state               │
├─────────────────────────────┤
│ id                          │
│ agent_name (ContentAgent)   │
│ status (idle/busy)          │
│ current_task_id             │
│ success_count               │
│ failure_count               │
│ avg_response_time           │
│ total_cost_usd              │
└─────────────────────────────┘
           ↓
┌─────────────────────────────┐
│  memories                   │
├─────────────────────────────┤
│ id                          │
│ agent_id                    │
│ content (text)              │
│ embedding (vector)          │
│ type (short/long_term)      │
│ created_at                  │
│ accessed_at                 │
└─────────────────────────────┘
```

---

## 8️⃣ FILE DEPENDENCY GRAPH

```
main.py (Entry Point)
    ├─→ routes/ (All route files)
    │   ├─→ content_routes.py
    │   ├─→ task_routes.py
    │   ├─→ models.py
    │   ├─→ agents_routes.py
    │   ├─→ auth_routes.py
    │   └─→ etc...
    │
    ├─→ multi_agent_orchestrator.py
    │   └─→ agents/ (All agent types)
    │       ├─→ content_agent/
    │       ├─→ financial_agent/
    │       ├─→ market_insight_agent/
    │       ├─→ compliance_agent/
    │       └─→ social_media_agent/
    │
    ├─→ services/
    │   ├─→ database_service.py
    │   ├─→ model_router.py
    │   ├─→ task_store_service.py
    │   └─→ etc...
    │
    ├─→ mcp/
    │   ├─→ base_server.py
    │   ├─→ client_manager.py
    │   └─→ orchestrator.py
    │
    ├─→ memory_system.py
    │
    └─→ logging configuration
```

---

## 9️⃣ REQUEST PROCESSING SEQUENCE

```
Time    Component              Action
────────────────────────────────────────────────────────
t=0ms   Oversight Hub          User clicks "Generate Content"
t=1ms   HTTP Layer             POST /api/generate-content sent
t=2ms   main.py                Request received, routed
t=3ms   content_routes.py      Parse & validate request
t=4ms   Orchestrator           Determine agents needed
t=5ms   ContentAgent           Execute 6-phase pipeline
        ├─ ResearchAgent       (50ms)
        ├─ CreativeAgent       (100ms)
        ├─ QAAgent             (50ms)
        ├─ CreativeAgent       (100ms) [if needed]
        ├─ ImageAgent          (50ms)
        └─ PublishingAgent     (50ms)
t=110ms Model Router           Request to Ollama for LLM
t=120ms Ollama                 Generate text (1000ms)
t=1120ms Model Result          Return to agents
t=1200ms Database              Save task & results
t=1210ms Response              Return JSON to frontend
t=1220ms Oversight Hub         Display results ✓
────────────────────────────────────────────────────────
Total Time: ~1.2 seconds
Cost: $0 (used local Ollama)
```

---

## 🔟 Error Handling Flow

```
Agent executes task
        ↓
    Success? ──── YES ──→ Return result
        │
       NO
        ↓
   Agent logs error
        ↓
   Check error type
        ↓
   ┌────┴────┬──────────┬──────────┐
   │          │          │          │
   ▼          ▼          ▼          ▼
Model      Network   Database   Other
Failure    Failure   Failure    Error
   │          │          │          │
   ▼          ▼          ▼          ▼
Try next  Retry with  Use cache  Return
model in  backoff                error
fallback
chain

Result:
✓ System resilient to failures
✓ Automatic fallback chains
✓ Graceful error messages
```

---

## Quick Lookup Table

| Need                     | Location                      | File                |
| ------------------------ | ----------------------------- | ------------------- |
| Add endpoint             | routes/                       | content_routes.py   |
| Change how agents work   | multi_agent_orchestrator.py   | -                   |
| New agent type           | src/agents/                   | Create new folder   |
| Fix database issue       | services/                     | database_service.py |
| Change AI model priority | services/                     | model_router.py     |
| Store agent memory       | src/                          | memory_system.py    |
| Check logs               | src/cofounder_agent/services/ | logger_config.py    |
| Add MCP tools            | src/mcp/                      | tool_registry.py    |

---

## Summary: The Pipeline in One Sentence

**Request enters main.py → Routes to handler → Orchestrator decomposes → Agents execute in parallel → Models selected via fallback chain → Results stored in PostgreSQL → Response returned to user.**
