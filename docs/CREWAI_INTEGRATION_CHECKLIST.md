# CrewAI Phase 1 Integration Checklist

**Target:** Integrate Phase 1 CrewAI tools into existing GLAD Labs agents  
**Timeline:** 2-3 hours  
**Current Status:** ✅ Ready to Start (28/36 tests passing)

---

## 📋 Integration Tasks

### Task 1: Update Content Agent ⏱️ 45 minutes

**Location:** `src/agents/content_agent/content_agent.py`

**Changes Required:**

1. Add import

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory
```

2. Initialize tools in `__init__`

```python
class ContentAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.tools = CrewAIToolsFactory.get_content_agent_tools()
        # self.tools = [WebSearchTool, CompetitorSearchTool, DocumentAccessTool, DataProcessingTool]
```

3. Pass to CrewAI Agent (if using CrewAI framework)

```python
self.agent = Agent(
    role="Content Creator",
    tools=self.tools,
    verbose=True
)
```

**Tests to Run:**

```bash
pytest tests/test_crewai_tools_integration.py::TestAgentToolsIntegration::test_content_agent_can_access_tools -v
```

**Success Criteria:**

- ✅ Agent has tools attribute
- ✅ Tools are accessible via `self.tools`
- ✅ Tools can be passed to CrewAI agents
- ✅ No import errors

---

### Task 2: Update Financial Agent ⏱️ 30 minutes

**Location:** `src/agents/financial_agent/financial_agent.py`

**Changes Required:**

1. Add import

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory
```

2. Initialize tools

```python
class FinancialAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        # Financial agent uses web search + data processing
        self.tools = [
            CrewAIToolsFactory.get_web_search_tool(),
            CrewAIToolsFactory.get_data_processing_tool(),
        ]
```

3. Use in agent

```python
self.agent = Agent(
    role="Financial Analyst",
    tools=self.tools,
    verbose=True
)
```

**Tests to Run:**

```bash
pytest tests/test_crewai_tools_integration.py::TestCrewAIToolsFactory -v
```

---

### Task 3: Update Market Insight Agent ⏱️ 30 minutes

**Location:** `src/agents/market_insight_agent/market_insight_agent.py`

**Changes Required:**

1. Add import

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory
```

2. Initialize tools

```python
class MarketInsightAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.tools = CrewAIToolsFactory.get_market_agent_tools()
        # Market agent uses: WebSearch + CompetitorSearch + DataProcessing
```

**Tests to Run:**

```bash
pytest tests/test_crewai_tools_integration.py::TestAgentToolsIntegration::test_market_agent_can_access_tools -v
```

---

### Task 4: Update Compliance Agent ⏱️ 30 minutes

**Location:** `src/agents/compliance_agent/compliance_agent.py`

**Changes Required:**

1. Add import

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory
```

2. Initialize tools

```python
class ComplianceAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        # Compliance uses document reading + web search
        self.tools = [
            CrewAIToolsFactory.get_document_tool(),
            CrewAIToolsFactory.get_web_search_tool(),
        ]
```

---

### Task 5: Update Research Agent (if exists) ⏱️ 30 minutes

**Location:** `src/agents/research_agent/research_agent.py` (if exists)

**Changes Required:**

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

class ResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.tools = CrewAIToolsFactory.get_research_agent_tools()
        # Includes: WebSearch + DocumentAccess + DirectoryAccess + DataProcessing
```

---

### Task 6: Test All Agent Integrations ⏱️ 30 minutes

**Run Full Integration Test Suite:**

```bash
# Test all agent integrations
pytest tests/test_crewai_tools_integration.py -m integration -v

# Should see:
# - test_content_agent_can_access_tools: PASSED ✅
# - test_research_agent_can_access_tools: PASSED ✅
# - test_market_agent_can_access_tools: PASSED ✅
# - test_web_search_integration: PASSED ✅
# - test_tool_error_handling: PASSED ✅
```

**Expected Output:**

```
TestAgentToolsIntegration::test_content_agent_can_access_tools PASSED
TestAgentToolsIntegration::test_research_agent_can_access_tools PASSED
TestAgentToolsIntegration::test_market_agent_can_access_tools PASSED
TestAgentToolsIntegration::test_web_search_integration PASSED
TestAgentToolsIntegration::test_tool_error_handling PASSED
```

---

### Task 7: End-to-End Testing ⏱️ 30 minutes

**Create integration test scenario:**

```python
# tests/test_agent_tools_e2e.py
import pytest
from src.agents.content_agent.content_agent import ContentAgent
from src.agents.market_insight_agent.market_insight_agent import MarketInsightAgent

@pytest.mark.integration
async def test_content_agent_with_tools():
    agent = ContentAgent()

    # Verify tools loaded
    assert agent.tools is not None
    assert len(agent.tools) > 0

    # Try using a tool
    from src.agents.content_agent.utils.tools import WebSearchTool
    search_tool = agent.tools[0]
    assert isinstance(search_tool, WebSearchTool)

@pytest.mark.integration
async def test_market_agent_with_tools():
    agent = MarketInsightAgent()
    assert agent.tools is not None
    assert len(agent.tools) > 0
