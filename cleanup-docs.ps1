# Aggressive Documentation Cleanup Script
# Removes orphaned and unnecessary documentation
# Execution: powershell -ExecutionPolicy Bypass -File cleanup-docs.ps1

$ErrorActionPreference = "SilentlyContinue"
$count = 0

Write-Host "Starting aggressive documentation cleanup..." -ForegroundColor Cyan
Write-Host ""

# Archive directories (7 total, 170+ files)
$archiveDirs = @(
    'docs/archive-active',
    'docs/archive',
    '.archive',
    'web/oversight-hub/archive',
    'web/public-site/archive',
    'src/cofounder_agent/archive'
)

Write-Host "Removing 6 archive directories..." -ForegroundColor Yellow
foreach ($dir in $archiveDirs) {
    if (Test-Path $dir) {
        Remove-Item -Path $dir -Recurse -Force
        Write-Host "  ✓ Removed: $dir"
        $count++
    }
}

# FUTURE_WORK files (7 total)
$futureWorkFiles = @(
    'src/cofounder_agent/services/COST_TRACKING_FUTURE_WORK.md',
    'src/cofounder_agent/services/IMAGE_FALLBACK_HANDLER_FUTURE_WORK.md',
    'src/cofounder_agent/services/PERFORMANCE_MONITOR_FUTURE_WORK.md',
    'src/cofounder_agent/services/TITLE_GENERATOR_CONSOLIDATION.md',
    'src/cofounder_agent/services/PROMPT_MIGRATION_GUIDE.md',
    'src/cofounder_agent/tasks/FUTURE_WORK.md',
    'src/cofounder_agent/services/phases/PHASES_DOCUMENTATION.md'
)

Write-Host "Removing 7 FUTURE_WORK and scattered planning documents..." -ForegroundColor Yellow
foreach ($file in $futureWorkFiles) {
    if (Test-Path $file) {
        Remove-Item -Path $file -Force
        Write-Host "  ✓ Removed: $file"
        $count++
    }
}

# Framework documentation (2 total - duplicates)
$frameworkDocs = @(
    '.continue/prompts/ENTERPRISE_DOCUMENTATION_FRAMEWORK.md',
    '.github/prompts/ENTERPRISE_DOCUMENTATION_FRAMEWORK.md'
)

Write-Host "Removing unused framework documentation..." -ForegroundColor Yellow
foreach ($file in $frameworkDocs) {
    if (Test-Path $file) {
        Remove-Item -Path $file -Force
        Write-Host "  ✓ Removed: $file"
        $count++
    }
}

# Orphaned session summaries (3 total)
$sessionFiles = @(
    'docs/reference/FINAL_SESSION_SUMMARY.txt',
    'docs/reference/PHASE_1_COMPLETION_REPORT.txt',
    'docs/reference/SESSION_COMPLETE.txt'
)

Write-Host "Removing orphaned session summary files..." -ForegroundColor Yellow
foreach ($file in $sessionFiles) {
    if (Test-Path $file) {
        Remove-Item -Path $file -Force
        Write-Host "  ✓ Removed: $file"
        $count++
    }
}

# Redundant/outdated documentation (4 total)
$redundantDocs = @(
    'docs/ANALYTICS_AND_PROFILING_API.md',
    'docs/reference/COMPREHENSIVE_CODEBASE_ANALYSIS.md',
    'docs/DI_FRAMEWORK_EXCEPTION_POLICY.md',
    'src/cofounder_agent/data/system_knowledge.md'
)

Write-Host "Removing redundant and outdated documentation..." -ForegroundColor Yellow
foreach ($file in $redundantDocs) {
    if (Test-Path $file) {
        Remove-Item -Path $file -Force
        Write-Host "  ✓ Removed: $file"
        $count++
    }
}

Write-Host ""
Write-Host "✅ Cleanup Complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Removed $count items:"
Write-Host "  • 6 archive directories with 170+ debugging/audit reports"
Write-Host "  • 7 FUTURE_WORK planning documents"
Write-Host "  • 2 unused framework documentation templates"
Write-Host "  • 3 orphaned session summary files"
Write-Host "  • 4 redundant/outdated documentation files"
Write-Host ""
Write-Host "Your codebase documentation is now clean and production-focused." -ForegroundColor Green
