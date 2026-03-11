# GitHub Actions Testing & Cost Analysis

**Last Updated:** March 10, 2026
**Status:** Analysis of current setup and optimization tracking

---

## 📊 Current Testing Setup

### Workflow Triggers & Testing

| Workflow                  | Trigger                  | Tests Run                  | Frequency           | Cost Impact                |
| ------------------------- | ------------------------ | -------------------------- | ------------------- | -------------------------- |
| **test-on-feat.yml**      | `feat/**` branches + PRs | Frontend + Backend         | On EVERY commit     | 🔴 HIGH - ~20 runs/month   |
| **deploy-staging.yml**    | `dev` branch push        | Frontend only              | After merge to dev  | 🟡 MEDIUM - ~10 runs/month |
| **deploy-production.yml** | `main` branch push       | Frontend + security checks | After merge to main | 🟢 LOW - ~2 runs/month     |

---

## 🔍 What Tests Are Currently Running?

### test-on-feat.yml (Feature Branches)

```
TRIGGER: Every push to feat/*, feature/* branches OR pull requests to dev/main
TESTS:
  ✅ npm run test:frontend:ci (52+ React tests)
  ✅ npm run test:python (41 backend tests) ← Added recently
  ❌ Security checks: NOT run on feature branches

FREQUENCY: Every time you push code to a feature branch
RUNS PER MONTH: ~20 runs (4-5 commits × ~5 active feature branches)
DURATION: ~3-5 minutes per run
COST: ~1.5-2.5 hours/month of compute time
```

### deploy-staging.yml (Dev Branch)

```
TRIGGER: Push to dev branch (after merge from feature branch)
TESTS:
  ✅ npm run test:frontend:ci (52+ React tests)
  ❌ npm run test:python NOT included (only frontend)
  ❌ Security checks: NOT run

FREQUENCY: After each feature merge to dev
RUNS PER MONTH: ~10 runs
DURATION: ~2-3 minutes
COST: ~0.3-0.5 hours/month
```

### deploy-production.yml (Main Branch)

```
TRIGGER: Push to main branch
TESTS:
  ✅ npm run test:frontend:ci (52+ React tests)
  ✅ npm audit (security checks)
  ❌ npm run test:python NOT included

FREQUENCY: When releasing to production
RUNS PER MONTH: ~2 runs
DURATION: ~4-5 minutes (includes security checks)
COST: ~0.1 hours/month
```

---

## 💰 Cost Breakdown

### GitHub Actions Pricing (Free Tier)

- **Free for public repos:** Unlimited minutes
- **Free for private repos:** 2,000 minutes/month free
- **Cost overages:** $0.25 per minute

### Your Current Usage

| Workflow          | Runs/Month | Duration        | Total Minutes      | Cost                   |
| ----------------- | ---------- | --------------- | ------------------ | ---------------------- |
| test-on-feat      | 20         | 4 min avg       | 80 min             | 🟢 Free (within quota) |
| deploy-staging    | 10         | 3 min avg       | 30 min             | 🟢 Free (within quota) |
| deploy-production | 2          | 5 min avg       | 10 min             | 🟢 Free (within quota) |
| **TOTAL**         | **32**     | **~12 min avg** | **~120 min/month** | **🟢 Free ($0)**       |

---

## ⚠️ Hidden Cost: Duplicate Testing

You have **backend tests running twice** on feature branches:

```
test-on-feat.yml
├── npm run test:python        (41 tests) ← Run 1
├── npm run test:python:smoke  (subset)   ← Run 2 (redundant!)
└── npm run test:frontend:ci   (52 tests)

= Every feature branch commit runs backend tests TWICE
```

**Cost of Redundancy:**

- Extra ~2-3 minutes per feature branch push
- Unnecessary compute cycles
- No additional value (same tests)

---

## 🎯 Your Preference: "Testing on staging & prod (main/dev) only"

### Recommended Setup

**Option A: Minimal Testing (Your Request)**

| Branch    | Tests                    | Cost          | When                                |
| --------- | ------------------------ | ------------- | ----------------------------------- |
| `feat/**` | ❌ NONE                  | Free          | Development - let devs test locally |
| `dev`     | ✅ Frontend only         | ~30 min/month | Before staging deploy               |
| `main`    | ✅ Full suite + security | ~10 min/month | Before production deploy            |

**Benefits:**

- ✅ Minimal GitHub Actions cost (~40 min/month = free tier)
- ✅ Developers test locally first
- ✅ Staging/production get full validation before deployment
- ✅ Faster feature branch feedback

**Risks:**

- ⚠️ Broken tests might only show up after merge to dev
- ⚠️ No automated safety net for feature work

---

**Option B: Balanced Approach (Recommended)**

