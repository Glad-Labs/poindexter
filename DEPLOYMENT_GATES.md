# Production Deployment Gates & Validation

Pre-deployment checklist and validation procedures to ensure production reliability.

---

## Pre-Deployment Checklist

### ✅ Public Site (`web/public-site`)

**Code Quality:**

- [ ] All unit tests passing (`npm test -- --watchAll=false`)
- [ ] ESLint passes (`npm run lint`)
- [ ] No TypeScript errors (`npm run build`)
- [ ] Code coverage above 70%
- [ ] No console errors in build output

**Performance:**

- [ ] Build size under 500KB (Vercel limit is 50MB)
- [ ] Lighthouse score above 80 for:
  - [ ] Performance
  - [ ] Accessibility
  - [ ] Best Practices
  - [ ] SEO
- [ ] Page load time < 3 seconds
- [ ] Core Web Vitals green:
  - [ ] LCP < 2.5s
  - [ ] FID < 100ms
  - [ ] CLS < 0.1

**Functional Testing:**

- [ ] All pages render without 404 errors
- [ ] Navigation works across all pages
- [ ] API integration working:
  - [ ] Posts load correctly
  - [ ] Categories display properly
  - [ ] Tags filter correctly
- [ ] Search functionality works
- [ ] Contact form submits successfully
- [ ] Error pages handle missing content

**API Validation:**

- [ ] Strapi backend is running
- [ ] All content type endpoints responding
- [ ] CORS headers configured correctly
- [ ] Rate limiting configured
- [ ] Error responses formatted correctly

**Security:**

- [ ] No hardcoded secrets in code
- [ ] Environment variables configured
- [ ] HTTPS enforced
- [ ] CSP headers set properly
- [ ] XSS/CSRF protections enabled

**Database:**

- [ ] Backup created
- [ ] Migration scripts tested
- [ ] Rollback plan documented

---

### ✅ Strapi Backend (`cms/strapi-main`)

**Code Quality:**

- [ ] All API tests passing
- [ ] Database schema validated
- [ ] Content types configured correctly
- [ ] Plugins enabled and configured
- [ ] Admin UI accessible

**Performance:**

- [ ] Response time < 500ms for API calls
- [ ] Database queries optimized
- [ ] Connection pooling configured
- [ ] Cache strategy implemented

**Functional Testing:**

- [ ] All content types CRUD working
- [ ] Permissions configured correctly
- [ ] User roles working as expected
- [ ] Authentication functioning
- [ ] Media uploads working
- [ ] Draft/publish workflow operational

**API Endpoints:**

- [ ] GET /api/posts responding
- [ ] GET /api/categories responding
- [ ] GET /api/tags responding
- [ ] POST endpoints secured
- [ ] DELETE endpoints require auth

**Security:**

- [ ] Admin password strong and saved
- [ ] JWT tokens configured
- [ ] CORS whitelist set to production domain
- [ ] Rate limiting enabled
- [ ] SQL injection protections enabled
- [ ] File upload restrictions set

**Database:**

- [ ] Production database created
- [ ] Backups automated
- [ ] Disaster recovery tested
- [ ] Connection string secured

---

## Automated Validation Scripts

### Pre-Build Validation

**File: `scripts/validate-build.sh`**

```bash
#!/bin/bash

echo "🔍 Running pre-build validation..."

# Check Node version
NODE_VERSION=$(node --version | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
  echo "❌ Node 18+ required"
  exit 1
fi

# Run tests
echo "🧪 Running tests..."
npm run test:public:ci || exit 1

# Run linting
echo "📝 Running linting..."
npm run lint --workspaces || exit 1

# Check for console errors
echo "🔎 Checking for console.log statements..."
if grep -r "console\\.log\|console\\.error\|console\\.warn" src --include="*.js" --include="*.jsx" --include="*.ts" --include="*.tsx"; then
  echo "⚠️  Found console statements (allowed, just be aware)"
else
  echo "✅ No console statements found"
fi

# Check for hardcoded secrets
echo "🔐 Checking for hardcoded secrets..."
if grep -r "password\|secret\|api_key\|token" src --include="*.env*" --include="*.js" --include="*.jsx"; then
  echo "❌ Potential hardcoded secrets found"
  exit 1
fi

echo "✅ Pre-build validation passed!"
```

### Post-Build Validation

**File: `scripts/validate-deployment.sh`**

```bash
#!/bin/bash

SITE_URL=$1

if [ -z "$SITE_URL" ]; then
  echo "Usage: ./validate-deployment.sh <site-url>"
  exit 1
fi

echo "🚀 Validating deployment to $SITE_URL..."

# Check site is accessible
echo "🌐 Checking site accessibility..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$SITE_URL")
if [ "$STATUS" -ne 200 ]; then
  echo "❌ Site returned status $STATUS"
  exit 1
fi
echo "✅ Site accessible (HTTP $STATUS)"

# Check API connectivity
echo "📡 Checking API connectivity..."
API_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$SITE_URL/api/posts")
if [ "$API_STATUS" -ne 200 ]; then
  echo "❌ API returned status $API_STATUS"
  exit 1
fi
echo "✅ API responding (HTTP $API_STATUS)"

# Check page load time
echo "⏱️  Checking page load time..."
LOAD_TIME=$(curl -s -w "%{time_total}" -o /dev/null "$SITE_URL")
LOAD_MS=$((${LOAD_TIME%.*} * 1000))
if [ "$LOAD_MS" -gt 5000 ]; then
  echo "⚠️  Page load slow: ${LOAD_TIME}s (target < 3s)"
else
  echo "✅ Page load: ${LOAD_TIME}s"
fi

echo "✅ Deployment validation passed!"
```

