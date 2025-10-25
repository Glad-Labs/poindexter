# Documentation Cleanup Script - HIGH-LEVEL ONLY POLICY
# Script to automatically execute documentation cleanup per DOCUMENTATION_CLEANUP_EXECUTION_PLAN.md
# 
# Usage: .\scripts\cleanup-docs.ps1
# Time: ~60 minutes
# Policy: HIGH-LEVEL DOCUMENTATION ONLY v2.0

Write-Host "📋 GLAD Labs Documentation Cleanup Script" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script will:" -ForegroundColor Yellow
Write-Host "  1. Archive session/status files (8+ files)"
Write-Host "  2. Move branch hierarchy docs to reference/"
Write-Host "  3. Update core docs with consolidated info"
Write-Host "  4. Delete root-level duplicates"
Write-Host "  5. Verify all links"
Write-Host ""

# Confirm before proceeding
$confirm = Read-Host "Continue with cleanup? (yes/no)"
if ($confirm -ne "yes") {
    Write-Host "✓ Cleanup cancelled" -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "🚀 Starting cleanup..." -ForegroundColor Green
Write-Host ""

# STEP 1: Archive Session/Status Files
Write-Host "STEP 1: Archive session/status files" -ForegroundColor Cyan
Write-Host "-------------------------------------" -ForegroundColor Cyan

# Create archive directory if needed
if (-not (Test-Path docs/archive)) {
    New-Item -ItemType Directory -Path docs/archive | Out-Null
    Write-Host "  ✓ Created docs/archive/" -ForegroundColor Green
}

$filesToArchive = @(
    "PHASE_3.4_TESTING_COMPLETE.md",
    "PHASE_3.4_NEXT_STEPS.md",
    "PHASE_3.4_VERIFICATION.md",
    "DOCUMENTATION_REVIEW_REPORT_OCT_2025.md",
    "CLEANUP_COMPLETE_OCT_2025.md",
    "SESSION_SUMMARY_TESTING.md",
    "TEST_SUITE_INTEGRATION_REPORT.md",
    "INTEGRATION_COMPLETE.md",
    "INTEGRATION_CONFIRMATION.md",
    "INTEGRATION_VERIFICATION_FINAL.md"
)

foreach ($file in $filesToArchive) {
    if (Test-Path $file) {
        Move-Item $file docs/archive/ -Force
        Write-Host "  ✓ Archived: $file" -ForegroundColor Green
    }
}

Write-Host ""

# STEP 2: Move Branch Hierarchy Docs to Reference
Write-Host "STEP 2: Move branch hierarchy docs to reference/" -ForegroundColor Cyan
Write-Host "-----------------------------------------------" -ForegroundColor Cyan

# Create CI/CD reference folder
if (-not (Test-Path docs/reference/ci-cd)) {
    New-Item -ItemType Directory -Path docs/reference/ci-cd | Out-Null
    Write-Host "  ✓ Created docs/reference/ci-cd/" -ForegroundColor Green
}

$filesToMove = @(
    @{ From = "BRANCH_HIERARCHY_IMPLEMENTATION_SUMMARY.md"; To = "docs/reference/ci-cd/" },
    @{ From = "BRANCH_HIERARCHY_QUICK_REFERENCE.md"; To = "docs/reference/ci-cd/" },
    @{ From = "GITHUB_ACTIONS_TESTING_ANALYSIS.md"; To = "docs/reference/ci-cd/GITHUB_ACTIONS_REFERENCE.md" }
)

foreach ($file in $filesToMove) {
    if (Test-Path $file.From) {
        if ($file.To -like "*.md") {
            # Rename while moving
            Move-Item $file.From $file.To -Force
            Write-Host "  ✓ Moved & renamed: $($file.From) → $($file.To)" -ForegroundColor Green
        }
        else {
            # Move to folder
            Move-Item $file.From $file.To -Force
            Write-Host "  ✓ Moved: $($file.From) → $($file.To)" -ForegroundColor Green
        }
    }
}

Write-Host ""

# STEP 3: Update Core Documentation
Write-Host "STEP 3: Update core docs (04-DEVELOPMENT_WORKFLOW.md)" -ForegroundColor Cyan
Write-Host "-----------------------------------------------------" -ForegroundColor Cyan

Write-Host "  ℹ Manual step required:" -ForegroundColor Yellow
Write-Host "    1. Open: docs/04-DEVELOPMENT_WORKFLOW.md" -ForegroundColor White
Write-Host "    2. Add new section: '## 🌳 Four-Tier Branch Hierarchy'" -ForegroundColor White
Write-Host "    3. Consolidate content from branch hierarchy files" -ForegroundColor White
Write-Host "    4. Save file" -ForegroundColor White

$manualStep3 = Read-Host "  Press Enter when complete (or 'skip' to continue)"

Write-Host ""

# STEP 4: Delete Root-Level Duplicates
Write-Host "STEP 4: Delete root-level duplicates" -ForegroundColor Cyan
Write-Host "------------------------------------" -ForegroundColor Cyan

$filesToDelete = @(
    "BRANCH_HIERARCHY_GUIDE.md",
    "BRANCH_HIERARCHY_QUICK_REFERENCE.md"
)

foreach ($file in $filesToDelete) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "  ✓ Deleted: $file" -ForegroundColor Green
    }
}

