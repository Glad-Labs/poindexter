# 🗄️ PostgreSQL Setup & Connection Guide

**Status:** Ready for Setup  
**Last Updated:** November 14, 2025  
**Database:** glad_labs_dev  
**User:** postgres  
**Port:** 5432  
**Connection String:** `postgresql://postgres:postgres@localhost:5432/glad_labs_dev`

---

## 🚀 Quick Start (5 Minutes)

### Step 1: Verify PostgreSQL is Running

```bash
# Check if PostgreSQL service is running
psql --version
# Should output: psql (PostgreSQL) 15.x (or higher)

# If not installed, install PostgreSQL:
# Windows: Download from https://www.postgresql.org/download/windows/
# macOS: brew install postgresql@15
# Linux: apt-get install postgresql
```

### Step 2: Connect to PostgreSQL

```bash
# Connect as postgres user
psql -U postgres -h localhost -p 5432

# If prompted for password, enter: postgres
# (or whatever password was set during installation)

# You should see: postgres=#
```

### Step 3: Create Database

```sql
-- Create database for development
CREATE DATABASE glad_labs_dev;

-- Verify it was created
\l
-- You should see glad_labs_dev in the list

-- Exit psql
\q
```

### Step 4: Verify Connection String

```bash
# Test connection using the full connection string
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev

# Should connect successfully
# If you see "postgres=>#" you're connected!
```

---

## 📋 Database Schema

### Users Table

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE NOT NULL,
  username VARCHAR(255) UNIQUE NOT NULL,
  avatar_url VARCHAR(500),
  display_name VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index on email for faster lookups
CREATE INDEX idx_users_email ON users(email);
```

### OAuth Accounts Table

```sql
CREATE TABLE oauth_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider VARCHAR(50) NOT NULL, -- "github", "google", "facebook", etc.
  provider_user_id VARCHAR(255) NOT NULL,
  provider_data JSONB, -- Store provider-specific data
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_used TIMESTAMP,
  UNIQUE(provider, provider_user_id)
);

-- Create indexes
CREATE INDEX idx_oauth_accounts_user_id ON oauth_accounts(user_id);
CREATE INDEX idx_oauth_accounts_provider ON oauth_accounts(provider);
```

### Tasks Table (Optional - for task management)

```sql
CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  status VARCHAR(50) DEFAULT 'pending', -- pending, in_progress, completed, failed
  type VARCHAR(100),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP
);

CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_tasks_status ON tasks(status);
```

---

## 🔐 PostgreSQL Configuration

### Step 1: Secure PostgreSQL User

By default, postgres user has no password. Set a strong password:

```bash
# Connect as postgres
sudo -u postgres psql

# Inside psql, set password
\password postgres
# Enter new password (recommended: change from "postgres" to something stronger)

# Exit
\q
```

**For Development:** Keep it as `postgres` for simplicity  
**For Production:** Use a strong password and store in secret manager

### Step 2: Configure Connection Settings

**File:** `/etc/postgresql/15/main/postgresql.conf` (Linux/Mac)

```ini
# Enable network connections (if needed)
listen_addresses = 'localhost'

# Connection settings
max_connections = 100
shared_buffers = 256MB
```

**Windows:** Connection settings are in `%APPDATA%\PostgreSQL\15\data\postgresql.conf`

### Step 3: Configure Client Authentication

**File:** `/etc/postgresql/15/main/pg_hba.conf` (Linux/Mac)

```ini
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             postgres                                trust
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
```

This allows:

- Local connections without password
- Network connections with password

---

## 💻 Using pgsql_connect Tool

### Connect to glad_labs_dev Database

```bash
# Using pgsql_connect to connect to the database
# Connection ID format: pgsql/servername/databasename
# For local PostgreSQL: pgsql/localhost/glad_labs_dev

# Example usage in AI Toolkit:
# 1. Tool: pgsql_connect
# 2. serverName: "localhost" (or "PostgreSQL" if registered)
# 3. database: "glad_labs_dev"
# Result: connection_id = "pgsql/localhost/glad_labs_dev"
```

### Example Commands

```bash
# After connecting, you can:
# 1. Query data
pgsql_query(
  connectionId="pgsql/localhost/glad_labs_dev",
  query="SELECT * FROM users;",
  queryName="list_all_users",
  queryDescription="Get all registered users"
)

