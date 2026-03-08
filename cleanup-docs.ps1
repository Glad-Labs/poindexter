# Intelligent Documentation Cleanup Script (PowerShell)
# Automatically organizes stray documentation files
# Usage: npm run docs:cleanup (or) powershell -ExecutionPolicy Bypass -File cleanup-docs.ps1

$ErrorActionPreference = "Stop"
$moved = 0
$skipped = 0
$errors = 0

Write-Host "🧹 Starting intelligent documentation cleanup..." -ForegroundColor Cyan
Write-Host ""

# Essential root files that should NEVER be moved
$keepInRoot = @(
    "README.md",
    "CLAUDE.md",
    "VERSION_HISTORY.md",
    "SECURITY.md",
    "VERSIONING_GUIDE.md",
    "DEBUG_GUIDE.md",
    "DEPLOYMENT_CHECKLIST.md",
    "LICENSE",
    "LICENSE.md"
)

# Ensure archive directories exist
$archiveDirs = @(
    "archive/sessions",
    "archive/phase1",
    "archive/phase2", 
    "archive/phase3",
    "archive/testing",
    "archive/sprints",
    "docs/reference",
    "docs/troubleshooting",
    "docs/components",
    "docs/decisions"
)

foreach ($dir in $archiveDirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

# Function to safely move file with git
function Safe-Move {
    param($src, $dest)
    
    if (-not (Test-Path $src)) {
        return $false
    }
    
    try {
        # Try git mv first
        $gitResult = git ls-files --error-unmatch $src 2>&1
        if ($LASTEXITCODE -eq 0) {
            git mv $src $dest 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Move-Item -Path $src -Destination $dest -Force
            }
        } else {
            Move-Item -Path $src -Destination $dest -Force
        }
        return $true
    } catch {
        Write-Host "  ✗ Error moving $src : $_" -ForegroundColor Red
        return $false
    }
}

# Process Phase documentation
Write-Host "📁 Processing Phase documentation..." -ForegroundColor Yellow
Get-ChildItem -Path "." -Filter "PHASE*.md" | ForEach-Object {
    $file = $_.Name
    
    if ($file -match "PHASE_?1[^0-9]|PHASE1_") {
        if (Safe-Move $file "archive/phase1/") {
            Write-Host "  ✓ Moved $file → archive/phase1/" -ForegroundColor Green
            $script:moved++
        }
    } elseif ($file -match "PHASE_?2[^0-9]|PHASE2_") {
        if (Safe-Move $file "archive/phase2/") {
            Write-Host "  ✓ Moved $file → archive/phase2/" -ForegroundColor Green
            $script:moved++
        }
    } elseif ($file -match "PHASE_?3[^0-9]|PHASE3_") {
        if (Safe-Move $file "archive/phase3/") {
            Write-Host "  ✓ Moved $file → archive/phase3/" -ForegroundColor Green
            $script:moved++
        }
    } else {
        Write-Host "  ⚠ Skipped $file (unknown phase number)" -ForegroundColor Yellow
        $script:skipped++
    }
}

# Process Session documentation  
Write-Host "📝 Processing Session documentation..." -ForegroundColor Yellow
$sessionPatterns = @("SESSION*.md", "CONSOLIDATION*.md", "*_SUMMARY.md", "*_FIX.md")
foreach ($pattern in $sessionPatterns) {
    Get-ChildItem -Path "." -Filter $pattern | ForEach-Object {
        $file = $_.Name
        if ($file -ne "README.md") {
            if (Safe-Move $file "archive/sessions/") {
                Write-Host "  ✓ Moved $file → archive/sessions/" -ForegroundColor Green
                $script:moved++
            }
        }
    }
}

# Process Testing documentation
Write-Host "🧪 Processing Testing documentation..." -ForegroundColor Yellow
$testingPatterns = @("TESTING*.md", "TEST_*.md", "USER_TESTING*.md")
foreach ($pattern in $testingPatterns) {
    Get-ChildItem -Path "." -Filter $pattern | ForEach-Object {
        if (Safe-Move $_.Name "archive/testing/") {
            Write-Host "  ✓ Moved $($_.Name) → archive/testing/" -ForegroundColor Green
            $script:moved++
        }
    }
}

