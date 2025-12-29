# Phase 1 Cleanup - Ready to Execute

**Summary:** Delete 14 orphaned files (8 services + 6 tests)

## Services to DELETE (8 files)

```
services/database_service_old_sqlalchemy.py
services/gemini_client.py
services/intelligent_orchestrator.py
services/intervention_handler.py
services/llm_provider_manager.py
services/orchestrator_memory_extensions.py
services/poindexter_orchestrator.py
services/poindexter_tools.py
```

## Tests to DELETE (6 files)

```
tests/test_ollama_client.py.bak
tests/test_e2e_comprehensive.py
tests/test_poindexter_orchestrator.py
tests/test_poindexter_routes.py
tests/test_poindexter_e2e.py
tests/test_poindexter_tools.py
```

## Services to KEEP for Future (7 files)

```
services/ai_cache.py - Phase 2: Caching optimization
services/huggingface_client.py - Phase 2: HuggingFace LLM provider
services/mcp_discovery.py - Phase 2: MCP locating
services/performance_monitor.py - Phase 2: Model testing/comparison
services/pexels_client.py - Phase 2: Image integration
services/serper_client.py - Phase 2: Content research
services/settings_service.py - Phase 2: Settings page backend
```

## Execution Steps

1. Stop services: `Get-Process | Where-Object {$_.ProcessName -match "node|python"} | Stop-Process -Force`
2. Delete 14 files (see lists above)
3. Verify: `cd src/cofounder_agent; pytest tests/ -v` (tests should pass)
4. Restart FastAPI: Should start without import errors
5. Verify: curl http://127.0.0.1:8000/api/health

## Rollback (if needed)

`git revert <commit-hash>` to restore all deleted files

---

**Risk Level:** LOW - All deletions are completely orphaned  
**Status:** ✅ APPROVED BY USER - Ready to Execute
