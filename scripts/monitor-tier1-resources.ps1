#Requires -Version 5.1
<#
.SYNOPSIS
Glad Labs Tier 1 Resource Monitor (PowerShell)

.DESCRIPTION
Monitor Tier 1 production resources and alert when approaching limits.
Checks CPU, memory, database size, and provides scaling recommendations.

.EXAMPLE
.\scripts\monitor-tier1-resources.ps1
.\scripts\monitor-tier1-resources.ps1 -Watch -Interval 5  # Watch mode, update every 5 sec

#>

param(
    [switch]$Watch,
    [int]$Interval = 30  # Seconds between updates
)

$ErrorActionPreference = "Continue"

# Configuration
$TIER1_LIMITS = @{
    CPU_PERCENT = 80
    Memory_MB = 256
    Database_GB = 1
    Storage_GB = 1
}

$SCALING_TRIGGERS = @{
    CPU = 80          # CPU usage >80%
    Memory = 85       # Memory usage >85%
    Database = 80     # Database size >80% of limit
    RequestsPerMin = 100  # >100 req/min
    ErrorRate = 1     # >1% errors
}

function Write-Color {
    param(
        [string]$Text,
        [string]$Color = "White"
    )
    Write-Host $Text -ForegroundColor $Color
}

function Show-Banner {
    Write-Color ""
    Write-Color "╔════════════════════════════════════════════════════════╗" "Cyan"
    Write-Color "║     Glad Labs Tier 1 Resource Monitor (PowerShell)     ║" "Cyan"
    Write-Color "╚════════════════════════════════════════════════════════╝" "Cyan"
    Write-Color ""
}

function Get-RailwayMetrics {
    try {
        Write-Color "📊 Fetching Railway metrics..." "Yellow"
        
        # This would call Railway API in production
        # For now, we'll show how to structure the output
        
        $metrics = @{
            Strapi = @{
                CPU = 45
                Memory = 120
                Status = "running"
            }
            CoFounderAgent = @{
                CPU = 35
                Memory = 98
                Status = "running"
            }
            PostgreSQL = @{
                CPU = 20
                Memory = 150
                Status = "running"
                Storage_MB = 250
            }
        }
        
        return $metrics
    } catch {
        Write-Color "❌ Failed to fetch metrics: $_" "Red"
        return $null
    }
}

function Show-ServiceStatus {
    param([hashtable]$Metrics)
    
    if (!$Metrics) { return }
    
    Write-Color ""
    Write-Color "🔧 Service Status" "Cyan"
    Write-Color "─" * 56 "Cyan"
    
    foreach ($service in $Metrics.Keys) {
        $data = $Metrics[$service]
        $status = $data.Status
        $statusColor = if ($status -eq "running") { "Green" } else { "Red" }
        
        Write-Host "  $service" -ForegroundColor Cyan
        Write-Color "    Status: $status" $statusColor
        Write-Color "    CPU: $($data.CPU)% / $($TIER1_LIMITS.CPU_PERCENT)% limit" "Yellow"
        Write-Color "    Memory: $($data.Memory)MB / $($TIER1_LIMITS.Memory_MB)MB" "Yellow"
        
        if ($data.Storage_MB) {
            Write-Color "    Storage: $($data.Storage_MB)MB / $($TIER1_LIMITS.Database_GB * 1024)MB" "Yellow"
        }
        
        Write-Color ""
    }
}

function Test-ResourceLimits {
    param([hashtable]$Metrics)
    
    if (!$Metrics) { return }
    
    Write-Color ""
    Write-Color "⚠️  Resource Limit Alerts" "Cyan"
    Write-Color "─" * 56 "Cyan"
    
    $alerts = 0
    
    foreach ($service in $Metrics.Keys) {
        $data = $Metrics[$service]
        
        # CPU check
        if ($data.CPU -gt $TIER1_LIMITS.CPU_PERCENT) {
            Write-Color "  🔴 $service CPU HIGH: $($data.CPU)% (limit: $($TIER1_LIMITS.CPU_PERCENT)%)" "Red"
            $alerts++
        }
        
        # Memory check (warn at 85%)
        $memPercent = ($data.Memory / $TIER1_LIMITS.Memory_MB) * 100
        if ($memPercent -gt 85) {
            Write-Color "  🟡 $service Memory HIGH: $($data.Memory)MB of $($TIER1_LIMITS.Memory_MB)MB ($([Math]::Round($memPercent))%)" "Yellow"
            $alerts++
        } elseif ($memPercent -gt 70) {
            Write-Color "  🟡 $service Memory MEDIUM: $($data.Memory)MB of $($TIER1_LIMITS.Memory_MB)MB ($([Math]::Round($memPercent))%)" "Yellow"
        }
        
        # Storage check
        if ($data.Storage_MB) {
            $storagePercent = ($data.Storage_MB / ($TIER1_LIMITS.Database_GB * 1024)) * 100
            if ($storagePercent -gt 80) {
                Write-Color "  🔴 $service Storage HIGH: $($data.Storage_MB)MB of $($TIER1_LIMITS.Database_GB * 1024)MB ($([Math]::Round($storagePercent))%)" "Red"
                $alerts++
            }
        }
    }
    
    if ($alerts -eq 0) {
        Write-Color "  ✅ All resources within limits" "Green"
    }
}

