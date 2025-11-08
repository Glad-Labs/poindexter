#!/usr/bin/env pwsh
# Cleanup script - remove all debug and session-specific files from root

$filesToDelete = @(
    'API_TO_STRAPI_TEST_GUIDE.md',
    'COFOUNDER_AGENT_QUICK_START.md',
    'COFOUNDER_AGENT_REVIEW_COMPLETE.md',
    'COFOUNDER_AGENT_STARTUP_GUIDE.md',
    'CRITICAL_ISSUES_DIAGNOSIS.md',
    'ENDPOINT_FIX_SUMMARY.md',
    'FINAL_CHECKLIST.md',
    'FINAL_STATUS_REPORT.txt',
    'FINAL_TEST_RESULT.txt',
    'FIXES_APPLIED.md',
    'FIXES_VERIFICATION_FINAL.md',
    'FIX_PLAN_SESSION_2.md',
    'FIX_SESSION_SUMMARY_COMPLETE.md',
    'FIX_SUMMARY_AND_NEXT_STEPS.md',
    'FORM_VALIDATION_FIX.md',
    'IMPLEMENTATION_STATUS.md',
    'INTEGRATION_CHECKLIST.md',
    'INTEGRATION_COMPLETE.md',
    'OLLAMA_DIAGNOSTIC_AND_FIX.md',
    'OLLAMA_FIXES_IMPLEMENTATION_PLAN.md',
    'OLLAMA_MISTRAL_DIAGNOSTIC.md',
    'OLLAMA_TESTING_COMPLETE.md',
    'OLLAMA_TESTS_READY.txt',
    'PHASE1_ACTION_ITEMS.py',
    'PIPELINE_SUCCESS.md',
    'PIPELINE_TEST_RESULTS.md',
    'PRODUCTION_FIX_SUMMARY.md',
    'PRODUCTION_LAUNCH_GUIDE.md',
    'QUICK_START_INTEGRATION.md',
    'QUICK_START_TASK_PIPELINE.md',
    'QUICK_START_TESTING.md',
    'QUICK_TEST_GUIDE.md',
    'QUICK_VERIFICATION_GUIDE.md',
    'RAILWAY_ENV_CONFIG.md',
    'README_INTEGRATION_COMPLETE.md',
    'README_TASK_EXECUTOR.md',
    'README_TASK_PIPELINE_DEBUG.md',
    'SCHEMA_SETUP_SUMMARY.txt',
    'SCRIPTS_CREATED_SUMMARY.txt',
    'SESSION_COMPLETE.md',
    'SESSION_SUMMARY_TASK_EXECUTOR.md',
    'SOLUTION_READY.txt',
    'START_HERE.md',
    'START_HERE.txt',
    'START_HERE_COFOUNDER_AGENT.md',
    'START_HERE_DEBUG_PACKAGE.md',
    'START_HERE_NOW.md',
    'STRAPI_CONSOLE_LOGS_EXPLAINED.md',
    'STRAPI_FIX_IMMEDIATE_ACTIONS.md',
    'STRAPI_SIGTERM_FIX_GUIDE.md',
    'SUCCESS_SUMMARY.md',
    'SYSTEM_INTEGRATION_GUIDE.md',
    'TASK_CREATION_DEBUG_GUIDE.md',
    'TASK_EXECUTOR_IMPLEMENTATION.md',
    'TASK_MANAGEMENT_FIX_SUMMARY.md',
    'TASK_PIPELINE_ANALYSIS.md',
    'TASK_PIPELINE_COMPLETE_FIX.md',
    'TASK_PIPELINE_FIX.md',
    'TASK_PIPELINE_STATUS_REPORT.md',
    'TESTING_GUIDE_QUICK.md',
    'TESTING_REFERENCE.md',
    'TROUBLESHOOTING_PIPELINE.md',
    'UPGRADE_CONTENT_GENERATION.md',
    'VERBOSE_LOGGING_AND_FIXES.md',
    'VERIFICATION_CHECKLIST.md',
    'VERIFICATION_REPORT.md',
    'backend.log',
    'backend_startup.log',
    'check_db.py',
    'check_tasks.py',
    'debug_task_pipeline.ps1',
    'fix-bom.js',
    'fix-bom.ps1',
    'integration_test.py',
    'test_api_to_strapi.ps1',
    'test_complete_fix.py',
    'test_content_generation.py',
    'test_graphql.py',
    'test_graphql_get.py',
    'test_poindexter.py',
    'test_production_pipeline.py',
    'test_publisher_output.txt',
    'test_strapi_post.py',
    'test_strapi_publisher.py',
    'test_tasks.py',
    'test_task_direct.py',
    'test_task_pipeline.py',
    'CLEANUP_DELETE_LIST.txt'
)

$deletedCount = 0
$failedCount = 0

foreach ($file in $filesToDelete) {
    if (Test-Path $file) {
        try {
            Remove-Item $file -Force
            $deletedCount++
            Write-Host "[OK] $file"
        } catch {
            $failedCount++
            Write-Host "[ERR] $file - $_"
        }
    }
}

Write-Host ""
Write-Host "[DONE] Deleted $deletedCount files, Failed: $failedCount"