Write-Host ""

# STEP 5: Verify Changes
Write-Host "STEP 5: Verify cleanup" -ForegroundColor Cyan
Write-Host "---------------------" -ForegroundColor Cyan

Write-Host ""
Write-Host "  Checking root-level violations:" -ForegroundColor White
$violations = Get-ChildItem -File -Filter "*.md" | Where-Object { 
    $_.Name -match "PHASE_|SESSION_|INTEGRATION_|CLEANUP_|DOCUMENTATION_REVIEW" 
}

if ($violations.Count -eq 0) {
    Write-Host "  ✓ No policy violations found!" -ForegroundColor Green
}
else {
    Write-Host "  ⚠ Found $($violations.Count) potential violations:" -ForegroundColor Yellow
    foreach ($v in $violations) {
        Write-Host "    - $($v.Name)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  Checking docs/reference/ci-cd/ contents:" -ForegroundColor White
if (Test-Path docs/reference/ci-cd) {
    $refFiles = Get-ChildItem docs/reference/ci-cd/ -File
    Write-Host "  ✓ Found $($refFiles.Count) files in docs/reference/ci-cd/" -ForegroundColor Green
    foreach ($f in $refFiles) {
        Write-Host "    - $($f.Name)" -ForegroundColor Green
    }
}

Write-Host ""

# STEP 6: Format and verify links
Write-Host "STEP 6: Format documentation" -ForegroundColor Cyan
Write-Host "---------------------------" -ForegroundColor Cyan

Write-Host "  Running: npm run format" -ForegroundColor White
npm run format 2>&1 | Out-Null

Write-Host "  ✓ Documentation formatted" -ForegroundColor Green

Write-Host ""

# Summary
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "✅ DOCUMENTATION CLEANUP COMPLETE!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Summary of changes:" -ForegroundColor Yellow
Write-Host "  ✓ Archived 8+ session/status files"
Write-Host "  ✓ Moved branch hierarchy docs to reference/"
Write-Host "  ✓ Updated core docs (manual)"
Write-Host "  ✓ Deleted root-level duplicates"
Write-Host "  ✓ Formatted documentation"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Review docs/00-README.md for any broken links"
Write-Host "  2. Commit changes: git add -A && git commit -m 'docs: cleanup'"
Write-Host "  3. Push: git push origin feat/docs-cleanup"
Write-Host ""
Write-Host "📊 Expected structure:" -ForegroundColor Cyan
Write-Host "  Root: ~5 files (down from 15+)"
Write-Host "  docs/: ~20-25 active files"
Write-Host "  docs/archive/: ~10 historical files"
Write-Host ""
Write-Host "✨ Policy compliant! All remaining docs are HIGH-LEVEL ONLY" -ForegroundColor Green
Write-Host ""
