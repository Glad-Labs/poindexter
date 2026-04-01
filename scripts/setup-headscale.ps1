# Glad Labs — Headscale Setup (replaces Tailscale cloud)
#
# Run this AFTER docker compose brings up headscale:
#   docker compose -f docker-compose.local.yml up -d headscale
#   powershell -ExecutionPolicy Bypass -File scripts/setup-headscale.ps1

Write-Host "=== Glad Labs Headscale Setup ===" -ForegroundColor Cyan

# Step 1: Create user
Write-Host "`n[1/4] Creating headscale user 'matt'..." -ForegroundColor Yellow
docker exec gladlabs-headscale headscale users create matt 2>&1 | Out-Host

# Step 2: Create pre-auth key (reusable, 1 year expiry)
Write-Host "`n[2/4] Creating pre-auth key..." -ForegroundColor Yellow
$authKey = (docker exec gladlabs-headscale headscale preauthkeys create --user matt --reusable --expiration 365d 2>&1) | Select-String -Pattern "^[a-f0-9]" | ForEach-Object { $_.ToString().Trim() }

if (-not $authKey) {
    # Try alternative output format
    $authKey = (docker exec gladlabs-headscale headscale preauthkeys list --user matt 2>&1) | Select-String -Pattern "true" | ForEach-Object { ($_.ToString().Trim() -split '\s+')[0] }
}

Write-Host "Auth key: $authKey" -ForegroundColor Green

# Step 3: Get local IP for headscale server URL
$localIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch "Loopback|vEthernet|WSL" -and $_.IPAddress -notmatch "^169\." } | Select-Object -First 1).IPAddress
Write-Host "`n[3/4] Your local IP: $localIP" -ForegroundColor Yellow

# Step 4: Instructions
Write-Host "`n[4/4] Connect your devices:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  ON THIS PC (run in admin PowerShell):" -ForegroundColor White
Write-Host "    tailscale up --login-server http://${localIP}:8080 --authkey $authKey --force-reauth" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ON YOUR PHONE:" -ForegroundColor White
Write-Host "    1. Open Tailscale app" -ForegroundColor White
Write-Host "    2. Settings > Use custom coordination server" -ForegroundColor White
Write-Host "    3. Enter: http://${localIP}:8080" -ForegroundColor Cyan
Write-Host "    4. Or use auth key in Tailscale settings" -ForegroundColor White
Write-Host ""
Write-Host "  After connecting, check nodes:" -ForegroundColor Yellow
Write-Host "    docker exec gladlabs-headscale headscale nodes list" -ForegroundColor Cyan
Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green

# Save auth key for reference
$keyFile = Join-Path $env:USERPROFILE ".gladlabs" "headscale-authkey.txt"
New-Item -Path (Split-Path $keyFile) -ItemType Directory -Force | Out-Null
$authKey | Out-File -FilePath $keyFile -Force
Write-Host "Auth key saved to: $keyFile" -ForegroundColor Gray
