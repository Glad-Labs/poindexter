# Sprint 5 Analytics & Profiling: Deployment Checklist

**Version:** 1.0  
**Last Updated:** February 20, 2026  
**Deployment Date:** [FILL IN]  
**Deployed By:** [FILL IN]  

---

## Pre-Deployment Validation (Dev/Staging)

### Code Quality Checks

- [ ] All Python code passes lint
  ```bash
  # From project root
  npm run format:check
  ```

- [ ] All tests passing
  ```bash
  # Full test suite
  npm run test:python
  
  # Smoke tests only (faster)
  npm run test:python:smoke
  ```

- [ ] No critical security issues
  ```bash
  # Check for known vulnerabilities
  npm audit --workspace=oversight-hub
  pip audit
  ```

- [ ] Import statements all correct
  ```bash
  # Verify all new imports work
  cd src/cofounder_agent && python -c "
from middleware.profiling_middleware import ProfilingMiddleware
from routes.profiling_routes import router as profiling_router
from services.task_executor import TaskMetrics
print('✅ All imports successful')
"
  ```

### Functional Testing

- [ ] Profiling middleware capturing requests
  ```bash
  # Make a test request
  curl -X POST http://localhost:8000/api/tasks \
    -H "Content-Type: application/json" \
    -d '{"title": "Deployment Test", "category": "blog_post"}'
  
  # Verify it was profiled
  curl http://localhost:8000/api/profiling/recent-requests | \
    jq '.profiles[] | select(.endpoint | contains("/api/tasks"))'
  ```

- [ ] Analytics endpoints returning data
  ```bash
  # All analytics endpoints should return 200
  for endpoint in kpis tasks costs content quality agents; do
    echo -n "Testing /api/analytics/$endpoint ... "
    curl -s http://localhost:8000/api/analytics/$endpoint?range=7d | jq . > /dev/null && echo "✅" || echo "❌"
  done
  ```

- [ ] Profiling endpoints operational
  ```bash
  # All profiling endpoints should return 200
  for endpoint in health slow-endpoints endpoint-stats recent-requests phase-breakdown; do
    echo -n "Testing /api/profiling/$endpoint ... "
    curl -s http://localhost:8000/api/profiling/$endpoint | jq . > /dev/null && echo "✅" || echo "❌"
  done
  ```

- [ ] AnalyticsDashboard renders without errors
  ```bash
  # Check browser console (F12 → Console)
  # No errors should appear after navigating to http://localhost:3001/analytics
  ```

- [ ] PerformanceDashboard charts load
  ```bash
  # Navigate to http://localhost:3001/dashboard
  # Verify both charts (latencies + model decisions) render with data
  ```

### Database Readiness

- [ ] PostgreSQL running and accessible
  ```bash
  psql $DATABASE_URL -c "SELECT 1;" && echo "✅ Database connected"
  ```

- [ ] admin_logs table exists with correct schema
  ```bash
  psql $DATABASE_URL -c "
  \d admin_logs
  -- Should show: id, task_id, data (JSONB), created_at
  "
  ```

- [ ] No migrations pending
  ```bash
  # If using migration system:
  alembic current
  alembic heads
  # Both should show same version
  ```

- [ ] Database has sufficient disk space
  ```bash
  psql $DATABASE_URL -c "
  SELECT 
    pg_database.datname,
    pg_size_pretty(pg_database_size(pg_database.datname)) AS size
  FROM pg_database
  WHERE datname = current_database();"
  ```

### Performance Validation

- [ ] Profiling overhead acceptable
  ```bash
  # Typical: 0.5-2ms per request
  curl http://localhost:8000/api/profiling/endpoint-stats | \
    jq '.endpoints | .[] | select(.avg_duration_ms > 5000) | .endpoint'
  
  # Should have few or no results
  ```

- [ ] No memory leaks in profiling middleware
  ```bash
  # Monitor for 5 minutes, memory should stay stable
  curl http://localhost:8000/api/profiling/health
  sleep 300
  curl http://localhost:8000/api/profiling/health
  # profiles_tracked should not be at max (1000)
  ```

