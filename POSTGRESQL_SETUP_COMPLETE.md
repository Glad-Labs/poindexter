# PostgreSQL Local Development Setup - Complete ✅

**Status:** ✅ Configuration Complete  
**Date:** October 31, 2025  
**Database:** glad_labs_dev  
**User:** postgres

## Summary

Your local development environment is now configured to use PostgreSQL with the **shared `glad_labs_dev` database** for both Strapi CMS and Co-Founder Agent, matching your Railway production setup.

## Configuration Details

### Environment Variables (.env.local)

```bash
# PostgreSQL Configuration (LOCAL DEV)
DATABASE_CLIENT=postgres
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres

# Co-Founder Agent: Full PostgreSQL connection URL
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

### Strapi CMS (.cms/strapi-main/.env)

```bash
DATABASE_CLIENT=postgres
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

### Database Configuration Files

**Co-Founder Agent** (`src/cofounder_agent/database.py`):

- Reads `DATABASE_URL` environment variable first
- Falls back to `DATABASE_CLIENT` + component environment variables
- Supports both PostgreSQL and SQLite

**Strapi CMS** (`cms/strapi-main/config/database.js`):

- Enhanced to check `DATABASE_URL` first (Railway pattern)
- Automatically detects PostgreSQL connection strings
- Falls back to SQLite if no PostgreSQL URL found

## Verification ✅

### 1. PostgreSQL Running

```
PostgreSQL 18.0 on x86_64-windows ✅
```

### 2. Database Exists

```sql
CREATE DATABASE glad_labs_dev ✅
(Already exists)
```

### 3. Tables Created (16 total)

```
✅ agent_status
✅ api_keys
✅ content_tasks
✅ feature_flags
✅ financial_entries
✅ health_checks
✅ logs
✅ permissions
✅ role_permissions
✅ roles
✅ sessions
✅ settings
✅ settings_audit_log
✅ tasks
✅ user_roles
✅ users
```

### 4. Connection Verified

```
✅ Database URL: postgresql://postgres:postgres@localhost:5432/glad_labs_dev
✅ Python can access database configuration
```

## How It Works

### Local Development Architecture

```
PostgreSQL (glad_labs_dev)
│
├─ Strapi CMS (port 1337)
│  └─ Uses DATABASE_URL from .env
│  └─ Connects via connection pooling
│
└─ Co-Founder Agent (port 8000)
   └─ Uses DATABASE_URL from environment
   └─ Manages task store and models
   └─ Shares all data with Strapi
```

### Key Features

✅ **Shared Database** - Both services use the same `glad_labs_dev` database  
✅ **Production Parity** - Same setup as Railway deployment  
✅ **Connection Pooling** - Strapi pools: min 0, max 7  
✅ **Environment Variable Priority** - DATABASE_URL takes precedence  
✅ **Fallback Support** - Both SQLite and PostgreSQL supported

## Starting Services

### Option 1: Start All Services (Recommended)

```bash
npm run dev
```

This will start:

- Strapi CMS (port 1337)
- Oversight Hub (port 3001)
- Public Site (port 3000)
- Co-Founder Agent (port 8000)

### Option 2: Start Individual Services

**Terminal 1: Strapi CMS**

```bash
cd cms/strapi-main
npm run develop
```

**Terminal 2: Co-Founder Agent**

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload --port 8000
```

**Terminal 3: Frontend Services**

```bash
cd web
npm run dev
```

## Testing Configuration

### Test Strapi Connection

```bash
curl http://localhost:1337/admin
```

Should load admin panel (may show setup wizard if first time)

### Test Co-Founder Agent

```bash
curl http://localhost:8000/health
```

Should return:

```json
{
  "status": "healthy",
  "database_url_configured": true
}
```

### Test Database Directly

```bash
psql -U postgres -d glad_labs_dev
# Or verify tables
psql -U postgres -d glad_labs_dev -c "\dt"
```

## Data Integrity

Both Strapi and Co-Founder Agent now share:

- **Users** (`users` table)
- **Roles & Permissions** (`roles`, `permissions` tables)
- **Settings** (`settings`, `settings_audit_log` tables)
- **Content Tasks** (`content_tasks`, `tasks` tables)
- **Session Management** (`sessions` table)

When you create content in Strapi, it's immediately available to Co-Founder Agent queries.

## Important Notes

⚠️ **Password Security:**

- Default password `postgres` is for local development only
- Change for any multi-user local environment
- Production uses Railway's managed credentials

⚠️ **Port 5432:**

- Ensure no other services use PostgreSQL's default port
- Change in `.env.local` if needed

⚠️ **SQLite Still Available:**

- To revert to SQLite, remove `DATABASE_URL` from `.env.local`
- Strapi will default to `.tmp/data.db`
- Co-Founder Agent will use `.tmp/data.db`

## Troubleshooting

### Database Connection Refused

```bash
# Check if PostgreSQL is running
tasklist | find /i "postgres"

# Start PostgreSQL (if installed as service)
net start PostgreSQL*
```

### "Database already exists"

This is normal - the database was created on first setup. This message can be safely ignored.

### Strapi Showing SQLite in Logs

This happens if `DATABASE_URL` is not loaded. Verify:

1. `.env.local` has `DATABASE_URL=postgresql://...`
2. Restart Strapi after changing `.env.local`
3. Check that env file is in root directory

### Port Already in Use

```bash
# Change ports in .env.local
STRAPI_PORT=1338
COFOUNDER_AGENT_PORT=8001
```

## Production Deployment

When deploying to Railway:

1. **No code changes needed** - Same configuration files work
2. **Railway sets DATABASE_URL automatically**
3. **Connection pooling** configured for production loads
4. **Both services** auto-detect PostgreSQL from DATABASE_URL

Example Railway DATABASE_URL:

```
postgresql://user:password@region.railway.app:5432/database_name
```

Our code automatically handles this! ✅

## Next Steps

✅ PostgreSQL configured  
✅ Database created  
✅ Tables verified  
✅ Environment variables set

→ **Now run:** `npm run dev`

## References

- 📖 [PostgreSQL Windows Setup](https://www.postgresql.org/download/windows/)
- 📖 [Strapi Database Config](https://docs.strapi.io/dev-docs/configurations/database)
- 📖 [SQLAlchemy PostgreSQL](https://docs.sqlalchemy.org/en/20/dialects/postgresql/)
- 📖 [Railway PostgreSQL](https://docs.railway.app/deploy/postgresql)

---

**Configuration Status:** ✅ COMPLETE AND VERIFIED
