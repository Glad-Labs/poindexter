# Local Development Setup with PostgreSQL

**Last Updated:** October 31, 2025

## Overview

This guide sets up your local development environment to use PostgreSQL (glad_labs_dev) for both Strapi CMS and the Co-Founder Agent, matching your Railway production setup.

## Prerequisites

### 1. PostgreSQL Installation

**Windows:**

```powershell
# Using Chocolatey
choco install postgresql

# Or download from: https://www.postgresql.org/download/windows/
```

**macOS:**

```bash
brew install postgresql@15
brew services start postgresql@15
```

**Linux (Ubuntu/Debian):**

```bash
sudo apt-get update
sudo apt-get install postgresql postgresql-contrib
sudo systemctl start postgresql
```

### 2. Verify PostgreSQL is Running

```bash
# Check version
psql --version

# Connect to default postgres database
psql -U postgres
```

## Setup Steps

### Step 1: Create the Development Database

```sql
-- Connect to PostgreSQL
psql -U postgres

-- Create database
CREATE DATABASE glad_labs_dev;

-- Create user if doesn't exist (optional)
CREATE USER glad_labs_dev WITH ENCRYPTED PASSWORD 'postgres';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE glad_labs_dev TO postgres;

-- Verify
\l
\q
```

Or using a single command:

```bash
createdb -U postgres glad_labs_dev
```

### Step 2: Configure Environment Variables

The configuration files are already set up:

**Root `.env.local`:**

```bash
DATABASE_CLIENT=postgres
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

**Strapi `cms/strapi-main/.env`:**

```bash
DATABASE_CLIENT=postgres
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

### Step 3: Install Dependencies

```bash
# Install all workspace dependencies
npm run setup:all

# Or just Python dependencies
npm run setup:python
```

### Step 4: Start Services

**Option A: Start all services**

```bash
npm run dev
```

This starts:

- Strapi CMS (port 1337)
- Oversight Hub (port 3001)
- Public Site (port 3000)
- Co-Founder Agent (port 8000)

**Option B: Start individual services**

```bash
# Terminal 1: Strapi CMS
cd cms/strapi-main
npm run develop

# Terminal 2: Co-Founder Agent
cd src/cofounder_agent
python -m uvicorn main:app --reload --port 8000

# Terminal 3: Frontend
cd web
npm run dev
```

## Verification

### Check PostgreSQL Connection

```bash
# From root directory
python -c "from src.cofounder_agent.database import get_database_url; print(get_database_url())"
```

Expected output:

```
postgresql://postgres@localhost:5432/glad_labs_dev
```

### Check Strapi Connection

1. Open http://localhost:1337/admin
2. Check for any database connection errors
3. Strapi should auto-migrate schema

### Check Co-Founder Agent

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "healthy",
  "database_url_configured": true
}
```

### Verify Same Database

```sql
-- Connect to glad_labs_dev
psql -U postgres glad_labs_dev

-- Check tables
\dt

-- Should see both Strapi and Co-Founder Agent tables
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────┐
│           Local Development Machine             │
├─────────────────────────────────────────────────┤
│                                                 │
│  PostgreSQL (glad_labs_dev)                    │
│  └─ Shared Database (port 5432)                │
│                                                 │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ Strapi CMS (port 1337)                   │  │
│  │ - User: postgres                         │  │
│  │ - Uses glad_labs_dev                    │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ Co-Founder Agent (port 8000)            │  │
│  │ - User: postgres                         │  │
│  │ - Uses glad_labs_dev                    │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ Oversight Hub (port 3001)                │  │
│  │ Public Site (port 3000)                  │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
└─────────────────────────────────────────────────┘
```

## Troubleshooting

### PostgreSQL Connection Refused

```bash
# Check if PostgreSQL is running
sudo systemctl status postgresql  # Linux
brew services list | grep postgres  # macOS

# Start PostgreSQL if stopped
sudo systemctl start postgresql  # Linux
brew services start postgresql@15  # macOS
```

### Database "glad_labs_dev" Does Not Exist

```bash
# Create database
createdb -U postgres glad_labs_dev

# Verify
psql -U postgres -l | grep glad_labs_dev
```

### Permission Denied for User "postgres"

```bash
# Reset postgres user password
sudo -u postgres psql
ALTER USER postgres WITH PASSWORD 'postgres';
\q
```

### Strapi Shows Migration Errors

```bash
# Clear Strapi cache and restart
cd cms/strapi-main
rm -rf .strapi
npm run develop
```

### Co-Founder Agent Can't Connect

```bash
# Verify DATABASE_URL
echo $DATABASE_URL

# Test connection directly
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev

# Check firewall (Windows)
# Allow PostgreSQL port 5432 in firewall
```

## Railway Production Comparison

Both local and production use the same database configuration:

| Setting        | Local Dev     | Railway Prod        |
| -------------- | ------------- | ------------------- |
| **Host**       | localhost     | db.\*.railway.app   |
| **Port**       | 5432          | 5432                |
| **Database**   | glad_labs_dev | glad_labs_db        |
| **User**       | postgres      | root/user           |
| **Connection** | Direct        | SSH tunnel / Direct |

To deploy to Railway:

1. Set Railway DATABASE_URL environment variable
2. No code changes needed - same database.py handles both

## Next Steps

- ✅ Local PostgreSQL configured
- ✅ Strapi and Co-Founder Agent share database
- ✅ Development environment ready
- 📝 Run tests: `npm test`
- 🚀 Deploy to Railway when ready

## References

- [PostgreSQL Official Docs](https://www.postgresql.org/docs/)
- [Railway PostgreSQL](https://docs.railway.app/deploy/postgresql)
- [SQLAlchemy PostgreSQL](https://docs.sqlalchemy.org/en/20/dialects/postgresql/)
- [Strapi Database Config](https://docs.strapi.io/dev-docs/configurations/database)