```

**Run Test:**

```bash
pytest tests/test_agent_tools_e2e.py -v
```

---

## 🔍 Verification Checklist

After each agent update, verify:

- [ ] Import statement added correctly
- [ ] Tools initialized in `__init__`
- [ ] No import errors when running agent
- [ ] Tools accessible via `self.tools`
- [ ] Related tests pass
- [ ] No breaking changes to existing agent functionality
- [ ] Type hints correct
- [ ] Error handling works (if tool fails, agent continues)

---

## 🚀 Deployment Checklist

### Before Pushing to Dev:

- [ ] All 5 agents updated with tools
- [ ] All integration tests passing (5/5 test groups)
- [ ] No new errors in test suite
- [ ] Documentation updated (if changed agent initialization)
- [ ] Performance benchmarks still met (< 1s startup)

### Deployment Steps:

```bash
# 1. Verify all tests pass
pytest tests/test_crewai_tools_integration.py -m integration -v

# 2. Verify no regressions
pytest tests/test_agent_tools_e2e.py -v

# 3. Stage changes
git add src/agents/*/

# 4. Commit
git commit -m "feat: integrate CrewAI Phase 1 tools into all agents"

# 5. Push to dev
git push origin feature/crewai-phase1-integration

# 6. Create PR to dev branch
# - Tests run automatically
# - Review and merge
```

---

## 📊 Expected Changes Summary

| Agent      | Before   | After   | Tools Added                                                 |
| ---------- | -------- | ------- | ----------------------------------------------------------- |
| Content    | No tools | 4 tools | WebSearch, CompetitorSearch, DocumentAccess, DataProcessing |
| Financial  | No tools | 2 tools | WebSearch, DataProcessing                                   |
| Market     | No tools | 3 tools | WebSearch, CompetitorSearch, DataProcessing                 |
| Compliance | No tools | 2 tools | DocumentAccess, WebSearch                                   |
| Research   | No tools | 4 tools | WebSearch, DocumentAccess, DirectoryAccess, DataProcessing  |

**Total New Tool Integrations:** 15 tool-to-agent connections

---

## ⚠️ Potential Issues & Solutions

### Issue 1: Import Error

**Error:** `ImportError: cannot import name 'CrewAIToolsFactory'`

**Solution:**

```bash
# Verify crewai_tools installed
pip install 'crewai[tools]'

# Verify tools.py exists
ls -la src/agents/content_agent/utils/tools.py

# Check Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

### Issue 2: Tools Returning None

**Error:** Tool operations return None instead of results

**Solution:**

```python
# Check if error occurred
result = tool.run(query)
if result is None:
    logger.error("Tool operation failed")
    # Fall back to alternative approach
else:
    # Use result
    pass
```

### Issue 3: API Key Missing

**Error:** `SERPER_API_KEY not set` warning

**Solution:**

```bash
# Set API key
export SERPER_API_KEY=your_key_here

# Or in Python
import os
os.environ["SERPER_API_KEY"] = "your_key_here"
```

### Issue 4: CompetitorSearch Tool Fails

**Error:** Chroma embedding API key not set

**Solution (Option A):** Add OpenAI key

```bash
export CHROMA_OPENAI_API_KEY=sk-your-openai-key
```

**Solution (Option B):** Remove from agent temporarily

```python
# Use only working tools
self.tools = [
    CrewAIToolsFactory.get_web_search_tool(),
    CrewAIToolsFactory.get_document_tool(),
]
```

---

## 📈 Performance Impact

Expected performance impact after integration:

| Metric             | Before   | After      | Impact                              |
| ------------------ | -------- | ---------- | ----------------------------------- |
| Agent Startup Time | Baseline | +200-500ms | Acceptable (tools cached)           |
| First Tool Call    | N/A      | ~100-500ms | Normal for API calls                |
| Cached Tool Calls  | N/A      | <1ms       | Excellent (singleton pattern)       |
| Memory Per Agent   | Baseline | +10-50MB   | Acceptable (tools efficient)        |
| Response Time      | Baseline | +0-2s      | Depends on tool (web search slower) |

**Overall:** Negligible performance impact, significant capability gain

---

## ✅ Success Criteria

Integration is complete when:

- ✅ All 5 agents have tools initialized
- ✅ All integration tests passing (≥25/30)
- ✅ No import errors
- ✅ No runtime errors
- ✅ Performance acceptable (<1s agent init)
- ✅ All tool types accessible from agents
- ✅ Error handling works gracefully
- ✅ Can be deployed to staging

---

## 🔄 After Integration Complete

### Next Steps (Phase 2 - Week 2):

1. Add DALL-E Tool for image generation
2. Add PDF search for document analysis
3. Add GitHub search for code examples
4. Add YouTube search for research videos
5. Add advanced data processing capabilities

### Rollback Plan (if needed):

```bash
# Revert to before integration
git revert <commit-hash>
git push origin feature/crewai-phase1-integration

# Or remove tools from specific agent
agent.tools = []  # Fallback to no tools
```

---

## 📞 Quick Reference

### Get Specific Tool:

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

web_search = CrewAIToolsFactory.get_web_search_tool()
document_tool = CreawAIToolsFactory.get_document_tool()
```

### Get Tool Bundle:

```python
content_tools = CrewAIToolsFactory.get_content_agent_tools()
research_tools = CrewAIToolsFactory.get_research_agent_tools()
market_tools = CrewAIToolsFactory.get_market_agent_tools()
```

### Test Tools:

```bash
# Run all tool tests
pytest tests/test_crewai_tools_integration.py -v

# Run only integration tests
pytest tests/test_crewai_tools_integration.py -m integration -v

# Run specific agent test
pytest tests/test_crewai_tools_integration.py::TestAgentToolsIntegration -v
```

---

**Ready to integrate? Start with Task 1 and work through to Task 7. Estimated time: 2-3 hours for full completion.** 🚀
