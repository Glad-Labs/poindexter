# Railway Project Setup Script for GLAD Labs Strapi Backend
# Windows PowerShell version - Run as Administrator

Write-Host "🚀 Setting up GLAD Labs Strapi Backend on Railway CLI..." -ForegroundColor Green
Write-Host ""

# Step 1: Check if Railway CLI is installed
Write-Host "📦 Checking Railway CLI installation..." -ForegroundColor Blue
try {
    $railwayVersion = railway --version
    Write-Host "✅ Railway CLI found: $railwayVersion" -ForegroundColor Green
}
catch {
    Write-Host "❌ Railway CLI not found. Installing..." -ForegroundColor Red
    npm install -g @railway/cli
    Write-Host "✅ Railway CLI installed" -ForegroundColor Green
}

Write-Host ""
Write-Host "🔐 Authenticating with Railway..." -ForegroundColor Blue
railway login

Write-Host ""
Write-Host "📁 Initializing new Railway project..." -ForegroundColor Blue
railway init --name glad-labs-strapi

Write-Host ""
Write-Host "✅ Project initialized!" -ForegroundColor Green
Write-Host ""
Write-Host "📋 Next steps:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1️⃣  Add PostgreSQL plugin:" -ForegroundColor Yellow
Write-Host "    railway add --plugin postgres" -ForegroundColor White
Write-Host ""
Write-Host "2️⃣  Set environment variables:" -ForegroundColor Yellow
Write-Host "    railway variables set DATABASE_CLIENT=postgres" -ForegroundColor White
Write-Host "    railway variables set HOST=0.0.0.0" -ForegroundColor White
Write-Host "    railway variables set PORT=1337" -ForegroundColor White
Write-Host "    railway variables set STRAPI_TELEMETRY_DISABLED=true" -ForegroundColor White
Write-Host ""
Write-Host "3️⃣  Deploy Strapi:" -ForegroundColor Yellow
Write-Host "    railway deploy" -ForegroundColor White
Write-Host ""
Write-Host "4️⃣  View logs:" -ForegroundColor Yellow
Write-Host "    railway logs" -ForegroundColor White
Write-Host ""
Write-Host "5️⃣  Open admin panel:" -ForegroundColor Yellow
Write-Host "    railway open" -ForegroundColor White