- [ ] Database query performance acceptable
  ```bash
  psql $DATABASE_URL -c "
  EXPLAIN ANALYZE
  SELECT * FROM admin_logs 
  WHERE created_at > NOW() - INTERVAL '7 days'
  LIMIT 1000;
  -- Should complete in < 100ms
  "
  ```

### Dependencies Verified

- [ ] All Python packages installed
  ```bash
  pip list | grep -E "fastapi|uvicorn|pydantic"
  # Should show all installed
  ```

- [ ] All Node packages installed
  ```bash
  npm ls --workspace=oversight-hub recharts
  # Should show: recharts@^2.14.6 or higher
  ```

- [ ] poetry.lock is committed (Python reproducibility)
  ```bash
  git status poetry.lock
  # Should show: (no changes)
  ```

- [ ] package-lock.json is updated
  ```bash
  npm ls recharts --workspace=oversight-hub
  # Should match package.json version
  ```

---

## Production Deployment Steps

### 1. Code Deployment

- [ ] Pull latest from main/production branch
  ```bash
  git pull origin main
  git log --oneline -5  # Verify latest commits
  ```

- [ ] Verify branch is clean
  ```bash
  git status
  # Should show: "nothing to commit, working tree clean"
  ```

- [ ] Checkout commit hash (optional but recommended)
  ```bash
  # Use this specific commit for deployment
  git checkout abc123def456  # Use actual commit hash
  ```

### 2. Backend Deployment

- [ ] Stop current backend service
  ```bash
  # If using systemd
  sudo systemctl stop cofounder_agent
  
  # If using Docker
  docker stop glad-labs-backend
  
  # If using screen/tmux
  # Ctrl+C in the running terminal
  ```

- [ ] Install/update Python dependencies
  ```bash
  cd src/cofounder_agent
  pip install -r requirements.txt
  # OR if using poetry
  poetry install --no-dev
  ```

- [ ] Verify Python can import new modules
  ```bash
  python -c "
from middleware.profiling_middleware import ProfilingMiddleware
from routes.profiling_routes import router
print('✅ New modules import successfully')
"
  ```

- [ ] Run any pending database migrations (if applicable)
  ```bash
  # If using Alembic or similar
  alembic upgrade head
  
  # Or manual: ensure admin_logs table exists
  psql $DATABASE_URL -c "
  CREATE TABLE IF NOT EXISTS admin_logs (
    id SERIAL PRIMARY KEY,
    task_id UUID REFERENCES tasks(id),
    data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
  );
  CREATE INDEX IF NOT EXISTS idx_admin_logs_created_at ON admin_logs(created_at);
  "
  ```

- [ ] Start backend service with new code
  ```bash
  # If using systemd
  sudo systemctl start cofounder_agent
  
  # If using Docker
  docker start glad-labs-backend
  
  # If using screen/tmux
  screen -S backend -d -m bash -c "cd src/cofounder_agent && poetry run uvicorn main:app --reload --port 8000"
  
  # Verify it started
  sleep 3 && curl http://localhost:8000/health
  ```

- [ ] Check backend logs for errors
  ```bash
  # View recent logs
  tail -50 server.log
  
  # Filter for errors
  tail -f server.log | grep -i "error\|exception\|warning"
  
  # Should see: "Application startup complete"
  ```

### 3. Frontend Deployment

- [ ] Install frontend dependencies
  ```bash
  cd web/oversight-hub
  npm install
  
  # Verify recharts installed
  npm ls recharts
  ```

- [ ] Build frontend for production
  ```bash
  npm run build
  
  # Verify build output exists
  ls -la build/
  ```

- [ ] Stop current frontend service
  ```bash
  # If using systemd
  sudo systemctl stop oversight-hub
  
  # If using Docker
  docker stop oversight-hub
  ```

- [ ] Deploy built files
  ```bash
  # Copy to production directory
  cp -r build/* /var/www/oversight-hub/
  
  # Or restart container
  docker start oversight-hub
  ```

- [ ] Verify frontend loads
  ```bash
  curl http://localhost:3001 | head -20
  # Should return HTML (not error)
  ```

### 4. Smoke Testing (Post-Deployment)

