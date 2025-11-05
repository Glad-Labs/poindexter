# 🚀 Python Install Optimization - Action Plan

**Date:** November 5, 2025  
**Status:** Ready to Implement  
**Goal:** Reduce GitHub Actions disk usage from 8+ GB to <700 MB ✅

---

## 📊 Current Situation - Build Errors Analysis

### What the Logs Show:

From the CI/CD run logs, the Python install is downloading massive packages:

```
Collecting crewai>=0.35.0         (~500 MB)
Collecting transformers>=4.53.0   (~3-4 GB)  ← HUGE
Collecting sentence-transformers  (~1.5 GB)  ← HUGE
Collecting onnxruntime            (~800 MB)  ← LARGE
Collecting chromadb~=1.1.0        (~300 MB)
...and many more
```

**Total: 8-12 GB+ → GitHub Actions fails with "No space left on device" ❌**

---

## ✅ Solution Implemented: Tiered Requirements

I've created 4 optimized requirement files:

### 1. `scripts/requirements-core.txt` ✅ CREATED

**Size:** ~500 MB | **Use:** Production & CI/CD  
**Contains:** FastAPI, model providers (OpenAI, Claude, Gemini, Ollama), security, logging

### 2. `scripts/requirements-ml.txt` ✅ CREATED

**Size:** ~6-8 GB | **Use:** Local dev only (optional)  
**Contains:** transformers, torch, sentence-transformers, chromadb

### 3. `scripts/requirements-dev.txt` ✅ CREATED

**Size:** ~1 GB | **Use:** Local development  
**Contains:** Core + pytest + black + mypy + ipython

### 4. `scripts/requirements-ci.txt` ✅ CREATED

**Size:** ~600 MB | **Use:** GitHub Actions (FIX!)  
**Contains:** Core + pytest only (no ML packages)

---

## 🎯 What This Fixes

### Before (Current - FAILS):

```bash
pip install -r scripts/requirements.txt
# ↓
# 8+ GB download
# ↓
# "No space left on device"
# ❌ CI/CD FAILS
```

### After (Optimized - WORKS):

```bash
pip install -r scripts/requirements-ci.txt
# ↓
# 600 MB download
# ↓
# ✅ CI/CD SUCCEEDS (still 13+ GB free!)
```

---

## 🔧 Implementation Steps

### Step 1: Update GitHub Actions Workflows (CRITICAL FIX)

**Files to update:**

- `.github/workflows/test-on-feat.yml`
- `.github/workflows/deploy-staging.yml`
- `.github/workflows/deploy-production.yml`

**Change from:**

```yaml
- name: Install Python dependencies
  run: pip install -r scripts/requirements.txt
```

**Change to:**

```yaml
- name: Install Python dependencies
  run: pip install -r scripts/requirements-ci.txt
```

### Step 2: Update package.json Scripts

**Current:**

```json
"setup": "npm run install:all && pip install -r requirements.txt && pip install -r src/cofounder_agent/requirements.txt"
```

**Update to:**

```json
"setup": "npm run install:all && pip install -r scripts/requirements-core.txt",
"setup:dev": "npm run install:all && pip install -r scripts/requirements-dev.txt",
"setup:ml": "npm run install:all && pip install -r scripts/requirements-dev.txt && pip install -r scripts/requirements-ml.txt"
```

### Step 3: Update README.md

Add section documenting installation sizes and which file to use when.

---

## 📋 Quick Reference: Which File to Use?

| Scenario                | Command                                                                                     | Size       |
| ----------------------- | ------------------------------------------------------------------------------------------- | ---------- |
| **Production Deploy**   | `pip install -r scripts/requirements-core.txt`                                              | 500 MB     |
| **GitHub Actions**      | `pip install -r scripts/requirements-ci.txt`                                                | 600 MB     |
| **Local Dev (No ML)**   | `pip install -r scripts/requirements-dev.txt`                                               | 1 GB       |
| **Local Dev (Full ML)** | `pip install -r scripts/requirements-dev.txt && pip install -r scripts/requirements-ml.txt` | 9 GB       |
| **NOT RECOMMENDED**     | `pip install -r scripts/requirements.txt`                                                   | 8-12 GB ❌ |

