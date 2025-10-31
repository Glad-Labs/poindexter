# PostgreSQL Local Development Setup - Change Summary

**Completed:** October 31, 2025

## Overview

Your Glad Labs development environment is now configured to use PostgreSQL with a **shared `glad_labs_dev` database** for both Strapi CMS and Co-Founder Agent, mirroring your Railway production setup.

## Files Modified

### 1. `.env.local` (Root Configuration)

✅ Updated DATABASE configuration to use PostgreSQL:

- `DATABASE_CLIENT=postgres`
- `DATABASE_HOST=localhost`
- `DATABASE_PORT=5432`
- `DATABASE_NAME=glad_labs_dev`
- `DATABASE_USER=postgres`
- `DATABASE_PASSWORD=postgres`
- `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev`

### 2. `cms/strapi-main/.env` (Strapi Configuration)

✅ Added PostgreSQL connection variables:

- `DATABASE_CLIENT=postgres`
- `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev`

### 3. `cms/strapi-main/config/database.js` (Strapi Database Config)

✅ Enhanced to support both Railway and local PostgreSQL:

- Checks `DATABASE_URL` first (Railway production pattern)
- Auto-detects PostgreSQL connection strings
- Falls back to `DATABASE_CLIENT` + component variables
- Maintains SQLite fallback support

## Verification Completed ✅

### PostgreSQL Instance

```
✅ PostgreSQL 18.0 running on localhost:5432
```

### Database

```
✅ Database "glad_labs_dev" exists
✅ 16 tables created and ready
```

### Configuration

```
✅ Environment variables correctly set
✅ Python database module verified
✅ Strapi database config enhanced
```

### Shared Tables (Both Services)

```
✅ agent_status          (Agent monitoring)
✅ api_keys              (Authentication)
✅ content_tasks         (Content generation tasks)
✅ feature_flags         (Feature management)
✅ financial_entries     (Financial data)
✅ health_checks         (Health monitoring)
✅ logs                  (Application logs)
✅ permissions           (Role permissions)
✅ role_permissions      (Permission mappings)
✅ roles                 (User roles)
✅ sessions              (User sessions)
✅ settings              (App settings)
✅ settings_audit_log    (Settings changes)
✅ tasks                 (Background tasks)
✅ user_roles            (User-role mappings)
✅ users                 (User accounts)
```

## How It Works

### Architecture

```
Local Development Machine
├── PostgreSQL (glad_labs_dev)
│   └── Port: 5432
│
├── Strapi CMS (port 1337)
│   └── Reads: DATABASE_URL=postgresql://...
│   └── Connection Pool: min 0, max 7
│
└── Co-Founder Agent (port 8000)
    └── Reads: DATABASE_URL=postgresql://...
    └── Async: asyncpg driver
    └── Connection Pooling: Enabled
```

### Data Flow

1. **Strapi CMS** creates content → stored in shared database
2. **Co-Founder Agent** queries content → reads from same database
3. **Both services** share users, roles, settings, and sessions
4. **Single source of truth** - no data duplication

## Running the Services

### Start Everything at Once

```bash
npm run dev
```

This automatically starts:

- PostgreSQL (must be running independently)
- Strapi CMS (port 1337)
- Co-Founder Agent (port 8000)
- Oversight Hub (port 3001)
- Public Site (port 3000)

### Start Services Individually

```bash
# Terminal 1: Strapi
cd cms/strapi-main
npm run develop

# Terminal 2: Agent
cd src/cofounder_agent
python -m uvicorn main:app --reload --port 8000

# Terminal 3: Frontend
cd web
npm run dev
```

## Key Configuration Details

### Co-Founder Agent (FastAPI)

- **File**: `src/cofounder_agent/database.py`
- **Logic**: Checks `DATABASE_URL` first, then component variables
- **Driver**: asyncpg for PostgreSQL, sqlite3 for SQLite
- **Connection Pool**: min=0, max=5 (configurable)

### Strapi CMS

- **File**: `cms/strapi-main/config/database.js`
- **Logic**: Enhanced to detect PostgreSQL URL pattern
- **Connection Pool**: min=0, max=7 (Strapi standard)
- **Auto-migration**: Runs migrations on startup

### Environment Priority

1. `DATABASE_URL` (production Railway pattern) - **FIRST**
2. `DATABASE_CLIENT` + component variables
3. SQLite defaults (.tmp/data.db)

## Testing

### Verify Strapi Connection

```bash
curl http://localhost:1337/admin
# Should load admin interface
```

### Verify Agent Connection

```bash
curl http://localhost:8000/health
# Should return: {"status": "healthy", "database_url_configured": true}
```

### Verify Database Directly

```bash
psql -U postgres -d glad_labs_dev
\dt              # List all tables
SELECT COUNT(*) FROM users;  # Check users table
\q              # Exit
```

## Important Notes

### Security for Development

- Default password `postgres` used for local development only
- Change for multi-user local environments
- **Never commit** sensitive credentials to git

### Data Persistence

- PostgreSQL stores data on disk (survives restarts)
- All changes persist across service restarts
- Backups should be taken before major changes

### SQLite Still Available

- To use SQLite temporarily:
  1. Remove `DATABASE_URL` from `.env.local`
  2. Restart services
  3. Services fall back to `.tmp/data.db`

### Production Deployment

- Same code works for Railway deployment
- Railway automatically sets `DATABASE_URL`
- No code changes needed
- Connection pooling adjusted for production load

## Troubleshooting

### PostgreSQL Not Responding

```bash
# Check if running
tasklist | find /i "postgres"

# Restart service (Windows)
net stop PostgreSQL*
net start PostgreSQL*
```

### "Database does not exist" Error

```bash
# Create it
createdb -U postgres glad_labs_dev

# Verify
psql -U postgres -l | grep glad_labs_dev
```

### Strapi Still Using SQLite

1. Check `.env.local` has correct `DATABASE_URL`
2. Verify Strapi `.env` in `cms/strapi-main/`
3. Clear Strapi cache: `rm -rf cms/strapi-main/.strapi`
4. Restart Strapi: `npm run develop`

### Connection Refused on Port 5432

1. Ensure PostgreSQL is running
2. Check if port 5432 is available: `netstat -an | find "5432"`
3. Change port in `.env.local` if needed

## Next Steps

✅ PostgreSQL configured  
✅ Database created  
✅ Shared tables ready  
✅ Environment variables set

**👉 Run `npm run dev` to start all services**

## Files Created/Modified

| File                                 | Action   | Purpose                  |
| ------------------------------------ | -------- | ------------------------ |
| `.env.local`                         | Modified | Root PostgreSQL config   |
| `cms/strapi-main/.env`               | Modified | Strapi PostgreSQL config |
| `cms/strapi-main/config/database.js` | Modified | Enhanced DB detection    |
| `docs/LOCAL_POSTGRESQL_SETUP.md`     | Created  | Detailed setup guide     |
| `POSTGRESQL_SETUP_COMPLETE.md`       | Created  | Completion summary       |
| `setup-postgres.ps1`                 | Created  | Windows setup script     |
| `setup-postgres.sh`                  | Created  | Unix setup script        |

## References

- 📖 [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- 📖 [SQLAlchemy + PostgreSQL](https://docs.sqlalchemy.org/en/20/dialects/postgresql/)
- 📖 [Strapi Database Config](https://docs.strapi.io/dev-docs/configurations/database)
- 📖 [Railway PostgreSQL](https://docs.railway.app/deploy/postgresql)

---

**Status: ✅ SETUP COMPLETE AND VERIFIED**

Your local development environment now uses the same PostgreSQL database as your Railway production setup!
