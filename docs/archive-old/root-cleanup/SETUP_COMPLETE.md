# ✅ Glad Labs Database Configuration - Quick Fix Summary

## Problem

Co-Founder Agent fails with "attempted relative import with no known parent package" because:

1. `.env` file didn't exist
2. No `DATABASE_URL` environment variable was set
3. SQLite fallback path wasn't properly configured for Windows

## Solution Applied

### 1. ✅ Created `.env` File

**File:** `c:\Users\mattm\glad-labs-website\.env`

Contains all essential local development settings:

- Database: SQLite at `.tmp/data.db`
- AI Models: Ollama (free), OpenAI, Claude, or Gemini options
- Service URLs: Strapi, FastAPI, Frontend URLs
- Feature flags and timeouts

### 2. ✅ Updated DatabaseService

**File:** `src/cofounder_agent/services/database_service.py`

**Changes:**

- Now reads `DATABASE_FILENAME` from `.env` (or defaults to `.tmp/data.db`)
- Creates parent directories if they don't exist
- Converts paths to absolute paths for Windows compatibility
- Properly falls back to SQLite when `DATABASE_URL` not set

**Before:**

```python
self.database_url = database_url or os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./test.db",  # ❌ Bad relative path
)
```

**After:**

```python
# Reads DATABASE_FILENAME from .env
database_filename = os.getenv("DATABASE_FILENAME", ".tmp/data.db")

# Creates directory if needed
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

# Converts to absolute path for Windows
database_filename = os.path.abspath(database_filename)
self.database_url = f"sqlite+aiosqlite:///{database_filename}"  # ✅ Proper path
```

### 3. ✅ Created `.tmp` Directory

**Location:** `c:\Users\mattm\glad-labs-website\.tmp\`

This is where SQLite database file (`data.db`) will be stored during development.

## How to Use

### Option 1: Free Local AI (Recommended)

```powershell
# 1. Install and start Ollama
winget install Ollama.Ollama
ollama serve  # In a terminal

# 2. In another terminal, start the Co-Founder Agent
cd src/cofounder_agent
python -m uvicorn main:app --reload

# 3. Test at http://localhost:8000/docs
```

### Option 2: Using OpenAI/Claude/Gemini

Edit `.env` and add your API key:

```bash
OPENAI_API_KEY=sk-your-key-here
# OR
ANTHROPIC_API_KEY=sk-ant-your-key-here
# OR
GOOGLE_API_KEY=your-key-here
```

Then start:

```powershell
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

## Verify It Works

### Check Logs

When starting the Co-Founder Agent, you should see:

```
✅ Database connection established
✅ PostgreSQL connection (or SQLite for local dev)
✅ Orchestrator initialized successfully
```

### Access API

Open your browser: **http://localhost:8000/docs**

You should see the Swagger UI with all endpoints available.

## File Changes Summary

| File                                               | Change                             | Status     |
| -------------------------------------------------- | ---------------------------------- | ---------- |
| `.env`                                             | Created new file with all settings | ✅ Created |
| `src/cofounder_agent/services/database_service.py` | Fixed SQLite path handling         | ✅ Updated |
| `.tmp/`                                            | Created directory for SQLite       | ✅ Created |
| `DATABASE_CONFIG_FIX.md`                           | Detailed documentation             | ✅ Created |

## Next Steps

1. **Start Ollama** (if using):

   ```powershell
   ollama serve
   ```

2. **Start Co-Founder Agent**:

   ```powershell
   cd src/cofounder_agent
   python -m uvicorn main:app --reload
   ```

3. **Test the API**:
   - Open http://localhost:8000/docs
   - Try creating a task or querying models

4. **Start other services** (optional):
   ```powershell
   npm run dev  # Starts Strapi, Public Site, Oversight Hub
   ```

## Environment Variable Priority

The system now uses this order:

1. `DATABASE_URL` (Production - Railway)
2. `DATABASE_FILENAME` from `.env` (Development - SQLite)
3. Default: `.tmp/data.db`

**Local development:** Just use `.env` - everything else is automatic! ✅

## Troubleshooting

**Error: "Permission denied" on `.tmp/data.db`**

```powershell
Remove-Item .tmp/data.db
# Restart app - file will be recreated
```

**Error: "Cannot find Ollama"**

```powershell
ollama serve
# In a separate terminal - keeps it running
```

**Error: "Connection refused" to backend**

- Make sure you're in the right directory: `cd src/cofounder_agent`
- Check that port 8000 is not in use: `netstat -ano | findstr :8000`

## Questions?

See the full documentation:

- **Setup Guide:** `docs/01-SETUP_AND_OVERVIEW.md`
- **Architecture:** `docs/02-ARCHITECTURE_AND_DESIGN.md`
- **Detailed Config Fix:** `DATABASE_CONFIG_FIX.md` (this folder)

---

**Status:** ✅ Ready to use!

Start coding with `npm run dev` or manually start services as needed.
