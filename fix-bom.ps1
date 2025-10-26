#!/usr/bin/env pwsh
<#
.SYNOPSIS
Remove BOM (Byte Order Mark) from JavaScript files
#>

$file = "web/oversight-hub/src/services/cofounderAgentClient.js"

Write-Host "🔧 Removing BOM from: $file" -ForegroundColor Cyan

# Read file content without BOM
$content = Get-Content -Path $file -Encoding UTF8BOM -Raw

# Write back without BOM (UTF8 without BOM)
$utf8NoBOM = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($file, $content, $utf8NoBOM)

Write-Host "✅ BOM removed successfully!" -ForegroundColor Green
Write-Host "📝 File: $file" -ForegroundColor Gray
