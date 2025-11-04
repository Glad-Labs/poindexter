# CrewAI Phase 1 - Quick Start for Next Session

**Updated:** November 4, 2025  
**Session Goal:** Complete Phase 1 integration and deploy  
**Estimated Time:** 2-3 hours

---

## 🎯 Current Status (End of Session 3d)

✅ **Complete:**

- CrewAI tools discovered and evaluated (30+ tools available)
- Phase 1/2/3 roadmap created
- 5 Phase 1 tool wrappers implemented
- CrewAIToolsFactory created (singleton pattern)
- 36-test suite created
- 28/36 tests passing (77.8%)
- All documentation complete

⏳ **Next:** Agent integration (2-3 hours)

---

## 📋 Session Roadmap

### Part 1: Verification (15 minutes)

```bash
# 1. Confirm tests still passing
cd c:\Users\mattm\glad-labs-website
python -m pytest tests/test_crewai_tools_integration.py -m unit -v

# Expected: 14 tests passing (all core tool unit tests)

# 2. Confirm no regressions
python -m pytest tests/test_crewai_tools_integration.py -m smoke -v

# Expected: 2 smoke tests passing
```

### Part 2: Integration (90 minutes)

**Update 5 agents with tools:**

1. Content Agent - 15 min
   - Add import
   - Initialize tools
   - Test

2. Financial Agent - 10 min
   - Add import
   - Initialize tools (smaller subset)

3. Market Insight Agent - 10 min
   - Add import
   - Initialize tools

4. Compliance Agent - 10 min
   - Add import
   - Initialize tools

5. Research Agent - 10 min
   - Add import
   - Initialize tools (full set)

**Test all integrations:** 25 min

```bash
pytest tests/test_crewai_tools_integration.py -m integration -v
```

### Part 3: Deployment (30 minutes)

```bash
# 1. Create feature branch
git checkout -b feature/crewai-phase1-integration

# 2. Stage changes
git add src/agents/

# 3. Commit
git commit -m "feat: integrate CrewAI Phase 1 tools into all agents"

# 4. Push
git push origin feature/crewai-phase1-integration

# 5. Create PR and merge to dev
```

---

## 🚀 One-Command Start

Get everything ready in one command:

```bash
cd c:\Users\mattm\glad-labs-website

# Run verification
python -m pytest tests/test_crewai_tools_integration.py -m "unit or smoke" -v
```

**Expected output:**

```
14 unit tests PASSING
2 smoke tests PASSING
16/16 passing
```

---

## 📚 Key Files to Modify

### 1. Content Agent

**File:** `src/agents/content_agent/content_agent.py`

```python
# ADD THIS:
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# IN __init__:
self.tools = CrewAIToolsFactory.get_content_agent_tools()
```

### 2. Financial Agent

**File:** `src/agents/financial_agent/financial_agent.py`

```python
# ADD THIS:
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# IN __init__:
self.tools = [
    CrewAIToolsFactory.get_web_search_tool(),
    CrewAIToolsFactory.get_data_processing_tool(),
]
```

### 3. Market Agent

**File:** `src/agents/market_insight_agent/market_insight_agent.py`

```python
# ADD THIS:
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# IN __init__:
self.tools = CrewAIToolsFactory.get_market_agent_tools()
```

### 4. Compliance Agent

**File:** `src/agents/compliance_agent/compliance_agent.py`

```python
# ADD THIS:
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# IN __init__:
self.tools = [
    CrewAIToolsFactory.get_document_tool(),
    CrewAIToolsFactory.get_web_search_tool(),
]
```

### 5. Research Agent

**File:** `src/agents/research_agent/research_agent.py`

```python
# ADD THIS:
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# IN __init__:
self.tools = CrewAIToolsFactory.get_research_agent_tools()
```

---

## ✅ Integration Pattern (Copy & Paste)

Every agent needs these 3 lines:

```python
# At top of file
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# In __init__ method
self.tools = CrewAIToolsFactory.get_<agent_type>_agent_tools()
# OR
self.tools = CrewAIToolsFactory.get_<specific>_tool()
```

**Available tool collections:**

