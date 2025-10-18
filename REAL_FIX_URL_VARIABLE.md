# 🚨 CRITICAL: The REAL Reason for Your Cookie Error

## What Your Logs Show

```
[2025-10-18 06:02:29.759] error: Failed to create admin refresh session 
Cannot send secure cookie over unencrypted connection
```

This happens **EVERY TIME** you try to login, even after our config fix.

---

## The Real Root Cause

Your **`URL` environment variable is NOT SET** on Railway.

When Strapi doesn't have a URL configured:

```
Strapi defaults to: http://localhost:1337
                          ↓
Session tries to set secure cookie
                          ↓
"Wait, this is HTTP, not HTTPS!"
                          ↓
ERROR: Can't send secure cookie over unencrypted connection ❌
```

---

## ✅ The Fix (Right Now)

### Step 1: Go to Railway Dashboard

https://railway.app → Your Project → Strapi Service → Variables

### Step 2: Check if URL Exists

**Look for a variable named: `URL`**

- If it doesn't exist → Create it
- If it exists but is empty → Set it
- If it exists but is wrong → Fix it

### Step 3: Set URL Value

**Exact value:**
```
https://glad-labs-strapi-v5-backend-production.up.railway.app
```

**OR** use Railway's variable reference:
```
https://${{RAILWAY_PUBLIC_DOMAIN}}
```

### Step 4: Save

Click the **Save** button

### Step 5: Redeploy

Scroll to **Deployments** → Click **Redeploy latest**

Wait 2-3 minutes for build to complete.

### Step 6: Test

Once deployment finishes:

```
https://glad-labs-strapi-v5-backend-production.up.railway.app/admin
```

Try login. **Should work now!** ✅

---

## Why This Works

When URL is set correctly:

```
Strapi knows: "My public URL is https://..."
                    ↓
"So I'm running on HTTPS!"
                    ↓
Session: "Set secure cookie on HTTPS" ✓
                    ↓
Cookie sent with Secure flag ✓
                    ↓
Login succeeds ✅
```

---

## Verify All Required Variables

While you're in the Variables section, make sure you also have:

```
✅ URL = https://glad-labs-strapi-v5-backend-production.up.railway.app
✅ DATABASE_CLIENT = postgres
✅ ADMIN_JWT_SECRET = (auto-generated value)
✅ APP_KEYS = (auto-generated value)
✅ API_TOKEN_SALT = (auto-generated value)
✅ TRANSFER_TOKEN_SALT = (auto-generated value)
```

If any are missing, add them!

---

## Quick Check

To verify URL is set, run:

```bash
railway shell
echo $URL
```

Should output:
```
https://glad-labs-strapi-v5-backend-production.up.railway.app
```

If it shows nothing, that's your problem!

---

## Timeline to Success

```
NOW:   Set URL on Railway dashboard
+30s:  Click Redeploy
+1m:   Build starting
+2m:   Build in progress
+3m:   ✅ "Strapi fully loaded"
+4m:   Test /admin login
+5m:   ✅ SUCCESS!
```

---

## 🎯 TL;DR

**Your URL variable is missing. That's why login fails.**

1. Go to Railway dashboard
2. Add: `URL=https://glad-labs-strapi-v5-backend-production.up.railway.app`
3. Click Redeploy
4. Test login in 3 minutes
5. ✅ Works!

**That's it. Do this now and you're done!**
