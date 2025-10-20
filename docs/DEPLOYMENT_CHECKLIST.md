# Production Deployment Checklist

## 🚀 Pre-Deployment Verification

### Local Testing
- [ ] Run `npm test` in `web/public-site` - confirm all 4 test suites pass (5 tests)
- [ ] Run `npm run build` - confirm build completes with no errors
- [ ] Run `npm run lint` - confirm no linting errors
- [ ] Test locally: `npm run dev` and verify pages load
- [ ] Test dynamic pages: archive, categories, tags all accessible

### Environment Variables
- [ ] Verify `NEXT_PUBLIC_STRAPI_API_URL` is set in Vercel dashboard
- [ ] Value should be: `https://your-strapi-instance.railway.app`
- [ ] Test connectivity from local machine:
  ```powershell
  .\scripts\diagnose-timeout.ps1
  ```

### Code Review
- [ ] Verify timeout is set to 10 seconds in `lib/api.js`
- [ ] Verify error handling exists in `getStaticPaths` for:
  - `pages/archive/[page].js`
  - `pages/category/[slug].js`
  - `pages/tag/[slug].js`
- [ ] Verify error handling exists in `getStaticProps` (should return `notFound: true`)
- [ ] Verify `vercel.json` has schema and security headers

### Dependencies
- [ ] Run `npm list` and confirm all packages are at correct versions
- [ ] Specifically check: Jest 30.2.0, Next.js 15.1.0
- [ ] No warnings or conflicts in package.json

## ✅ Pre-Deployment Commits

### Verify Git History
```bash
git log --oneline -10
```

Should show these recent commits (in reverse chronological order):
1. "docs: add quick summary for 504 timeout fix"
2. "fix: resolve Vercel 504 timeout errors by adding request timeouts and error handling"
3. "docs: add comprehensive guide for Vercel 504 timeout resolution"
4. "fix: update vercel.json to follow Vercel best practices"
5. "docs: add VERCEL_CONFIG_FIX documentation"

If any are missing, run:
```bash
git status
git add .
git commit -m "<message>"
```

## 🌐 Vercel Deployment

### Push to Vercel
```bash
git push origin main  # or your deploy branch
```

Vercel will automatically trigger a build. Monitor the build in Vercel dashboard.

### Monitor Build Process
1. Go to https://vercel.com/dashboard
2. Click on `public-site` project
3. Watch the build log for:
   - ✅ **`npm install` completes** (~2-3 min)
   - ✅ **`npm run build` completes** (~3-5 min)
   - ✅ **Entire build completes** (should be <10 min total)
   - ❌ **NO timeout errors** (this was the issue we fixed)

### Expected Build Output

You should see:
```
> npm run build

Creating an optimized production build...
✓ Compiled successfully
✓ Linted successfully
✓ Generated static files

exported 45 pages
exported 7 subfolders
```

**DO NOT see:**
```
Code: FUNCTION_INVOCATION_TIMEOUT
Error: Serverless Function has timed out
504 Gateway Timeout
```

If you still see timeouts, proceed to **Troubleshooting** section.

## ✨ Post-Deployment Verification

### Immediate Checks (First 5 minutes)
- [ ] Visit https://gladlabs.io - homepage loads
- [ ] Check response time: should be <2 seconds
- [ ] Open browser DevTools → Network tab
- [ ] Verify no 504 errors in network requests

### Functional Testing
- [ ] Navigate to archive: https://gladlabs.io/archive
- [ ] Test pagination: navigate between pages
- [ ] Click on a category: https://gladlabs.io/category/[name]
- [ ] Click on a tag: https://gladlabs.io/tag/[name]
- [ ] All pages should load within 2-3 seconds

### Search Console
- [ ] Log into Google Search Console
- [ ] Verify site indexation: https://search.google.com/search-console
- [ ] Check for any crawl errors
- [ ] Request indexing if needed

### Monitoring Setup
- [ ] Set up Vercel error notifications (if not already done)
  - Dashboard → Settings → Notifications
  - Enable "Build Failed"
  - Enable "Function Error"
- [ ] Set up Railway alerts for Strapi downtime
  - Railway.app → Project → Settings → Alerts
  - Enable failure notifications

## 🔧 Troubleshooting Guide

### If Build Still Times Out After Deployment

1. **Check Strapi Status**
   ```powershell
   .\scripts\diagnose-timeout.ps1
   ```
   
   If this fails:
   - Check Railway dashboard: https://railway.app
   - Verify Strapi deployment is "running" (not "crashed")
   - Check Strapi logs for errors
   - Restart Strapi if needed