| Branch    | Tests                                  | Cost          | When                      |
| --------- | -------------------------------------- | ------------- | ------------------------- |
| `feat/**` | ✅ Frontend + Backend (no duplication) | ~80 min/month | Catch issues early        |
| `dev`     | ✅ Frontend only                       | ~30 min/month | Quick gate before staging |
| `main`    | ✅ Full suite + security               | ~10 min/month | Final validation          |

**Benefits:**

- ✅ Catch bugs early in feature branches
- ✅ Still within free tier
- ✅ Faster deployments to staging/prod (already tested)
- ✅ Best of both worlds

**Risks:**

- None - well within free tier limits

---

**Option C: Full Testing Everywhere (Current Setup)**

| Branch    | Tests                             | Cost          | When                     |
| --------- | --------------------------------- | ------------- | ------------------------ |
| `feat/**` | ✅ Frontend + Backend (duplicate) | ~80 min/month | Catch issues early       |
| `dev`     | ✅ Frontend only                  | ~30 min/month | Before staging deploy    |
| `main`    | ✅ Full suite + security          | ~10 min/month | Before production deploy |

**Cost:** ~120 min/month = Still FREE (within 2,000 minute quota)

---

## 📋 Summary Table

| Scenario                                  | Total Min/Month | Monthly Cost | Recommendation              |
| ----------------------------------------- | --------------- | ------------ | --------------------------- |
| **Option A** (No feat testing)            | 40              | $0           | ✅ Cheapest, but risky      |
| **Option B** (No duplication)             | 120             | $0           | ✅✅ BEST - Safe & Free     |
| **Option C** (Current - with duplication) | 120             | $0           | ✅ Fine, but has redundancy |
| Option D (Test everything twice)          | 200+            | $0-5         | ❌ Wasteful                 |

---

## 🔧 What I'd Recommend

**Go with Option B (Balanced):**

```yaml
# Remove duplicate backend test run from test-on-feat.yml
# Keep: npm run test:frontend:ci + npm run test:python (NO duplicate smoke test)
# Add: npm run test:python to deploy-staging.yml (before deploy)
# Keep: Full suite in deploy-production.yml

Result: ✅ Feature branches validate code
  ✅ Staging validates again (safety net)
  ✅ Production validates again (final gate)
  ✅ Free tier (120 min/month)
  ✅ No redundant testing
```

---

## 🚀 If You Really Want "Main/Dev Only"

If you want testing **only** on staging/prod and NOT on feature branches:

**Update test-on-feat.yml:**

```yaml
# Change from running tests to just running linting/build check
- name: 🔨 Build check (no tests)
  run: npm run build --if-present
# This gives fast feedback without running full test suite
```

**Result:**

- ✅ ~5 min per feature branch run (just build check)
- ✅ ~10 min/month total (feature branches)
- ✅ ~40 min/month total (all workflows)
- ✅ Still free
- ⚠️ Broken tests only caught at dev/main merge

---

## 📊 Cost Projection (Next 12 Months)

| Scenario                 | Monthly    | Yearly     | Notes                   |
| ------------------------ | ---------- | ---------- | ----------------------- |
| Option A (dev/main only) | 40 min     | 480 min    | Stays free ✅           |
| Option B (balanced)      | 120 min    | 1,440 min  | Stays free ✅           |
| Option C (current)       | 120 min    | 1,440 min  | Stays free ✅           |
| Scenarios with overage   | 2,100+ min | $1-5/month | Only if you 10x commits |

**Bottom line:** You have plenty of headroom. GitHub's free tier is 2,000 minutes/month. Even if you 5x your current testing, you'd still be under free tier.

---

## ✅ Recommendation Summary

### What to Do

1. **Keep test-on-feat.yml running** (it's within free tier and catches bugs early)
2. **Remove the duplicate smoke test** (it's redundant, costs extra minutes)
3. **Verify deploy-staging.yml includes backend tests** (add `npm run test:python`)
4. **Keep deploy-production.yml full testing** (includes security checks)

### Result

- ✅ Safe, well-tested code
- ✅ No cost
- ✅ Fast feedback
- ✅ Multiple safety nets

### If You Really Want Less Testing

- Only run linting/build checks on feature branches
- Run full tests only on staging and production
- But this means broken tests appear after PR merge (caught at dev branch)
- Recommend staying with balanced approach instead

---

## 🔄 Next Steps

1. Review your preference:
   - **Option A:** Testing only on main/dev (skip feature branches)
   - **Option B:** Balanced (test everywhere, no duplication) ← **Recommended**
   - **Keep Current:** Already working, stays free

2. Let me know which you prefer, and I'll update the workflows accordingly

3. Changes needed if you choose Option A:
   - Simplify test-on-feat.yml (build check only)
   - Add full backend testing to deploy-staging.yml
   - Verify deploy-production.yml complete

---

**Questions about costs or testing strategy?** Let me know! 🚀
