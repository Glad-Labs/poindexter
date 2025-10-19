# 🚀 GLAD Labs Strapi - Quick Start Railway Deployment (5 minutes)

## Copy-Paste Quick Start

Open PowerShell and run these commands one at a time:

### Step 1: Install Railway CLI
```powershell
npm install -g @railway/cli
railway --version
```

### Step 2: Login to Railway
```powershell
railway login
```
This opens your browser to authenticate.

### Step 3: Create Railway Project
```powershell
cd "C:\Users\mattm\glad-labs-website\cms\strapi-v5-backend"
railway init --name glad-labs-strapi
```

### Step 4: Add PostgreSQL Database
```powershell
railway add --plugin postgres
```

### Step 5: Configure Environment Variables
Copy-paste each line one at a time:

```powershell
railway variables set DATABASE_CLIENT=postgres
railway variables set HOST=0.0.0.0
railway variables set PORT=1337
railway variables set STRAPI_TELEMETRY_DISABLED=true
railway variables set APP_KEYS="KVGQqa6VwePvks8tdkaH5w==,6ElFgh2NCH5u9jmoYCw4IQ==,SlMzleUfkELcbW2KbZNPxg==,5cHGp1K3ysmzSStnGJbHzw=="
railway variables set API_TOKEN_SALT="pwO5ldCP1ANUUcVu8EUzEg=="
railway variables set ADMIN_JWT_SECRET="nHC6Rtek+16MnucJ9WdUew=="
railway variables set TRANSFER_TOKEN_SALT="ATDiyx4XmcSfMwT4SqESEQ=="
railway variables set JWT_SECRET="u+q3dyJ0qDkmdu2Al58iWg=="
```

Verify variables:
```powershell
railway variables
```

### Step 6: Deploy!
```powershell
railway deploy
```

Watch the logs (Ctrl+C to stop):
```powershell
railway logs --follow
```

Wait for: `✔ Strapi started successfully`

### Step 7: Create Admin User
```powershell
railway open
```

Follow the first-time admin setup wizard.

---

## ✅ Verification

Test your deployment:

```powershell
# Get your app URL
railway domain

# Test the API (replace with your domain)
Invoke-WebRequest -Uri "https://your-project.railway.app/api/posts"

# View admin panel
railway open
```

---

## 🆘 If Something Goes Wrong

**View detailed logs:**
```powershell
railway logs --follow
```

**Check environment variables:**
```powershell
railway variables
```

**Redeploy:**
```powershell
railway deploy
```

**Connection issues? Make sure PostgreSQL is added:**
```powershell
railway services
```

Should show both "Strapi" and "PostgreSQL" services.

---

## 📊 What This Costs

- Strapi service: $5-10/month
- PostgreSQL database: $15/month
- **Total: $20-25/month**

(Much cheaper than Heroku or traditional hosting!)

---

## 🎉 You're Done!

Your Strapi backend is now live on Railway:
- Admin panel: `https://your-project.railway.app/admin`
- API: `https://your-project.railway.app/api`

Next: Deploy your frontend apps to Vercel (free tier) for the full stack!

---

## Need More Help?

- Full guide: See `RAILWAY_CLI_SETUP.md`
- Troubleshooting: See `RAILWAY_PROJECT_REVIEW.md`
- Railway docs: https://docs.railway.app
