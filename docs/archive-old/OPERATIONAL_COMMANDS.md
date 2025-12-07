# 🎮 OPERATIONAL COMMANDS - Quick Reference

**Last Updated:** November 11, 2025  
**Purpose:** Quick command reference for running and testing Glad Labs system

---

## 🚀 START/STOP SERVICES

### Start All Services (PowerShell)

```powershell
# Terminal 1: FastAPI Backend
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python main.py

# Terminal 2: Strapi CMS
cd c:\Users\mattm\glad-labs-website\cms\strapi-main
npm run develop

# Terminal 3: Oversight Hub (React)
cd c:\Users\mattm\glad-labs-website\web\oversight-hub
npm start

# Terminal 4: Public Site (Next.js)
cd c:\Users\mattm\glad-labs-website\web\public-site
npm run dev
```

### Or Use npm Workspace Commands

```powershell
cd c:\Users\mattm\glad-labs-website
npm run dev              # Starts frontend services
npm run dev:cofounder    # Starts backend only
```

### Stop All Services

```powershell
Get-Process | Where-Object {$_.ProcessName -match "node|python"} | Stop-Process -Force
```

---

## ✅ HEALTH CHECKS

### Check Backend Health

```powershell
Invoke-WebRequest -Uri http://localhost:8000/api/health | ConvertTo-Json
```

### Check Strapi Status

```powershell
Invoke-WebRequest -Uri http://localhost:1337/admin -ErrorAction SilentlyContinue | Select-Object StatusCode
```

### Check Database Connection

```powershell
# Test PostgreSQL
$connString = "Server=localhost;Port=5432;User Id=postgres;Password=postgres;Database=strapi_dev"
# Or via Python:
python -c "import psycopg2; conn = psycopg2.connect('dbname=strapi_dev user=postgres password=postgres'); print('Connected')"
```

---

## 📋 TASK MANAGEMENT

### Create New Task

```powershell
$task = @{
    topic = "Your Topic Here"
    category = "general"
    primary_keyword = "main_keyword"
    target_audience = "developers"
} | ConvertTo-Json

Invoke-WebRequest -Uri http://localhost:8000/api/tasks `
    -Method POST `
    -Headers @{"Content-Type"="application/json"} `
    -Body $task
```

### Get All Tasks

```powershell
Invoke-WebRequest -Uri http://localhost:8000/api/tasks | Select-Object -ExpandProperty Content | ConvertFrom-Json
```

### Get Specific Task

```powershell
# Replace TASK_ID with actual task ID
$taskId = "2649980a-28b9-45e9-82c5-28b38f955d55"
Invoke-WebRequest -Uri "http://localhost:8000/api/tasks/$taskId" | Select-Object -ExpandProperty Content | ConvertFrom-Json
```

---

## 🧠 ORCHESTRATOR COMMANDS

### Test Intelligent Orchestrator Routes

```powershell
# Get orchestrator status
Invoke-WebRequest -Uri http://localhost:8000/api/orchestrator/status

# Execute via orchestrator
$request = @{
    request = "Generate a blog post about AI trends"
    context = "general"
} | ConvertTo-Json

Invoke-WebRequest -Uri http://localhost:8000/api/orchestrator/execute `
    -Method POST `
    -Headers @{"Content-Type"="application/json"} `
    -Body $request
```

---

## 🤖 MODEL TESTING

### Test Model Connectivity

```powershell
# Test all models
Invoke-WebRequest -Uri "http://localhost:8000/api/models/test-all"

# Test specific provider
Invoke-WebRequest -Uri "http://localhost:8000/api/models/test?provider=ollama"

# Check model status
Invoke-WebRequest -Uri "http://localhost:8000/api/models/status"
```

### List Available Models

```powershell
Invoke-WebRequest -Uri "http://localhost:8000/api/models" | Select-Object -ExpandProperty Content | ConvertFrom-Json
```

---

## 🧪 PIPELINE TESTING

### Complete Pipeline Test Flow

```powershell
# 1. Create task
$task = @{
    topic = "Pipeline Test - $(Get-Date -Format 'yyyyMMdd-HHmmss')"
    category = "testing"
    primary_keyword = "pipeline"
    target_audience = "developers"
} | ConvertTo-Json

$response = Invoke-WebRequest -Uri http://localhost:8000/api/tasks `
    -Method POST `
    -Headers @{"Content-Type"="application/json"} `
    -Body $task | Select-Object -ExpandProperty Content | ConvertFrom-Json

$taskId = $response.id

# 2. Monitor task
Write-Host "Task created: $taskId"
Write-Host "Monitoring execution..."
Start-Sleep -Seconds 5

