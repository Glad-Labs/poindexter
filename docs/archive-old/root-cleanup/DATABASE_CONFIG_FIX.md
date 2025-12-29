# 🔧 Database Configuration Fix - Glad Labs

**Last Updated:** October 29, 2025  
**Problem:** Co-Founder Agent fails with "attempted relative import with no known parent package" when DATABASE_URL not configured  
**Root Cause:** Missing `.env` file and improper SQLite path handling  
**Solution:** Created `.env` file with proper database configuration

---

## ✅ What Was Fixed

### 1. **Created `.env` File**

- **Location:** `c:\Users\mattm\glad-labs-website\.env`
- **Purpose:** Local development environment configuration
- **Contents:** All essential settings for running Glad Labs locally

### 2. **Updated DatabaseService**

- **File:** `src/cofounder_agent/services/database_service.py`
- **Changes:**
  - Now reads `DATABASE_FILENAME` from `.env` (defaults to `.tmp/data.db`)
  - Creates parent directories if they don't exist
  - Converts paths to absolute paths for Windows compatibility
  - Falls back to SQLite when `DATABASE_URL` not set

### 3. **Created `.tmp` Directory**

- **Location:** `c:\Users\mattm\glad-labs-website\.tmp`
- **Purpose:** Store SQLite database file during development

---

## 📝 Environment Configuration Overview

### `.env` File Settings

Your `.env` file now includes:

**Database Configuration:**

```bash
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db
```

**AI Model Configuration (choose one):**

```bash
# Option 1: Free Local AI (Recommended for development)
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral

# Option 2: OpenAI
# OPENAI_API_KEY=sk-your-key-here

# Option 3: Anthropic Claude
# ANTHROPIC_API_KEY=sk-ant-your-key-here

# Option 4: Google Gemini
# GOOGLE_API_KEY=your-key-here
```

**Service URLs:**

```bash
STRAPI_URL=http://localhost:1337
API_BASE_URL=http://localhost:8000
```

---

## 🚀 How to Use

### Step 1: Verify `.env` File

The `.env` file has been created at the project root. Verify it exists:

```powershell
cd c:\Users\mattm\glad-labs-website
Get-Content .env | Select-Object -First 20
```

### Step 2: Set Up AI Models

#### Option A: Use Ollama (Free, Recommended)

```powershell
# 1. Install Ollama
winget install Ollama.Ollama

# 2. Pull a model
ollama pull mistral

# 3. Start Ollama service
ollama serve
```

The `.env` file already has:

```bash
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral
```

#### Option B: Use OpenAI

Edit `.env` and add:

```bash
OPENAI_API_KEY=sk-your-actual-key-here
```

#### Option C: Use Anthropic Claude

Edit `.env` and add:

```bash
ANTHROPIC_API_KEY=sk-ant-your-actual-key-here
```

### Step 3: Start Co-Founder Agent

```powershell
# Navigate to the co-founder agent directory
cd src/cofounder_agent

# Start the server
python -m uvicorn main:app --reload
```

Or use the VS Code task:

1. Press `Ctrl+Shift+P`
2. Type "Tasks: Run Task"
3. Select "Start Co-founder Agent"

### Step 4: Verify Everything Works

Check the API docs:

- Open browser: http://localhost:8000/docs
- You should see the Swagger UI with all endpoints

---

## 🗄️ Database Configuration Details

### How Database URL Resolution Works

The system uses this priority order:

```
1. DATABASE_URL environment variable (production - Railway)
   ↓
2. DATABASE_FILENAME from .env (development - SQLite)
   ↓
3. Default: .tmp/data.db
```

### Local Development (SQLite)

When running locally with no `DATABASE_URL`:

```
.env (DATABASE_FILENAME=.tmp/data.db)
    ↓
DatabaseService reads DATABASE_FILENAME
    ↓
Creates .tmp directory if needed
    ↓
Uses SQLite: sqlite+aiosqlite:////absolute/path/to/.tmp/data.db
    ↓
No external database server needed ✅
```

### Production (PostgreSQL)

When deploying to Railway:

```
Railway sets DATABASE_URL environment variable
    ↓
DatabaseService reads DATABASE_URL
    ↓
Converts to async: postgresql+asyncpg://user:pass@host:port/db
    ↓
Uses PostgreSQL with connection pooling ✅
```