- [ ] All services responding
  ```bash
  echo "Backend:" && curl http://localhost:8000/health && \
  echo "Frontend:" && curl http://localhost:3001 > /dev/null && echo "✅" && \
  echo "Oversight:" && curl http://localhost:3001/analytics > /dev/null && echo "✅"
  ```

- [ ] Profiling system operational
  ```bash
  curl http://localhost:8000/api/profiling/health | jq .
  # Should show: "status": "healthy"
  ```

- [ ] Analytics endpoints accessible
  ```bash
  curl http://localhost:8000/api/analytics/kpis?range=7d | \
    jq '.kpis | length'
  # Should return: 4 (number of KPI cards)
  ```

- [ ] Dashboard loads successfully
  ```bash
  # Check in browser: http://localhost:3001/analytics
  # Should see: KPI, Tasks, Costs tabs
  # Should NOT see: JavaScript errors in console
  ```

- [ ] Create test task to verify full pipeline
  ```bash
  curl -X POST http://localhost:8000/api/tasks \
    -H "Content-Type: application/json" \
    -d '{
      "title": "Deployment Test Task",
      "category": "blog_post",
      "description": "Testing profiling and metrics after deployment"
    }' | jq '.id'
  
  # Wait 1-2 minutes for task to execute
  sleep 120
  
  # Verify it appears in analytics
  curl http://localhost:8000/api/analytics/tasks?range=7d | jq '.total_tasks'
  ```

### 5. Monitoring Setup

- [ ] Set up log aggregation (if applicable)
  ```bash
  # If using ELK/Splunk/CloudWatch
  # Ensure new profiling_middleware.py logs are being sent
  tail -f server.log | grep "ProfileData"
  ```

- [ ] Configure alerts for slow endpoints
  ```bash
  # Example monitoring command to run periodically
  cat > /etc/cron.d/check-slow-endpoints << 'EOF'
  */5 * * * * curl -s http://localhost:8000/api/profiling/slow-endpoints?threshold_ms=1000 | jq '.count' > /tmp/slow-endpoint-count.txt
  EOF
  ```

- [ ] Set up dashboards
  ```bash
  # Verify Grafana/Datadog/etc. has access to metrics endpoints
  curl http://[MONITORING_SYSTEM]/api/metrics/profiling
  ```

- [ ] Test alerting (send test alert)
  ```bash
  # For PagerDuty/Slack/etc.
  # Send test alert to verify on-call receives it
  ```

---

## Rollback Plan

If deployment fails, use this rollback procedure:

- [ ] Identify issue
  ```bash
  # Check recent logs
  tail -100 server.log
  
  # Check endpoint status
  curl http://localhost:8000/api/profiling/health
  ```

- [ ] Rollback backend (revert to previous version)
  ```bash
  git checkout HEAD~1  # Go back one commit
  cd src/cofounder_agent
  poetry install
  
  # Stop and restart
  sudo systemctl restart cofounder_agent
  sleep 3 && curl http://localhost:8000/health
  ```

- [ ] Rollback frontend
  ```bash
  git checkout HEAD~1
  cd web/oversight-hub
  npm install
  npm run build
  cp -r build/* /var/www/oversight-hub/
  ```

- [ ] Verify services recovered
  ```bash
  curl http://localhost:8000/health
  curl http://localhost:3001
  ```

- [ ] Document incident
  ```bash
  # Create post-mortem
  cat >> INCIDENTS.log << 'EOF'
  [DATE] Sprint 5 Deployment Rollback
  Reason: [DESCRIBE ISSUE]
  Action: Reverted to [PREVIOUS COMMIT]
  Resolution: [WHAT WAS FIXED]
  EOF
  ```

---

## Post-Deployment Verification (24 Hours)

### System Health

- [ ] No errors in logs
  ```bash
  tail -1000 server.log | grep -i "error\|exception" | wc -l
  # Should be 0 or minimal
  ```

- [ ] Database performance normal
  ```bash
  psql $DATABASE_URL -c "
  SELECT 
    COUNT(*) as total_logs,
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour
  FROM admin_logs;"
  ```

