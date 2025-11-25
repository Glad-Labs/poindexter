# 06 - Operations & Maintenance

**Last Updated:** November 5, 2025  
**Version:** 1.1  
**Status:** ✅ Production Ready

---

## 🎯 Quick Links

- **[Health Monitoring](#health-monitoring)** - System health checks
- **[Backups & Recovery](#backups--recovery)** - Disaster recovery
- **[Performance Optimization](#performance-optimization)** - Speed and efficiency
- **[Security](#security)** - Data protection and compliance
- **[Troubleshooting](#troubleshooting)** - Common issues

---

## 🏥 Health Monitoring

### Service Health Checks

```bash
# Backend API
curl https://api.example.com/api/health
# Expected: {"status": "healthy", "timestamp": "..."}

# Frontend
curl https://example.com/
# Expected: 200 OK with HTML content
```

### Automated Monitoring

**Set up monitoring for:**

- API response time (target: <500ms)
- Database connection pool
- Memory usage (alert if >80%)
- Disk space (alert if >90%)
- Error rate (alert if >1%)

### Log Aggregation

```bash
# Railway logs
railway logs --service=backend

# Vercel logs
vercel logs

# Local logs
tail -f logs/*.log
```

### Alert Configuration

**Send alerts for:**

- Service downtime
- High error rates (>1%)
- Slow response times (>2s)
- Resource exhaustion (>90%)
- Failed deployments

### Agent Execution Monitoring

**Monitor AI agent performance and health:**

```bash
# Check all agent status
curl https://api.example.com/api/agents/status
# Expected: {"status": "healthy", "agents": [...], "timestamp": "..."}

# Check specific agent execution
curl https://api.example.com/api/agents/{agent-name}/status
# Example: /api/agents/content/status

# View agent memory usage
curl https://api.example.com/api/agents/memory/stats

# Check agent error logs
curl https://api.example.com/api/agents/logs?level=error&limit=50

# Monitor model router status
curl https://api.example.com/api/models/status
# Shows: Active providers, fallback chain, current provider usage
```

**Agent-Specific Metrics to Monitor:**

- **Content Agent:** Generation speed, quality score, self-critique feedback loop time
- **Financial Agent:** Calculation accuracy, cost tracking updates
- **Market Agent:** Trend detection latency, accuracy score
- **Compliance Agent:** Violation detection rate, false positive percentage

**Alert Conditions for Agents:**

- Any agent execution time >5 minutes (slow)
- Model router fallback chain exhausted (all models failing)
- Agent memory exceeding 500MB (leak detection)
- QA agent rejection rate >30% (content quality issue)
- Self-critique loop iterations >3 (stuck refining)

**Troubleshooting Agent Issues:**

```bash
# Restart agent system
railway redeploy --service=cofounder-agent

# Check model provider connectivity
curl https://api.example.com/api/models/test-all

# View real-time agent logs
railway logs --service=cofounder-agent --follow

# Check Ollama status (if using)
curl http://localhost:11434/api/tags
curl http://localhost:11434/api/status
```

---

## 💾 Backups & Recovery

### Database Backups

```bash
# Automated daily backup
0 2 * * * pg_dump $DATABASE_URL | gzip > /backups/db-$(date +\%Y\%m\%d).sql.gz

# Manual backup
pg_dump $DATABASE_URL > backup.sql

# Restore from backup
psql $DATABASE_URL < backup.sql
```

### Media File Backups

```bash
# If using S3 for media storage
aws s3 sync s3://bucket-name /backups/media/ --region us-east-1

# Or if using local file system
rsync -av /media/ /backups/media/
```

### Backup Retention Policy

```text
Daily:    Keep last 7 days
Weekly:   Keep last 4 weeks
Monthly:  Keep last 12 months
```

### Disaster Recovery Test

```bash
# Monthly: Test recovery procedure
1. Restore latest backup to test database
2. Verify all data is present
3. Run smoke tests
4. Document results
```

---

## ⚡ Performance Optimization

### Database Optimization

```sql
-- Check slow queries
SELECT * FROM pg_stat_statements
WHERE mean_exec_time > 1000
ORDER BY mean_exec_time DESC;

-- Add indexes for frequently queried columns
CREATE INDEX idx_posts_status ON posts(status);
CREATE INDEX idx_tasks_agent ON tasks(assigned_agents);

-- Vacuum and analyze
VACUUM ANALYZE;
```

### API Response Times

**Monitor:**

- Average response time
- 95th percentile response time
- Cache hit rates

**Optimize:**

- Add Redis caching for frequently accessed data
- Implement pagination for large datasets
- Use database query optimization

### Frontend Performance

```bash
# Check Core Web Vitals
npm run build
npm run analyze  # Bundle size analysis

# Lighthouse audit
lighthouse https://example.com
```

### Caching Strategy

```text
Static content:    Cache for 1 year
API responses:     Cache for 5 minutes
User data:         Cache for 1 minute
Dynamic content:   No cache
```

---

## 🔐 Security

### SSL/HTTPS

```bash
# Verify certificate
openssl s_client -connect example.com:443

# Renew Let's Encrypt certificate
certbot renew --dry-run
```

### Access Control

```python
# Implement RBAC (Role-Based Access Control)
class Permission:
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

# Check permissions on API endpoints
@app.post("/api/posts")
async def create_post(current_user: User = Depends(get_current_user)):
    if current_user.role != Permission.ADMIN:
        raise HTTPException(status_code=403)
```

### Environment Variables

```bash
# Never commit secrets to git
.env              # Local (ignored)
.env.example      # Template (committed)
.env.production   # Secrets (use Railway/Vercel dashboard)
```

### API Key Rotation

```bash
# Rotate API keys every 90 days
# 1. Generate new key
# 2. Update in secrets
# 3. Test with new key
# 4. Revoke old key
```

### Dependency Security

```bash
# Check for vulnerabilities
npm audit
pip audit

# Update vulnerable packages
npm audit fix
pip-audit --fix
```

---

## 🐛 Troubleshooting

### Service Is Down

```bash
# 1. Check service status
railway logs
vercel logs

# 2. Check resource usage
railway service info

# 3. Restart service
railway redeploy

# 4. If still down, rollback
git revert <commit-hash>
git push origin main
```

### Database Connection Issues

```bash
# 1. Test connection
psql $DATABASE_URL -c "SELECT 1"

# 2. Check firewall rules
# 3. Verify credentials
# 4. Check connection pool
```

### High Memory Usage

```bash
# 1. Identify memory consumer
top -p $(pgrep -f "python|node")

# 2. Check for memory leaks
# 3. Increase allocated memory
# 4. Optimize queries
```

### High Error Rate

```bash
# 1. Check error logs
railway logs --service=backend

# 2. Identify error pattern
# 3. Check external service dependencies
# 4. Review recent deployments

# 4. Rollback if necessary
git revert <commit-hash>
```

---

## 📊 Regular Maintenance Tasks

### Daily

- [ ] Check service health dashboard
- [ ] Monitor error rates
- [ ] Review backup completion

### Weekly

- [ ] Review performance metrics
- [ ] Update dependencies (non-critical)
- [ ] Verify backup integrity

### Monthly

- [ ] Full backup test/restore
- [ ] Security audit
- [ ] Review access logs
- [ ] Performance optimization review

### Quarterly

- [ ] Update documentation
- [ ] Review architecture decisions
- [ ] Plan for capacity upgrades
- [ ] Security assessment

---

## 📝 Runbooks

### Runbook: Deploy Hotfix

```bash
1. Create hotfix branch: git checkout -b hotfix/issue-name
2. Fix issue and test locally
3. Commit: git commit -m "fix: issue description"
4. Push: git push origin hotfix/issue-name
5. Merge to main: git checkout main && git merge hotfix/issue-name
6. Deploy: git push origin main
7. Verify production: curl https://api.example.com/api/health
8. Merge to dev: git checkout dev && git merge main
9. Delete branch: git branch -d hotfix/issue-name
```

### Runbook: Scale Service

```bash
1. Identify bottleneck (CPU, memory, database)
2. For vertical scaling: Increase instance size in Railway/Vercel
3. For horizontal scaling: Add replica instances
4. Monitor metrics after scaling
5. Run load tests to verify improvement
6. Document configuration changes
```

### Runbook: Emergency Rollback

```bash
1. Identify last working version: git log --oneline | head -5
2. Create rollback commit: git revert <commit-hash>
3. Push: git push origin main
4. Monitor: railway logs / vercel logs
5. Notify team
6. Post-mortem: Identify root cause and prevent recurrence
```

---

## 📈 Metrics to Track

| Metric                  | Target | Alert If |
| ----------------------- | ------ | -------- |
| API Response Time (p95) | <500ms | >2s      |
| Error Rate              | <0.1%  | >1%      |
| Uptime                  | 99.9%  | <99%     |
| Database Connections    | <50    | >80      |
| Memory Usage            | <60%   | >80%     |
| Disk Space              | <70%   | >90%     |
| Backup Age              | <24h   | >48h     |

---

## 🔗 Related Documentation

- **[Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Production setup
- **[Development](./04-DEVELOPMENT_WORKFLOW.md)** - Development practices
- **[Architecture](./02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[Setup](./01-SETUP_AND_OVERVIEW.md)** - Initial setup

---

**[← Back to Documentation Hub](./00-README.md)**

[Setup](./01-SETUP_AND_OVERVIEW.md) • [Architecture](./02-ARCHITECTURE_AND_DESIGN.md) • [Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) • [Development](./04-DEVELOPMENT_WORKFLOW.md)