# 3. Check result
$result = Invoke-WebRequest -Uri "http://localhost:8000/api/tasks/$taskId" | Select-Object -ExpandProperty Content | ConvertFrom-Json

# 4. Display results
$result | ConvertTo-Json -Depth 5 | Write-Host
```

---

## 📊 DATABASE QUERIES

### View Tasks via Database

```powershell
# Connect to PostgreSQL and query tasks
python -c "
import psycopg2
conn = psycopg2.connect('dbname=strapi_dev user=postgres password=postgres')
cur = conn.cursor()
cur.execute('SELECT id, task_name, status, created_at FROM tasks LIMIT 10')
for row in cur.fetchall():
    print(row)
conn.close()
"
```

### Check Strapi Content

```powershell
# Get posts from Strapi
Invoke-WebRequest -Uri "http://localhost:1337/api/posts" | Select-Object -ExpandProperty Content
```

---

## 🔍 LOGGING & DEBUGGING

### Enable Debug Logging

```powershell
# Update .env.local
# LOG_LEVEL=DEBUG

# Restart FastAPI with debug enabled
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
$env:LOG_LEVEL="DEBUG"
python main.py
```

### View Application Logs

```powershell
# FastAPI logs (in terminal where it's running)
# Watch output for errors and performance metrics

# Strapi logs (in terminal where it's running)
# Look for database connection info

# Python logs
# Check console output or log files in logs/ directory
```

### Check Ollama Status

```powershell
# Verify Ollama is running
Invoke-WebRequest -Uri http://localhost:11434/api/tags | Select-Object -ExpandProperty Content

# Pull model if needed
ollama pull llama2
ollama pull mistral

# List models
ollama list
```

---

## 📈 PERFORMANCE MONITORING

### Simple Performance Test

```powershell
# Create 10 tasks and measure average time
$times = @()
for ($i = 1; $i -le 10; $i++) {
    $start = Get-Date
    $task = @{
        topic = "Performance Test $i"
        category = "testing"
    } | ConvertTo-Json

    Invoke-WebRequest -Uri http://localhost:8000/api/tasks `
        -Method POST `
        -Headers @{"Content-Type"="application/json"} `
        -Body $task -ErrorAction SilentlyContinue | Out-Null

    $elapsed = (Get-Date) - $start
    $times += $elapsed.TotalMilliseconds
    Write-Host "Task $i created in $($elapsed.TotalMilliseconds)ms"
}

$avg = ($times | Measure-Object -Average).Average
Write-Host "Average creation time: ${avg}ms"
```

---

## 🆘 TROUBLESHOOTING

### Reset System

```powershell
# 1. Stop all services
Get-Process | Where-Object {$_.ProcessName -match "node|python"} | Stop-Process -Force

# 2. Clear caches
Remove-Item c:\Users\mattm\glad-labs-website\src\cofounder_agent\.tmp -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item c:\Users\mattm\glad-labs-website\cms\strapi-main\.cache -Recurse -Force -ErrorAction SilentlyContinue

# 3. Restart services (use commands above)
```

### Common Issues

**FastAPI won't start:**

```powershell
# Check Python version
python --version      # Should be 3.12+

# Check dependencies
pip list | findstr fastapi

# Reinstall dependencies
pip install -r requirements.txt
```

**Strapi won't connect to PostgreSQL:**

```powershell
# Check PostgreSQL is running
psql -U postgres -d strapi_dev -c "SELECT 1"

# Check connection string in .env
type cms\strapi-main\.env | findstr DATABASE_URL

# Verify database exists
psql -U postgres -l | findstr strapi_dev
```

**Content not publishing:**

```powershell
# Check if Strapi API token is valid
# Check error logs in FastAPI terminal
# Verify PostgreSQL connection

# Test publishing endpoint directly
$data = @{
    task_id = "test-id"
    content = "Test content"
    metadata = @{title="Test"}
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:8000/api/publish" `
    -Method POST `
    -Headers @{"Content-Type"="application/json"} `
    -Body $data
```

---

## 📚 USEFUL LINKS

- **FastAPI Docs:** http://localhost:8000/docs
- **FastAPI ReDoc:** http://localhost:8000/redoc
- **Strapi Admin:** http://localhost:1337/admin
- **Public Site:** http://localhost:3000
- **Oversight Hub:** http://localhost:3001

---

## 🎯 NEXT STEPS

1. Verify all services are running
2. Check health endpoints
3. Create a test task to verify pipeline
4. Monitor logs for any issues
5. Access Oversight Hub to monitor in real-time

---

**Ready to operate!** 🚀
