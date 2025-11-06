# Strapi Setup Helper - PowerShell Script
# Quick setup wizard for content type registration

Write-Host "`n╔════════════════════════════════════════════════════════╗"
Write-Host "║  Glad Labs Strapi Setup Helper                         ║"
Write-Host "╚════════════════════════════════════════════════════════╝`n"

# Menu
Write-Host "Choose an option:`n"
Write-Host "1. Auto-discover content types (RECOMMENDED - no token needed)"
Write-Host "2. Register with API token (for automation/CI-CD)"
Write-Host "3. Use improved script (hybrid - smartly handles both)"
Write-Host "4. View troubleshooting guide"
Write-Host "5. Generate API token instructions"
Write-Host "6. Exit`n"

$choice = Read-Host "Enter your choice (1-6)"

switch ($choice) {
    "1" {
        Write-Host "`n✅ Option 1: Auto-Discovery Setup`n"
        Write-Host "Steps:"
        Write-Host "  1. Go to: http://localhost:1337/admin"
        Write-Host "  2. Create admin account (first-time setup)"
        Write-Host "  3. Go to Content Manager"
        Write-Host "  4. Check Collection Types and Single Types"
        Write-Host "  5. Types should appear automatically`n"
        Write-Host "  If they don't appear:"
        Write-Host "  - Restart Strapi: Ctrl+C, then 'npm run develop'"
        Write-Host "  - Refresh the admin panel"
        Write-Host "  - Types should now be visible`n"
        Write-Host "Then seed data:"
        Write-Host "  npm run seed`n"
    }

    "2" {
        Write-Host "`n🔐 Option 2: API Token Registration`n"
        Write-Host "Step 1: Create API Token"
        Write-Host "  Go to: http://localhost:1337/admin"
        Write-Host "  Settings → API Tokens → Create new API Token"
        Write-Host "  Name: 'Content Type Registration'"
        Write-Host "  Type: 'Full access'"
        Write-Host "  Click Save and copy the token`n"
        
        Write-Host "Step 2: Set environment variable"
        $token = Read-Host "Paste your API token (or press Enter to skip)"
        
        if ($token) {
            Write-Host "`nSetting STRAPI_API_TOKEN..."
            $env:STRAPI_API_TOKEN = $token
            Write-Host "✅ Token set: $($token.Substring(0, 10))...`n"
            
            Write-Host "Step 3: Run registration"
            $runNow = Read-Host "Run registration now? (y/n)"
            if ($runNow -eq "y") {
                Write-Host "`n⏳ Running registration...`n"
                npm run register-types:improved
            } else {
                Write-Host "`nTo run later, use:`n"
                Write-Host "  `$env:STRAPI_API_TOKEN = '$token'"
                Write-Host "  npm run register-types:improved`n"
            }
        } else {
            Write-Host "Skipped. To use token later:`n"
            Write-Host "  1. Create token in Strapi admin"
            Write-Host "  2. Run: `$env:STRAPI_API_TOKEN = 'your-token-here'"
            Write-Host "  3. Run: npm run register-types:improved`n"
        }
    }

    "3" {
        Write-Host "`n🧪 Option 3: Improved Hybrid Script`n"
        Write-Host "This script is smart:"
        Write-Host "  ✅ Works with OR without API token"
        Write-Host "  ✅ Checks if types already exist"
        Write-Host "  ✅ Gracefully handles auth errors"
        Write-Host "  ✅ Provides helpful guidance`n"
        
        $runNow = Read-Host "Run improved script now? (y/n)"
        if ($runNow -eq "y") {
            Write-Host "`n⏳ Running improved registration...`n"
            npm run register-types:improved
            
            Write-Host "`nWould you like to seed sample data? (y/n)"
            $seed = Read-Host
            if ($seed -eq "y") {
                npm run seed
            }
        } else {
            Write-Host "Run it later with: npm run register-types:improved`n"
        }
    }

    "4" {
        Write-Host "`n📖 Troubleshooting Guide`n"
        Write-Host "Open this file: scripts/TROUBLESHOOTING_401.md`n"
        Write-Host "Or view at: https://github.com/glad-labs/glad-labs-website`n"
        Write-Host "Key solutions:"
        Write-Host "  - Auto-discovery (simplest)"
        Write-Host "  - API token registration (automated)"
        Write-Host "  - Improved script (hybrid approach)`n"
        
        $openFile = Read-Host "Open guide in VS Code? (y/n)"
        if ($openFile -eq "y") {
            code scripts/TROUBLESHOOTING_401.md
        }
    }

    "5" {
        Write-Host "`n🔑 API Token Generation Steps`n"
        Write-Host "Step 1: Open Strapi Admin"
        Write-Host "   Go to: http://localhost:1337/admin`n"
        
        Write-Host "Step 2: Navigate to API Tokens"
        Write-Host "   Click Settings (gear icon)"
        Write-Host "   Click 'API Tokens'`n"
        
        Write-Host "Step 3: Create New Token"
        Write-Host "   Click 'Create new API Token'"
        Write-Host "   Name: Content Type Registration"
        Write-Host "   Type: Full access"
        Write-Host "   Click 'Save'`n"
        
        Write-Host "Step 4: Copy Token"
        Write-Host "   Token appears once!"
        Write-Host "   Copy it immediately (button appears)"
        Write-Host "   Format: 1234567890abcdef...`n"
        
        Write-Host "Step 5: Use Token"
        Write-Host "   In PowerShell:"
        Write-Host "   `$env:STRAPI_API_TOKEN = 'your-token-here'"
        Write-Host "   npm run register-types:improved`n"
    }

    "6" {
        Write-Host "`nGoodbye!`n"
        exit
    }

    default {
        Write-Host "`n❌ Invalid choice. Please run the script again.`n"
    }
}

Write-Host "`n╔════════════════════════════════════════════════════════╗"
Write-Host "║  Need more help? See TROUBLESHOOTING_401.md            ║"
Write-Host "╚════════════════════════════════════════════════════════╝`n"
