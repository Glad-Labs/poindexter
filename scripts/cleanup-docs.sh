#!/usr/bin/env bash
# Cross-platform Documentation Cleanup Script
# Intelligently organizes stray documentation files
# Usage: npm run docs:cleanup

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🧹 Starting intelligent documentation cleanup...${NC}\n"

# Counters
moved=0
skipped=0
errors=0

# Essential root files that should NEVER be moved
KEEP_IN_ROOT=(
    "README.md"
    "CLAUDE.md"
    "VERSION_HISTORY.md"
    "SECURITY.md"
    "VERSIONING_GUIDE.md"
    "DEBUG_GUIDE.md"
    "DEPLOYMENT_CHECKLIST.md"
    "LICENSE"
    "LICENSE.md"
)

# Ensure archive directories exist
mkdir -p archive/sessions
mkdir -p archive/phase1
mkdir -p archive/phase2
mkdir -p archive/phase3
mkdir -p archive/testing
mkdir -p archive/sprints
mkdir -p docs/reference
mkdir -p docs/troubleshooting
mkdir -p docs/components
mkdir -p docs/decisions

# Function to check if file should stay in root
should_keep_in_root() {
    local filename="$1"
    for keep in "${KEEP_IN_ROOT[@]}"; do
        if [[ "$filename" == "$keep" ]]; then
            return 0
        fi
    done
    return 1
}

# Function to safely move file with git
safe_move() {
    local src="$1"
    local dest="$2"
    
    if [[ ! -f "$src" ]]; then
        return 1
    fi
    
    # Check if it's a git-tracked file
    if git ls-files --error-unmatch "$src" &>/dev/null; then
        git mv "$src" "$dest" 2>/dev/null || mv "$src" "$dest"
    else
        mv "$src" "$dest"
    fi
    
    return 0
}

# Process Phase documentation
echo -e "${YELLOW}📁 Processing Phase documentation...${NC}"
for file in PHASE*.md; do
    [[ ! -f "$file" ]] && continue
    
    if [[ "$file" =~ PHASE_?1[^0-9] ]] || [[ "$file" =~ PHASE1_ ]]; then
        if safe_move "$file" "archive/phase1/"; then
            echo "  ✓ Moved $file → archive/phase1/"
            ((moved++))
        fi
    elif [[ "$file" =~ PHASE_?2[^0-9] ]] || [[ "$file" =~ PHASE2_ ]]; then
        if safe_move "$file" "archive/phase2/"; then
            echo "  ✓ Moved $file → archive/phase2/"
            ((moved++))
        fi
    elif [[ "$file" =~ PHASE_?3[^0-9] ]] || [[ "$file" =~ PHASE3_ ]]; then
        if safe_move "$file" "archive/phase3/"; then
            echo "  ✓ Moved $file → archive/phase3/"
            ((moved++))
        fi
    else
        echo "  ⚠ Skipped $file (unknown phase number)"
        ((skipped++))
    fi
done

# Process Session documentation
echo -e "${YELLOW}📝 Processing Session documentation...${NC}"
for pattern in "SESSION*.md" "CONSOLIDATION*.md" "*_SUMMARY.md" "*_FIX.md"; do
    for file in $pattern; do
        [[ ! -f "$file" ]] && continue
        [[ "$file" == "README.md" ]] && continue
        
        if safe_move "$file" "archive/sessions/"; then
            echo "  ✓ Moved $file → archive/sessions/"
            ((moved++))
        fi
    done
done

# Process Testing documentation
echo -e "${YELLOW}🧪 Processing Testing documentation...${NC}"
for pattern in "TESTING*.md" "TEST_*.md" "USER_TESTING*.md"; do
    for file in $pattern; do
        [[ ! -f "$file" ]] && continue
        
        if safe_move "$file" "archive/testing/"; then
            echo "  ✓ Moved $file → archive/testing/"
            ((moved++))
        fi
    done
done

# Process Sprint documentation
echo -e "${YELLOW}🏃 Processing Sprint documentation...${NC}"
for file in SPRINT*.md; do
    [[ ! -f "$file" ]] && continue
    
    if safe_move "$file" "archive/sprints/"; then
        echo "  ✓ Moved $file → archive/sprints/"
        ((moved++))
    fi
done

# Process implementation/status files
echo -e "${YELLOW}📊 Processing implementation and status files...${NC}"
for pattern in "IMPLEMENTATION*.md" "*_STATUS.md" "*_REPORT.md" "*_COMPLETE.md"; do
    for file in $pattern; do
        [[ ! -f "$file" ]] && continue
        [[ "$file" == "README.md" ]] && continue
        
        if safe_move "$file" "archive/sessions/"; then
            echo "  ✓ Moved $file → archive/sessions/"
            ((moved++))
        fi
    done
done

# Process remaining markdown files in root (excluding essential files)
echo -e "${YELLOW}🔍 Processing remaining markdown files...${NC}"
for file in *.md; do
    [[ ! -f "$file" ]] && continue
    
    # Check if file should stay in root
    if should_keep_in_root "$file"; then
        echo "  → Keeping $file in root (essential)"
        continue
    fi
    
    # Analyze content and move to appropriate location
    case "$file" in
        *GUIDE*)
            if safe_move "$file" "docs/reference/"; then
                echo "  ✓ Moved $file → docs/reference/"
                ((moved++))
            fi
            ;;
        *TROUBLESHOOT*|*DEBUG*|*ERROR*)
            if safe_move "$file" "docs/troubleshooting/"; then
                echo "  ✓ Moved $file → docs/troubleshooting/"
                ((moved++))
            fi
            ;;
        *ADR*|*DECISION*)
            if safe_move "$file" "docs/decisions/"; then
                echo "  ✓ Moved $file → docs/decisions/"
                ((moved++))
            fi
            ;;
        *COMPONENT*|*MODULE*)
            if safe_move "$file" "docs/components/"; then
                echo "  ✓ Moved $file → docs/components/"
                ((moved++))
            fi
            ;;
        *)
            # Default: move to docs/reference
            if safe_move "$file" "docs/reference/"; then
                echo "  ✓ Moved $file → docs/reference/"
                ((moved++))
            fi
            ;;
    esac
done

# Summary
echo ""
echo -e "${GREEN}✅ Documentation cleanup complete!${NC}"
echo -e "  ${GREEN}✓${NC} Moved: ${moved} files"
if [[ $skipped -gt 0 ]]; then
    echo -e "  ${YELLOW}⚠${NC} Skipped: ${skipped} files"
fi
if [[ $errors -gt 0 ]]; then
    echo -e "  ${RED}✗${NC} Errors: ${errors} files"
fi
echo ""
echo -e "${BLUE}Archive structure:${NC}"
echo "  - archive/sessions/    → Session summaries, implementation reports"
echo "  - archive/phase1/      → Phase 1 documentation"
echo "  - archive/phase2/      → Phase 2 documentation"
echo "  - archive/phase3/      → Phase 3 documentation"
echo "  - archive/testing/     → Testing documentation"
echo "  - archive/sprints/     → Sprint reports"
echo ""
echo -e "${BLUE}Docs structure:${NC}"
echo "  - docs/reference/      → Guides and references"
echo "  - docs/troubleshooting/→ Debugging and error guides"
echo "  - docs/decisions/      → ADRs and decision records"
echo "  - docs/components/     → Component-specific docs"
echo ""