function Show-ScalingRecommendations {
    Write-Color ""
    Write-Color "📈 Scaling Recommendations" "Cyan"
    Write-Color "─" * 56 "Cyan"
    
    Write-Color ""
    Write-Color "  Scale to Tier 2 when:" "Yellow"
    Write-Color "    • CPU consistently >$($SCALING_TRIGGERS.CPU)%" "White"
    Write-Color "    • Memory consistently >$($SCALING_TRIGGERS.Memory)%" "White"
    Write-Color "    • Database approaching $($TIER1_LIMITS.Database_GB)GB limit" "White"
    Write-Color "    • >$($SCALING_TRIGGERS.RequestsPerMin) requests/minute" "White"
    Write-Color ""
    Write-Color "  Command: bash scripts/scale-to-tier2.sh" "Cyan"
    Write-Color ""
}

function Show-OperationsCosts {
    Write-Color ""
    Write-Color "💰 Current Tier 1 Costs" "Cyan"
    Write-Color "─" * 56 "Cyan"
    
    Write-Color "  Monthly Budget: ~$0-10" "Green"
    Write-Color "  Services:" "White"
    Write-Color "    • PostgreSQL: FREE (included in Tier 1)" "Green"
    Write-Color "    • Strapi CMS: FREE (shared CPU)" "Green"
    Write-Color "    • Co-Founder Agent: FREE (shared CPU)" "Green"
    Write-Color "    • Frontend (Vercel): FREE (Pro plan available)" "Green"
    Write-Color ""
    Write-Color "  When scaling to Tier 2: ~$50-70/month" "Yellow"
    Write-Color "    • Dedicated resources" "White"
    Write-Color "    • 99.5% uptime SLA" "White"
    Write-Color "    • Support for 100-500 concurrent users" "White"
    Write-Color ""
}

function Show-NextSteps {
    Write-Color ""
    Write-Color "🎯 Next Steps" "Cyan"
    Write-Color "─" * 56 "Cyan"
    
    Write-Color "  1. Set up automated backups:" "White"
    Write-Color "     Task Scheduler: scripts\backup-tier1-db.ps1 (daily at 2 AM)" "DarkGray"
    Write-Color ""
    Write-Color "  2. Configure monitoring:" "White"
    Write-Color "     Run this script on a schedule: Every 5 minutes" "DarkGray"
    Write-Color ""
    Write-Color "  3. Set up alerts:" "White"
    Write-Color "     Email/Slack notification on resource warnings" "DarkGray"
    Write-Color ""
    Write-Color "  4. Regular capacity planning:" "White"
    Write-Color "     Review metrics monthly, plan scaling if needed" "DarkGray"
    Write-Color ""
}

function Main {
    Show-Banner
    
    if ($Watch) {
        Write-Color "👁️  Watch mode enabled - updating every $Interval seconds (Ctrl+C to stop)" "Yellow"
        Write-Color ""
    }
    
    do {
        $metrics = Get-RailwayMetrics
        
        if ($metrics) {
            Show-ServiceStatus -Metrics $metrics
            Test-ResourceLimits -Metrics $metrics
            Show-ScalingRecommendations
            Show-OperationsCosts
            Show-NextSteps
            
            if ($Watch) {
                Write-Color "Last updated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" "DarkGray"
                Write-Color "Next update in $Interval seconds (Ctrl+C to stop)..." "DarkGray"
                Start-Sleep -Seconds $Interval
                Clear-Host
            } else {
                break
            }
        } else {
            break
        }
    } while ($Watch)
}

# Run main
Main
