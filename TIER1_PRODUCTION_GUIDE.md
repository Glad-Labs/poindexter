# 🎯 GLAD Labs Tier 1 Production Setup - Complete Guide

**Configuration Date:** October 23, 2025  
**Cost:** $0-10/month  
**Max Users:** 50 concurrent  
**Uptime SLA:** ~95%  
**Database:** 1GB limit  

---

## 📋 What's Included

### ✅ Configuration Files Created

```
scripts/
├── setup-tier1.js                    # Initial setup wizard
├── deploy-tier1.sh                   # Deployment script
├── monitor-tier1-resources.js        # Resource monitoring
├── tier1-health-check.js            # Keep services warm
├── backup-tier1-db.sh               # Database backups
└── scale-to-tier2.sh                # Upgrade path

Configuration:
├── .railway.tier1.json              # Railway configuration
├── .env.tier1.production            # Environment variables
├── TIER1_COST_ANALYSIS.md          # Cost breakdown
└── TIER1_DEPLOYMENT.json           # Package.json with scripts

```

---

## 🚀 Deployment Steps

### 1. Run Setup Wizard

```bash
# Initialize Tier 1 configuration
npm run deploy:tier1:setup

# This creates:
# - Railway configuration
# - Environment variables
# - Health check scripts
# - Backup procedures
```

### 2. Deploy Services

```bash
# Deploy all services to Tier 1
npm run deploy:tier1

# Services deployed:
# - PostgreSQL (Railway free)
# - Strapi CMS (Free tier)
# - Co-Founder Agent (Free tier)
# - Frontend (Vercel Hobby)
```

### 3. Configure Health Checks (CRITICAL)

```bash
# Add Railway Cron Job to keep services warm
# Go to: https://railway.app/dashboard

# Create new Cron service:
# - Command: node scripts/tier1-health-check.js
# - Schedule: */25 * * * * (every 25 minutes)
# - Timeout: 10 seconds

# This prevents services from sleeping after 30 minutes
```

### 4. Set Up Backups

```bash
# Create weekly database backup
bash scripts/backup-tier1-db.sh

# Add to crontab:
# 0 0 * * 0 /path/to/scripts/backup-tier1-db.sh

# Stores backup in:
# - Local: backups/tier1/
# - Optional S3: AWS backup bucket
# - Optional Git: Repository (small DBs only)
```

### 5. Monitor Resources

```bash
# Check resource usage and get alerts
npm run monitor:resources

# Shows:
# - Database storage (1GB limit)
# - Memory usage per service (256MB limit)
# - Connection pool saturation
# - Response times
# - Recommendations for scaling
```

---

## 💰 Cost Breakdown

### Monthly Costs

| Service | Tier | Plan | Cost |
|---------|------|------|------|
| **Database** | 1 | PostgreSQL (Free) | $0 |
| **Strapi CMS** | 1 | Node (Free) | $0 |
| **API Agent** | 1 | Python (Free) | $0 |
| **Frontend** | 1 | Vercel Hobby | $0 |
| **TOTAL** | 1 | | **$0/month** |

### Additional Costs (if applicable)

- Data egress: $0 with Vercel + Railway free egress
- Database storage: $0 (1GB included)
- API calls: $0 (self-hosted models)
- S3 backups: ~$1/month (optional)

**TRUE COST: $0-10/month for production**

---

## ⚡ Performance Characteristics

### Response Times

| Metric | Tier 1 | Tier 2 |
|--------|--------|--------|
| **Cold start** (after sleep) | 3-5 sec | <100ms |
| **Warm response** | 200-500ms | 100-200ms |
| **P99 response** | 1-2 sec | 200-500ms |

### Capacity

- **Concurrent users:** 50 (with 3-5 sec first request)
- **Database size:** 1GB (must upgrade if exceeded)
- **Daily active users:** ~200-500
- **Queries per second:** 5-10

---

## ⚠️ Important Constraints

### Service Sleep

- ❌ Services sleep after **30 minutes** of inactivity
- ⏱️ Wake-up time: **3-5 seconds**
- ✅ Solution: Configure health check pings every 25 minutes

### Database Limits

- 💾 Storage: **1GB maximum**
- 🔌 Connections: **5 in pool**
- 📊 Queries: Throttled by Railway
- ⏰ Idle timeout: **2 minutes**

### Memory Constraints

- 🛢️ Strapi: **256MB** (watch for OOM)
- 🧠 API: **256MB** (lean deployments)
- 📝 Logs: **Limited retention** (use external logging)

---

## 🔧 Maintenance Tasks

### Daily

```bash
# Check service health
npm run health:check

# Verify all services responding:
# - Frontend loading
# - API responding
# - Strapi admin accessible
```

### Weekly

```bash
# Create database backup
bash scripts/backup-tier1-db.sh

# Monitor resources
npm run monitor:resources

# Check for errors in logs
railway logs --follow
```