- [ ] Profiling accuracy verified
  ```bash
  curl http://localhost:8000/api/profiling/endpoint-stats | \
    jq '.endpoints | .[] | {endpoint: .endpoint, avg_ms: .avg_duration_ms} | select(.avg_ms > 0)' | \
    head -5
  # Should show real latency data
  ```

### Data Integrity

- [ ] Metrics being recorded correctly
  ```bash
  # Check that tasks have cost/duration data
  psql $DATABASE_URL -c "
  SELECT COUNT(*) 
  FROM admin_logs 
  WHERE data @> '{\"phase\": \"generation\"}' 
  AND created_at > NOW() - INTERVAL '24 hours';"
  ```

- [ ] Analytics dashboard shows correct data
  ```bash
  # In browser: http://localhost:3001/analytics
  # Verify KPIs match database values
  curl http://localhost:8000/api/analytics/costs?range=24h | jq '.total_cost_usd'
  ```

### Performance Metrics

- [ ] P99 latency within baseline
  ```bash
  curl http://localhost:8000/api/profiling/endpoint-stats | \
    jq '.endpoints[] | select(.p99_duration_ms > 5000)'
  # Should return 0-1 results (not many slow endpoints)
  ```

- [ ] No memory leaks
  ```bash
  # Compare profiling memory usage
  curl http://localhost:8000/api/profiling/health | jq '.profiles_tracked'
  # Should be stable over time (not growing unbounded)
  ```

---

## Final Approval Sign-Off

- [ ] **Tested by:** [NAME]
- [ ] **Date:** [DATE]
- [ ] **All checks passed:** YES / NO

- [ ] **Approved by:** [MANAGER/LEAD]
- [ ] **Date:** [DATE]
- [ ] **Feedback:** [NOTES]

---

## Documentation Updates

- [ ] Updated CHANGELOG.md
  ```bash
  git log --oneline HEAD~10..HEAD >> CHANGELOG.md
  ```

- [ ] Updated team wiki/docs
  ```bash
  # Link to Sprint 5 documentation:
  # - ANALYTICS_AND_PROFILING_API.md
  # - MONITORING_AND_DIAGNOSTICS.md
  # - ANALYTICS_QUICK_START.md
  ```

- [ ] Notified team of new capabilities
  ```bash
  # Send email/Slack:
  # Subject: Sprint 5 Live - Analytics Dashboard Available
  # Link: http://[PRODUCTION_URL]/analytics
  ```

---

## Known Issues & Workarounds

### Issue 1: "Slow endpoints" showing false positives
```
Cause: Threshold too low
Fix: Increase threshold or investigate endpoint performance
curl http://localhost:8000/api/profiling/slow-endpoints?threshold_ms=2000
```

### Issue 2: Analytics showing no data
```
Cause: admin_logs table empty (no tasks executed)
Fix: Run tests to generate data or wait for first tasks to complete
npm run test:python:smoke
```

### Issue 3: Recharts charts not rendering
```
Cause: npm install not run in oversight-hub
Fix: cd web/oversight-hub && npm install
```

---

## Rollback Contacts

**Backend Issues:** [BACKEND_ONCALL]
**Frontend Issues:** [FRONTEND_ONCALL]
**Database Issues:** [DBA_ONCALL]
**General Lead:** [DEPLOYMENT_LEAD]

---

## Appendix: Command Reference

### Quick Health Check
```bash
# All-in-one health check
echo "=== Backend ===" && curl -s http://localhost:8000/health | jq .status && \
echo "=== Profiling ===" && curl -s http://localhost:8000/api/profiling/health | jq .status && \
echo "=== Frontend ===" && curl -s http://localhost:3001 > /dev/null && echo "ok"
```

### Emergency Stop
```bash
# Stop all services immediately
sudo systemctl stop cofounder_agent oversight-hub
# Or if using Docker
docker stop glad-labs-backend oversight-hub public-site
```

### Clear Profiling Cache
```bash
# Restart backend to clear in-memory profiling
sudo systemctl restart cofounder_agent
# Profiles will start fresh
```

---

**Deployment completed:** [FILL IN DATE/TIME]  
**Deployed by:** [FILL IN NAME]  
**Environment:** [DEV/STAGING/PRODUCTION]  
**Commit Hash:** [FILL IN GIT COMMIT]  
