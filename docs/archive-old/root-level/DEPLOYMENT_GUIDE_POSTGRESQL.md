# PostgreSQL-First Deployment Guide

**Created:** November 24, 2025  
**Version:** 1.0  
**Status:** Ready for Deployment

---

## 🚀 Pre-Deployment Checklist

Before deploying to production, verify:

- [ ] PostgreSQL 14+ installed and running
- [ ] `asyncpg` and `psycopg2-binary` installed
- [ ] DATABASE_URL environment variable configured
- [ ] All required LLM API keys set (at least one)
- [ ] PEXELS_API_KEY set for image functionality
- [ ] Network connectivity to PostgreSQL server
- [ ] File system permissions for image storage
- [ ] All code changes committed to git

---

## 📋 Step-by-Step Deployment

### 1. Prepare PostgreSQL Database

```bash
# Connect to PostgreSQL
psql -U postgres

# Create database for Glad Labs
CREATE DATABASE glad_labs;
CREATE USER glad_labs_user WITH PASSWORD 'secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE glad_labs TO glad_labs_user;
\q
```

### 2. Configure Environment Variables

**Create `.env.production`:**

```bash
# Database (REQUIRED)
DATABASE_URL=postgresql://glad_labs_user:secure_password_here@prod-db.railway.app:5432/glad_labs

# LLM Providers (at least one required)
OPENAI_API_KEY=sk-your-key-here
ANTHROPIC_API_KEY=sk-ant-your-key-here
GOOGLE_API_KEY=AIza-your-key-here
USE_OLLAMA=false  # Set to true if using local Ollama

# Image Provider
PEXELS_API_KEY=your-pexels-key-here

# Application Settings
ENVIRONMENT=production
DEBUG=False
LOG_LEVEL=INFO
```

**In Railway Dashboard:**

1. Go to your Co-Founder Agent service
2. Click "Variables"
3. Add each variable from above (except DATABASE_URL if using Railway PostgreSQL)
4. Save

### 3. Initialize Database Schema

The schema will be created automatically on first run, but you can verify manually:

```bash
# Connect to production database
psql -U glad_labs_user -h prod-db.railway.app -d glad_labs

# Verify tables exist
\dt

# Expected output:
# public | categories    | table | glad_labs_user
# public | media         | table | glad_labs_user
# public | post_tags     | table | glad_labs_user
# public | posts         | table | glad_labs_user
# public | tags          | table | glad_labs_user
```

### 4. Deploy Backend

#### Using Railway:

```bash
# Login to Railway
railway login

# Link to production project
railway link --project <production-project-id>

# Deploy
railway deploy

# View logs
railway logs --follow
```

#### Using Docker:

```bash
# Build image
docker build -t glad-labs-backend:prod .

# Run with environment variables
docker run -d \
  --name glad-labs-backend \
  -e DATABASE_URL='postgresql://user:pass@host/db' \
  -e OPENAI_API_KEY='sk-...' \
  -e PEXELS_API_KEY='...' \
  -p 8000:8000 \
  glad-labs-backend:prod

# Check logs
docker logs -f glad-labs-backend
```

### 5. Deploy Frontend

#### Using Vercel:

```bash
# Install Vercel CLI
npm install -g vercel

# Deploy public site
cd web/public-site
vercel --prod \
  --env NEXT_PUBLIC_API_BASE_URL=https://api.your-domain.com

# Deploy oversight hub
cd ../oversight-hub
vercel --prod \
  --env REACT_APP_API_URL=https://api.your-domain.com
```

#### Using Railway:

```bash
railway link --project <frontend-project-id>
railway deploy
```

### 6. Verify Deployment

Test all services:

```bash
# Backend health check
curl https://api.your-domain.com/api/health
# Expected: {"status": "healthy", ...}

# Test database connection
curl https://api.your-domain.com/api/models/status
# Should return model provider status

# Test models endpoint
curl https://api.your-domain.com/api/models
# Should return list of available models

# Test content pipeline
curl -X POST https://api.your-domain.com/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Post",
    "type": "content_generation",
    "parameters": {"topic": "AI Trends"}
  }'
# Should return 201 with task_id
```

---

## 🔄 Post-Deployment Steps

### 1. Configure Monitoring

Set up alerts for:

- API response time > 2 seconds
- Error rate > 1%
- Database connection failures
- Disk space usage > 80%

### 2. Set Up Backups

**Automated Daily Backups:**

```bash
# Create backup script
cat > backup-database.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/backups/postgresql"
DATABASE_URL="postgresql://user:pass@host:5432/db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Full backup
pg_dump $DATABASE_URL | gzip > $BACKUP_DIR/glad_labs_$TIMESTAMP.sql.gz

# Keep only last 7 days
find $BACKUP_DIR -mtime +7 -delete

echo "Backup completed: $TIMESTAMP"
EOF

chmod +x backup-database.sh

# Add to crontab
crontab -e
# Add: 0 2 * * * /path/to/backup-database.sh
```

### 3. Configure Logging

**Railway Logging:**

```bash
# View logs
railway logs --tail 100

# Export logs
railway logs --output json > logs.json
```

**Application Logging:**

Application logs are automatically output to stdout/stderr and captured by Railway.

Configure log level in environment:

```bash
LOG_LEVEL=INFO    # Production
LOG_LEVEL=DEBUG   # Troubleshooting
```

### 4. Set Up CDN (Optional)

For better performance on images:

