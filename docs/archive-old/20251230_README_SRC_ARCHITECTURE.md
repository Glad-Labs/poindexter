# 🎯 Glad Labs src/ Architecture - Complete Guide

**Three comprehensive documents explaining how the src/ folder pipeline works**

---

## 📚 Documents in This Guide

I've created **3 detailed documents** to help you understand the src/ architecture:

### 1️⃣ **SRC_FOLDER_PIPELINE_WALKTHROUGH.md** (Primary Reference)

**Best For:** Understanding the complete pipeline end-to-end

**Contains:**

- High-level architecture overview
- Step-by-step breakdown of each component
- Complete request-to-response cycle example
- Data flow visualization
- Key design patterns
- Common use cases

**Start Here** if you want to understand: "How does the whole system work together?"

---

### 2️⃣ **SRC_QUICK_REFERENCE_DIAGRAMS.md** (Visual Reference)

**Best For:** Quick lookups and understanding relationships

**Contains:**

- Visual journey through request processing
- Agent interaction map
- API route mapping
- Service layer architecture
- Agent inheritance hierarchy
- Model selection cascade
- Database schema
- File dependency graph
- Request processing sequence
- Error handling flow

**Use This** when you need: A quick diagram or relationship reference

---

### 3️⃣ **SRC_CODE_EXAMPLES.md** (Implementation Reference)

**Best For:** Seeing actual code and implementation patterns

**Contains:**

- main.py - FastAPI setup
- Routes - Request handling
- Orchestrator - Task routing
- BaseAgent - Agent parent class
- ContentAgent - Specialized agent example
- Model router - LLM selection
- Database service - Data persistence

**Use This** when you need: To see how things are actually coded

---

## 🗺️ Quick Navigation Guide

### Need to understand...

| Question                                      | Document             | Section                              |
| --------------------------------------------- | -------------------- | ------------------------------------ |
| How do requests flow through the system?      | Pipeline Walkthrough | "Complete Request-to-Response Cycle" |
| Where does each file fit in the architecture? | Quick Reference      | "File Dependency Graph"              |
| How do agents communicate?                    | Quick Reference      | "Agent Interaction Map"              |
| What services support the agents?             | Pipeline Walkthrough | "Step 5: Services"                   |
| How is data stored?                           | Quick Reference      | "Database Schema"                    |
| How are models selected?                      | Quick Reference      | "Model Selection Cascade"            |
| What's the code for main.py?                  | Code Examples        | Section 1                            |
| How do routes work?                           | Code Examples        | Section 2                            |
| How does an agent execute tasks?              | Code Examples        | Section 5                            |
| How does the database persist data?           | Code Examples        | Section 7                            |

---

## 🚀 Recommended Reading Order

### For First-Time Learners:

1. Start with **SRC_FOLDER_PIPELINE_WALKTHROUGH.md**
   - Read: Overview, Steps 1-2, Complete Cycle example
   - Time: 15 minutes
2. Then **SRC_QUICK_REFERENCE_DIAGRAMS.md**
   - Read: Request Journey, Agent Interaction Map, Service Layer
   - Time: 10 minutes
3. Finally **SRC_CODE_EXAMPLES.md**
   - Read: main.py, Routes, Orchestrator
   - Time: 10 minutes

**Total: 35 minutes to understand the complete architecture**

---

### For Developers Implementing Features:

1. Refer to **Pipeline Walkthrough** for architectural context
2. Use **Code Examples** to see implementation patterns
3. Check **Quick Reference** for component relationships
4. Examine actual source files in `src/`

---

### For Debugging Issues:

1. Check **Quick Reference** "Error Handling Flow"
2. Review **Code Examples** for the specific component
3. Trace through **Pipeline Walkthrough** to understand data flow
4. Add logging and check PostgreSQL database

---

## 📊 src/ Folder Structure Overview

```
src/
├── cofounder_agent/          # Main application
│   ├── main.py              # ← FastAPI entry point
│   ├── multi_agent_orchestrator.py  # ← Task routing
│   ├── memory_system.py      # Agent context storage
│   ├── notification_system.py
│   ├── routes/              # ← API endpoints
│   │   ├── content_routes.py
│   │   ├── task_routes.py
│   │   ├── models.py
│   │   ├── agents_routes.py
│   │   └── ... (15 total)
│   ├── services/            # ← Supporting services
│   │   ├── database_service.py
│   │   ├── model_router.py
│   │   ├── task_store_service.py
│   │   └── ...
│   └── tests/               # Unit & E2E tests
│
├── agents/                  # ← Specialized agents
│   ├── base_agent.py        # Parent class
│   ├── content_agent/       # Content creation (6-phase pipeline)
│   ├── financial_agent/     # Financial tracking
│   ├── market_insight_agent/# Market analysis
│   ├── compliance_agent/    # Compliance checking
│   └── social_media_agent/  # Social media management
│
├── mcp/                     # Model Context Protocol
│   ├── base_server.py
│   ├── client_manager.py
│   └── orchestrator.py
│
└── services/               # Shared services
    └── dynamic_model_router.py
```

---

## 🔄 The Pipeline in 30 Seconds

```
1. User makes request (Oversight Hub)
   ↓
2. main.py receives it via FastAPI
   ↓
3. Routes layer (content_routes.py, etc.) parses the request
   ↓
4. Orchestrator decides which agent(s) needed
   ↓
5. Agent(s) execute task in parallel
   ↓
6. Each agent selects best LLM (Ollama → Claude → GPT → Gemini)
   ↓
7. Results stored in PostgreSQL
   ↓
8. Response returned as JSON
   ↓
9. Frontend displays result ✓
```

