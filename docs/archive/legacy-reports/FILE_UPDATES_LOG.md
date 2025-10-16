# 🔄 File Reference Updates

## Files Moved

### Documentation (Root → docs/):

- `ARCHITECTURE.md` → `docs/ARCHITECTURE.md`
- `CODEBASE_ANALYSIS_REPORT.md` → `docs/CODEBASE_ANALYSIS_REPORT.md`
- `data_schemas.md` → `docs/data_schemas.md`
- `GLAD-LABS-STANDARDS.md` → `docs/GLAD-LABS-STANDARDS.md`
- `INSTALLATION_SUMMARY.md` → `docs/INSTALLATION_SUMMARY.md`
- `NEXT_STEPS.md` → `docs/NEXT_STEPS.md`
- `TESTING.md` → `docs/TESTING.md`

### Scripts (Root → scripts/):

- `setup-dependencies.ps1` → `scripts/setup-dependencies.ps1`
- `requirements.txt` → `scripts/requirements.txt`
- `requirements-core.txt` → `scripts/requirements-core.txt`

### Workspace (Root → .vscode/):

- `glad-labs-workspace.code-workspace` → `.vscode/glad-labs-workspace.code-workspace`

## Files Requiring Updates

### 1. README.md (Root)

**Current references:**

- `./ARCHITECTURE.md` → `./docs/ARCHITECTURE.md`
- `./INSTALLATION_SUMMARY.md` → `./docs/INSTALLATION_SUMMARY.md`
- `./GLAD_LABS_STANDARDS.md` → `./docs/GLAD-LABS-STANDARDS.md`

### 2. docs/MASTER_DOCS_INDEX.md

**Current references:**

- `../ARCHITECTURE.md` → `./ARCHITECTURE.md`
- `../INSTALLATION_SUMMARY.md` → `./INSTALLATION_SUMMARY.md`
- `../GLAD_LABS_STANDARDS.md` → `./GLAD-LABS-STANDARDS.md`
- `../CODEBASE_ANALYSIS_REPORT.md` → `./CODEBASE_ANALYSIS_REPORT.md`
- `../data_schemas.md` → `./data_schemas.md`
- `../TESTING.md` → `./TESTING.md`

### 3. docs/README.md

**Current references:**

- `../ARCHITECTURE.md` → `./ARCHITECTURE.md`
- `../INSTALLATION_SUMMARY.md` → `./INSTALLATION_SUMMARY.md`
- `../GLAD_LABS_STANDARDS.md` → `./GLAD-LABS-STANDARDS.md`
- `../data_schemas.md` → `./data_schemas.md`
- `./TESTING.md` → `./TESTING.md`

### 4. docs/DEVELOPER_GUIDE.md

**Current references:**

- `../ARCHITECTURE.md` → `./ARCHITECTURE.md`
- `../INSTALLATION_SUMMARY.md` → `./INSTALLATION_SUMMARY.md`
- `../GLAD_LABS_STANDARDS.md` → `./GLAD-LABS-STANDARDS.md`

### 5. docs/REVIEW_COMPLETE_SUMMARY.md

**Current references:**

- `../ARCHITECTURE.md` → `./ARCHITECTURE.md`
- `../INSTALLATION_SUMMARY.md` → `./INSTALLATION_SUMMARY.md`

### 6. docs/CODEBASE_HEALTH_REPORT.md

**Current references:**

- Check for any relative paths to moved files

### 7. docs/NEXT_STEPS.md

**Current references:**

- `./docs/MASTER_DOCS_INDEX.md` → `./MASTER_DOCS_INDEX.md`
- `./docs/CODEBASE_HEALTH_REPORT.md` → `./CODEBASE_HEALTH_REPORT.md`
- `./docs/DEVELOPER_GUIDE.md` → `./DEVELOPER_GUIDE.md`
- `./docs/TEST_IMPLEMENTATION_SUMMARY.md` → `./TEST_IMPLEMENTATION_SUMMARY.md`
- `./docs/CI_CD_TEST_REVIEW.md` → `./CI_CD_TEST_REVIEW.md`
- `./ARCHITECTURE.md` → `./ARCHITECTURE.md`