2. **Check Environment Variables**
   - Vercel dashboard → Settings → Environment Variables
   - Verify `NEXT_PUBLIC_STRAPI_API_URL` is correct
   - Verify URL has no typos
   - Verify URL is accessible from outside your network

3. **Check Network Connectivity**
   - From your local machine, run diagnostics script
   - If fails locally, it will fail on Vercel too
   - Check firewall/security groups on Railway
   - Verify Strapi's CORS settings allow Vercel domain

4. **Check Recent Code Changes**
   ```bash
   git diff HEAD~5 HEAD --name-only
   ```
   - Verify no breaking changes were introduced
   - Verify timeout values are correct (10000ms = 10 seconds)
   - Verify error handling code is syntactically correct

5. **Last Resort: Revert and Retry**
   ```bash
   git log --oneline -5
   git revert HEAD
   git push origin main
   ```
   - Vercel will auto-redeploy
   - If this works, timeout issue is in most recent commit

### If Pages Return 404 Errors

**Symptom:** Pages like `/archive`, `/category/[slug]`, `/tag/[slug]` return 404

**Cause:** Error handling code triggered (API failed during build)

**Solution:**
1. Check Strapi is running: `.\scripts\diagnose-timeout.ps1`
2. Verify environment variables in Vercel
3. Redeploy: `git push origin main` (triggers full rebuild)
4. If problem persists, check Strapi API manually:
   ```bash
   curl https://your-strapi.railway.app/api/posts?pagination[limit]=1
   ```

### If Pages Load Slowly

**Symptom:** Pages take >5 seconds to load

**Cause:** Strapi API is slow (but not timing out)

**Solution:**
1. Check Railway CPU/Memory usage
2. Optimize Strapi database queries
3. Consider upgrading Railway tier
4. Add CDN caching layer (Cloudflare)

## 📊 Performance Benchmarks

Expected performance after deployment:

| Metric | Target | Current |
|--------|--------|---------|
| Homepage load time | <2s | ⏳ Measure |
| Archive page load | <2s | ⏳ Measure |
| Category page load | <2s | ⏳ Measure |
| Build time | <10min | ✅ <10min |
| Build timeout | 0 | ✅ 0 |
| Test suite pass rate | 100% | ✅ 100% |

After deployment, measure these and document in your monitoring.

## 🔐 Security Checklist

- [ ] Verify security headers are present:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `X-XSS-Protection: 1; mode=block`
  
  Check headers:
  ```bash
  curl -i https://gladlabs.io | grep -i "X-"
  ```

- [ ] Verify no secrets in environment variables
  - Database passwords: ❌ Never in code
  - API keys: ❌ Never in code
  - Use Vercel dashboard or Railway for secrets
  
- [ ] Verify Strapi API URL is HTTPS (not HTTP)
  - Should be: `https://...`
  - Not: `http://...`

- [ ] Verify CORS is properly configured in Strapi
  - Only allow: Vercel production domain
  - Not: `*` (wildcard)

## 📝 Documentation Updates

After successful deployment:

- [ ] Update README.md with new production URL
- [ ] Document any new environment variables
- [ ] Add deployment date and version to docs
- [ ] Create runbook for future deployments
- [ ] Document any issues encountered and solutions

## 🎯 Success Criteria

✅ Deployment is successful when:

1. **Build completes** without timeout (<10 minutes)
2. **All tests pass** (4 suites, 5 tests)
3. **Homepage loads** in <2 seconds
4. **Dynamic pages** (archive, category, tag) all accessible
5. **No 504 errors** in browser or Vercel logs
6. **Security headers** present in responses
7. **API calls** complete within 10-second timeout
8. **Error handling** works (returns 404 instead of crashing)

## 📞 Rollback Procedure

If deployment is unstable:

```bash
# Find last good commit
git log --oneline

# Reset to good state
git reset --hard <commit-hash>

# Force push to redeploy old version
git push --force-with-lease origin main
```

**This should only be used in emergencies.** Vercel will automatically redeploy.

## 📞 Support Contacts

If you encounter issues:

1. **Vercel Support**: https://vercel.com/support
2. **Railway Support**: https://railway.app/support
3. **Next.js Issues**: https://github.com/vercel/next.js/issues
4. **Strapi Issues**: https://github.com/strapi/strapi/issues

---

**Deployment Date:** _______________
**Deployed By:** _______________
**Notes:** _______________

Last updated: October 20, 2025
