# Detailed Script Audit

**Date:** November 14, 2025  
**Objective:** Categorize all 50 scripts in scripts/ folder by usage status

## Scripts Used in npm Commands

**ACTIVE - Required in npm scripts:**

1. ✅ `select-env.js` - Environment selection (called by: `npm run env:select`)
2. ✅ `generate-sitemap.js` - Sitemap generation (called by: `web/public-site` postbuild)

**REQUIRED - Deployment dependencies:**

3. ✅ `requirements-core.txt` - Python core packages (called by: GitHub Actions deploy workflows)
4. ✅ `requirements.txt` - Python test packages (called by: GitHub Actions test workflows)

---

## PowerShell Setup & Utility Scripts (Active)

**ACTIVE - Development setup:**

5. ✅ `setup-dev.ps1` - Development environment setup
6. ✅ `setup-dependencies.ps1` - Install project dependencies
7. ✅ `setup-postgres.ps1` - PostgreSQL local setup

**ACTIVE - Service management:**

8. ✅ `kill-services.ps1` - Kill dev processes on ports
9. ✅ `kill-all-dev-ports.sh` - Kill all dev processes (bash version)
10. ✅ `check-services.ps1` - Check service status
11. ✅ `quick-test-api.ps1` - Quick API validation

**ACTIVE - Database utilities:**

12. ✅ `init-db.ps1` - Initialize database
13. ✅ `backup-tier1-db.sh` - Database backup

---

## PowerShell Test Scripts (Archive Referenced Only)

**ARCHIVED - Legacy test scripts (only referenced in docs/archive/):**

These are only mentioned in archived documentation and NOT called by npm or workflows:

- `test-blog-creator-simple.ps1` - Old test (Phase 1)
- `test-blog-creator-api.ps1` - Old test (Phase 1)
- `test-blog-post.ps1` - Old test (Phase 1)
- `test-cofounder-api.ps1` - Old test (Phase 1)
- `test-pipeline.ps1` - Old test (Phase 1)
- `test-pipeline-complete.ps1` - Old test (Phase 1)
- `test_pipeline_quick.ps1` - Old test (Phase 1)
- `test-pipeline-quick.ps1` - Old test (Phase 1)
- `test-unified-table.ps1` - Old test (Phase 1)
- `test-unified-table-new.ps1` - Old test (Phase 1)
- `Test-TaskPipeline.ps1` - Old test (Phase 1)
- `test-e2e-workflow.ps1` - Old test (Phase 1)

**Status:** ⚠️ CANDIDATES FOR DELETION - Never called by current npm/GitHub workflows

---

## PowerShell Diagnostic & Fix Scripts (Archive Referenced Only)

**ARCHIVED - Troubleshooting scripts (only in archived docs):**

- `diagnose-backend.ps1` - Backend diagnostics
- `diagnose-timeout.ps1` - Timeout diagnostics
- `diagnose-table.ps1` - Database table diagnostics
- `dev-troubleshoot.ps1` - Development troubleshooting
- `fix-ollama-warmup.ps1` - Ollama warmup fix
- `fix-strapi-build.ps1` - ⚠️ Strapi-related (DEPRECATED)

**Status:** ⚠️ CANDIDATES FOR DELETION - Only referenced in archived/troubleshooting docs

**Special Case:** `fix-strapi-build.ps1` - **DELETE** (Strapi no longer in architecture)

---

## PowerShell Deployment & Monitoring Scripts (Unknown Status)

**UNKNOWN - May be legacy or unused:**

- `deploy-tier1.ps1` - Tier 1 deployment (verify if still used)
- `deploy-tier1.sh` - Tier 1 deployment bash version
- `generate-secrets.ps1` - Secret generation (verify if automated or manual)
- `monitor-tier1-resources.ps1` - Resource monitoring (verify if active)
- `verify-phase1.ps1` - Phase 1 verification (legacy?)
- `verify-pipeline.ps1` - Pipeline verification (legacy?)

