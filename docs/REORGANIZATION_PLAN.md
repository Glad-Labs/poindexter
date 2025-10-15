# 🗂️ Repository Reorganization Plan

**Date:** October 15, 2025  
**Goal:** Clean up root directory by moving files into logical subdirectories

---

## 📊 Current State

**Root Directory Files:** 24 files (too many!)

### Files Currently in Root:

**Configuration Files (9):**
- `.dockerignore`
- `.gitignore`
- `.gitlab-ci.yml`
- `.markdownlint.json`
- `.prettierrc.json`
- `postcss.config.js`
- `pyproject.toml`
- `package.json` / `package-lock.json`
- `glad-labs-workspace.code-workspace`

**Environment Files (2):**
- `.env`
- `.env.example`

**Documentation Files (8):**
- `README.md` ← KEEP IN ROOT
- `ARCHITECTURE.md`
- `CODEBASE_ANALYSIS_REPORT.md`
- `data_schemas.md`
- `GLAD-LABS-STANDARDS.md`
- `INSTALLATION_SUMMARY.md`
- `NEXT_STEPS.md`
- `TESTING.md`

**Dependency Files (3):**
- `requirements.txt`
- `requirements-core.txt`
- `setup-dependencies.ps1`

**Other (2):**
- `LICENSE` ← KEEP IN ROOT

---

## 🎯 Proposed Structure

```
glad-labs-website/
├── README.md                          ← Keep (primary)
├── LICENSE                            ← Keep (standard)
├── package.json                       ← Keep (monorepo root)
├── package-lock.json                  ← Keep (monorepo root)
├── .gitignore                         ← Keep (standard)
├── .env                               ← Keep (standard)
├── .env.example                       ← Keep (standard)
│
├── .config/                           ← NEW: Configuration files
│   ├── .dockerignore                 (move from root)
│   ├── .gitlab-ci.yml                (move from root)
│   ├── .markdownlint.json            (move from root)
│   ├── .prettierrc.json              (move from root)
│   ├── postcss.config.js             (move from root)
│   ├── pyproject.toml                (move from root)
│   └── glad-labs-workspace.code-workspace (move from root)
│
├── docs/                              ← EXISTING: Move more docs here
│   ├── README.md                     (existing index)
│   ├── MASTER_DOCS_INDEX.md          (existing)
│   ├── ARCHITECTURE.md               (move from root)
│   ├── INSTALLATION_SUMMARY.md       (move from root)
│   ├── TESTING.md                    (move from root)
│   ├── NEXT_STEPS.md                 (move from root)
│   ├── GLAD_LABS_STANDARDS.md        (move from root)
│   ├── CODEBASE_ANALYSIS_REPORT.md   (move from root)
│   ├── data_schemas.md               (move from root)
│   ├── DEVELOPER_GUIDE.md            (existing)
│   ├── CI_CD_TEST_REVIEW.md          (existing)
│   └── ... (other existing docs)
│
├── scripts/                           ← NEW: Setup and utility scripts
│   ├── setup-dependencies.ps1        (move from root)
│   ├── requirements.txt              (move from root)
│   └── requirements-core.txt         (move from root)
│
├── src/                               (existing)
├── web/                               (existing)
├── cms/                               (existing)
├── cloud-functions/                   (existing)
└── logs/                              (existing)
```

---

## 📋 Migration Steps

### Phase 1: Create New Directories ✅

```bash
mkdir .config
mkdir scripts
```

### Phase 2: Move Configuration Files

```bash
# Move to .config/
git mv .dockerignore .config/
git mv .gitlab-ci.yml .config/
git mv .markdownlint.json .config/
git mv .prettierrc.json .config/
git mv postcss.config.js .config/
git mv pyproject.toml .config/
git mv glad-labs-workspace.code-workspace .config/
```

### Phase 3: Move Documentation Files

```bash
# Move to docs/
git mv ARCHITECTURE.md docs/
git mv CODEBASE_ANALYSIS_REPORT.md docs/
git mv data_schemas.md docs/
git mv GLAD-LABS-STANDARDS.md docs/
git mv INSTALLATION_SUMMARY.md docs/
git mv NEXT_STEPS.md docs/
git mv TESTING.md docs/
```

### Phase 4: Move Scripts and Dependencies

```bash
# Move to scripts/
git mv setup-dependencies.ps1 scripts/
git mv requirements.txt scripts/
git mv requirements-core.txt scripts/
```

### Phase 5: Update References

**Files that need updating:**

1. **README.md** - Update documentation links
2. **docs/MASTER_DOCS_INDEX.md** - Update all file paths
3. **package.json** - Update script paths if needed
4. **.gitlab-ci.yml** - Update paths (after moving)
5. **setup-dependencies.ps1** - Update requirements.txt paths (after moving)
6. **All documentation** - Update cross-references

---

## 🔍 Files That Reference Paths

### Files to Check and Update:

1. **README.md**
   - Links to docs: `./ARCHITECTURE.md` → `./docs/ARCHITECTURE.md`
   - Links to TESTING.md, INSTALLATION_SUMMARY.md, etc.

2. **docs/MASTER_DOCS_INDEX.md**
   - Links to all moved docs
   - Update relative paths

3. **package.json**
   - Check if any scripts reference moved files
   - Lint scripts may reference `.prettierrc.json`, `.markdownlint.json`