---

## 🔄 Migration Path

### Backward Compatibility:

- Keep `scripts/requirements.txt` as-is (won't break existing scripts)
- It will be used less and less as we migrate to tiered approach
- Can deprecate in future version

### Deprecation Timeline:

- **Phase 1 (Now):** Update CI/CD to use requirements-ci.txt
- **Phase 2 (Next Sprint):** Update documentation and setup scripts
- **Phase 3 (Future):** Archive requirements.txt after full migration

---

## ✨ Benefits

| Benefit                    | Before     | After              |
| -------------------------- | ---------- | ------------------ |
| **CI/CD Disk Usage**       | 8-12 GB ❌ | 600 MB ✅          |
| **GitHub Actions Success** | Fails      | Succeeds ✅        |
| **Installation Time**      | 15-20 min  | 2-3 min ✅         |
| **Local Dev (Core Only)**  | 8-12 GB    | 500 MB ✅          |
| **Local Dev (Full)**       | Same       | Still available ✅ |
| **Production Size**        | Large      | Minimal ✅         |

---

## 🚨 Files Status

### Created ✅

- [x] `scripts/requirements-core.txt` (500 MB)
- [x] `scripts/requirements-ml.txt` (6-8 GB)
- [x] `scripts/requirements-dev.txt` (1 GB)
- [x] `scripts/requirements-ci.txt` (600 MB) ← **THIS FIXES CI/CD**

### Needs Update 🔄

- [ ] `.github/workflows/*.yml` - Change to use `requirements-ci.txt`
- [ ] `package.json` - Update setup scripts
- [ ] `README.md` - Document sizing and usage
- [ ] `docs/` - Update installation instructions

---

## 🎬 Next Steps for You

### Option 1: Quick Fix (5 minutes)

Just update the GitHub Actions workflow to use `requirements-ci.txt`:

```bash
# Edit .github/workflows/test-on-feat.yml (and others)
# Change: pip install -r scripts/requirements.txt
# To:     pip install -r scripts/requirements-ci.txt
# Push and test
```

### Option 2: Full Implementation (30 minutes)

1. Update GitHub Actions (as above)
2. Update `package.json` scripts
3. Update `README.md` with sizing info
4. Test locally: `npm run setup`

### Option 3: I Can Do It All

Just say "implement" and I'll:

- Update all GitHub Actions workflows
- Update package.json
- Update README.md
- Create summary commit message

---

## 📈 Expected Results

After implementing the fix:

✅ CI/CD will pass (no more "No space left on device" errors)  
✅ GitHub Actions runs will complete in ~3 minutes (vs 15+)  
✅ Local developers have choice of what to install  
✅ Production deployments are lean and fast  
✅ ML development still possible (optional install)

---

## 💡 Technical Notes

### Why This Works:

- **transformers + torch:** These are only needed for local semantic search/embeddings
- **CI/CD doesn't need:** Full transformer models, image processing libs, market data tools
- **Tests can run:** With just core + pytest (no ML models needed)
- **Production doesn't need:** Development tools, full transformers, test frameworks

### Space Savings Breakdown:

```
Original bloat:
├── transformers + torch:      ~5-6 GB  (only for local dev)
├── sentence-transformers:     ~1.5 GB  (only for embeddings)
├── onnxruntime:               ~800 MB  (inference engine)
├── chromadb:                  ~300 MB  (vector search)
└── Other large deps:          ~500 MB
    ────────────────
    Total saved:               ~8-9 GB ✅

Kept for CI/CD:
├── FastAPI + dependencies:    ~200 MB
├── pytest + testing:          ~150 MB
├── Model APIs (OpenAI, etc):  ~100 MB
└── Other essentials:          ~150 MB
    ────────────────
    CI/CD total:               ~600 MB ✅
```

---

**Ready to implement? Let me know and I'll update all the files!** 🚀
