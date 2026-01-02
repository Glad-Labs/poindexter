# Agents Legacy Folder Archival

**Date Archived:** January 1, 2026  
**Status:** Legacy code - refactored into cofounder_agent structure

## Why This Was Archived

The agents logic that was in `src/agents/` has been **completely refactored** into the `src/cofounder_agent/` structure:

### Migration Summary

| Component                        | Old Location                       | New Location                                           |
| -------------------------------- | ---------------------------------- | ------------------------------------------------------ |
| **Content Agent**                | `src/agents/content_agent/`        | `src/cofounder_agent/agents/content_agent/`            |
| **Compliance Agent**             | `src/agents/compliance_agent/`     | `src/cofounder_agent/agents/compliance_agent/`         |
| **Financial Agent**              | `src/agents/financial_agent/`      | `src/cofounder_agent/agents/financial_agent/`          |
| **Market Insight Agent**         | `src/agents/market_insight_agent/` | `src/cofounder_agent/agents/market_insight_agent/`     |
| **Content Orchestrator Service** | n/a                                | `src/cofounder_agent/services/content_orchestrator.py` |
| **Quality Service**              | n/a                                | `src/cofounder_agent/services/quality_service.py`      |
| **Task Executor**                | n/a                                | `src/cofounder_agent/services/task_executor.py`        |

### Import Fixes Applied

All imports in `src/cofounder_agent/services/content_orchestrator.py` were updated from:

```python
from agents.content_agent.agents.research_agent import ResearchAgent
```

To relative imports:

```python
from ..agents.content_agent.agents.research_agent import ResearchAgent
```

This ensures agents are accessed from the consolidated `cofounder_agent/agents/` directory, not a separate `src/agents/` location.

## Consolidation Benefits

✅ **Single Source of Truth:** All agent code now in one place (`cofounder_agent/`)  
✅ **Simpler Imports:** Relative imports within the module  
✅ **Easier Maintenance:** No duplication or split logic  
✅ **Better Packaging:** Agents shipped as part of cofounder_agent module

## If You Need Legacy Code

This archive contains the original agents structure for reference only. **Do not import from here** - use the consolidated versions in `src/cofounder_agent/agents/` instead.

## Verification

Server started successfully after consolidation:

```
INFO:     Application startup complete.
```

All agent imports now resolve correctly from the `cofounder_agent/agents/` directory.