```bash
# Configure CloudFlare or similar
# Origin: https://api.your-domain.com
# Cache Rules:
# - /images/* : Cache 30 days
# - /api/* : No cache (bypass)
```

---

## 🔒 Security Configuration

### 1. Database Security

```sql
-- Limit user permissions (principle of least privilege)
REVOKE ALL ON DATABASE glad_labs FROM glad_labs_user;
GRANT CONNECT ON DATABASE glad_labs TO glad_labs_user;
GRANT USAGE ON SCHEMA public TO glad_labs_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO glad_labs_user;
```

### 2. Environment Variables

Store secrets in:

- Railway Dashboard (not in git)
- GitHub Secrets (for GitHub Actions)
- `.env.production` (local only, not committed)

Never commit:

```
.env
.env.local
.env.production
DATABASE_URL with real credentials
API keys
```

### 3. HTTPS/SSL

- ✅ Vercel: Automatic SSL
- ✅ Railway: Automatic SSL
- ⚠️ Custom domains: Configure Let's Encrypt

### 4. Rate Limiting

Configure in FastAPI:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/tasks")
@limiter.limit("100/minute")
async def create_task(request: Request, task: TaskCreate):
    pass
```

---

## 🚨 Troubleshooting Deployment

### Issue: Database Connection Refused

```bash
# Check DATABASE_URL format
echo $DATABASE_URL
# Should be: postgresql://user:password@host:port/database

# Test connection locally
psql "$DATABASE_URL" -c "SELECT 1"

# Check network connectivity
telnet prod-db.railway.app 5432
```

### Issue: Schema Not Created

```bash
# Check if database exists
psql -U postgres -l | grep glad_labs

# Manually create schema if needed
psql -U glad_labs_user -d glad_labs -f schema.sql

# Or let application create it on startup
# Set DATABASE_URL and restart the service
```

### Issue: API Returns 502 Bad Gateway

```bash
# Check backend service health
curl https://api.your-domain.com/api/health

# View logs
railway logs --follow

# Check memory/CPU usage
railway service info

# Restart service if needed
railway redeploy --service cofounder-agent
```

### Issue: Images Not Saving

```bash
# Check PEXELS_API_KEY
echo $PEXELS_API_KEY

# Check file system permissions
ls -la /image-storage/

# Test image endpoint directly
curl https://api.your-domain.com/api/agents/image/test
```

---

## 📊 Performance Monitoring

### Key Metrics to Monitor

| Metric                  | Target | Alert If |
| ----------------------- | ------ | -------- |
| API Response Time (p95) | <500ms | >2s      |
| Error Rate              | <0.1%  | >1%      |
| Database Connections    | <10    | >15      |
| Memory Usage            | <60%   | >80%     |
| Disk Space              | <70%   | >90%     |
| Uptime                  | 99.9%  | <99%     |

### Using Railway Insights

1. Open Railway Dashboard
2. Select Co-Founder Agent service
3. Click "Insights"
4. View:
   - CPU usage
   - Memory usage
   - Request count
   - Error rate
   - Response time

---

## 🔄 Rollback Procedure

If deployment fails:

```bash
# Identify last working version
git log --oneline | head -5

# Revert to previous version
git revert <commit-hash>
git push origin main

# Railway will automatically redeploy the reverted code

# Monitor the revert
railway logs --follow

# If database schema changed, you may need to restore from backup
pg_restore $BACKUP_FILE -U glad_labs_user -d glad_labs
```

---

## ✅ Post-Deployment Verification

Run these checks after deployment:

```bash
# 1. Health check
curl https://api.your-domain.com/api/health

# 2. Database connectivity
curl https://api.your-domain.com/api/health?check=database

# 3. Model providers
curl https://api.your-domain.com/api/models/status

# 4. Create test task
curl -X POST https://api.your-domain.com/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Deployment Test",
    "type": "content_generation",
    "parameters": {"topic": "Test"}
  }'

# 5. Verify database tables
psql "$DATABASE_URL" -c "\dt"

# 6. Check frontend loading
curl https://your-domain.com/

# 7. Check oversight hub
curl https://admin.your-domain.com/

# All should return 200 OK
```

---

## 📞 Support & Help

### Logs & Debugging

```bash
# View application logs
railway logs --tail 100

# Filter by error level
railway logs | grep ERROR

# Export logs for analysis
railway logs --output json > analysis.json
```

### Database Debugging

```bash
# Connect to database
psql "$DATABASE_URL"

# List tables
\dt

# Check post count
SELECT COUNT(*) FROM posts;

# View latest posts
SELECT title, slug, created_at FROM posts ORDER BY created_at DESC LIMIT 5;

# Check for errors
SELECT * FROM posts WHERE status = 'error';
```

### API Testing

```bash
# Test with curl
curl -v https://api.your-domain.com/api/health

# Test with httpie
http https://api.your-domain.com/api/health

# Load test (with artillery)
artillery quick --count 100 --num 10 https://api.your-domain.com/api/health
```

---

## 🎯 Deployment Complete! 🎉

Your PostgreSQL-First Glad Labs deployment is ready!

**Next Steps:**

1. ✅ Monitor logs and metrics
2. ✅ Test content generation pipeline
3. ✅ Set up automated backups
4. ✅ Configure monitoring/alerting
5. ✅ Document any customizations
6. ✅ Train team on operations

**Questions?** Check:

- `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md` - Detailed deployment info
- `docs/06-OPERATIONS_AND_MAINTENANCE.md` - Operations guide
- `POSTGRESQL_MIGRATION_COMPLETE.md` - Architecture overview