### 8. scripts/setup-dependencies.ps1

**Current references:**

- `requirements.txt` → ALREADY IN SAME DIR (no change)
- `requirements-core.txt` → ALREADY IN SAME DIR (no change)

### 9. package.json

**Check for:**

- Any scripts referencing moved files
- Paths to setup-dependencies.ps1

### 10. docs/DOCUMENTATION_SUMMARY.md

**Current references:**

- `../ARCHITECTURE.md` → `./ARCHITECTURE.md`
- `../INSTALLATION_SUMMARY.md` → `./INSTALLATION_SUMMARY.md`
- `../GLAD_LABS_STANDARDS.md` → `./GLAD-LABS-STANDARDS.md`
- `../data_schemas.md` → `./data_schemas.md`

## Update Commands

```powershell
# Update all references in one go using PowerShell
$files = @(
    "README.md",
    "docs/MASTER_DOCS_INDEX.md",
    "docs/README.md",
    "docs/DEVELOPER_GUIDE.md",
    "docs/REVIEW_COMPLETE_SUMMARY.md",
    "docs/NEXT_STEPS.md",
    "docs/DOCUMENTATION_SUMMARY.md"
)

foreach ($file in $files) {
    if (Test-Path $file) {
        $content = Get-Content $file -Raw

        # From root README (going into docs)
        $content = $content -replace '\(\./ARCHITECTURE\.md\)', '(./docs/ARCHITECTURE.md)'
        $content = $content -replace '\(\./INSTALLATION_SUMMARY\.md\)', '(./docs/INSTALLATION_SUMMARY.md)'
        $content = $content -replace '\(\./GLAD_LABS_STANDARDS\.md\)', '(./docs/GLAD-LABS-STANDARDS.md)'
        $content = $content -replace '\(\./TESTING\.md\)', '(./docs/TESTING.md)'

        # From docs/ files (same directory now)
        $content = $content -replace '\(\.\./ARCHITECTURE\.md\)', '(./ARCHITECTURE.md)'
        $content = $content -replace '\(\.\./INSTALLATION_SUMMARY\.md\)', '(./INSTALLATION_SUMMARY.md)'
        $content = $content -replace '\(\.\./GLAD_LABS_STANDARDS\.md\)', '(./GLAD-LABS-STANDARDS.md)'
        $content = $content -replace '\(\.\./GLAD-LABS-STANDARDS\.md\)', '(./GLAD-LABS-STANDARDS.md)'
        $content = $content -replace '\(\.\./CODEBASE_ANALYSIS_REPORT\.md\)', '(./CODEBASE_ANALYSIS_REPORT.md)'
        $content = $content -replace '\(\.\./data_schemas\.md\)', '(./data_schemas.md)'
        $content = $content -replace '\(\.\./TESTING\.md\)', '(./TESTING.md)'

        # From docs/NEXT_STEPS.md (references were already in docs/)
        $content = $content -replace '\(\./docs/MASTER_DOCS_INDEX\.md\)', '(./MASTER_DOCS_INDEX.md)'
        $content = $content -replace '\(\./docs/CODEBASE_HEALTH_REPORT\.md\)', '(./CODEBASE_HEALTH_REPORT.md)'
        $content = $content -replace '\(\./docs/DEVELOPER_GUIDE\.md\)', '(./DEVELOPER_GUIDE.md)'
        $content = $content -replace '\(\./docs/TEST_IMPLEMENTATION_SUMMARY\.md\)', '(./TEST_IMPLEMENTATION_SUMMARY.md)'
        $content = $content -replace '\(\./docs/CI_CD_TEST_REVIEW\.md\)', '(./CI_CD_TEST_REVIEW.md)'
        $content = $content -replace '\(\./ARCHITECTURE\.md\)', '(./ARCHITECTURE.md)'

        Set-Content $file -Value $content -NoNewline
        Write-Host "Updated: $file" -ForegroundColor Green
    }
}
```
