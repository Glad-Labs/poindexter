# 🚀 Production Readiness Checklist

**Last Updated:** November 4, 2025  
**Status:** ✅ Ready for Use  
**Purpose:** Final verification before deploying Glad Labs to production

---

## 📋 Pre-Deployment Verification (Do This First!)

### Code Quality

- [ ] All tests passing locally: `npm test`
- [ ] Backend tests passing: `npm run test:python`
- [ ] No linting errors: `npm run lint`
- [ ] TypeScript checks pass: `npm run type-check`
- [ ] Code formatted: `npm run format`
- [ ] No console.log or debug code in commits
- [ ] Security audit clean: `npm audit` and `pip audit`

### Configuration Files

- [ ] `package.json` versions match (should all be 3.0.0):
  - [ ] Root: 3.0.0
  - [ ] web/oversight-hub: 3.0.0
  - [ ] web/public-site: 3.0.0
  - [ ] cms/strapi-main: 3.0.0

- [ ] Package names correct:
  - [ ] web/oversight-hub: "oversight-hub" (not "dexters-lab")
  - [ ] All others named appropriately

- [ ] Node engines specified for all packages:
  - [ ] Root has engines field
  - [ ] oversight-hub has engines field
  - [ ] public-site has engines field
  - [ ] strapi-main has restrictive node engines

- [ ] Python workspace NOT in npm workspaces
  - [ ] `src/cofounder_agent` removed from root package.json workspaces array

- [ ] Environment variables configured:
  - [ ] `.env.production` exists with production endpoints
  - [ ] `.env.staging` exists with staging endpoints
  - [ ] `.env.example` shows all available variables
  - [ ] No secrets in any .env files (use GitHub Secrets)

### Dependencies

- [ ] All npm dependencies up to date: `npm update --save`
- [ ] No critical vulnerabilities: `npm audit --production`
- [ ] Python requirements pinned: `pip freeze > requirements.txt`
- [ ] asyncpg present in Python requirements
- [ ] NO psycopg2 in Python requirements (use asyncpg)

---

## 🔐 GitHub Secrets Configuration

### Essential Secrets Added (18 Required)

**Railway Deployment:**

- [ ] `RAILWAY_TOKEN` - Can deploy to Railway
- [ ] `RAILWAY_STAGING_PROJECT_ID` - Points to staging
- [ ] `RAILWAY_PROD_PROJECT_ID` - Points to production

**Staging Database:**

- [ ] `STRAPI_STAGING_DB_HOST` - Connects to staging DB
- [ ] `STRAPI_STAGING_DB_USER` - Staging DB user
- [ ] `STRAPI_STAGING_DB_PASSWORD` - Staging DB password

**Staging Strapi:**

- [ ] `STRAPI_STAGING_ADMIN_PASSWORD` - Strapi admin password
- [ ] `STRAPI_STAGING_JWT_SECRET` - JWT signing key

**Production Database:**

- [ ] `STRAPI_PROD_DB_HOST` - Connects to production DB
- [ ] `STRAPI_PROD_DB_USER` - Production DB user
- [ ] `STRAPI_PROD_DB_PASSWORD` - Production DB password

**Production Strapi:**

- [ ] `STRAPI_PROD_ADMIN_PASSWORD` - Strapi admin password
- [ ] `STRAPI_PROD_JWT_SECRET` - JWT signing key

**AI Model Providers (Choose At Least 1):**

- [ ] `OPENAI_API_KEY` - OR
- [ ] `ANTHROPIC_API_KEY` - OR
- [ ] `GOOGLE_API_KEY` (one or more required)

**Vercel Deployment:**

- [ ] `VERCEL_TOKEN` - Deploy to Vercel
- [ ] `VERCEL_PROJECT_ID` - Vercel project ID

**Verification:**

```bash
# Check secrets are configured
gh secret list
# Should show all 18 secrets
```

---

## 🌐 Railway Setup

### Staging Environment

- [ ] Railway staging project created
- [ ] PostgreSQL service added to staging project
- [ ] Database created: `glad_labs_staging`
- [ ] Database user created with proper permissions
- [ ] Strapi service configured
- [ ] Co-Founder Agent service configured
- [ ] Environment variables set in Railway dashboard
- [ ] Health endpoint responds: `GET /api/health` → 200

### Production Environment

- [ ] Railway production project created
- [ ] PostgreSQL service added to production project
- [ ] Database created: `glad_labs_production`
- [ ] Database user created (strong password)
- [ ] Database backups configured (daily)
- [ ] Strapi service configured
- [ ] Co-Founder Agent service configured
- [ ] Environment variables set in Railway dashboard
- [ ] SSL/TLS certificates configured
- [ ] Health endpoint responds: `GET /api/health` → 200