# 2. Insert data
pgsql_modify(
  connectionId="pgsql/localhost/glad_labs_dev",
  statement="INSERT INTO users (email, username) VALUES ('user@example.com', 'username');",
  statementName="create_user",
  statementDescription="Create new user"
)

# 3. Create tables
pgsql_modify(
  connectionId="pgsql/localhost/glad_labs_dev",
  statement="CREATE TABLE IF NOT EXISTS users (...);",
  statementName="init_schema",
  statementDescription="Initialize database schema"
)
```

---

## 🧪 Verify Database Setup

### Test 1: Connection

```bash
# From terminal
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "SELECT NOW();"
# Should return: current timestamp

# If successful, you see:
#              now
# 2025-11-14 12:34:56.123456
```

### Test 2: Tables Exist

```bash
# Check tables
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "\dt"

# Should show:
# Schema |      Name      | Type  |  Owner
# -------+----------------+-------+----------
#  public | oauth_accounts | table | postgres
#  public | users          | table | postgres
```

### Test 3: Insert Test Data

```bash
# Insert test user
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "
INSERT INTO users (email, username, display_name)
VALUES ('test@example.com', 'testuser', 'Test User');
"

# Verify insertion
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "SELECT * FROM users;"
```

### Test 4: OAuth Account Linking

```bash
# Get user ID from above query
USER_ID="<uuid_from_above>"

# Link GitHub OAuth account
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "
INSERT INTO oauth_accounts (user_id, provider, provider_user_id, provider_data)
VALUES ('$USER_ID', 'github', '12345', '{\"login\": \"testuser\", \"id\": 12345}');
"

# Verify OAuth account
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "SELECT * FROM oauth_accounts;"
```

---

## 🔧 Environment Variable Configuration

### .env.local (Local Development)

```bash
# PostgreSQL Connection
DATABASE_CLIENT=postgres
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres

# Verify with
python -c "
import os
from dotenv import load_dotenv
load_dotenv('.env.local')
print(f'DATABASE_URL: {os.getenv(\"DATABASE_URL\")}')
"
```

### .env.staging (Staging Environment)

```bash
# PostgreSQL Connection (Railway or similar)
DATABASE_CLIENT=postgres
DATABASE_URL=postgresql://user:password@staging-db.railway.app:5432/glad_labs_staging
DATABASE_HOST=staging-db.railway.app
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_staging
DATABASE_USER=<secret>
DATABASE_PASSWORD=<secret>
```

### .env.production (Production Environment)

```bash
# PostgreSQL Connection (Production database)
DATABASE_CLIENT=postgres
DATABASE_URL=postgresql://user:password@prod-db.railway.app:5432/glad_labs_production
DATABASE_HOST=prod-db.railway.app
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_production
DATABASE_USER=<secret>
DATABASE_PASSWORD=<secret>
```

---

## 🚀 Backend Integration

### Automatic Schema Initialization

The backend FastAPI application automatically creates tables on startup:

```python
# src/cofounder_agent/main.py

from services.database_service import DatabaseService

# On startup, the backend will:
# 1. Check if tables exist
# 2. Create tables if missing
# 3. Initialize indexes
# 4. Print status to logs

# You should see:
# ✅ Database tables created successfully
# ✅ OAuth routes registered
# ✅ Server running on http://localhost:8000
```

### Using DatabaseService

```python
# In your code, use DatabaseService to interact with database

from services.database_service import DatabaseService

db_service = DatabaseService()

# Get or create OAuth user
user = await db_service.get_or_create_oauth_user(
    provider='github',
    provider_user_id='12345',
    provider_data={'login': 'testuser', 'avatar_url': '...'}
)

# Get all OAuth accounts for a user
accounts = await db_service.get_oauth_accounts(user_id=user.id)

# Unlink OAuth account
await db_service.unlink_oauth_account(user_id=user.id, provider='github')
```

---

## 📊 Backup & Recovery

### Create Backup

```bash
# Backup entire database
pg_dump postgresql://postgres:postgres@localhost:5432/glad_labs_dev > backup.sql

