# 🐘 Local PostgreSQL Development Database Setup

**Last Updated:** October 30, 2025  
**Purpose:** Create a local PostgreSQL database for Glad Labs development  
**OS:** Windows (with PostgreSQL 17 or 18 installed)

---

## 📋 Quick Start (5 Minutes)

### Step 1: Open pgAdmin 4

1. Launch **pgAdmin 4** (search in Start menu or go to `http://localhost:5050`)
2. Log in with your pgAdmin credentials (email/password you created during installation)

### Step 2: Create Development Database

1. In the left sidebar, expand **Servers**
2. Right-click on **PostgreSQL 17** (or 18)
3. Select **Create** → **Database**
4. Fill in the form:
   - **Database name:** `glad_labs_dev`
   - **Owner:** `postgres`
   - Click **Save**

### Step 3: Verify Connection

In pgAdmin, expand your new database and verify it appears in the tree.

### Step 4: Update .env.local

Edit `c:\Users\mattm\glad-labs-website\.env.local` and change the database settings:

```bash
# OLD (SQLite)
# DATABASE_CLIENT=sqlite
# DATABASE_FILENAME=.tmp/data.db

# NEW (PostgreSQL)
DATABASE_CLIENT=postgres
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev
DATABASE_USER=postgres
DATABASE_PASSWORD=your_postgres_password
```

### Step 5: Initialize Database Schema

```bash
# From project root
cd c:\Users\mattm\glad-labs-website

# Run Strapi migrations (if using Strapi)
cd cms/strapi-v5-backend
npm run build
npm run develop

# OR for Co-Founder Agent migrations
cd src/cofounder_agent
python -m alembic upgrade head
```

---

## 🔧 Detailed Setup (If Quick Start Doesn't Work)

### Method A: Using pgAdmin 4 GUI (Recommended for Beginners)

#### Step 1: Open pgAdmin

```bash
# pgAdmin usually starts automatically, or:
# Windows Start Menu → Search "pgAdmin 4"
# Or: http://localhost:5050 (default port)
```

#### Step 2: Register PostgreSQL Server (If Not Already Done)

1. Click **Add New Server**
2. **Name tab:**
   - Name: `PostgreSQL 18` (or your version)
3. **Connection tab:**
   - Host name/address: `localhost`
   - Port: `5432`
   - Username: `postgres`
   - Password: (your postgres password from installation)
   - **Save password?** Check the box
4. Click **Save**

#### Step 3: Create Database

1. In left sidebar, expand **Servers** → **PostgreSQL 18** → **Databases**
2. Right-click **Databases** folder
3. Select **Create** → **Database**
4. **Properties tab:**
   - Name: `glad_labs_dev`
   - Owner: `postgres`
   - Tablespace: `pg_default`
5. Click **Save**

#### Step 4: Verify

1. Refresh the page (F5)
2. You should see `glad_labs_dev` in the database list

---

### Method B: Using PowerShell (Command Line)

#### Step 1: Connect to PostgreSQL

```powershell
# Open PowerShell and connect to PostgreSQL
psql -U postgres -h localhost

# You'll be prompted for password (your postgres password)
# After connecting, you should see: postgres=#
```

#### Step 2: Create Database

```sql
-- Create the development database
CREATE DATABASE glad_labs_dev OWNER postgres;

-- Verify it was created
\l
```

You should see `glad_labs_dev` in the list.

#### Step 3: Connect to New Database

```sql
-- Switch to your new database
\c glad_labs_dev

-- Verify (should show: You are now connected to database "glad_labs_dev" as user "postgres")
\q
```

---

### Method C: One-Command Setup (Fastest)

If you want to create it all at once via psql:

```powershell
# Run this single command from PowerShell
psql -U postgres -h localhost -c "CREATE DATABASE glad_labs_dev OWNER postgres;"

# Verify
psql -U postgres -h localhost -l | Select-String glad_labs_dev
```

---

## 🗂️ Project-Specific Setup

### For Strapi (CMS)

Strapi will auto-create tables when it starts. Just point it to your PostgreSQL database:

**File:** `cms/strapi-v5-backend/.env`

```bash
DATABASE_CLIENT=postgres
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev
DATABASE_USERNAME=postgres
DATABASE_PASSWORD=your_postgres_password
DATABASE_SSL=false
DATABASE_SCHEMA=public
```

Then start Strapi:

```bash
cd cms/strapi-v5-backend
npm run develop
```

Strapi will automatically create its tables on startup.

### For Co-Founder Agent (FastAPI + SQLAlchemy)

**File:** `src/cofounder_agent/.env`

```bash
DATABASE_URL=postgresql://postgres:your_postgres_password@localhost:5432/glad_labs_dev
```

Then initialize the database:

```bash
cd src/cofounder_agent

# Run migrations
python -m alembic upgrade head

# Or if using SQLAlchemy direct:
python -c "from database import Base, engine; Base.metadata.create_all(bind=engine)"
```

---

## 🧪 Testing Your Connection

### Test 1: Direct PostgreSQL Connection

