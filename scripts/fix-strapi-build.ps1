# Fix Strapi Cloud Build - Date-fns Issue
# This script fixes the date-fns package version issue that breaks Strapi Cloud builds

Write-Host "🔧 Fixing Strapi Cloud Build Issue..." -ForegroundColor Cyan
Write-Host ""

# Navigate to Strapi directory
Set-Location "C:\Users\mattm\glad-labs-website\cms\strapi-main"

Write-Host "📦 Removing node_modules and package-lock.json..." -ForegroundColor Yellow
Remove-Item -Path "node_modules" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "package-lock.json" -Force -ErrorAction SilentlyContinue

Write-Host "✅ Cleaned up" -ForegroundColor Green
Write-Host ""

Write-Host "📥 Installing dependencies with date-fns@3.6.0..." -ForegroundColor Yellow
npm install

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Dependencies installed successfully" -ForegroundColor Green
    Write-Host ""
    
    Write-Host "🏗️  Testing build..." -ForegroundColor Yellow
    npm run build
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Build successful!" -ForegroundColor Green
        Write-Host ""
        Write-Host "📤 Ready to commit and push to trigger Strapi Cloud rebuild" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Run these commands:" -ForegroundColor White
        Write-Host "  git add cms/strapi-main/package.json" -ForegroundColor Gray
        Write-Host "  git add cms/strapi-main/package-lock.json" -ForegroundColor Gray
        Write-Host "  git commit -m 'fix: downgrade date-fns to v3.6.0 for Strapi Cloud compatibility'" -ForegroundColor Gray
        Write-Host "  git push origin main" -ForegroundColor Gray
        Write-Host ""
        Write-Host "🚀 Strapi Cloud will automatically rebuild!" -ForegroundColor Green
    } else {
        Write-Host "❌ Build failed. Check the error messages above." -ForegroundColor Red
    }
} else {
    Write-Host "❌ npm install failed" -ForegroundColor Red
}

Write-Host ""
Write-Host "💡 What was fixed:" -ForegroundColor Cyan
Write-Host "  - date-fns pinned to v3.6.0 (compatible with Strapi v5)" -ForegroundColor Gray
Write-Host "  - v4.x has export issues with Vite/Rollup in Strapi builds" -ForegroundColor Gray
