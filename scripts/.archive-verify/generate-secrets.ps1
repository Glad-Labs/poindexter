# Generate-Secrets.ps1
function New-RandomSecret {
    param([int]$Length = 32)
    $bytes = New-Object byte[] $Length
    $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
    $rng.GetBytes($bytes)
    [Convert]::ToBase64String($bytes)
}

# Define all secrets needed
$secrets = @{
    # Staging Secrets
    'STAGING_STRAPI_TOKEN' = New-RandomSecret
    'STAGING_DB_PASSWORD' = New-RandomSecret
    'STAGING_JWT_SECRET' = New-RandomSecret
    'STAGING_API_TOKEN_SALT' = New-RandomSecret
    
    # Production Secrets
    'PROD_STRAPI_TOKEN' = New-RandomSecret
    'PROD_DB_PASSWORD' = New-RandomSecret
    'PROD_JWT_SECRET' = New-RandomSecret
    'PROD_API_TOKEN_SALT' = New-RandomSecret
    
    # Shared Secrets
    'ADMIN_JWT_SECRET' = New-RandomSecret
    'JWT_SECRET' = New-RandomSecret
    'API_TOKEN_SALT' = New-RandomSecret
}

# Display in table format
$secrets | ConvertTo-Json | Write-Host -ForegroundColor Green

# Or export to CSV for easy import
$secrets.GetEnumerator() | Select-Object @{N='Name';E={$_.Key}}, @{N='Value';E={$_.Value}} | Export-Csv -Path "secrets.csv" -NoTypeInformation

Write-Host "`n✅ Secrets saved to secrets.csv" -ForegroundColor Green