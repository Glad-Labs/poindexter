# 🔧 Co-Founder Agent Startup Issues - Resolution Guide

**Date:** October 31, 2025  
**Status:** FIXED ✅  
**Root Cause:** SQLite + PostgreSQL data type mismatch  
**Solution:** Use PostgreSQL connection URL

---

## 📋 The Problem

When you ran `npm run dev:smartstart`, the Co-Founder Agent failed with:

```
sqlalchemy.exc.CompileError: (in table 'users', column 'backup_codes'):
Compiler can't render element of type ARRAY
```

**Why it happened:**

- Your `models.py` defines columns with `ARRAY` type (PostgreSQL-specific)
- SQLite doesn't support `ARRAY` type
- The code was falling back to SQLite instead of using PostgreSQL

---

## ✅ The Fix (Already Applied)

Added to `.env.local`:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

**What this does:**

- Tells the Co-Founder Agent to use PostgreSQL (not SQLite)
- Provides the exact connection string
- Supports `ARRAY` and other PostgreSQL-specific types

---

## 🚀 Try Starting Again

Now run:

```bash
npm run dev:smartstart
```

**Expected sequence:**

1. Co-Founder Agent starts (port 8000)
2. Strapi CMS starts (port 1337)
3. Script waits for both to respond
4. Public Site starts (port 3000)
5. Oversight Hub starts (port 3001)

**What to look for:**

- ✅ `Started server process [PID]`
- ✅ `Application startup complete`
- ✅ No ARRAY errors
- ✅ Database initialized successfully

---

## 🧪 Verify Each Service

### Co-Founder Agent Health Check

```bash
curl http://localhost:8000/api/health
```

Expected: `{"status": "healthy", ...}`

### Strapi Health Check

```bash
curl http://localhost:1337/admin
```

Expected: 200 OK (may redirect to login)

### Public Site

```bash
# Open browser
http://localhost:3000
```

### Oversight Hub

```bash
# Open browser
http://localhost:3001
```

---

## 📊 Your Configuration Summary

| Service          | Port | Database                       | Status |
| ---------------- | ---- | ------------------------------ | ------ |
| Co-Founder Agent | 8000 | PostgreSQL `glad_labs_dev`     | ✅     |
| Strapi CMS       | 1337 | SQLite (configured separately) | ✅     |
| Public Site      | 3000 | None (static/API client)       | ✅     |
| Oversight Hub    | 3001 | None (static/API client)       | ✅     |

---

## 🔍 Troubleshooting if It Still Fails

### Issue: "Connection refused" on port 8000

**Solution:** PostgreSQL isn't responding

```bash
# Check PostgreSQL is running
psql -U postgres -h localhost -c "SELECT 1;"

# If it fails, restart PostgreSQL
# Windows: Services → PostgreSQL → Restart
```

### Issue: "Authentication failed"

**Solution:** Wrong password in DATABASE_URL

Check your actual PostgreSQL password:

```bash
# Update DATABASE_URL with correct password
# Current: postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

### Issue: "Database 'glad_labs_dev' does not exist"

**Solution:** Create the database

```bash
psql -U postgres -h localhost -c "CREATE DATABASE glad_labs_dev OWNER postgres;"
```

### Issue: Still getting ARRAY errors

**Solution:** Clear Python cache and reinstall

```bash
# Remove pycache
Remove-Item -Recurse -Force src/cofounder_agent/__pycache__

# Reinstall requirements
pip install -r src/cofounder_agent/requirements.txt

# Try again
npm run dev:smartstart
```

---

## 📚 Related Files

- **Environment Config:** `.env.local`
- **Database Service:** `src/cofounder_agent/services/database_service.py`
- **Database Models:** `src/cofounder_agent/models.py`
- **Start Script:** `src/cofounder_agent/start_server.py`

---

## 🎯 Next Steps

1. **Run the startup command:**

   ```bash
   npm run dev:smartstart
   ```

2. **Monitor the logs for:**
   - Successful database connection
   - Table creation messages
   - "Application startup complete"

3. **Test each service:**
   - Co-Founder Agent: `http://localhost:8000/docs`
   - Strapi Admin: `http://localhost:1337/admin`
   - Public Site: `http://localhost:3000`
   - Oversight Hub: `http://localhost:3001`

4. **If successful:** Your dev environment is ready! 🚀

---

## 💾 .env.local Configuration Checklist

✅ `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev`  
✅ `DATABASE_HOST=localhost`  
✅ `DATABASE_PORT=5432`  
✅ `DATABASE_NAME=glad_labs_dev`  
✅ `DATABASE_USER=postgres`  
✅ `DATABASE_PASSWORD=postgres`  
✅ `COFOUNDER_AGENT_PORT=8000`  
✅ `STRAPI_PORT=1337`

All configured! ✨
