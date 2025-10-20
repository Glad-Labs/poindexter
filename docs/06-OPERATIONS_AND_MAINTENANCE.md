# 06 - Operations & Maintenance

**Role**: DevOps, Operations, All Teams  
**Reading Time**: 15-18 minutes  
**Last Updated**: October 18, 2025

---

## 🚀 Quick Navigation

- **[← Back to Docs](./00-README.md)** | **[↑ Development](./04-DEVELOPMENT_WORKFLOW.md)** | **[↑ AI Agents](./05-AI_AGENTS_AND_INTEGRATION.md)** | **[Go to Guides →](../guides/)**

---

## Overview

This document covers operational procedures, monitoring, maintenance, and troubleshooting for GLAD Labs in production. It includes best practices for keeping services healthy, responding to incidents, and maintaining performance.

---

## 📋 Table of Contents

1. [Service Health Monitoring](#service-health-monitoring)
2. [Logging & Observability](#logging--observability)
3. [Performance Optimization](#performance-optimization)
4. [Backup & Disaster Recovery](#backup--disaster-recovery)
5. [Security Maintenance](#security-maintenance)
6. [Incident Response](#incident-response)
7. [Maintenance Windows](#maintenance-windows)

---

## Service Health Monitoring

### Health Check Endpoints

Every service exposes a health check endpoint:

```bash
# Strapi CMS
curl https://api.example.com/health
# Response: { "status": "ok", "uptime": 3600 }

# Frontend
curl https://app.example.com/health
# Response: { "status": "ok", "version": "1.0.0" }

# AI Agent
curl https://agent.example.com/health
# Response: { "status": "healthy", "agents_active": 5 }
```

### Monitoring Dashboard

All services report to a centralized monitoring dashboard:

```
Monitoring Services:
├─ Application Insights (Azure) - Application monitoring
├─ Datadog (optional) - Infrastructure & logs
├─ Railway Dashboard - Deployment status
└─ Vercel Dashboard - Frontend status
```

### Key Metrics to Track

| Metric               | Target  | Alert Threshold |
| -------------------- | ------- | --------------- |
| **Uptime**           | 99.5%+  | < 99%           |
| **Response Time**    | < 500ms | > 2000ms        |
| **Error Rate**       | < 0.1%  | > 1%            |
| **Database Latency** | < 100ms | > 500ms         |
| **Memory Usage**     | < 70%   | > 85%           |
| **CPU Usage**        | < 60%   | > 80%           |

### Setting Up Alerts

#### Railway Alerts

```
1. Go to Railway Dashboard
2. Click Service
3. Settings → Alerts
4. Add alert for:
   - High CPU (> 80%)
   - High Memory (> 85%)
   - Deployment failed
5. Set notification email
```

#### Application Insights

```
1. Go to Azure Portal
2. Select Application Insights
3. Alerts → New alert rule
4. Choose metric (response time, failures, etc.)
5. Set threshold and notification
```

---

## Logging & Observability

### Log Aggregation

Logs from all services are collected in one place:

```
Strapi Logs →
Frontend Logs →
Agent Logs →
        └─→ Application Insights / Datadog / CloudWatch
```

### Viewing Logs

#### Railway Logs

```bash
# Live logs
railway logs -f

# Filtered by date
railway logs --since "2025-10-18"

# Search logs
railway logs | grep "ERROR"

# Export logs
railway logs > logs_export.txt
```

#### Application Insights

```
Azure Portal → Select Resource → Logs
Query example:
traces
| where severity == "error"
| where timestamp > ago(24h)
| summarize Count = count() by operation_Name
```

#### Local Development

```bash
# View all service logs
npm run logs:all

# Strapi logs
tail -f cms/strapi-main/logs/app.log

# Frontend logs
tail -f web/public-site/logs/app.log
```

### Structured Logging Best Practices

When adding logs to code:

```javascript
// ❌ Avoid
console.log('Something happened');

// ✅ Prefer
logger.info('User login successful', {
  userId: user.id,
  timestamp: new Date().toISOString(),
  ipAddress: req.ip,
  duration: elapsedTime,
});

// ✅ Errors with context
logger.error('Database query failed', {
  query: 'SELECT * FROM users',
  error: err.message,
  stack: err.stack,
  attemptNumber: retryCount,
});
```

---

## Performance Optimization

### Database Performance

#### Query Optimization

```sql
-- ❌ Slow: N+1 queries
SELECT * FROM users;
-- Then loop and query orders for each user

-- ✅ Fast: Join queries
SELECT users.*, orders.*
FROM users
LEFT JOIN orders ON orders.user_id = users.id;

-- ✅ Use indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_orders_user_id ON orders(user_id);
```

#### Connection Pooling

```javascript
// Use connection pooling to reuse connections
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 20, // Max connections in pool
  idleTimeoutMillis: 30000,
});

// Don't create new connection for each query!
const result = await pool.query('SELECT * FROM users');
```

### Caching Strategy

```javascript
// Cache frequently accessed data
const cache = new Map();

async function getUserProfile(userId) {
  // Check cache first
  if (cache.has(userId)) {
    return cache.get(userId);
  }

  // Fetch from database
  const profile = await db.query('SELECT * FROM users WHERE id = ?', [userId]);

  // Cache for 5 minutes
  cache.set(userId, profile);
  setTimeout(() => cache.delete(userId), 5 * 60 * 1000);

  return profile;
}
```

### API Response Optimization

```javascript
// ✅ Return only needed fields
GET /api/users?fields=id,name,email
Response: [{ id: 1, name: "John", email: "john@example.com" }]

// ✅ Paginate large result sets
GET /api/orders?page=1&limit=20
Response: { data: [...], total: 1000, page: 1, pages: 50 }

// ✅ Compress responses
// Server automatically gzip compresses responses
curl -H "Accept-Encoding: gzip" https://api.example.com/data
```

### Frontend Performance

```javascript
// ✅ Code splitting
const Dashboard = lazy(() => import('./Dashboard'));

// ✅ Image optimization
<img src="image.webp" alt="..." loading="lazy" />;

// ✅ Minimize API calls
// Use GraphQL batching or REST request batching

// ✅ Local caching
localStorage.setItem('user', JSON.stringify(user));
```

---

## Backup & Disaster Recovery

### Database Backups

#### Automated Backups

Railroad automatically backs up PostgreSQL:

```
Daily backups → Stored for 30 days → Point-in-time recovery available
```

#### Manual Backup

```bash
# Create backup
pg_dump postgresql://user:pass@host/db > backup-$(date +%Y%m%d).sql

# Compress backup
gzip backup-20251018.sql

# Upload to storage
aws s3 cp backup-20251018.sql.gz s3://my-bucket/backups/
```

#### Restore from Backup

```bash
# Create new database
createdb restored_db

# Restore from backup
pg_restore -d restored_db backup-20251018.sql

# Or SQL backup
psql -d restored_db < backup-20251018.sql
```

### Application Backups

#### GitHub is Your Backup

```bash
# Everything in git is backed up on GitHub
git push origin main

# Create release/snapshot
git tag -a v1.0.0 -m "Production release"
git push origin v1.0.0
```

#### Disaster Recovery Plan

```
1. Database compromised?
   → Restore from backup to staging
   → Verify data
   → Restore to production

2. Code corrupted?
   → Clone fresh from GitHub
   → Git handles version history

3. Services down?
   → Redeploy from Railway/Vercel
   → Auto-scaling brings capacity back

4. Total data loss?
   → Database: Railway backup (30-day retention)
   → Code: GitHub (permanent)
   → User data: Reconstruct from logs
```

---

## Security Maintenance

### Regular Security Updates

```bash
# Check for vulnerabilities
npm audit

# Fix vulnerabilities
npm audit fix

# Automated security updates
# Enable Dependabot on GitHub:
# Settings → Security → Dependabot → Enable all
```

### Access Control

#### Database Access

```
Only production:
- Database password in Railway secrets
- No production credentials in code
- Access logs enabled

Access via:
- Railway dashboard (read-only for most)
- SSH tunnel with VPN (for direct access)
```

#### API Authentication

```javascript
// All API endpoints require authentication
// Invalid request (no auth)
GET /api/users
Response: 401 Unauthorized

// Valid request
GET /api/users
Header: Authorization: Bearer token-xyz
Response: 200 OK
```

#### Secret Rotation

```
Monthly:
- [ ] Rotate API keys
- [ ] Rotate database passwords
- [ ] Rotate JWT secrets

Immediately if:
- [ ] Secret exposed in public repo
- [ ] Employee with access leaves
- [ ] Security incident
```

### SSL/TLS Certificates

```
Railway & Vercel auto-manage SSL:
- Certificates generated automatically
- Auto-renewed before expiration
- Always HTTPS required

Check certificate:
openssl s_client -connect api.example.com:443
```

---

## Incident Response

### Incident Severity Levels

| Level        | Definition                                | Response Time       |
| ------------ | ----------------------------------------- | ------------------- |
| **Critical** | Service down, data loss risk              | Immediate (< 5 min) |
| **High**     | Partial outage, performance degraded      | 15 minutes          |
| **Medium**   | Functionality impaired, workaround exists | 1 hour              |
| **Low**      | Minor issue, no user impact               | 24 hours            |

### Critical Incident Response

#### Step 1: Immediate Action (5 minutes)

```
1. ASSESS
   - Which service is down?
   - How many users affected?
   - Business impact?

2. COMMUNICATE
   - Notify team (Slack)
   - Post status update
   - Estimate time to resolution

3. MITIGATE
   - Stop the bleeding
   - Temporary workaround if possible
   - Prevent data loss
```

#### Step 2: Investigation (15-30 minutes)

```
1. Check logs
   tail -f logs/error.log

2. Check metrics
   - CPU usage
   - Memory usage
   - Database connections
   - Error rate

3. Identify root cause
   - Recent deployment?
   - Database issue?
   - API rate limit?
   - External dependency?
```

#### Step 3: Resolution (30+ minutes)

```
1. Apply fix
   - Rollback recent deployment if needed
   - Scale up service if capacity issue
   - Fix database connection if needed

2. Verify fix
   - Service responds to requests
   - Performance normal
   - No new errors

3. Monitor closely
   - Watch logs for 30 minutes
   - Monitor error rate
   - Confirm stability
```

#### Step 4: Post-Incident (Next day)

```
1. Write incident report
   - What happened?
   - Why did it happen?
   - How was it fixed?

2. Identify improvements
   - Better monitoring needed?
   - Better testing needed?
   - Process improvement?

3. Implement safeguards
   - Add monitoring alert
   - Add automated test
   - Add documentation
```

### Common Incidents & Solutions

#### Service Not Responding

```bash
# 1. Check if running
railway logs -f
ps aux | grep node

# 2. Check metrics
# CPU? Memory? Connections maxed?

# 3. Restart service
railway redeploy

# 4. Check if issue persists
curl https://api.example.com/health
```

#### High Error Rate

```bash
# 1. Check error logs
railway logs | grep ERROR

# 2. Identify error pattern
# Database connection error?
# API timeout?
# Validation error?

# 3. Fix and redeploy
git commit -m "fix: resolve error"
git push origin main
```

#### Database Slow/Unresponsive

```sql
-- 1. Check active connections
SELECT count(*) FROM pg_stat_activity;

-- 2. Kill long-running queries
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE duration > interval '10 minutes';

-- 3. Analyze slow queries
EXPLAIN ANALYZE SELECT * FROM users WHERE ...;

-- 4. Add index if needed
CREATE INDEX idx_users_email ON users(email);
```

---

## Maintenance Windows

### Planning Maintenance

Schedule maintenance during low-traffic times:

```
Best times (UTC):
- Weekday: 2am-4am (0200-0400)
- Weekend: 3am-6am (0300-0600)

Notify users 48 hours in advance
```

### Maintenance Checklist

```
Before Maintenance:
- [ ] Notify users (email, in-app)
- [ ] Take database backup
- [ ] Document rollback plan
- [ ] Have team on standby

During Maintenance:
- [ ] Put service in "maintenance mode"
- [ ] Update status page
- [ ] Perform upgrade/fix
- [ ] Run verification tests

After Maintenance:
- [ ] Verify all services healthy
- [ ] Monitor closely for 1 hour
- [ ] Send completion notification
- [ ] Document what was done
```

### Database Maintenance

```bash
# Vacuum & analyze (monthly)
VACUUM ANALYZE;

# Reindex (quarterly)
REINDEX INDEX index_name;

# Check table sizes
SELECT
  tablename,
  pg_size_pretty(pg_total_relation_size(tablename))
FROM pg_tables
WHERE schemaname='public'
ORDER BY pg_total_relation_size(tablename) DESC;

# Clean up old data (if applicable)
DELETE FROM audit_logs
WHERE created_at < NOW() - INTERVAL '1 year';
```

### Update Dependencies

```bash
# Check outdated packages
npm outdated

# Update safely
npm update  # Non-breaking updates
npm install some-package@latest  # Breaking updates

# Test before deploying
npm test
npm run build

# Deploy to staging first
git push staging main

# Monitor staging
# Then deploy to production if stable
git push origin main
```

---

## Operations Runbook

### Daily Operations

```
Every morning:
- [ ] Check service health dashboard
- [ ] Review error logs
- [ ] Check backup status
- [ ] Verify database size within limits

Every 6 hours:
- [ ] Check performance metrics
- [ ] Monitor uptime
- [ ] Check alert thresholds
```

### Weekly Operations

```
Every Monday:
- [ ] Review incident reports (if any)
- [ ] Check security alerts
- [ ] Review backup integrity

Every Friday:
- [ ] Plan weekend monitoring
- [ ] Prepare for maintenance if needed
- [ ] Test disaster recovery procedures
```

### Monthly Operations

```
Every month:
- [ ] Review and rotate secrets
- [ ] Analyze performance trends
- [ ] Identify optimization opportunities
- [ ] Update documentation
- [ ] Database maintenance (VACUUM, REINDEX)
- [ ] Dependency updates (if needed)
```

---

## Troubleshooting Quick Reference

| Issue               | Symptom                     | Solution                                 |
| ------------------- | --------------------------- | ---------------------------------------- |
| **Service Down**    | 502 error                   | `railway redeploy`                       |
| **Slow Responses**  | > 5s response time          | Check database, scale up service         |
| **High Error Rate** | 500 errors                  | Check logs, rollback recent deploy       |
| **Database Issue**  | Connection timeout          | Check connection string, restart Railway |
| **Memory Leak**     | Gradually increasing memory | Restart service, profile code            |
| **Disk Full**       | Disk space exhausted        | Delete old logs, upgrade storage         |

---

## Resources

- **Railway Dashboard**: https://railway.app/dashboard
- **Vercel Dashboard**: https://vercel.com/dashboard
- **Azure Portal**: https://portal.azure.com
- **GitHub**: https://github.com/mattg-stack/glad-labs-website
- **Emergency Contact**: (Setup in team communication)

---

## Next Steps

1. **[← Back to Documentation](./00-README.md)**
2. **Read**: [Deployment Guide](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)
3. **Setup**: Configure monitoring alerts
4. **Practice**: Run through incident response scenario

---

**Last Updated**: October 18, 2025 | **Version**: 1.0 | **Status**: Operational
