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
    Tasks trigger AtLogon (immediate start on an interactive session) plus
    every 5 minutes thereafter (the actual safety net - see Install-Services),
    with no execution time limit. RestartCount/RestartInterval additionally
    restart a task up to 3 times (5-min interval) on a nonzero-exit crash.

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
    "ollama-vision-gpu1" = @{
        Description = "Second Ollama instance pinned to GPU 1 (RTX 3090), port 11435 - keeps eviction-prone QA-rail models (qwen3-vl vision) warm so writer reloads on GPU 0 cannot displace them (glad-labs-stack#2051)"
        Execute     = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        Argument    = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$WorkDir\scripts\ollama-vision-gpu1.ps1`""
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

        # AtLogOn gets the daemon running immediately on an interactive
        # session; the repeating trigger is the actual safety net. Windows'
        # RestartCount/RestartInterval below only fires on a NONZERO exit -
        # a daemon that exits 0 (e.g. `ollama.exe serve` quitting cleanly
        # during reboot-chaos, poindexter#860 - ollama-vision-gpu1 sat
        # dead for 24h+ with LastTaskResult=0 and silently starved the
        # qa.vision QA rail) is invisible to it, and with only AtLogOn the
        # task then never restarts until the next logon - which on a box
        # that stays logged in for days can mean "silently dead for days".
        # MultipleInstances=IgnoreNew (below) makes every repeat a no-op
        # once the daemon is actually alive, so polling every 5 min costs
        # nothing when healthy.
        $logonTrigger = New-ScheduledTaskTrigger -AtLogOn
        $repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
            -RepetitionInterval (New-TimeSpan -Minutes 5) `
            -RepetitionDuration (New-TimeSpan -Days 3650)

        # No ExecutionTimeLimit - these are persistent daemons, not one-shot jobs.
        # RestartCount + RestartInterval auto-recover from crashes (nonzero
        # exit); the repeating trigger above covers the clean-exit gap they
        # can't see.
        $settings = New-ScheduledTaskSettingsSet `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 5) `
            -MultipleInstances IgnoreNew

        $null = Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger @($logonTrigger, $repeatTrigger) `
            -Settings $settings `
            -Description $svc.Description `
            -RunLevel Highest `
            -Force | Out-Null

        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Write-Host "Registered: $taskName (AtLogon + every 5min, no time limit, restart x3)"
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
