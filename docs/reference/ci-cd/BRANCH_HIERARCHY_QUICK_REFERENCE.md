# 🚀 Quick Reference: New Branch Hierarchy

**Last Updated:** October 24, 2025 | **Status:** ✅ Ready to Use

---

## 📋 Branch Quick Reference

```
FEATURE DEVELOPMENT (No CI/CD)
git checkout -b feat/my-feature
git push origin feat/my-feature        # ✅ Push 1
git push origin feat/my-feature        # ✅ Push 2
git push origin feat/my-feature        # ✅ Push 3 (50+ times OK - no cost)

TESTING GATE (Before Staging)
git checkout dev
git merge feat/my-feature
git push origin dev                    # ⏳ Tests: 8-10 min

STAGING DEPLOYMENT
git checkout staging
git merge dev
git push origin staging                # ⏳ Deploy: 15-20 min

PRODUCTION DEPLOYMENT
git checkout main
git merge staging
git push origin main                   # ⏳ Deploy: 20-25 min
```

---

## 🎯 Decision Tree

```
Question: What branch should I use?
├─ "I'm actively developing"
│  └─ → Use feat/* branch (commit freely, no CI/CD)
│
├─ "I finished a feature and want testing"
│  └─ → Merge to dev (full test suite runs)
│
├─ "Tests passed, ready to test in environment"
│  └─ → Merge to staging (deploy to staging)
│
└─ "Verified in staging, ready for production"
   └─ → Merge to main (deploy to production)
```

---

## 💰 Cost Per Action

| Action       | Branch  | Cost    | Time         |
| ------------ | ------- | ------- | ------------ |
| Push code    | feat/\* | $0      | Instant ✅   |
| Commit 50x   | feat/\* | $0      | Instant ✅   |
| Merge & push | dev     | ~10 min | 8-10 min ⏳  |
| Merge & push | staging | ~20 min | 15-20 min ⏳ |
| Merge & push | main    | ~25 min | 20-25 min ⏳ |

---

## 🔄 Typical Workflow

### Monday: Start Feature

```bash
git checkout -b feat/implement-settings
# ... work all day ...
git add . && git commit -m "feat: add settings page"
git push origin feat/implement-settings    # Cost: $0 ✅
```

### Monday-Wednesday: Frequent Commits

```bash
# Throughout the day, commit as much as you want
git push origin feat/implement-settings    # Cost: $0 ✅
git push origin feat/implement-settings    # Cost: $0 ✅
git push origin feat/implement-settings    # Cost: $0 ✅
```

### Wednesday: Ready for Testing

```bash
git checkout dev
git merge feat/implement-settings
git push origin dev                        # Cost: ~10 min (tests run) ⏳
```

### Wednesday Afternoon: Tests Pass

```bash
git checkout staging
git merge dev
git push origin staging                    # Cost: ~20 min (deploy) ⏳
# Test in staging...
```

### Thursday: Verify & Release

```bash
git checkout main
git merge staging
git push origin main                       # Cost: ~25 min (deploy + security) ⏳
# Now live on production!
```

---

## ✅ Checklist

- [ ] Read `docs/04-DEVELOPMENT_WORKFLOW.md` (complete overview)
- [ ] Review `.github/workflows/test-on-dev.yml` (new workflow)
- [ ] Configure GitHub Secrets for staging (see guide)
- [ ] Configure GitHub Secrets for production (see guide)
- [ ] Create your first feat/\* branch
- [ ] Commit 10+ times (verify no workflows trigger)
- [ ] Merge to dev and watch tests run
- [ ] Follow promotion path: dev → staging → main

---

## 🆘 Troubleshooting

### Q: Why don't my feat/\* commits trigger workflows?

**A:** That's intentional! Feature branches have no workflows so you can commit freely. Tests run when you merge to dev.

### Q: How do I test my code before merging to dev?

**A:** Run locally: `npm test` (or `npm run test:frontend:ci`, `npm run test:python`)

### Q: What if tests fail on dev?

**A:** Fix them on your feat/\* branch, commit, merge to dev again. Tests rerun automatically.

### Q: Can I skip staging and go straight to main?

**A:** Not recommended, but technically yes. Staging provides an extra verification gate before production.

### Q: What's the cost if I commit 100 times on feat/?

**A:** $0 - feature branches have no workflows!

### Q: Will I run out of free minutes?

**A:** No. Your usage is ~230 min/month on GitHub's 2,000 min free tier (11.5%).

---

## 📊 Monthly Cost at Different Commit Volumes

| Scenario    | Commits/Month | Feature Cost | Dev Tests | Staging | Prod    | Total    |
| ----------- | ------------- | ------------ | --------- | ------- | ------- | -------- |
| Quiet       | 10            | $0           | 50 min    | 50 min  | 25 min  | ~125 min |
| Normal      | 50            | $0           | 80 min    | 100 min | 50 min  | ~230 min |
| Active      | 200           | $0           | 100 min   | 150 min | 75 min  | ~325 min |
| Very Active | 500           | $0           | 150 min   | 200 min | 100 min | ~450 min |

**Result:** All scenarios = 🟢 **FREE** (under 2,000 min/month limit)

---

## 🎁 What You Get

✅ **Unlimited feature branch commits** - No CI/CD cost  
✅ **Full test suite on merge** - Caught bugs early  
✅ **Automatic deployments** - From staging → production  
✅ **Security validation** - Before production deploy  
✅ **Complete free tier** - Stay under 2,000 min/month  
✅ **Clear branch hierarchy** - Know exactly what each branch does

---

## 🔗 Related Documents

- **BRANCH_HIERARCHY_IMPLEMENTATION_SUMMARY.md** - Complete detailed implementation guide
- **GITHUB_ACTIONS_REFERENCE.md** - Cost breakdown analysis

---

**You're ready to go! Start committing on feat/\* branches today 🚀**