```powershell
# Test connection
psql -U postgres -h localhost -d glad_labs_dev -c "SELECT version();"

# Expected output:
# PostgreSQL 18.0 (Debian 18.0-1.pgdg13+1) on x86_64-pc-linux-gnu, ...
```

### Test 2: From Python

```python
import psycopg2

try:
    conn = psycopg2.connect(
        host="localhost",
        database="glad_labs_dev",
        user="postgres",
        password="your_postgres_password"
    )
    cur = conn.cursor()
    cur.execute("SELECT version();")
    print(cur.fetchone())
    conn.close()
    print("✅ Connection successful!")
except Exception as e:
    print(f"❌ Connection failed: {e}")
```

### Test 3: From pgAdmin

1. Open pgAdmin
2. Click on your database `glad_labs_dev`
3. Click **Tools** → **Query Tool**
4. Run: `SELECT now();`
5. Should return current timestamp

---

## 🔄 Switching Between SQLite and PostgreSQL

### Keep Both Databases in Sync

Create both environment files:

**`.env.sqlite`** (For quick testing):

```bash
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db
```

**`.env.postgres`** (For development):

```bash
DATABASE_CLIENT=postgres
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev
DATABASE_USER=postgres
DATABASE_PASSWORD=your_postgres_password
```

Then switch by copying:

```powershell
# Use PostgreSQL
Copy-Item .env.postgres .env.local -Force

# Or use SQLite
Copy-Item .env.sqlite .env.local -Force
```

---

## 🛠️ Troubleshooting

### Problem: "psql: error: could not translate host name 'localhost' to address"

**Solution:** PostgreSQL service isn't running

```powershell
# Check service status
Get-Service PostgreSQL*

# Start the service
Start-Service PostgreSQL-x64-18  # Or your version

# Or restart it
Restart-Service PostgreSQL-x64-18
```

### Problem: "FATAL: password authentication failed for user 'postgres'"

**Solution:** Wrong password

```powershell
# Reset postgres password via pgAdmin:
# 1. Right-click "postgres" user in pgAdmin
# 2. Select "Properties"
# 3. Set password in "Password" field
# 4. Click "Save"
```

### Problem: "database 'glad_labs_dev' does not exist"

**Solution:** Database wasn't created

```powershell
# Verify database exists
psql -U postgres -h localhost -l | Select-String glad_labs_dev

# If not found, create it
psql -U postgres -h localhost -c "CREATE DATABASE glad_labs_dev OWNER postgres;"
```

### Problem: "role 'postgres' does not exist"

**Solution:** PostgreSQL installation incomplete

```powershell
# Reinstall PostgreSQL and make sure "postgres" user is created during installation
# Or create user manually:
psql -U postgres -h localhost -c "CREATE USER postgres WITH SUPERUSER PASSWORD 'your_password';"
```

### Problem: pgAdmin won't start on http://localhost:5050

**Solution:** pgAdmin service not running or different port

```powershell
# Find pgAdmin process
Get-Process pgAdmin4

# If not running, start it manually from:
# Start Menu → pgAdmin 4 → pgAdmin 4

# Or check what port it's using:
netstat -ano | Select-String ":5050"
```

---

## 📊 Verify Full Setup

Run this checklist to verify everything works:

```powershell
# ✅ 1. PostgreSQL service running
Get-Service PostgreSQL* | Where-Object Status -eq Running

# ✅ 2. psql command available
psql --version

# ✅ 3. Can connect as postgres user
psql -U postgres -h localhost -c "\du"

# ✅ 4. Database exists
psql -U postgres -h localhost -l | Select-String glad_labs_dev

# ✅ 5. Can connect to dev database
psql -U postgres -h localhost -d glad_labs_dev -c "SELECT count(*) FROM information_schema.tables;"
```

---

## 🚀 Next Steps

Once your database is set up:

1. **Start Strapi with PostgreSQL:**

   ```bash
   cd cms/strapi-v5-backend
   npm run develop
   ```

2. **Start Co-Founder Agent:**

   ```bash
   cd src/cofounder_agent
   python -m uvicorn main:app --reload
   ```

3. **Start Public Site:**

   ```bash
   cd web/public-site
   npm run dev
   ```

4. **Verify all services:**
   - Strapi: http://localhost:1337/admin
   - Co-Founder Agent: http://localhost:8000/docs
   - Public Site: http://localhost:3000

---

## 📚 Reference

| Task                | Command                                                                                    |
| ------------------- | ------------------------------------------------------------------------------------------ |
| List databases      | `psql -U postgres -l`                                                                      |
| Connect to database | `psql -U postgres -d glad_labs_dev`                                                        |
| Create database     | `psql -U postgres -c "CREATE DATABASE glad_labs_dev;"`                                     |
| Drop database       | `psql -U postgres -c "DROP DATABASE glad_labs_dev;"`                                       |
| Reset database      | `psql -U postgres -d glad_labs_dev -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"` |
| View tables         | `\d` (in psql)                                                                             |
| Exit psql           | `\q`                                                                                       |

---

**Ready to go!** Your local PostgreSQL development database is now set up. 🎉