### Monthly

```bash
# Full system check
npm run monitor:resources

# Archive old data
bash scripts/cleanup-old-data.sh

# Test backup restoration
# (in staging environment)
```

---

## 📈 Scaling Path: Tier 1 → Tier 2 → Tier 3

### When to Upgrade

Upgrade to **Tier 2** ($50/month) when:

- 📊 Database approaching 1GB
- 👥 Users > 50 concurrent
- ⏱️ Response times > 2 seconds
- 📈 Daily actives > 500

**One command upgrade:**

```bash
npm run scale:to-tier2
```

### Cost Comparison

| Metric | Tier 1 | Tier 2 | Tier 3 |
|--------|--------|--------|--------|
| **Cost** | $0 | $50 | $155+ |
| **Users** | 50 | 500 | 2,000+ |
| **DB Size** | 1GB | 10GB | 100GB+ |
| **Uptime** | 95% | 99.5% | 99.9% |
| **Response** | 200-500ms | 100-200ms | 50-100ms |

---

## 🚨 Troubleshooting

### Services Taking 5+ Seconds to Respond

**Cause:** Services are waking up from sleep  
**Solution:** Configure health check cron every 25 minutes

```bash
# In Railway dashboard:
# Create Cron → node scripts/tier1-health-check.js → */25 * * * *
```

### Database Query Timeout

**Cause:** Hitting 1GB storage limit or connection pool saturated  
**Solution:** Archive data or upgrade to Tier 2

```bash
# Check database size
railway postgresql connect

# Inside psql:
SELECT pg_database.datname, pg_size_pretty(pg_database.datsize) 
FROM pg_database;

# If approaching 1GB: npm run scale:to-tier2
```

### Memory Errors in Logs

**Cause:** 256MB limit insufficient for current load  
**Solution:** Optimize code or upgrade to Tier 2

```bash
# Check memory usage
npm run monitor:resources

# If consistently > 200MB: npm run scale:to-tier2
```

### Services Crashing After Deploy

**Cause:** Build taking too long or memory spike  
**Solution:** Simplify deployment or upgrade

```bash
# Check logs
railway logs --follow

# Restart service
railway service restart strapi
# or
railway service restart cofounder_agent
```

---

## 📊 Monitoring & Alerts

### Set Up Alerts

Use Railway's built-in alerting:

1. Go to Railway dashboard
2. Settings → Notifications
3. Alert on:
   - CPU > 80%
   - Memory > 200MB (on 256MB service)
   - Error rate > 1%
   - Response time > 2 sec

### Manual Monitoring

```bash
# Check resource usage
npm run monitor:resources

# Shows real-time metrics and alerts
# Suggests upgrade when approaching limits
```

---

## 🔒 Security Notes

### Tier 1 Considerations

- ✅ HTTPS: Enabled by default (Railway + Vercel)
- ✅ Database: Encrypted at rest
- ✅ Environment variables: Secure in Railway
- ⚠️ Limited DDoS protection (upgrade to Tier 2 for better)

### Backups

- ✅ Local backups: In `backups/tier1/`
- ✅ Cloud backups: Optional S3 storage
- ✅ Retention: Keep 4 weeks minimum

---

## 📞 Support & Help

### Railway Support

- Dashboard: https://railway.app
- Docs: https://docs.railway.app
- Community: https://railway.app/community

### Vercel Support

- Dashboard: https://vercel.com/dashboard
- Docs: https://vercel.com/docs

### GLAD Labs Docs

- Setup guide: See docs/01-SETUP_AND_OVERVIEW.md
- Architecture: See docs/02-ARCHITECTURE_AND_DESIGN.md
- Deployment: See docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md

---

## ✅ Deployment Checklist

- [ ] Run `npm run deploy:tier1:setup`
- [ ] Run `npm run deploy:tier1`
- [ ] Services deployed and running
- [ ] Configure Railway Cron for health checks
- [ ] Test all endpoints responding
- [ ] Set up backup schedule
- [ ] Create first database backup
- [ ] Configure monitoring/alerts
- [ ] Document service URLs
- [ ] Brief team on constraints
- [ ] Plan Tier 2 upgrade trigger
- [ ] Schedule monthly reviews

---

## 🎉 Success Metrics

**You've successfully deployed Tier 1 when:**

✅ All services running on Railway free tier  
✅ Frontend live on Vercel  
✅ Database backups created  
✅ Health checks keeping services warm  
✅ Response times < 500ms (normal)  
✅ No OOM errors  
✅ Cost at $0-10/month  

---

**Ready to deploy? Run:**

```bash
npm run deploy:tier1
```

**Questions? Check:** TIER1_COST_ANALYSIS.md

**Need more power? Run:** npm run scale:to-tier2

