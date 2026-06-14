<#
.SYNOPSIS
    Long-running background services that must survive reboots.
    Each service is a persistent daemon (not a one-shot job).

.PARAMETER Install
    Register all services as Windows Scheduled Tasks (requires elevation).

.PARAMETER Uninstall
    Remove all registered background-service tasks (requires elevation).

.PARAMETER List
    Show task status for all registered services.

.NOTES
    Run Install/Uninstall from an elevated PowerShell terminal.
    Tasks trigger AtLogon with no execution time limit; they restart
    automatically up to 3 times (5-min interval) on crash.

.EXAMPLE
    .\background-services.ps1 -Install
    .\background-services.ps1 -List
    .\background-services.ps1 -Uninstall
#>
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$List
)

$ErrorActionPreference = "Continue"
$WorkDir  = "C:\Users\mattm\glad-labs-website"
$Venv     = "$WorkDir\.venv\Scripts"
$TaskPrefix = "poindexter"

# Service definitions - each is a persistent daemon started at logon.
$Services = @{
    "gpu-scraper" = @{
        Description = "GPU metrics scraper - polls nvidia-smi exporter (port 9835), writes to poindexter gpu_metrics table every 60s"
        Execute     = "$Venv\pythonw.exe"
        Argument    = "$WorkDir\scripts\gpu-scraper.py"
        WorkDir     = $WorkDir
    }
    "nvidia-smi-exporter" = @{
        Description = "Prometheus exporter for NVIDIA GPU + system power metrics - serves on port 9835, scraped by local Prometheus every 15s"
        Execute     = "$Venv\pythonw.exe"
        Argument    = "$WorkDir\scripts\nvidia-smi-exporter.py"
        WorkDir     = $WorkDir
    }
    "video-server" = @{
        Description = "Video generation server - creates narrated slideshow MP4s from images + audio, listens on port 9837"
        Execute     = "$Venv\pythonw.exe"
        Argument    = "$WorkDir\scripts\video-server.py"
        WorkDir     = $WorkDir
    }
}

function Install-Services {
    foreach ($name in $Services.Keys) {
        $svc = $Services[$name]
        $taskName = "$TaskPrefix-$name"

        $action = New-ScheduledTaskAction `
            -Execute $svc.Execute `
            -Argument $svc.Argument `
            -WorkingDirectory $svc.WorkDir

        $trigger = New-ScheduledTaskTrigger -AtLogOn

        # No ExecutionTimeLimit - these are persistent daemons, not one-shot jobs.
        # RestartCount + RestartInterval auto-recover from crashes.
        $settings = New-ScheduledTaskSettingsSet `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 5) `
            -MultipleInstances IgnoreNew

        $null = Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $trigger `
            -Settings $settings `
            -Description $svc.Description `
            -RunLevel Highest `
            -Force | Out-Null

        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Write-Host "Registered: $taskName (AtLogon, no time limit, restart x3)"
        } else {
            Write-Host "FAILED to register: $taskName"
        }
    }
}

function Uninstall-Services {
    foreach ($name in $Services.Keys) {
        $taskName = "$TaskPrefix-$name"
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Removed: $taskName"
    }
}

function List-Services {
    Write-Host "Background services:`n"
    foreach ($name in ($Services.Keys | Sort-Object)) {
        $taskName = "$TaskPrefix-$name"
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $status = if ($task) { $task.State } else { "NOT REGISTERED" }
        Write-Host "  $taskName - $status"
        Write-Host "    $($Services[$name].Description)"
    }
}

if ($Install)   { Install-Services }
elseif ($Uninstall) { Uninstall-Services }
elseif ($List)  { List-Services }
else {
    Write-Host "Usage: .\background-services.ps1 -Install | -Uninstall | -List"
    Write-Host ""
    List-Services
}