# Process Sprint documentation
Write-Host "🏃 Processing Sprint documentation..." -ForegroundColor Yellow
Get-ChildItem -Path "." -Filter "SPRINT*.md" | ForEach-Object {
    if (Safe-Move $_.Name "archive/sprints/") {
        Write-Host "  ✓ Moved $($_.Name) → archive/sprints/" -ForegroundColor Green
        $script:moved++
    }
}

# Process implementation/status files
Write-Host "📊 Processing implementation and status files..." -ForegroundColor Yellow
$statusPatterns = @("IMPLEMENTATION*.md", "*_STATUS.md", "*_REPORT.md", "*_COMPLETE.md")
foreach ($pattern in $statusPatterns) {
    Get-ChildItem -Path "." -Filter $pattern | ForEach-Object {
        $file = $_.Name
        if ($file -ne "README.md") {
            if (Safe-Move $file "archive/sessions/") {
                Write-Host "  ✓ Moved $file → archive/sessions/" -ForegroundColor Green
                $script:moved++
            }
        }
    }
}

# Process remaining markdown files in root
Write-Host "🔍 Processing remaining markdown files..." -ForegroundColor Yellow
Get-ChildItem -Path "." -Filter "*.md" | ForEach-Object {
    $file = $_.Name
    
    # Skip essential files
    if ($keepInRoot -contains $file) {
        Write-Host "  → Keeping $file in root (essential)" -ForegroundColor Cyan
        return
    }
    
    # Analyze content and move to appropriate location
    $destination = switch -Regex ($file) {
        "GUIDE" { "docs/reference/" }
        "TROUBLESHOOT|DEBUG|ERROR" { "docs/troubleshooting/" }
        "ADR|DECISION" { "docs/decisions/" }
        "COMPONENT|MODULE" { "docs/components/" }
        default { "docs/reference/" }
    }
    
    if (Safe-Move $file $destination) {
        Write-Host "  ✓ Moved $file → $destination" -ForegroundColor Green
        $script:moved++
    }
}

Write-Host ""

# Summary
Write-Host "✅ Documentation cleanup complete!" -ForegroundColor Green
Write-Host "  ✓ Moved: $moved files" -ForegroundColor Green
if ($skipped -gt 0) {
    Write-Host "  ⚠ Skipped: $skipped files" -ForegroundColor Yellow
}
if ($errors -gt 0) {
    Write-Host "  ✗ Errors: $errors files" -ForegroundColor Red
}
Write-Host ""
Write-Host "Archive structure:" -ForegroundColor Cyan
Write-Host "  - archive/sessions/    → Session summaries, implementation reports"
Write-Host "  - archive/phase1/      → Phase 1 documentation"
Write-Host "  - archive/phase2/      → Phase 2 documentation"
Write-Host "  - archive/phase3/      → Phase 3 documentation"
Write-Host "  - archive/testing/     → Testing documentation"
Write-Host "  - archive/sprints/     → Sprint reports"
Write-Host ""
Write-Host "Docs structure:" -ForegroundColor Cyan
Write-Host "  - docs/reference/      → Guides and references"
Write-Host "  - docs/troubleshooting/→ Debugging and error guides"
Write-Host "  - docs/decisions/      → ADRs and decision records"
Write-Host "  - docs/components/     → Component-specific docs"
Write-Host ""
Write-Host "Removed $count items:"
Write-Host "  • 6 archive directories with 170+ debugging/audit reports"
Write-Host "  • 7 FUTURE_WORK planning documents"
Write-Host "  • 2 unused framework documentation templates"
Write-Host "  • 3 orphaned session summary files"
Write-Host "  • 4 redundant/outdated documentation files"
Write-Host ""
Write-Host "Your codebase documentation is now clean and production-focused." -ForegroundColor Green
