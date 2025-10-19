# GLAD Labs Strapi - Railway Deployment Checklist

## ✅ Quick Fix for "Unknown dialect" Error

The error happens because `DATABASE_CLIENT` environment variable is not set or is empty in Railway.

### Step 1: Add PostgreSQL Plugin to Railway

1. Go to your Strapi service in Railway dashboard
2. Click "Add Plugin"
3. Select "PostgreSQL"
4. Railway will automatically create a database and set `DATABASE_URL`

### Step 2: Set Environment Variables in Railway Dashboard

In your Strapi service settings, go to **Variables** tab and add these:

```
DATABASE_CLIENT=postgres
HOST=0.0.0.0
PORT=1337
APP_KEYS=KVGQqa6VwePvks8tdkaH5w==,6ElFgh2NCH5u9jmoYCw4IQ==,SlMzleUfkELcbW2KbZNPxg==,5cHGp1K3ysmzSStnGJbHzw==
API_TOKEN_SALT=pwO5ldCP1ANUUcVu8EUzEg==
ADMIN_JWT_SECRET=nHC6Rtek+16MnucJ9WdUew==
TRANSFER_TOKEN_SALT=ATDiyx4XmcSfMwT4SqESEQ==
JWT_SECRET=u+q3dyJ0qDkmdu2Al58iWg==
STRAPI_TELEMETRY_DISABLED=true
```

**IMPORTANT:** Do NOT set `DATABASE_URL` manually - Railway automatically injects it when you add the PostgreSQL plugin.

### Step 3: Deploy

```bash
git push github main
```

Railway will auto-redeploy and should now:

1. Read `DATABASE_CLIENT=postgres` ✅
2. Auto-detect PostgreSQL from `DATABASE_URL` ✅
3. Start successfully ✅

---

## 🔍 What the Fix Does

Your updated `config/database.js` now:

1. **Auto-detects database type** from `DATABASE_URL` if `DATABASE_CLIENT` is empty
2. **Falls back to SQLite** if no database is configured
3. **Validates the dialect** to prevent "Unknown dialect" errors
4. **Provides helpful error messages** if something goes wrong

---

## ✅ Verification Checklist

After deploying to Railway, verify:

- [ ] Railway PostgreSQL plugin is added
- [ ] `DATABASE_CLIENT=postgres` is set in Variables
- [ ] Container starts without "Unknown dialect" error
- [ ] Strapi logs show "Strapi started successfully"
- [ ] Admin panel loads at `https://your-railway-app.railway.app/admin`

---

## 📝 Railway Variables (Copy-Paste Ready)

Go to Railway Dashboard → Your Strapi Service → Variables tab and paste each line:

```
DATABASE_CLIENT=postgres
HOST=0.0.0.0
PORT=1337
APP_KEYS=KVGQqa6VwePvks8tdkaH5w==,6ElFgh2NCH5u9jmoYCw4IQ==,SlMzleUfkELcbW2KbZNPxg==,5cHGp1K3ysmzSStnGJbHzw==
API_TOKEN_SALT=pwO5ldCP1ANUUcVu8EUzEg==
ADMIN_JWT_SECRET=nHC6Rtek+16MnucJ9WdUew==
TRANSFER_TOKEN_SALT=ATDiyx4XmcSfMwT4SqESEQ==
JWT_SECRET=u+q3dyJ0qDkmdu2Al58iWg==
STRAPI_TELEMETRY_DISABLED=true
```

---

## 🆘 Still Getting "Unknown dialect"?

If it still fails, your `DATABASE_URL` might not be set by Railway. Try this workaround:

In Railway Variables, manually set:

```
DATABASE_HOST=your-postgresql-host
DATABASE_PORT=5432
DATABASE_NAME=your-database-name
DATABASE_USERNAME=postgres
DATABASE_PASSWORD=your-password
DATABASE_CLIENT=postgres
```

(You'll find these values in Railway PostgreSQL plugin details)

---

## 🚀 Next Steps

1. ✅ Add PostgreSQL plugin to Railway
2. ✅ Set `DATABASE_CLIENT=postgres` in Variables
3. ✅ Push this updated code: `git push github main`
4. ✅ Monitor Railway logs for successful startup
5. ✅ Visit admin panel at your Railway URL + `/admin`

Let me know once you set `DATABASE_CLIENT=postgres` in Railway and I'll help troubleshoot if needed!
