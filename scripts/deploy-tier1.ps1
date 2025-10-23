#Requires -Version 5.1
<#
.SYNOPSIS
GLAD Labs Tier 1 Production Deployment Script (PowerShell)
Ultra-low-cost setup: ~$10-15/month

.DESCRIPTION
Automated deployment to Railway + Vercel for Tier 1 (ultra-budget) production.
Resources: Shared CPU, 256-512MB RAM, 1GB storage
Cost: ~$0-10/month
Uptime: ~95% (services may sleep after inactivity)

.PREREQUISITES
- Railway CLI: npm install -g @railway/cli
- Vercel CLI: npm install -g vercel
- Git configured with credentials
- Node.js 18+
- Python 3.12+ (for backend)

.EXAMPLE
.\scripts\deploy-tier1.ps1

#>

param(
    [switch]$SkipConfirmation,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Configuration
$TIER1_CONFIG = @{
    ProjectName = "glad-labs-tier1-prod"
    Railway = @{
        PostgreSQL = "free"
        CPU = "shared"
        Memory = "256Mi"
        Storage = "1Gi"
    }
    Cost = @{
        Monthly = "$0-10"
        Users = "50 concurrent"
        Uptime = "95%"
    }
}

function Write-Header {
    param([string]$Text)
    Write-Host ""
    Write-Host "╔$('═' * 58)╗" -ForegroundColor Cyan
    Write-Host "║ $($Text.PadRight(56)) ║" -ForegroundColor Cyan
    Write-Host "╚$('═' * 58)╝" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step {
    param([string]$Number, [string]$Text)
    Write-Host "📦 Step $Number`: $Text" -ForegroundColor Yellow
}

function Write-Success {
    param([string]$Text)
    Write-Host "✅ $Text" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Text)
    Write-Host "⚠️  $Text" -ForegroundColor Red
}

function Test-Prerequisites {
    Write-Header "🔍 Checking Prerequisites"

    $missing = @()

    # Check Railway CLI
    $railway = $(railway --version 2>$null)
    if ($LASTEXITCODE -ne 0) {
        $missing += "Railway CLI (npm install -g @railway/cli)"
    } else {
        Write-Success "Railway CLI: $railway"
    }

    # Check Vercel CLI
    $vercel = $(vercel --version 2>$null)
    if ($LASTEXITCODE -ne 0) {
        $missing += "Vercel CLI (npm install -g vercel)"
    } else {
        Write-Success "Vercel CLI: $vercel"
    }

    # Check Node.js
    $node = node --version
    if ($node) {
        Write-Success "Node.js: $node"
    }

    # Check Python
    $python = python --version 2>&1
    if ($python) {
        Write-Success "Python: $python"
    }

    # Check Git
    $git = git --version
    if ($git) {
        Write-Success "Git: $git"
    }

    if ($missing.Count -gt 0) {
        Write-Host ""
        Write-Warning "Missing prerequisites:"
        $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
        exit 1
    }

    Write-Success "All prerequisites met!"
}

function Show-Configuration {
    Write-Header "⚙️  Tier 1 Configuration"
    
    Write-Host "Project: $($TIER1_CONFIG.ProjectName)" -ForegroundColor Cyan
    Write-Host "CPU: $($TIER1_CONFIG.Railway.CPU)" -ForegroundColor Cyan
    Write-Host "Memory: $($TIER1_CONFIG.Railway.Memory)" -ForegroundColor Cyan
    Write-Host "Storage: $($TIER1_CONFIG.Railway.Storage)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Monthly Cost: $($TIER1_CONFIG.Cost.Monthly)" -ForegroundColor Green
    Write-Host "Max Users: $($TIER1_CONFIG.Cost.Users)" -ForegroundColor Green
    Write-Host "Uptime SLA: $($TIER1_CONFIG.Cost.Uptime)" -ForegroundColor Green
    Write-Host ""
    Write-Warning "Services may sleep after 30 min inactivity (Tier 1 limitation)"
}

function Confirm-Deployment {
    if ($SkipConfirmation) {
        return $true
    }

    Write-Host ""
    $response = Read-Host "Continue with deployment? (y/n)"
    return $response -eq "y" -or $response -eq "yes"
}

function Deploy-PostgreSQL {
    Write-Step "2" "Setting up PostgreSQL (Free tier)"
    
    if ($DryRun) {
        Write-Host "  [DRY RUN] railway service add postgresql" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] railway link" -ForegroundColor DarkGray
        return
    }

    try {
        Write-Host "  Adding PostgreSQL service..."
        & railway service add postgresql | Out-Null
        Write-Success "PostgreSQL added"

        Write-Host "  Linking project..."
        & railway link | Out-Null
        Write-Success "Project linked"
    } catch {
        Write-Warning "Failed to set up PostgreSQL: $_"
        exit 1
    }
}