---

## 💡 Key Concepts to Remember

### 1. **Separation of Concerns**

- main.py = Entry point
- routes/ = Request handling
- orchestrator = Task routing
- agents/ = Task execution
- services/ = Support functions

### 2. **Agent Specialization**

- Each agent has ONE job
- Agents communicate through orchestrator
- Results aggregated for complex tasks

### 3. **Model Selection**

- Always prefer cheapest option first (Ollama = free)
- Automatic fallback if one provider fails
- Tracks cost per request

### 4. **Data Flow**

- All tasks stored in PostgreSQL
- Agents poll task store
- Results persisted
- Historical data available

### 5. **Async Processing**

- Multiple agents run in parallel
- No blocking operations
- Fast response times

---

## 🎯 Common Scenarios

### Scenario 1: "I need to add a new API endpoint"

1. Create new route in `src/cofounder_agent/routes/`
2. Import from `main.py` and register with `app.include_router()`
3. Route handler calls orchestrator if needed
4. Return JSON response

**Files to edit:** routes/\*.py, main.py

---

### Scenario 2: "I need to create a new agent type"

1. Create folder in `src/agents/my_new_agent/`
2. Create agent class inheriting from `BaseAgent`
3. Implement `execute()` method
4. Register in `MultiAgentOrchestrator`
5. Create route to expose via API

**Files to create:** agents/my_new_agent/agent.py, update orchestrator.py

---

### Scenario 3: "The system is slow"

1. Check if using Ollama (should be free and fast)
2. Review orchestrator parallel execution
3. Check database query performance
4. Monitor memory usage

**Files to check:** services/model_router.py, multi_agent_orchestrator.py

---

### Scenario 4: "A task is failing"

1. Check database for task status
2. Review agent logs
3. Check model router fallback chain
4. Verify API keys are set

**Files to check:** database_service.py, services/model_router.py

---

## 📋 Quick Reference Table

| Need               | Location             | Key File                                      |
| ------------------ | -------------------- | --------------------------------------------- |
| Add endpoint       | routes/              | Any route file                                |
| Fix task routing   | src/cofounder_agent/ | multi_agent_orchestrator.py                   |
| New agent          | src/agents/          | Create new folder + base_agent.py inheritance |
| Database operation | services/            | database_service.py                           |
| LLM selection      | services/            | model_router.py                               |
| Agent memory       | src/                 | memory_system.py                              |
| API startup        | src/cofounder_agent/ | main.py                                       |
| Task queue         | services/            | task_store_service.py                         |

---

## 🔗 Related Files in Glad Labs

### Frontend (Calls src/ APIs):

- `web/oversight-hub/` - React dashboard
- `web/public-site/` - Next.js public site

### Database (Persists src/ data):

- PostgreSQL (replaced Google Firestore)

### Configuration:

- `.env` - API keys and settings
- `requirements.txt` - Python dependencies

---

## 🎓 Learning Path

1. **Day 1: Understanding**
   - Read Pipeline Walkthrough (30 min)
   - Look at src/ folder structure (10 min)
   - Review Quick Reference diagrams (15 min)

2. **Day 2: Implementation**
   - Study Code Examples (30 min)
   - Examine actual files in src/ (30 min)
   - Try adding simple endpoint (1 hour)

3. **Day 3: Advanced**
   - Create new agent (2-3 hours)
   - Modify orchestrator logic (1-2 hours)
   - Run tests and debug (1 hour)

---

## ❓ FAQ

### Q: Why Ollama first instead of Claude/GPT?

**A:** Cost optimization - Ollama is free and runs locally. Only uses paid APIs if local inference fails.

### Q: How many agents can run in parallel?

**A:** As many as needed - they use Python's asyncio for concurrent execution.

### Q: Where is data stored?

**A:** PostgreSQL database (replaced Google Firestore for better control and offline capability).

### Q: Can I add a new agent type easily?

**A:** Yes! Create folder in `src/agents/`, inherit from `BaseAgent`, register in orchestrator.

### Q: What if all models fail?

**A:** Fallback chain ensures at least one model available. If all fail, request returns error with details.

### Q: How do I debug a failing task?

**A:** Check PostgreSQL task status, review agent logs, verify model availability.

---

## 📚 Summary

You now have:

- ✅ Complete understanding of src/ architecture
- ✅ Visual diagrams showing relationships
- ✅ Code examples for implementation
- ✅ Quick reference for common questions
- ✅ Learning path for different skill levels

**Next Steps:**

1. Open the three guide documents
2. Choose your reading path above
3. Explore actual code in src/ folder
4. Try implementing a small change
5. Ask questions about specific components

---

## 📞 Quick Help

**Need to understand something specific?** Here's what to check:

| Question                      | Check                              |
| ----------------------------- | ---------------------------------- |
| How does X component work?    | SRC_FOLDER_PIPELINE_WALKTHROUGH.md |
| What do Y components talk to? | SRC_QUICK_REFERENCE_DIAGRAMS.md    |
| How do I code Z?              | SRC_CODE_EXAMPLES.md               |
| Where is file X?              | src/ folder structure above        |
| What service handles X?       | Services layer section             |

---

**Happy exploring! The src/ architecture is designed to be modular, scalable, and easy to understand. Start with the Pipeline Walkthrough and work your way through.** 🚀
