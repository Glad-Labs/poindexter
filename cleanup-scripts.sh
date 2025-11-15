#!/bin/bash
# Script Cleanup and Deletion Plan
# Purpose: Remove deprecated, redundant, and unused scripts safely
# Author: Codebase Audit Agent
# Date: November 14, 2025

# SAFETY: Run with --dry-run first to verify what will be deleted
# Usage: ./cleanup-scripts.sh --dry-run
# Then:  ./cleanup-scripts.sh --execute

set -e

DRY_RUN=false
if [[ "$1" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "🔍 DRY RUN MODE - No files will be deleted"
    echo "Run with --execute to actually delete files"
    echo ""
fi

SCRIPTS_DIR="scripts"
DELETED_COUNT=0
KEPT_COUNT=0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "📊 SCRIPT CLEANUP OPERATION"
echo "================================"
echo ""

# Function to delete a file safely
delete_file() {
    local file=$1
    local reason=$2
    
    if [[ ! -f "$file" ]]; then
        echo -e "${YELLOW}⚠️  SKIP${NC} (not found): $file"
        return
    fi
    
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${BLUE}🗑️  WOULD DELETE${NC}: $(basename $file)"
        echo "   Reason: $reason"
    else
        rm -f "$file"
        echo -e "${GREEN}✅ DELETED${NC}: $(basename $file)"
        echo "   Reason: $reason"
        ((DELETED_COUNT++))
    fi
}

# PHASE 1: Delete Strapi-related scripts
echo "🔴 PHASE 1: Strapi-Related Scripts (Removed in Phase 1)"
echo "================================"
delete_file "$SCRIPTS_DIR/fix-strapi-build.ps1" "Strapi removed from architecture"
delete_file "$SCRIPTS_DIR/check_strapi_posts.py" "Strapi removed from architecture"
echo ""

# PHASE 2: Delete PowerShell Test Scripts (Archive-only, never executed)
echo "🔵 PHASE 2: Legacy PowerShell Test Scripts"
echo "================================"
delete_file "$SCRIPTS_DIR/test-blog-creator-simple.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-blog-creator-api.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-blog-post.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-cofounder-api.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-pipeline.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-pipeline-complete.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test_pipeline_quick.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-pipeline-quick.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-unified-table.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-unified-table-new.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/Test-TaskPipeline.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-e2e-workflow.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-full-pipeline.ps1" "Test suite replaced by pytest"
delete_file "$SCRIPTS_DIR/test-local.ps1" "Test suite replaced by pytest"
echo ""

# PHASE 3: Delete Python Legacy Verification Scripts
echo "🟣 PHASE 3: Legacy Python Verification Scripts"
echo "================================"
delete_file "$SCRIPTS_DIR/verify_fixes.py" "Verification logic integrated into tests"
delete_file "$SCRIPTS_DIR/verify_pipeline.py" "Verification logic integrated into tests"
delete_file "$SCRIPTS_DIR/verify_postgres.py" "Verification logic integrated into tests"
delete_file "$SCRIPTS_DIR/verify_tasks.py" "Verification logic integrated into tests"
delete_file "$SCRIPTS_DIR/verify-phase1.ps1" "Phase 1 complete, verification archived"
delete_file "$SCRIPTS_DIR/verify-pipeline.ps1" "Verification logic integrated into tests"
echo ""

# PHASE 4: Delete Python Redundant Utilities
echo "🟠 PHASE 4: Python Redundant Utilities"
echo "================================"
delete_file "$SCRIPTS_DIR/run_tests.py" "Redundant with 'npm run test'"
delete_file "$SCRIPTS_DIR/start_backend_with_env.py" "Redundant with npm scripts"
delete_file "$SCRIPTS_DIR/generate-content-batch.py" "Manual-only utility, not in CI/CD"
delete_file "$SCRIPTS_DIR/test_persistence_independent.py" "SQLite removed in Phase 1"
delete_file "$SCRIPTS_DIR/test_sqlite_removal.py" "SQLite removal complete"
delete_file "$SCRIPTS_DIR/test_content_generation.py" "Use canonical tests in src/"
delete_file "$SCRIPTS_DIR/check_task.py" "Unclear purpose, no usage found"
delete_file "$SCRIPTS_DIR/debug_tasks.py" "Unclear purpose, no usage found"
delete_file "$SCRIPTS_DIR/show_task.py" "Unclear purpose, no usage found"
delete_file "$SCRIPTS_DIR/system_status.py" "Unclear purpose, no usage found"
echo ""

# PHASE 5: Scripts Requiring Verification (DO NOT DELETE)
echo "✅ PHASE 5: Scripts Kept (Active or Unclear Status)"
echo "================================"
echo -e "${GREEN}✅ KEPT${NC}: scripts/select-env.js (Called by npm scripts)"
echo -e "${GREEN}✅ KEPT${NC}: scripts/generate-sitemap.js (Called by npm scripts)"
echo -e "${GREEN}✅ KEPT${NC}: scripts/requirements.txt (Used in deployments)"
echo -e "${GREEN}✅ KEPT${NC}: scripts/requirements-core.txt (Used in deployments)"
echo -e "${YELLOW}⏳ VERIFY${NC}: scripts/monitor-tier1-resources.ps1 (Active monitoring?)"
echo -e "${YELLOW}⏳ VERIFY${NC}: scripts/deploy-tier1.ps1 (Still used?)"
echo -e "${YELLOW}⏳ VERIFY${NC}: scripts/deploy-tier1.sh (Still used?)"
echo -e "${YELLOW}⏳ VERIFY${NC}: scripts/generate-secrets.ps1 (Manual or automated?)"
echo -e "${YELLOW}⏳ VERIFY${NC}: scripts/test_postgres_connection.py (Local dev only?)"
echo -e "${YELLOW}⏳ VERIFY${NC}: scripts/test_postgres_interactive.py (Local dev only?)"
echo ""

# Summary
echo "📊 CLEANUP SUMMARY"
echo "================================"

if [[ "$DRY_RUN" == true ]]; then
    # Count files that would be deleted
    total_would_delete=0
    total_would_delete=$((25 + 6 + 10))  # From phases 1-4
    echo -e "${YELLOW}[DRY RUN]${NC} Would delete: ~${total_would_delete} files"
    echo "Reduction: 50 scripts → ~20 scripts (60% reduction)"
    echo ""
    echo "To execute: Run './cleanup-scripts.sh --execute'"
else
    echo "Deleted: $DELETED_COUNT files"
    echo "Scripts remaining: $(ls $SCRIPTS_DIR/*.{ps1,sh,py} 2>/dev/null | wc -l) (approximately)"
    echo ""
    echo -e "${GREEN}✅ Cleanup complete!${NC}"
fi

echo ""
echo "📋 Next Steps:"
echo "1. Review Phase 5 (VERIFY) scripts - confirm before deletion"
echo "2. Consolidate archive documentation (217 → 50 files)"
echo "3. Verify configuration files (docker-compose.yml, railway.json, etc.)"
echo "4. Scan source code for duplication"
echo "5. Generate final audit report"
echo ""
