# 🪟 Windows Deployment Guide - Tier 1 Production

**For Windows users:** Use PowerShell and Batch scripts instead of Bash scripts.

---

## 🚀 Quick Start (Windows PowerShell)

### Option 1: Use PowerShell Deployment Script (Recommended)

```powershell
# From project root
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Run deployment
.\scripts\deploy-tier1.ps1

# Or with dry-run first
.\scripts\deploy-tier1.ps1 -DryRun

# Skip confirmation
.\scripts\deploy-tier1.ps1 -SkipConfirmation
```

### Option 2: Use Node.js Setup (Cross-Platform)

```bash
npm run setup:tier1     # Interactive setup wizard
```

---

## 📊 Available Windows Scripts

### Deploy to Production

```powershell
# Interactive deployment (recommended)
.\scripts\deploy-tier1.ps1

# Dry-run (preview changes without executing)
.\scripts\deploy-tier1.ps1 -DryRun

# Non-interactive (for automation)
.\scripts\deploy-tier1.ps1 -SkipConfirmation
```

**What it does:**

1. ✅ Checks prerequisites (Railway CLI, Vercel CLI, Git)
2. ✅ Initializes Railway project
3. ✅ Creates PostgreSQL database (free tier)
4. ✅ Deploys Strapi CMS to Railway
5. ✅ Deploys Co-Founder Agent to Railway
6. ✅ Deploys frontend to Vercel

**Estimated time:** 15-20 minutes

---

### Monitor Resources

```powershell
# One-time status check
.\scripts\monitor-tier1-resources.ps1

# Continuous watch mode (updates every 30 seconds, Ctrl+C to stop)
.\scripts\monitor-tier1-resources.ps1 -Watch

# Custom update interval
.\scripts\monitor-tier1-resources.ps1 -Watch -Interval 5
```

**Shows:**

- Service status (CPU, memory, storage)
- Resource limit alerts
- Scaling recommendations
- Cost breakdown
- Next steps

---

### Backup Database

```batch
# One-time backup
.\scripts\backup-tier1-db.bat

# For automated backups, schedule in Task Scheduler
# See "Schedule Automated Backups" section below
```

**What it does:**

1. ✅ Creates backup directory if needed
2. ✅ Connects to PostgreSQL via DATABASE_URL
3. ✅ Exports database to `.sql` file
4. ✅ Logs backup operations
5. ✅ Optional: Uploads to S3 (if AWS CLI available)
6. ✅ Cleans up old backups (>7 days)

---

## ⚙️ Prerequisites

### Required

```powershell
# 1. Check Node.js
node --version    # Should be v18+

# 2. Check Python (for backend)
python --version  # Should be v3.12+

# 3. Install Railway CLI
npm install -g @railway/cli

# 4. Install Vercel CLI
npm install -g vercel

# 5. Login to Railway
railway login

# 6. Login to Vercel
vercel --version
```

### Optional

```powershell
# For backups via pg_dump (instead of Railway CLI)
# Download: https://www.postgresql.org/download/windows/

# For S3 backups
npm install -g aws-cli
aws configure
```

---

## 🔧 Environment Setup

### 1. Create .env.tier1.production

```bash
# Copy template
copy .env.example .env.tier1.production

# Edit with your values
notepad .env.tier1.production
```

**Required variables:**

```bash
# Railway
RAILWAY_TOKEN=your_railway_token_here

# Vercel
VERCEL_TOKEN=your_vercel_token_here
VERCEL_ORG_ID=your_org_id
VERCEL_PROJECT_ID=your_project_id

# Strapi
DATABASE_URL=postgresql://user:pass@host:5432/dbname
ADMIN_JWT_SECRET=random_secret_string
JWT_SECRET=another_random_string

# Backend
OPENAI_API_KEY=sk-... (or other AI provider)
NODE_ENV=production
```

### 2. Set DATABASE_URL Environment Variable

```powershell
# Set for current session
$env:DATABASE_URL = "postgresql://user:pass@host:5432/dbname"

# Verify
echo $env:DATABASE_URL

# Make permanent (optional)
[Environment]::SetEnvironmentVariable("DATABASE_URL", "postgresql://...", "User")
```

---

## 📅 Schedule Automated Backups

### Using Windows Task Scheduler

#### Step 1: Open Task Scheduler

```text
Windows Key + R → taskschd.msc → Enter
```

#### Step 2: Create New Task

- Right-click "Task Scheduler Library" → "Create Task..."
- Name: `GLAD Labs Tier 1 Backup`
- Description: `Daily backup of Tier 1 production database`

#### Step 3: Set Trigger

