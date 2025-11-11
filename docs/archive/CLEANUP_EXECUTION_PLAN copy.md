# Cleanup Execution Plan - Approved by User

**Date:** November 10, 2025  
**Status:** Ready to Execute  
**Priority:** Phase 1 Deletions (Safe to Delete)

---

## User Preferences - Confirmed

### Services to KEEP (Future Implementation)

✅ **These will be enhanced/integrated in future phases:**

- `ai_cache.py` - Caching optimization (Phase 2)
- `huggingface_client.py` - HuggingFace as LLM provider (Phase 2)
- `mcp_discovery.py` - MCP locating (Phase 2, after core stable)
- `performance_monitor.py` - Model testing/comparison (Phase 2, priority)
- `pexels_client.py` - Image integration in content pipeline (Phase 2)
- `serper_client.py` - Research capability in pipeline (Phase 2)
- `settings_service.py` - Settings page backend (Phase 2)

### Services to DELETE (Phase 1)

❌ **These are completely unused/redundant:**

**Services (8 files):**

1. `services/database_service_old_sqlalchemy.py` - OLD BACKUP
2. `services/gemini_client.py` - Model router handles LLM selection
3. `services/intelligent_orchestrator.py` - Replaced by main.py
4. `services/intervention_handler.py` - Never used
5. `services/llm_provider_manager.py` - Replaced by model_router
6. `services/orchestrator_memory_extensions.py` - Unused
7. `services/poindexter_orchestrator.py` - Old implementation
8. `services/poindexter_tools.py` - Old implementation

**Test Files (6 files):** 9. `tests/test_ollama_client.py.bak` - Backup file 10. `tests/test_e2e_comprehensive.py` - Replaced by test_e2e_fixed.py 11. `tests/test_poindexter_orchestrator.py` - Old implementation 12. `tests/test_poindexter_routes.py` - Old implementation 13. `tests/test_poindexter_e2e.py` - Old implementation 14. `tests/test_poindexter_tools.py` - Old implementation

---

## Execution Commands

### Step 1: Stop All Services

```powershell
# Stop all running services before cleanup
Get-Process | Where-Object {$_.ProcessName -match "node|python"} | Stop-Process -Force
Start-Sleep -Seconds 2
```

### Step 2: Delete Phase 1 Services (8 files)

```powershell
# Navigate to services directory
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent\services

# Delete old/unused services
Remove-Item database_service_old_sqlalchemy.py -Force
Remove-Item gemini_client.py -Force
Remove-Item intelligent_orchestrator.py -Force
Remove-Item intervention_handler.py -Force
Remove-Item llm_provider_manager.py -Force
Remove-Item orchestrator_memory_extensions.py -Force
Remove-Item poindexter_orchestrator.py -Force
Remove-Item poindexter_tools.py -Force

Write-Host "✅ Phase 1 Services Deleted (8 files)"
```

### Step 3: Delete Phase 1 Tests (6 files)

```powershell
# Navigate to tests directory
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent\tests

# Delete old/unused tests
Remove-Item test_ollama_client.py.bak -Force
Remove-Item test_e2e_comprehensive.py -Force
Remove-Item test_poindexter_orchestrator.py -Force
Remove-Item test_poindexter_routes.py -Force
Remove-Item test_poindexter_e2e.py -Force
Remove-Item test_poindexter_tools.py -Force

Write-Host "✅ Phase 1 Tests Deleted (6 files)"
```

### Step 4: Verify Deletions

```powershell
# List remaining services to verify cleanup
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent\services
Write-Host "Remaining services:"
Get-Item *.py | Select-Object Name | Sort-Object Name

# List remaining tests to verify cleanup
cd ..\tests
Write-Host "Remaining tests:"
Get-Item test_*.py | Select-Object Name | Sort-Object Name
```

### Step 5: Restart Services

```powershell
# Navigate back to project root
cd c:\Users\mattm\glad-labs-website

# Restart FastAPI (it will fail if it imports deleted services, which means no imports were orphaned)
cd src\cofounder_agent
python main.py

# In separate terminal, restart other services
npm run dev
```

---

## Post-Cleanup Verification

After running the cleanup scripts, verify:

1. ✅ FastAPI starts successfully on http://127.0.0.1:8000
   - If import errors occur, those services were still being imported
   - If starts fine, all deletions were safe

2. ✅ All active tests pass

   ```bash
   cd src\cofounder_agent
   pytest tests/ -v --tb=short
   ```

3. ✅ Strapi CMS starts successfully
   - Verify PostgreSQL connection is working

4. ✅ Services are responsive
   - Test API health check: `curl http://localhost:8000/api/health`

---

## Phase 2: Future Services Integration (Not Yet)

After core pipeline is stable and tested:

1. Integrate `ai_cache.py` for response caching
2. Add `huggingface_client.py` as alternative LLM provider
3. Enhance `performance_monitor.py` for model A/B testing
4. Complete `pexels_client.py` for image selection
5. Complete `serper_client.py` for content research
6. Activate `settings_service.py` for Oversight Hub integration
7. Implement `mcp_discovery.py` for MCP server locating

---

## Phase 3: Conditional Review (If Time)

These files are only used in old auth routes - review for deletion:

- `services/auth.py`
- `services/totp.py`
- `services/permissions_service.py`
- `services/encryption.py`
- `routes/auth_routes_old_sqlalchemy.py`

---

## Rollback Plan

If cleanup causes issues:

```powershell
# Git rollback
cd c:\Users\mattm\glad-labs-website
git log --oneline | head -5  # Find commit before cleanup
git revert <commit-hash>
git push origin feat/bugs

# This will restore all deleted files
```

---

## Summary

- **Phase 1 Deletions:** 14 files (8 services + 6 tests)
- **Safe to Delete:** Yes - all are completely orphaned
- **Risk Level:** LOW - no active imports found
- **Estimated Time:** 5 minutes
- **Rollback Available:** Yes, via git

**Ready to Execute? Proceed with Step 1-5 above.**

---

**Last Updated:** November 10, 2025  
**Status:** ✅ Approved and Ready for Execution