---

## ⚠️ Important Notes

### `.env` File Management

**✅ DO:**

- Keep `.env` in `.gitignore` (never commit)
- Update `.env` when changing local configuration
- Use `.env.example` as a template for new developers

**❌ DON'T:**

- Commit `.env` to version control
- Store production secrets in `.env`
- Use `.env` for production deployments

### Database File Permissions

The SQLite database file (`.tmp/data.db`) needs write permissions:

```powershell
# If you get permission errors:
# 1. Delete the database file to start fresh
Remove-Item .tmp/data.db -ErrorAction SilentlyContinue

# 2. Restart the application
python -m uvicorn main:app --reload

# 3. The file will be created fresh
```

---

## 🔍 Troubleshooting

### Issue: "Cannot import module" error

**Cause:** Database environment variables not loaded  
**Solution:**

```powershell
# Verify .env file exists
Get-Content .env | Select-String DATABASE

# Expected output:
# DATABASE_CLIENT=sqlite
# DATABASE_FILENAME=.tmp/data.db
```

### Issue: "Permission denied" on database file

**Cause:** `.tmp/data.db` has wrong permissions  
**Solution:**

```powershell
# Remove and recreate
Remove-Item .tmp/data.db -ErrorAction SilentlyContinue
# Restart application - file will be created with correct permissions
```

### Issue: "Connection refused" to Ollama

**Cause:** Ollama service not running  
**Solution:**

```powershell
# Check if Ollama is running
Get-Process ollama

# If not running, start it
ollama serve

# Or install and run via Windows UI
winget install Ollama.Ollama
```

### Issue: "No module named 'main'" in FastAPI

**Cause:** Running from wrong directory  
**Solution:**

```powershell
# Must run from project root
cd c:\Users\mattm\glad-labs-website

# Then run from src/cofounder_agent
cd src/cofounder_agent
python -m uvicorn main:app --reload

# Or use full path
python -m uvicorn src.cofounder_agent.main:app --reload
```

---

## 📋 Quick Reference

### Start All Services (from project root)

**Option 1: Using npm**

```powershell
npm run dev
```

**Option 2: Using VS Code Tasks**

```
Ctrl+Shift+P → "Tasks: Run Task" → "Start All Services"
```

**Option 3: Manual (one terminal per service)**

Terminal 1 - Strapi CMS:

```powershell
cd cms/strapi-main
npm run develop
```

Terminal 2 - Public Site:

```powershell
cd web/public-site
npm run dev
```

Terminal 3 - Oversight Hub:

```powershell
cd web/oversight-hub
npm start
```

Terminal 4 - Co-Founder Agent:

```powershell
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

Terminal 5 - Ollama (if using):

```powershell
ollama serve
```

### Access the Services

| Service       | URL                             | Purpose            |
| ------------- | ------------------------------- | ------------------ |
| Public Site   | http://localhost:3000           | Public website     |
| Oversight Hub | http://localhost:3001           | Admin dashboard    |
| Strapi CMS    | http://localhost:1337/admin     | Content management |
| Backend API   | http://localhost:8000/docs      | API documentation  |
| Ollama        | http://localhost:11434/api/tags | AI model selection |

---

## 🔄 Next Steps

1. **Start Ollama** (if using free local AI):

   ```powershell
   ollama serve
   ```

2. **Verify `.env` configuration**:

   ```powershell
   Get-Content .env
   ```

3. **Start Co-Founder Agent**:

   ```powershell
   cd src/cofounder_agent
   python -m uvicorn main:app --reload
   ```

4. **Test the API**:
   - Open http://localhost:8000/docs
   - Try a test endpoint

5. **Check logs** for any connection errors

---

## 📚 Related Documentation

- **[Setup Guide](docs/01-SETUP_AND_OVERVIEW.md)** - Full local setup instructions
- **[Deployment Guide](docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Production setup
- **[Architecture](docs/02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[Database Configuration](docs/07-BRANCH_SPECIFIC_VARIABLES.md)** - Environment variables

---

**Status:** ✅ Configuration ready for local development

If you encounter any issues, check the [Troubleshooting](#-troubleshooting) section or the main documentation at `docs/00-README.md`.