**Status:** ⏳ VERIFY USAGE - Check if these are called by deployment pipelines

---

## Python Test Scripts (in src/cofounder_agent/tests/)

**ACTIVE - Core test suite (pytest):**

These are proper test files in src/cofounder_agent/tests/ (NOT scripts folder):

- ✅ `test_main_endpoints.py` - API endpoint tests
- ✅ `test_orchestrator.py` - Orchestrator tests
- ✅ `test_e2e_fixed.py` - Smoke test suite (5-10 min)
- ✅ `test_e2e_comprehensive.py` - Full pipeline tests
- ✅ `test_api_integration.py` - API integration tests
- ✅ `test_content_pipeline.py` - Content pipeline tests
- ✅ `conftest.py` - pytest fixtures and config

**Status:** ✅ ACTIVE - Run via `npm run test:python`

**Note:** These are properly organized in src/cofounder_agent/tests/, NOT in scripts/ folder.

---

## Python Utility Scripts in scripts/ Folder (Legacy)

**LEGACY - Old utility scripts (scripts/ folder only):**

These are standalone Python files that appear to be legacy utilities:

- `run_tests.py` - Custom test runner (vs npm test) - ⚠️ DUPLICATE
- `start_backend_with_env.py` - Backend startup (vs npm/standard methods) - ⚠️ DUPLICATE
- `system_status.py` - System status (unclear purpose)
- `generate-content-batch.py` - Batch content generation (manual use only?)
- `test_content_generation.py` - Standalone test (redundant with test suite?)
- `test_persistence_independent.py` - Legacy test
- `test_sqlite_removal.py` - Legacy test (SQLite removal was Phase 1)
- `test_postgres_connection.py` - Connection test (unclear current use)
- `test_postgres_interactive.py` - Interactive DB test
- `check_strapi_posts.py` - ⚠️ Strapi-related (DEPRECATED)
- `check_task.py` - Check task (unclear purpose)
- `debug_tasks.py` - Debug tasks (unclear purpose)
- `show_task.py` - Show task (unclear purpose)
- `verify_fixes.py` - Legacy verification
- `verify_pipeline.py` - Legacy verification
- `verify_postgres.py` - Legacy verification
- `verify_tasks.py` - Legacy verification

**Status:** ⚠️ CANDIDATES FOR DELETION - Appear to be development artifacts

---

## Shell Scripts (.sh)

**ACTIVE:**

- ✅ `setup-postgres.sh` - PostgreSQL setup (bash version)
- ✅ `kill-all-dev-ports.sh` - Kill dev ports (bash version)
- ✅ `backup-tier1-db.sh` - Database backup (bash version)
- ✅ `implement_fastapi_cms.sh` - FastAPI CMS setup (bash version)
- ✅ `diagnose-timeout.sh` - Timeout diagnosis (bash version)

**DEPRECATED:**

- ❌ `restart-strapi-clean.sh` - **ALREADY DELETED** ✅

**Status:** Most are duplicates of PowerShell versions (cross-platform compatibility)

---

## Summary of Findings

### Scripts to Delete Immediately

**Status: ⚠️ DEPRECATED (Strapi removed):**

1. ❌ `rebuild-strapi.ps1` - **ALREADY DELETED** ✅
2. ❌ `restart-strapi-clean.sh` - **ALREADY DELETED** ✅
3. ❌ `fix-strapi-build.ps1` - **TO DELETE**
4. ❌ `check_strapi_posts.py` - **TO DELETE**

**Status: ⚠️ LEGACY TEST SCRIPTS (Never called):** 5. ❌ `test-blog-creator-simple.ps1` 6. ❌ `test-blog-creator-api.ps1` 7. ❌ `test-blog-post.ps1` 8. ❌ `test-cofounder-api.ps1` 9. ❌ `test-pipeline.ps1` 10. ❌ `test-pipeline-complete.ps1` 11. ❌ `test_pipeline_quick.ps1` 12. ❌ `test-pipeline-quick.ps1` 13. ❌ `test-unified-table.ps1` 14. ❌ `test-unified-table-new.ps1` 15. ❌ `Test-TaskPipeline.ps1` 16. ❌ `test-e2e-workflow.ps1`