### Railway Database Verification

- [ ] Connection string format: `postgresql://user:pass@host:5432/dbname`
- [ ] Test connection from local machine
- [ ] Firewall rules allow Railway connections
- [ ] Database user has proper permissions (not root/admin)
- [ ] Backups enabled and tested

---

## ✨ Vercel Setup

### Public Site (Next.js)

- [ ] Vercel project created for public-site
- [ ] GitHub repository connected
- [ ] Build command: `npm run build --workspace=web/public-site`
- [ ] Output directory: `web/public-site/.next`
- [ ] Environment variables set:
  - [ ] `NEXT_PUBLIC_STRAPI_API_URL` = production Strapi URL
  - [ ] `NEXT_PUBLIC_STRAPI_API_TOKEN` = Strapi API token
  - [ ] `NODE_ENV` = production

- [ ] Custom domain configured (if applicable)
- [ ] SSL certificate auto-configured
- [ ] Preview deployments enabled for PRs

### Oversight Hub (React)

- [ ] Vercel project created for oversight-hub
- [ ] GitHub repository connected
- [ ] Build command: `npm start` or appropriate React build
- [ ] Environment variables set:
  - [ ] `REACT_APP_API_URL` = production API URL
  - [ ] `REACT_APP_DEBUG` = false

- [ ] Custom domain configured (if applicable)
- [ ] SSL certificate auto-configured

---

## 🔗 DNS & Domain Configuration

- [ ] Primary domain pointing to Vercel (public site)
- [ ] Admin domain pointing to Vercel (oversight hub)
- [ ] API domain pointing to Railway (backend)
- [ ] CMS domain pointing to Railway (Strapi)
- [ ] DNS propagation verified (wait 24-48 hours if just changed)
- [ ] HTTPS working on all domains
- [ ] Redirects configured (www → non-www or vice versa)

---

## 🔒 Security Configuration

### SSL/TLS

- [ ] HTTPS enforced on all domains
- [ ] SSL certificates valid (not self-signed)
- [ ] Security headers configured:
  - [ ] X-Content-Type-Options: nosniff
  - [ ] X-Frame-Options: DENY (or appropriate value)
  - [ ] X-XSS-Protection: 1; mode=block
  - [ ] Strict-Transport-Security: max-age=31536000

### Authentication

- [ ] JWT secrets strong and random (32+ chars)
- [ ] API authentication enabled
- [ ] Rate limiting configured
- [ ] CORS properly restricted (not `*` in production)

### Database

- [ ] Database encrypted at rest
- [ ] Database password strong (20+ chars, mixed case)
- [ ] Database firewall restricts access
- [ ] Database user has minimal required permissions (not admin)
- [ ] Backups encrypted
- [ ] Backups stored off-site or in secure location

### API Keys

- [ ] All API keys rotated within last 90 days
- [ ] API keys scoped to minimum required permissions
- [ ] Rate limits set on all API endpoints
- [ ] API monitoring enabled
- [ ] Unused API keys revoked

---

## 📊 Monitoring & Logging

### Health Checks

- [ ] Backend health endpoint: `GET /api/health` returns 200
- [ ] Strapi health check working
- [ ] Database connectivity verified
- [ ] All external service integrations tested

### Monitoring Setup

- [ ] Error tracking configured (Sentry, etc.)
- [ ] Performance monitoring enabled
- [ ] Uptime monitoring configured (pingdom, etc.)
- [ ] Alert notifications working
- [ ] Log aggregation configured

### Logging

- [ ] Application logging enabled
- [ ] Error logs captured
- [ ] Request/response logging enabled
- [ ] Database query logging enabled (staging only)
- [ ] Logs retained for 30 days minimum

---

## 🔄 Deployment Workflow

### GitHub Actions

- [ ] Staging workflow file exists: `.github/workflows/deploy-staging-with-environments.yml`
- [ ] Production workflow file exists: `.github/workflows/deploy-production-with-environments.yml`
- [ ] Workflows trigger on correct branches (dev, main)
- [ ] All GitHub Secrets referenced in workflows are set
- [ ] Workflows test before deploying
- [ ] Production workflow requires approval
- [ ] Rollback procedure documented

### Testing Before Deploy

- [ ] Run all tests: `npm test`
- [ ] Run Python tests: `npm run test:python`
- [ ] Build for production: `npm run build`
- [ ] Verify no build warnings or errors
- [ ] Test deployment to staging first

---

## 📱 Application Testing

### Public Site

- [ ] Homepage loads without errors
- [ ] All pages render correctly
- [ ] Images load properly
- [ ] Links work correctly
- [ ] Forms work (if any)
- [ ] Search functionality works (if applicable)
- [ ] Mobile responsive design verified
- [ ] Performance acceptable (Lighthouse score >80)