- Click "Triggers" tab → "New..."
- Begin task: `On a schedule`
- Daily at: `2:00 AM`
- Repeat: `Every 1 day`

#### Step 4: Set Action

- Click "Actions" tab → "New..."
- Action: `Start a program`
- Program/script: `C:\Windows\System32\cmd.exe`
- Arguments: `/c C:\path\to\scripts\backup-tier1-db.bat`

#### Step 5: Set Conditions

- Click "Conditions" tab
- Power: Check "Wake the computer to run this task"
- Network: Check "Start only if connected to network"

#### Step 6: Set History

- Click "History" tab → Check "Enable History"

#### Step 7: Click OK to save

---

## 🔍 Troubleshooting Windows Scripts

### PowerShell Execution Policy Error

```
File ... cannot be loaded because running scripts is disabled
```

**Fix:**

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Railway/Vercel CLI Not Found

```
Error: railway is not recognized as an internal or external command
```

**Fix:**

```powershell
npm install -g @railway/cli
npm install -g vercel

# Verify installation
railway --version
vercel --version
```

### DATABASE_URL Not Set

```
ERROR: DATABASE_URL environment variable not set
```

**Fix:**

```powershell
# Get from Railway dashboard
railway variables list

# Set locally
$env:DATABASE_URL = "postgresql://..."

# Verify
echo $env:DATABASE_URL
```

### Backup File Not Created

```
Failed to backup: pg_dump not found
```

**Fix: Option A** - Install PostgreSQL client

```powershell
# Download: https://www.postgresql.org/download/windows/
# Choose "Command Line Tools Only" option
```

**Fix: Option B** - Use Railway CLI

```powershell
railway database backup
```

---

## 📊 Commands Quick Reference

| Command                                        | Purpose                            |
| ---------------------------------------------- | ---------------------------------- |
| `.\scripts\deploy-tier1.ps1`                   | Deploy all services                |
| `.\scripts\deploy-tier1.ps1 -DryRun`           | Preview deployment                 |
| `.\scripts\monitor-tier1-resources.ps1`        | Check resources                    |
| `.\scripts\monitor-tier1-resources.ps1 -Watch` | Continuous monitoring              |
| `.\scripts\backup-tier1-db.bat`                | Manual backup                      |
| `npm run setup:tier1`                          | Interactive setup (cross-platform) |

---

## 🚨 Common Issues & Solutions

### Deployment Stalls/Times Out

**Cause:** Network latency, large initial download

**Solution:**

```powershell
# Increase timeout
$timeout = 600  # 10 minutes
.\scripts\deploy-tier1.ps1 -SkipConfirmation
```

### Services Not Responding After Deployment

**Cause:** Cold start, services still initializing

**Solution:**

```powershell
# Wait 5 minutes, then check status
Start-Sleep -Seconds 300
.\scripts\monitor-tier1-resources.ps1
```

### Database Connection Failed

**Cause:** Wrong connection string or firewall rules

**Solution:**

```powershell
# Verify connection string
echo $env:DATABASE_URL

# Check Railway dashboard for correct URL
railway variables list

# Update locally
$env:DATABASE_URL = "postgresql://..."
```

---

## 🎯 Next Steps After Deployment

1. **Verify Services Online**

   ```powershell
   .\scripts\monitor-tier1-resources.ps1
   ```

2. **Schedule Daily Backups**
   - Follow "Schedule Automated Backups" section above

3. **Set Up Monitoring**

   ```powershell
   # Create scheduled task to run monitor every 5 minutes
   # (See Task Scheduler section for details)
   ```

4. **Test Backup Recovery**

   ```powershell
   # Monthly: Test recovery procedure
   # Document steps in runbook
   ```

5. **Monitor Costs**
   - Check Railway/Vercel dashboards weekly
   - Alert triggers at ~$8/month (close to Tier 1 limit)

---

## 🔗 Related Documentation

- **[TIER1_PRODUCTION_GUIDE.md](./TIER1_PRODUCTION_GUIDE.md)** - General Tier 1 setup
- **[TIER1_COST_ANALYSIS.md](./TIER1_COST_ANALYSIS.md)** - Cost breakdown
- **[TIER1_DEPLOYMENT.json](./TIER1_DEPLOYMENT.json)** - Configuration template

---

## 📞 Support

**Having issues?**

1. Check troubleshooting section above
2. Review script output for specific errors
3. Check Railway/Vercel dashboards for service status
4. Review log files in `backups/tier1/backup.log`

**Need help?**

- Railway Support: [docs.railway.app](https://docs.railway.app)
- Vercel Support: [vercel.com/support](https://vercel.com/support)
- GLAD Labs Docs: See `docs/` folder
