# Quick Dev Startup Troubleshooting Script
# Usage: . scripts/dev-troubleshoot.ps1

Write-Host "`n🔍 Glad Labs Dev Troubleshooting`n" -ForegroundColor Cyan

# Check 1: Git branch
Write-Host "1️⃣  Checking git branch..." -ForegroundColor Yellow
$branch = git rev-parse --abbrev-ref HEAD
Write-Host "   Current branch: $branch" -ForegroundColor Green

if ($branch -eq "main") {
    Write-Host "   ⚠️  WARNING: You're on main branch. Switch to a feature branch:" -ForegroundColor Red
    Write-Host "   git checkout -b feat/your-feature-name`n" -ForegroundColor White
}

# Check 2: Environment files
Write-Host "2️⃣  Checking environment files..." -ForegroundColor Yellow
if (Test-Path ".env.local") {
    Write-Host "   ✅ .env.local exists" -ForegroundColor Green
} else {
    Write-Host "   ❌ .env.local missing! Creating from example..." -ForegroundColor Red
    Copy-Item ".env.example" ".env.local" -Force
    Write-Host "   ✅ Created .env.local" -ForegroundColor Green
}

# Check 3: Node version
Write-Host "`n3️⃣  Checking Node.js version..." -ForegroundColor Yellow
$nodeVersion = node --version
Write-Host "   Node version: $nodeVersion" -ForegroundColor Green

# Check 4: npm-run-all
Write-Host "`n4️⃣  Checking npm-run-all..." -ForegroundColor Yellow
$npmRunAll = npm ls npm-run-all 2>$null | Select-String "npm-run-all"
if ($npmRunAll) {
    Write-Host "   ✅ npm-run-all is installed" -ForegroundColor Green
} else {
    Write-Host "   ⚠️  npm-run-all might be missing" -ForegroundColor Yellow
}

# Check 5: Workspace installations
Write-Host "`n5️⃣  Checking workspace installations..." -ForegroundColor Yellow
$workspaces = @("cms/strapi-main", "web/public-site", "web/oversight-hub", "src/cofounder_agent")
foreach ($workspace in $workspaces) {
    if (Test-Path "$workspace/node_modules") {
        Write-Host "   ✅ $workspace/node_modules exists" -ForegroundColor Green
    } else {
        Write-Host "   ❌ $workspace/node_modules missing" -ForegroundColor Red
    }
}

# Check 6: Ports
Write-Host "`n6️⃣  Checking if required ports are available..." -ForegroundColor Yellow
$ports = @{1337="Strapi"; 3000="Public Site"; 3001="Oversight Hub"; 8000="Co-Founder Agent"}

foreach ($port in $ports.Keys) {
    $connection = Test-NetConnection -ComputerName localhost -Port $port -WarningAction SilentlyContinue
    if ($connection.TcpTestSucceeded -eq $true) {
        Write-Host "   ⚠️  Port $port ($($ports[$port])) is already in use" -ForegroundColor Yellow
    } else {
        Write-Host "   ✅ Port $port ($($ports[$port])) is available" -ForegroundColor Green
    }
}

Write-Host "`n✨ Ready to start development!" -ForegroundColor Cyan
Write-Host "`nNext steps:`n" -ForegroundColor White
Write-Host "Option 1: Start all services (Strapi + Frontends only, no Python):" -ForegroundColor White
Write-Host "  npm run dev`n" -ForegroundColor Cyan

Write-Host "Option 2: Start services individually (most reliable):" -ForegroundColor White
Write-Host "  Terminal 1: cd cms\strapi-main ; npm run develop" -ForegroundColor Cyan
Write-Host "  Terminal 2: cd web\public-site ; npm run dev" -ForegroundColor Cyan
Write-Host "  Terminal 3: cd web\oversight-hub ; npm start`n" -ForegroundColor Cyan

Write-Host "Option 3: Start all including Python backend:" -ForegroundColor White
Write-Host "  npm run dev:full`n" -ForegroundColor Cyan

Write-Host "Services will be available at:" -ForegroundColor White
Write-Host "  Strapi Admin: http://localhost:1337/admin" -ForegroundColor Green
Write-Host "  Public Site: http://localhost:3000" -ForegroundColor Green
Write-Host "  Oversight Hub: http://localhost:3001" -ForegroundColor Green
Write-Host "  Co-Founder Agent: http://localhost:8000/docs" -ForegroundColor Green