- `get_web_search_tool()` - WebSearch only
- `get_competitor_search_tool()` - CompetitorSearch only
- `get_document_tool()` - DocumentAccess only
- `get_directory_tool()` - DirectoryAccess only
- `get_data_processing_tool()` - DataProcessing only
- `get_content_agent_tools()` - [WebSearch, Competitor, Document, DataProcessing]
- `get_research_agent_tools()` - [WebSearch, Document, Directory, DataProcessing]
- `get_market_agent_tools()` - [WebSearch, Competitor, DataProcessing]

---

## 🧪 Test Commands (Copy & Paste)

```bash
# All integration tests
pytest tests/test_crewai_tools_integration.py -m integration -v

# Specific agent test
pytest tests/test_crewai_tools_integration.py::TestAgentToolsIntegration::test_content_agent_can_access_tools -v

# Quick smoke test
pytest tests/test_crewai_tools_integration.py -m smoke -v

# Everything
pytest tests/test_crewai_tools_integration.py -v
```

---

## 🔍 Quick Verification Checklist

After each agent update:

- [ ] No import errors
- [ ] Agent starts without errors
- [ ] Tools attribute exists
- [ ] Tools have correct type (list)
- [ ] Related test passes

One-liner to verify all:

```bash
pytest tests/test_crewai_tools_integration.py -m integration -v --tb=short
```

---

## 🎯 Success Criteria

Integration complete when:

- ✅ All 5 agents have tools initialized
- ✅ All integration tests pass (≥25/30)
- ✅ No import or runtime errors
- ✅ Can push to dev without issues

---

## 💡 Pro Tips

1. **Update agents in order listed** - Content first, then Financial, Market, Compliance, Research

2. **Test after each update** - Don't wait until all 5 are done

   ```bash
   # After updating Content Agent, run:
   pytest tests/test_crewai_tools_integration.py::TestAgentToolsIntegration::test_content_agent_can_access_tools -v
   ```

3. **Copy the pattern** - All 5 agents need identical changes (just different tool collections)

4. **Don't overthink it** - Just 3 lines per agent!

---

## 🚨 If Something Goes Wrong

**Import error?**

```bash
pip install 'crewai[tools]'
```

**Test failing?**

```bash
# See full error
pytest tests/test_crewai_tools_integration.py::TestAgentToolsIntegration -v --tb=long
```

**Performance issue?**

```bash
# Check performance
pytest tests/test_crewai_tools_integration.py -m performance -v
```

---

## 📈 Session Success = Phase 1 Complete

**When you finish this session:**

- ✅ Phase 1 fully integrated into all agents
- ✅ 28+ tests passing
- ✅ Ready to deploy to staging
- ✅ Can immediately move to Phase 2

---

## 📞 Reference Files

- **Status:** `docs/CREWAI_PHASE1_STATUS.md` (current test results)
- **Integration Guide:** `docs/CREWAI_INTEGRATION_CHECKLIST.md` (detailed steps)
- **Usage Guide:** `docs/CREWAI_TOOLS_USAGE_GUIDE.md` (how to use tools)
- **Planning:** `docs/CREWAI_TOOLS_INTEGRATION_PLAN.md` (Phase 1/2/3 roadmap)

---

## ⏱️ Time Breakdown

| Task             | Time         | Status   |
| ---------------- | ------------ | -------- |
| Verification     | 15 min       | 🔵 Ready |
| Content Agent    | 15 min       | 🔵 Ready |
| Financial Agent  | 10 min       | 🔵 Ready |
| Market Agent     | 10 min       | 🔵 Ready |
| Compliance Agent | 10 min       | 🔵 Ready |
| Research Agent   | 10 min       | 🔵 Ready |
| Testing          | 25 min       | 🔵 Ready |
| Deployment       | 30 min       | 🔵 Ready |
| **TOTAL**        | **2h 25min** | 🔵 Ready |

---

**Ready? Start with verification step above. Should take 2-3 hours total to complete Phase 1.** 🚀

**After this session: Phase 2 tools are next (DALL-E, PDF search, GitHub search, YouTube search)**