**Status: ⚠️ REDUNDANT UTILITIES (Duplicate functionality):** 17. ❌ `run_tests.py` - Use `npm run test:python` instead 18. ❌ `start_backend_with_env.py` - Use npm scripts or direct python -m uvicorn 19. ❌ `generate-content-batch.py` - Unclear usage, appears to be dev artifact

**Status: ⚠️ LEGACY VERIFICATION SCRIPTS:** 20. ❌ `test_persistence_independent.py` 21. ❌ `test_sqlite_removal.py` 22. ❌ `verify_fixes.py` 23. ❌ `verify_pipeline.py` 24. ❌ `verify_postgres.py` 25. ❌ `verify_tasks.py`

**Status: ⏳ UNCLEAR PURPOSE - Need verification:** 26. `test_postgres_connection.py` - Verify if still needed for local dev 27. `test_postgres_interactive.py` - Verify if still needed for local dev 28. `check_task.py` - Verify purpose 29. `debug_tasks.py` - Verify purpose 30. `show_task.py` - Verify purpose 31. `system_status.py` - Verify purpose 32. `monitor-tier1-resources.ps1` - Verify if active monitoring is needed 33. `deploy-tier1.ps1` - Verify if still used for deployments 34. `deploy-tier1.sh` - Verify if still used for deployments 35. `generate-secrets.ps1` - Verify if manual or automated 36. `verify-phase1.ps1` - Verify if still needed 37. `verify-pipeline.ps1` - Verify if still needed

### Scripts to Keep (Verified Active)

**✅ Required/Active (11 scripts):**

1. `select-env.js` - npm env:select
2. `generate-sitemap.js` - Public site postbuild
3. `requirements-core.txt` - Deployment
4. `requirements.txt` - Testing
5. `setup-dev.ps1` - Dev setup
6. `setup-dependencies.ps1` - Dependency setup
7. `setup-postgres.ps1` - DB setup
8. `kill-services.ps1` - Service cleanup
9. `kill-all-dev-ports.sh` - Service cleanup
10. `check-services.ps1` - Service verification
11. `quick-test-api.ps1` - API validation

**✅ Infrastructure support (5 scripts):**

- `init-db.ps1` - DB initialization
- `backup-tier1-db.sh` - DB backup
- `setup-postgres.sh` - Cross-platform DB setup
- `implement_fastapi_cms.ps1` - CMS route setup
- `implement_fastapi_cms.sh` - CMS route setup (cross-platform)

**✅ Diagnostic (kept for troubleshooting):**

- `quick-test-api.ps1` - Quick API check
- `diagnose-backend.ps1` - Backend debugging
- `diagnose-timeout.ps1` - Timeout debugging
- `dev-troubleshoot.ps1` - General troubleshooting
- `diagnose-timeout.sh` - Cross-platform timeout diagnosis

### Total Scripts Inventory

- **Total in scripts/ folder:** 50 scripts
- **✅ Keep (verified active):** 11 core + 5 infrastructure + 3 diagnostic = **19 scripts**
- **❌ Delete (deprecated/redundant):** 25 scripts
- **⏳ Verify (unclear status):** 7 scripts

**Recommendation:** Delete 25, verify 7, keep 19

---

## Files Referenced in Documentation

**Archive Documentation References:**

- `test-blog-creator-simple.ps1` - Referenced in 4 archived docs
- `test-pipeline.ps1` - Referenced in 2 archived docs
- `test_content_generation.py` - Referenced in 2 archived docs

**All references are in `docs/archive/` only** - Safe to delete from active codebase