4. **.gitlab-ci.yml**
   - Will be in `.config/` but CI tools expect it in root
   - **RECOMMENDATION: Keep in root or symlink**

5. **setup-dependencies.ps1**
   - References `requirements.txt` and `requirements-core.txt`
   - Update to `../requirements.txt` or keep relative paths

6. **VS Code Workspace**
   - `glad-labs-workspace.code-workspace` may have absolute paths
   - Check settings after move

7. **All .md files in docs/**
   - Check for relative links to moved files
   - Update cross-references

---

## ⚠️ Special Considerations

### Files That Should Stay in Root:

1. **`.gitlab-ci.yml`** - GitLab expects this in root
   - **Decision:** Keep in root (industry standard)

2. **`.dockerignore`** - Docker expects this in root
   - **Decision:** Keep in root (industry standard)

3. **`.gitignore`** - Git expects this in root
   - **Decision:** Already staying in root

4. **`.env` / `.env.example`** - Standard location
   - **Decision:** Already staying in root

5. **`package.json` / `package-lock.json`** - Monorepo root
   - **Decision:** Already staying in root

6. **`README.md`** - Primary documentation
   - **Decision:** Already staying in root

7. **`LICENSE`** - Standard location
   - **Decision:** Already staying in root

### Modified Plan (Practical):

Only move files that don't break tooling:

**Move to docs/:**
- ✅ ARCHITECTURE.md
- ✅ CODEBASE_ANALYSIS_REPORT.md
- ✅ data_schemas.md
- ✅ GLAD-LABS-STANDARDS.md
- ✅ INSTALLATION_SUMMARY.md
- ✅ NEXT_STEPS.md
- ✅ TESTING.md

**Move to scripts/:**
- ✅ setup-dependencies.ps1
- ✅ requirements.txt
- ✅ requirements-core.txt

**Move to .vscode/:**
- ✅ glad-labs-workspace.code-workspace

**Keep in root:**
- `.dockerignore` (Docker convention)
- `.gitlab-ci.yml` (GitLab convention)
- `.gitignore` (Git convention)
- `.markdownlint.json` (Linter looks here)
- `.prettierrc.json` (Prettier looks here)
- `postcss.config.js` (PostCSS looks here)
- `pyproject.toml` (Python tools look here)
- `.env` / `.env.example` (Standard)
- `package.json` / `package-lock.json` (Monorepo)
- `README.md` (Primary doc)
- `LICENSE` (Standard)

---

## 📊 Before & After

### Before:
```
Root: 24 files (cluttered)
```

### After:
```
Root: 13 files (essential config only)
docs/: +7 documentation files
scripts/: +3 dependency/setup files
.vscode/: +1 workspace file
```

**Reduction:** 24 → 13 files in root (46% reduction) ✅

---

## ✅ Recommended Actions

**Immediate (Safe to move):**

1. Create directories:
   ```bash
   mkdir scripts
   mkdir .vscode  # if doesn't exist
   ```

2. Move documentation (7 files):
   ```bash
   git mv ARCHITECTURE.md docs/
   git mv CODEBASE_ANALYSIS_REPORT.md docs/
   git mv data_schemas.md docs/
   git mv GLAD-LABS-STANDARDS.md docs/
   git mv INSTALLATION_SUMMARY.md docs/
   git mv NEXT_STEPS.md docs/
   git mv TESTING.md docs/
   ```

3. Move scripts (3 files):
   ```bash
   git mv setup-dependencies.ps1 scripts/
   git mv requirements.txt scripts/
   git mv requirements-core.txt scripts/
   ```

4. Move workspace file:
   ```bash
   git mv glad-labs-workspace.code-workspace .vscode/
   ```

5. Update references in:
   - README.md
   - docs/MASTER_DOCS_INDEX.md
   - All docs with cross-references
   - setup-dependencies.ps1
   - Any other files referencing moved paths

**Total files to move:** 11 files  
**Final root count:** 13 files (clean!) ✅

---

## 🎯 Final Result

**Clean Root Directory:**
```
glad-labs-website/
├── .dockerignore              (Docker standard)
├── .env                       (Environment)
├── .env.example               (Environment template)
├── .gitignore                 (Git standard)
├── .gitlab-ci.yml             (CI/CD standard)
├── .markdownlint.json         (Linter config)
├── .prettierrc.json           (Formatter config)
├── LICENSE                    (License)
├── package.json               (Monorepo)
├── package-lock.json          (Dependencies)
├── postcss.config.js          (PostCSS config)
├── pyproject.toml             (Python config)
├── README.md                  (Primary docs)
│
├── docs/                      (All documentation)
├── scripts/                   (Setup & dependencies)
├── .vscode/                   (IDE settings)
├── src/                       (Source code)
├── web/                       (Frontend)
├── cms/                       (Content management)
└── ... (other directories)
```

**Benefits:**
- ✅ Root reduced from 24 → 13 files
- ✅ All documentation centralized in `docs/`
- ✅ Setup scripts organized in `scripts/`
- ✅ Industry standards respected (CI, Docker, Git)
- ✅ Tool configurations remain discoverable
- ✅ Clear separation of concerns

---

**Ready to execute?** Review this plan and I can help implement the changes! 🚀