# Backup with compression
pg_dump -Fc postgresql://postgres:postgres@localhost:5432/glad_labs_dev > backup.dump

# Backup table-level
pg_dump -t users postgresql://postgres:postgres@localhost:5432/glad_labs_dev > users_backup.sql
```

### Restore from Backup

```bash
# Restore entire database
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev < backup.sql

# Restore from compressed backup
pg_restore -d glad_labs_dev backup.dump

# Restore single table
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev < users_backup.sql
```

---

## 🔍 Monitoring & Maintenance

### Check Database Size

```bash
# Size of entire database
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "
SELECT pg_size_pretty(pg_database_size('glad_labs_dev'));
"

# Size of each table
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
"
```

### Check Active Connections

```bash
# Number of active connections
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "
SELECT datname, usename, count(*) as connections
FROM pg_stat_activity
GROUP BY datname, usename;
"
```

### Vacuum & Analyze (Maintenance)

```bash
# Vacuum (remove dead rows)
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "VACUUM;"

# Analyze (update statistics)
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "ANALYZE;"

# Both combined
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "VACUUM ANALYZE;"
```

---

## ❌ Troubleshooting

### "Connection refused"

**Problem:** Can't connect to PostgreSQL

**Solution:**

1. Check PostgreSQL is running: `systemctl status postgresql` (Linux) or Services (Windows)
2. Check port is correct: `netstat -an | grep 5432` (should see LISTEN)
3. Check connection string: `postgresql://user:password@host:port/database`
4. Verify database exists: `psql -l`

### "password authentication failed"

**Problem:** Wrong password or user

**Solution:**

1. Verify username: should be `postgres`
2. Verify password: should match what you set
3. Reset password: `ALTER USER postgres WITH PASSWORD 'newpassword';`
4. Check .env.local has correct credentials

### "database 'glad_labs_dev' does not exist"

**Problem:** Database hasn't been created

**Solution:**

1. Create database: `createdb -U postgres glad_labs_dev`
2. Verify: `psql -l | grep glad_labs_dev`
3. Connect backend to initialize tables

### "relation 'users' does not exist"

**Problem:** Tables haven't been created

**Solution:**

1. Start backend application (will auto-create tables)
2. Or manually create tables using SQL scripts above
3. Verify with: `psql <connection_string> -c "\dt"`

### "too many connections"

**Problem:** Connection pool exhausted

**Solution:**

1. Increase max_connections in postgresql.conf
2. Check for connection leaks in application
3. Close unused connections: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'glad_labs_dev';`

---

## ✅ Verification Checklist

Before proceeding with integration:

- [ ] PostgreSQL installed and running
- [ ] Database `glad_labs_dev` created
- [ ] Can connect: `psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev`
- [ ] .env.local has DATABASE_URL configured
- [ ] Backend can start without connection errors
- [ ] Tables created (users, oauth_accounts)
- [ ] Can insert test data
- [ ] Can query data back
- [ ] Backups working

---

## 🔗 Connection Credentials Summary

**Local Development:**

```
Host: localhost
Port: 5432
Database: glad_labs_dev
User: postgres
Password: postgres
Connection String: postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

**Staging:**

```
Host: staging-db.railway.app (or similar)
Port: 5432
Database: glad_labs_staging
User: <from Railway dashboard>
Password: <from Railway dashboard>
Connection String: (stored in GitHub Secrets)
```

**Production:**

```
Host: prod-db.railway.app (or similar)
Port: 5432
Database: glad_labs_production
User: <from Railway dashboard>
Password: <from Railway dashboard>
Connection String: (stored in GitHub Secrets)
```

---

## 📚 Next Steps

1. ✅ Install PostgreSQL (if not already)
2. ✅ Create database glad_labs_dev
3. ✅ Verify connection works
4. ✅ Configure .env.local
5. ✅ Start backend (will auto-initialize tables)
6. ✅ Test with OAuth flow
7. 🔄 Setup staging database (on Railway)
8. 🔄 Setup production database (on Railway)

---

**Status: Ready for Database Integration** ✅