---

## Health Checks

### Uptime Monitoring

Add to `monitoring/health-check.js`:

```javascript
const http = require('http');

const checks = [
  {
    name: 'Public Site',
    url: 'https://your-site.com',
    expectedStatus: 200,
  },
  {
    name: 'Strapi API',
    url: 'https://api.your-site.com/api/posts',
    expectedStatus: 200,
  },
];

async function runHealthCheck() {
  console.log(`[${new Date().toISOString()}] Running health checks...`);

  for (const check of checks) {
    try {
      const response = await fetch(check.url);
      const status = response.status;

      if (status === check.expectedStatus) {
        console.log(`✅ ${check.name}: OK (${status})`);
      } else {
        console.error(
          `❌ ${check.name}: Expected ${check.expectedStatus}, got ${status}`
        );
        // Alert/notify team
      }
    } catch (error) {
      console.error(`❌ ${check.name}: ${error.message}`);
      // Alert/notify team
    }
  }
}

// Run every 5 minutes
setInterval(runHealthCheck, 5 * 60 * 1000);
runHealthCheck();
```

Deploy as a scheduled Cloud Function or Cron job.

---

## Rollback Procedure

### If Deployment Fails

1. **Immediate:** Stop new deployment
2. **Monitor:** Check error logs in Vercel/Railway dashboard
3. **Database:** Ensure database hasn't been modified
4. **Rollback:** Revert to previous deployment:

**Vercel Rollback:**

```
Vercel Dashboard → Deployments → [Previous Version] → "Promote to Production"
```

**Railway Rollback:**

```bash
railway rollback --service strapi-backend --environment production
```

1. **Verify:** Run health checks again
2. **Notify:** Inform team of rollback
3. **Post-Mortem:** Review what failed and why

### Database Rollback

If database schema migration failed:

```bash
# Strapi automatic rollback
strapi content-manager:rollback

# Or manual if available
psql -U postgres -d glad_labs_prod -f rollback.sql
```

---

## Incident Response

### Performance Degradation

1. **Identify:** Check monitoring dashboard
2. **Diagnose:** Check logs for errors
3. **Temporary Fix:**
   - Scale up resources on Railway/Vercel
   - Clear CDN cache
   - Restart services
4. **Root Cause:** Identify actual issue
5. **Permanent Fix:** Deploy fix or revert
6. **Monitor:** Watch metrics for 30 minutes

### Service Down

1. **Alert:** Notify team immediately
2. **Check:** Verify services are running
3. **Logs:** Review error logs for root cause
4. **Restart:** Try restarting services
   ```bash
   railway restart --service strapi-backend
   ```
5. **Rollback:** If recent deployment, rollback
6. **Escalate:** Contact platform support if needed

### Data Corruption

1. **Stop:** Halt all write operations
2. **Backup:** Create emergency backup
3. **Investigate:** Determine scope of corruption
4. **Restore:** Restore from latest good backup
5. **Verify:** Test data integrity
6. **Review:** Audit how corruption occurred

---

## Post-Deployment Validation (1 hour)

```
00:00 - Deployment complete
00:05 - Health checks passing?
00:10 - API responding normally?
00:15 - Page load times normal?
00:20 - Error logs clear?
00:25 - No unusual traffic patterns?
00:30 - Database queries performing?
00:45 - Spot check key features
01:00 - Declare "stable" or rollback
```

---

## Staging Environment

Before production, test on staging:

```bash
# Deploy to staging
VERCEL_ENV=preview npm run deploy

# Run full test suite
npm run test:comprehensive

# Run performance tests
npm run perf:test

# Load testing
npm run load:test

# User acceptance testing
# Manual testing by team
```

Once staging passes, deploy to production.

---

## Monitoring Dashboard

Set up monitoring for:

- **Uptime:** 99.9% target
- **Response Time:** < 500ms p95
- **Error Rate:** < 0.1%
- **CPU Usage:** < 80%
- **Memory Usage:** < 80%
- **Database:** Connection pool < 90% full
- **API Latency:** < 250ms median

Services:

- **Vercel Analytics:** https://vercel.com/analytics
- **Railway Monitoring:** https://railway.app/account/teams
- **Sentry (Error Tracking):** https://sentry.io/
- **Datadog (APM):** https://www.datadoghq.com/

---

## Success Criteria

Deployment is successful when:

✅ All health checks pass  
✅ Error rate < 0.1%  
✅ Response times normal  
✅ No critical errors in logs  
✅ Team confirms everything working  
✅ User-facing features functional  
✅ API endpoints responding  
✅ Database healthy
✅ No spike in resource usage
✅ All monitoring green
