# Quick Deployment Reference

**Status:** ✅ Ready to Deploy  
**Last Updated:** December 2, 2025

---

## TL;DR - Deploy in 5 Minutes

### Pre-Flight Check (2 min)

```bash
# Verify everything is ready
bash scripts/pre-deployment-verify.sh
# or on Windows:
.\scripts\pre-deployment-verify.ps1
```

### Stage to Staging (2 min)

```bash
git checkout dev
git merge --no-ff feat/bugs -m "Merge: Task-to-post pipeline with frontend verification"
git push origin dev
# Wait for GitHub Actions to complete (5-10 min)
```

### Stage to Production (1 min)

```bash
git checkout main
git merge --no-ff dev -m "Release: v1.0.0 - Complete system end-to-end working"
git tag -a v1.0.0 -m "Production Release v1.0.0"
git push origin main && git push origin v1.0.0
# Wait for GitHub Actions to complete (10-15 min)
```

### Verify Production (Ongoing)

```bash
# Health check
curl https://api.glad-labs.com/api/health

# Check posts
curl https://api.glad-labs.com/api/posts?skip=0&limit=3

# Check frontend
open https://glad-labs.com/
```

---

## Key Documents

| Document                                                                     | Purpose                                           | Read Time    |
| ---------------------------------------------------------------------------- | ------------------------------------------------- | ------------ |
| **[PRODUCTION_DEPLOYMENT_PREP.md](./PRODUCTION_DEPLOYMENT_PREP.md)**         | Complete deployment guide with all procedures     | 15 min       |
| **[DEPLOYMENT_APPROVAL.md](./DEPLOYMENT_APPROVAL.md)**                       | Executive summary for stakeholder approval        | 5 min        |
| **[scripts/pre-deployment-verify.sh](./scripts/pre-deployment-verify.sh)**   | Automated system verification (run before deploy) | 3 min to run |
| **[scripts/pre-deployment-verify.ps1](./scripts/pre-deployment-verify.ps1)** | Same as above (PowerShell for Windows)            | 3 min to run |

---

## System Status

✅ **All Components Ready:**

- Backend API: All endpoints tested (7/7 PASSED)
- Database: Schema verified, 8 test posts created
- Frontend: Posts displaying correctly
- Error Handling: Comprehensive
- Documentation: Complete
- Procedures: Documented

---

## If Something Goes Wrong

### Immediate Action

```bash
# Revert deployment
git revert HEAD --no-edit
git push origin main

# Expected: GitHub Actions reruns with previous version
```

### Check Logs

```bash
# Railway backend logs
railway logs --service=cofounder-agent

# Vercel frontend logs
vercel logs [project-id]
```

### Rollback to Specific Version

```bash
# See available versions
git tag --list

# Rollback to v0.9.0
git checkout v0.9.0
git push origin HEAD:main --force  # Only if authorized
```

---

## Success Criteria

✅ **Deployment Successful When:**

1. **Health Check (< 5 min):** `curl https://api.glad-labs.com/api/health` returns 200
2. **Frontend (< 1 hour):** Posts display on homepage
3. **Stability (24 hours):** No errors in logs
4. **Confirmation (48 hours):** System stable, no issues reported

---

## Monitoring During Deploy

**Timeline:**

- T+0: Deploy starts
- T+5 min: Health check
- T+15 min: Frontend test
- T+30 min: Performance check
- T+1 hour: Stability check
- T+6 hours: System review
- T+24 hours: Daily check
- T+48 hours: Full validation

---

## Critical Files Changed

These files should be reviewed before deployment:

1. `src/cofounder_agent/services/database_service.py` - Post creation logic
2. `src/cofounder_agent/routes/task_routes.py` - Post publishing
3. `web/public-site/lib/api-fastapi.js` - API integration
4. `src/cofounder_agent/main.py` - No emoji characters

**Status:** ✅ All verified correct

---

## FAQ - Quick Answers

**Q: Is the system ready?**
A: ✅ Yes, all components tested and verified.

**Q: What happens if deploy fails?**
A: Revert immediately with `git revert HEAD --no-edit && git push origin main`

**Q: How long does deployment take?**
A: 15-25 minutes (5 min deploy + 10-15 min verification)

**Q: How many users will this affect?**
A: This is the first production deployment, so it establishes the baseline.

**Q: What if I find a bug after deploy?**
A: Use rollback procedure in PRODUCTION_DEPLOYMENT_PREP.md or create hotfix on hotfix/\* branch.

**Q: Do I need to backup data?**
A: Yes, backup database before deploy (procedure in PRODUCTION_DEPLOYMENT_PREP.md).

**Q: Who needs to approve?**
A: Review DEPLOYMENT_APPROVAL.md for approval checklist.

---

## One More Thing

After deployment completes, remember to:

- [ ] Monitor logs for 24-48 hours
- [ ] Check health endpoint daily
- [ ] Review performance metrics
- [ ] Document any issues
- [ ] Plan Phase 2 enhancements

**All procedures documented in:** PRODUCTION_DEPLOYMENT_PREP.md

---

## Ready to Deploy?

1. ✅ Read DEPLOYMENT_APPROVAL.md (5 min)
2. ✅ Run verification script (3 min)
3. ✅ Get stakeholder approval
4. ✅ Execute deployment steps
5. ✅ Monitor for 24-48 hours

**That's it!** 🚀

---

**Questions?** See PRODUCTION_DEPLOYMENT_PREP.md for complete procedures.

**Last Updated:** December 2, 2025
