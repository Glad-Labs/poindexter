# 🔐 Security Mitigation - Phase 1: Secure Admin Access

**Date**: October 21, 2025  
**Path**: Path A - Mitigate Now + Upgrade Q1 2026  
**Status**: Implementation Started

---

## 🎯 Phase 1 Objectives

Implement immediate security hardening for Strapi admin access:

✅ Hide admin path (obfuscation)  
✅ Strengthen authentication  
✅ Disable unnecessary features  
✅ Document all changes  
✅ Test security measures

---

## 📋 Checklist: Phase 1 Actions

### A. Update .env Configuration

**Action 1.1: Update .env (Local Development)**

```bash
# File: .env

# SECURITY: Hide admin path (change from default /admin)
# Use a random, non-obvious path
STRAPI_ADMIN_PATH=/cms-admin-control-panel-v2

# SECURITY: Strong JWT secret (generate new, random, 32+ characters)
ADMIN_JWT_SECRET=your-super-long-random-secret-here-minimum-32-chars-abc123def456xyz

# SECURITY: Disable notifications center (reduces attack surface)
STRAPI_ADMIN_DISABLED_NOTIFICATIONS_CENTER=true

# SECURITY: Production mode
NODE_ENV=production

# SECURITY: Disable development features in production
STRAPI_PREVIEW_RELEASE=false
```

**How to Generate Strong Secret:**

```powershell
# PowerShell command to generate random secret
$randomSecret = -join ((33..126) | Get-Random -Count 32 | ForEach-Object {[char]$_})
Write-Output $randomSecret
```

---

### B. Update Environment Files

**Action 1.2: Update .env.staging**

```bash
# Same as above, with different admin path
STRAPI_ADMIN_PATH=/admin-staging-secure
ADMIN_JWT_SECRET=your-staging-secret-here-32-chars-minimum
STRAPI_ADMIN_DISABLED_NOTIFICATIONS_CENTER=true
NODE_ENV=production
```

**Action 1.3: Update .env.production**

```bash
# Same as above, with different admin path per environment
STRAPI_ADMIN_PATH=/admin-prod-secure-xyz
ADMIN_JWT_SECRET=your-production-secret-here-32-chars-minimum
STRAPI_ADMIN_DISABLED_NOTIFICATIONS_CENTER=true
NODE_ENV=production
```

⚠️ **CRITICAL**: Store secrets in Railway dashboard, NOT in git

---

## 🔐 Security Hardening Steps

### Step 1: Generate Strong Secrets

```powershell
# Generate 3 random secrets (local, staging, production)
$secrets = @()
1..3 | ForEach-Object {
    $secret = -join ((33..126) | Get-Random -Count 32 | ForEach-Object {[char]$_})
    $secrets += $secret
    Write-Output "Secret $_`: $secret"
}
```

### Step 2: Update Local Environment

Edit `.env` file:

```
STRAPI_ADMIN_PATH=/cms-admin-control-panel-v2
ADMIN_JWT_SECRET=[GENERATED_SECRET_1]
STRAPI_ADMIN_DISABLED_NOTIFICATIONS_CENTER=true
NODE_ENV=production
```

### Step 3: Test Locally

```bash
npm run dev

# Navigate to: http://localhost:1337/cms-admin-control-panel-v2
# Old path should NOT work: http://localhost:1337/admin ❌
```

### Step 4: Update Railway Dashboard

For each environment (dev, staging, production):

1. Go to Railway dashboard → Your project
2. Environment variables section
3. Add/update:
   - `STRAPI_ADMIN_PATH=` [hidden path per environment]
   - `ADMIN_JWT_SECRET=` [generated secret]
   - `STRAPI_ADMIN_DISABLED_NOTIFICATIONS_CENTER=true`
   - `NODE_ENV=production`

4. Redeploy service after each change

### Step 5: Verify Changes

```bash
# Local verification
curl http://localhost:1337/admin
# Should return: 404 Not Found ✓

curl http://localhost:1337/cms-admin-control-panel-v2
# Should return: login page ✓

# Staging/Production verification (after deployment)
# Same checks on your deployed URLs
```

---

## 📝 Configuration Reference

### Strapi Security Settings

```javascript
// strapi/config/admin.ts (if customizing further)
export default ({ env }) => ({
  auth: {
    // JWT settings
    secret: env('ADMIN_JWT_SECRET'),
    // Session timeout (milliseconds)
    session: {
      duration: 1000 * 60 * 60 * 24, // 24 hours
    },
  },
  // Disable telemetry
  telemetry: false,
  // Disable notifications
  notifications: {
    center: false,
  },
});
```

---

## ✅ Verification Checklist

After implementing Phase 1, verify:

- [ ] `.env` file updated with new admin path
- [ ] Strong ADMIN_JWT_SECRET generated (32+ chars)
- [ ] Environment variables set in Railway for all 3 environments
- [ ] Local dev tested: old path returns 404
- [ ] Local dev tested: new path shows login
- [ ] Staging redeployed and verified
- [ ] Production redeployed and verified
- [ ] Team notified of new admin paths
- [ ] Passwords changed to strong values
- [ ] No admin paths committed to git

---

## 🚨 Rollback Procedure

If issues occur:

```bash
# 1. Revert to default paths (temporarily)
STRAPI_ADMIN_PATH=/admin

# 2. Stop Strapi
npm run stop

# 3. Clear Strapi cache
rm -r cms/strapi-main/.cache

# 4. Restart
npm run dev

# 5. Contact DevOps team for investigation
```

---

## 🎯 Expected Outcome

✅ Admin panel path hidden (obfuscated)  
✅ Strong authentication credentials  
✅ Reduced attack surface  
✅ Documented security changes  
✅ Team aware of new access procedures

---

## ⏭️ Next Phase

After Phase 1 is complete:
→ Proceed to **Phase 2: Network Restrictions**

---

**Status**: Ready to implement  
**Estimated Time**: 30-45 minutes  
**Difficulty**: Easy  
**Risk**: Very Low (reversible)