### Oversight Hub

- [ ] Dashboard loads
- [ ] All pages accessible
- [ ] API calls work
- [ ] Authentication working
- [ ] Real-time updates functioning
- [ ] Forms submit correctly
- [ ] Mobile responsive (if applicable)

### Backend API

- [ ] All endpoints responding
- [ ] Authentication working
- [ ] Rate limiting functioning
- [ ] Error handling working
- [ ] Database operations working
- [ ] External service integrations working
- [ ] API documentation accessible

---

## 📧 Communication & Documentation

- [ ] Team notified of deployment window
- [ ] Runbook documented (how to deploy, rollback)
- [ ] Incident response plan documented
- [ ] Contact list updated (on-call rotation)
- [ ] Deployment notes documented
- [ ] Stakeholders informed of go-live

---

## ✅ Final Sign-Off

### Pre-Deployment Sign-Off

- [ ] Lead Developer: Reviewed and approved code
- [ ] DevOps/SRE: Verified infrastructure setup
- [ ] Product: Tested application features
- [ ] Security: Reviewed security configuration

### Post-Deployment Verification

- [ ] All services running without errors
- [ ] All endpoints responding
- [ ] Database connections working
- [ ] External integrations functioning
- [ ] Monitoring showing normal metrics
- [ ] No spike in error rates
- [ ] Team notified deployment complete

### Go-Live Confirmed

- [ ] Public announcement made (if applicable)
- [ ] Monitoring team standing by
- [ ] Incident response team on standby
- [ ] 2-hour window for immediate issues
- [ ] Follow-up monitoring for 24 hours

---

## 🔄 Post-Deployment Tasks (After 24 Hours)

- [ ] All metrics normal for 24+ hours
- [ ] No unresolved incidents
- [ ] Performance baseline established
- [ ] User feedback collected
- [ ] Team debrief completed
- [ ] Documentation updated with any learnings

---

## 📋 Rollback Procedures

### If Issues Found

1. **Identify Problem:** Check error logs and monitoring
2. **Notify Team:** All stakeholders informed
3. **Execute Rollback:**
   ```bash
   git revert <commit-hash>
   git push origin main
   # GitHub Actions will redeploy previous version
   ```
4. **Verify Rollback:** Confirm all services restored
5. **Investigate:** Find root cause
6. **Fix & Re-deploy:** After fix verified

### Rollback Triggers

Automatic rollback recommended if:

- Error rate >5% sustained for 5 minutes
- API response time >10 seconds sustained
- Database connectivity lost
- External service unavailable
- Security issue detected

---

## 📚 Reference Documentation

- **GitHub Secrets Guide:** `docs/reference/GITHUB_SECRETS_COMPLETE_SETUP_GUIDE.md`
- **Deployment Guide:** `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`
- **Architecture:** `docs/02-ARCHITECTURE_AND_DESIGN.md`
- **Operations Guide:** `docs/06-OPERATIONS_AND_MAINTENANCE.md`
- **Monorepo Audit:** `docs/MONOREPO_AUDIT_REPORT_NOVEMBER_2025.md`

---

## 🎯 Expected Timeline

| Phase                    | Duration      | Checklist                  |
| ------------------------ | ------------- | -------------------------- |
| Pre-checks               | 1 hour        | Code, config, secrets      |
| Staging deploy           | 30 min        | Testing, verification      |
| Staging validation       | 2-4 hours     | Full testing cycle         |
| Production deploy        | 30 min        | Deployment execution       |
| Production monitoring    | 2-24 hours    | Real-time validation       |
| **Total Estimated Time** | **6-8 hours** | Complete deployment window |

---

## ⚠️ Important Reminders

🔴 **DO NOT:**

- Skip any testing steps
- Deploy with known failing tests
- Use weak passwords or secrets
- Skip security verification
- Deploy without backups
- Forget to update documentation
- Ignore monitoring alerts

🟢 **DO:**

- Test on staging first
- Have rollback plan ready
- Notify team before deploying
- Monitor for 24 hours after deploy
- Document any issues
- Keep stakeholders informed
- Take breaks (don't deploy while tired!)

---

## ✨ Deployment Success Criteria

You'll know deployment was successful if:

✅ All services responding without errors  
✅ No spike in error rates (remains <0.1%)  
✅ Response times normal (<500ms p95)  
✅ Database operations working  
✅ External integrations functioning  
✅ User reports positive experience  
✅ Monitoring shows healthy metrics  
✅ Team confident in stability

---

**Ready to deploy? Start at the top and work through each section systematically!**

**Last Updated:** November 4, 2025  
**Next Review:** January 4, 2026 (quarterly)  
**Maintained By:** DevOps / SRE Team