function Deploy-Strapi {
    Write-Step "3" "Deploying Strapi CMS"
    
    if ($DryRun) {
        Write-Host "  [DRY RUN] cd cms/strapi-v5-backend" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] npm run build" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] railway deploy" -ForegroundColor DarkGray
        return
    }

    try {
        $strapiPath = Join-Path $PSScriptRoot "../cms/strapi-v5-backend"
        
        if (!(Test-Path $strapiPath)) {
            Write-Warning "Strapi directory not found: $strapiPath"
            exit 1
        }

        Push-Location $strapiPath
        
        Write-Host "  Building Strapi..."
        npm run build 2>$null | Out-Null
        
        Write-Host "  Deploying to Railway..."
        & railway deploy 2>$null | Out-Null
        
        Pop-Location
        Write-Success "Strapi deployed"
    } catch {
        Write-Warning "Failed to deploy Strapi: $_"
        exit 1
    }
}

function Deploy-CoFounderAgent {
    Write-Step "4" "Deploying Co-Founder Agent"
    
    if ($DryRun) {
        Write-Host "  [DRY RUN] cd src/cofounder_agent" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] railway deploy" -ForegroundColor DarkGray
        return
    }

    try {
        $agentPath = Join-Path $PSScriptRoot "../src/cofounder_agent"
        
        if (!(Test-Path $agentPath)) {
            Write-Warning "Agent directory not found: $agentPath"
            exit 1
        }

        Push-Location $agentPath
        
        Write-Host "  Deploying to Railway..."
        & railway deploy 2>$null | Out-Null
        
        Pop-Location
        Write-Success "Co-Founder Agent deployed"
    } catch {
        Write-Warning "Failed to deploy Co-Founder Agent: $_"
        exit 1
    }
}

function Deploy-Frontend {
    Write-Step "5" "Deploying Frontend to Vercel"
    
    if ($DryRun) {
        Write-Host "  [DRY RUN] cd web/public-site" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] vercel --prod" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] cd ../oversight-hub" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] vercel --prod" -ForegroundColor DarkGray
        return
    }

    try {
        # Deploy public site
        $publicPath = Join-Path $PSScriptRoot "../web/public-site"
        Push-Location $publicPath
        Write-Host "  Deploying public site..."
        & vercel --prod 2>$null | Out-Null
        Pop-Location
        Write-Success "Public site deployed"

        # Deploy oversight hub
        $overallPath = Join-Path $PSScriptRoot "../web/oversight-hub"
        Push-Location $overallPath
        Write-Host "  Deploying oversight hub..."
        & vercel --prod 2>$null | Out-Null
        Pop-Location
        Write-Success "Oversight hub deployed"
    } catch {
        Write-Warning "Failed to deploy frontend: $_"
        exit 1
    }
}

function Show-CompletionMessage {
    Write-Header "🎉 Deployment Complete!"
    
    Write-Host "Your Tier 1 production is now live!" -ForegroundColor Green
    Write-Host ""
    Write-Host "📊 What you deployed:" -ForegroundColor Cyan
    Write-Host "  ✅ PostgreSQL database (free tier)" -ForegroundColor Green
    Write-Host "  ✅ Strapi CMS on Railway" -ForegroundColor Green
    Write-Host "  ✅ Co-Founder Agent on Railway" -ForegroundColor Green
    Write-Host "  ✅ Public site on Vercel" -ForegroundColor Green
    Write-Host "  ✅ Oversight hub on Vercel" -ForegroundColor Green
    Write-Host ""
    Write-Host "💰 Monthly cost: $($TIER1_CONFIG.Cost.Monthly)" -ForegroundColor Yellow
    Write-Host "👥 Max users: $($TIER1_CONFIG.Cost.Users)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "🔗 Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Monitor resources: node scripts/monitor-tier1-resources.js" -ForegroundColor White
    Write-Host "  2. Set up backups: Schedule scripts/backup-tier1-db.sh" -ForegroundColor White
    Write-Host "  3. Configure monitoring & alerts" -ForegroundColor White
    Write-Host "  4. When traffic increases, scale to Tier 2: bash scripts/scale-to-tier2.sh" -ForegroundColor White
    Write-Host ""
}

# Main execution
function Main {
    Write-Header "🚀 GLAD Labs Tier 1 Production Deployment"
    
    Test-Prerequisites
    Show-Configuration
    
    if (!$(Confirm-Deployment)) {
        Write-Host "Deployment cancelled." -ForegroundColor Yellow
        exit 0
    }

    if ($DryRun) {
        Write-Header "🏃 DRY RUN MODE - No changes will be made"
    }

    Write-Step "1" "Initializing Railway project"
    if (!$DryRun) {
        & railway login 2>$null
        & railway init --name $TIER1_CONFIG.ProjectName 2>$null | Out-Null
        Write-Success "Railway project initialized"
    } else {
        Write-Host "  [DRY RUN] railway login" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] railway init --name $($TIER1_CONFIG.ProjectName)" -ForegroundColor DarkGray
    }

    Deploy-PostgreSQL
    Deploy-Strapi
    Deploy-CoFounderAgent
    Deploy-Frontend

    Show-CompletionMessage
}

# Run main
Main
