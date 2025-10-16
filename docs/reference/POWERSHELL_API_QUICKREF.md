# PowerShell Quick Reference - GLAD Labs Co-founder Agent

## The Problem

PowerShell's `curl` is an **alias** for `Invoke-WebRequest`, which has **different syntax** than Unix `curl`.

❌ **This DOESN'T work in PowerShell:**

```powershell
curl -X POST http://localhost:8000/command -H "Content-Type: application/json" -d '{"command": "status"}'
```

## The Solution

✅ **Use PowerShell-native commands:**

### Option 1: Automated Testing (Easiest!)

```powershell
# Start the server (if not running)
npm run dev:cofounder

# In another terminal, run tests
.\scripts\quick-test-api.ps1
```

### Option 2: Interactive API Documentation

```powershell
# Open Swagger/OpenAPI docs in browser
Start-Process 'http://localhost:8000/docs'
```

### Option 3: Manual Commands

#### Health Check

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/' -Method Get
```

#### Send Command

```powershell
$body = @{command="status"} | ConvertTo-Json
Invoke-RestMethod -Uri 'http://localhost:8000/command' -Method Post -Headers @{"Content-Type"="application/json"} -Body $body
```

#### Create Task

```powershell
$task = @{
    topic = "My Topic"
    category = "Blog Post"
} | ConvertTo-Json

Invoke-RestMethod -Uri 'http://localhost:8000/tasks' -Method Post -Headers @{"Content-Type"="application/json"} -Body $task
```

#### Get Agents

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/agents' -Method Get
```

## Complete Workflow

```powershell
# 1. Start server
npm run dev:cofounder

# 2. In another terminal - Test everything
.\scripts\quick-test-api.ps1

# 3. Or open interactive docs
Start-Process 'http://localhost:8000/docs'
```

## Helper Scripts Created

- `scripts/quick-test-api.ps1` - Automated test suite
- `scripts/test-cofounder-api.ps1` - Reference guide with examples

## Key Differences: curl vs PowerShell

| Feature | Unix curl                             | PowerShell                                      |
| ------- | ------------------------------------- | ----------------------------------------------- |
| Command | `curl`                                | `Invoke-RestMethod`                             |
| Method  | `-X POST`                             | `-Method Post`                                  |
| Headers | `-H "Content-Type: application/json"` | `-Headers @{"Content-Type"="application/json"}` |
| Body    | `-d '{"key":"value"}'`                | `-Body (@{key="value"} \| ConvertTo-Json)`      |

## Troubleshooting

**Server not running?**

```powershell
npm run dev:cofounder
```

**Check if server is up:**

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/' -Method Get
```

**View logs:**
Server logs appear in the terminal where you ran `npm run dev:cofounder`

---

**Documentation**: See `docs/COFOUNDER_AGENT_DEV_MODE.md` for full details
