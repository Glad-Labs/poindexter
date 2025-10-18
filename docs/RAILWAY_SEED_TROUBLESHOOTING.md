# Railway Strapi Seed Script - Troubleshooting Guide

## Error: "Cannot connect to Strapi"

### What This Means
The script connected to the Strapi instance, but authentication failed. This usually happens when:
- ❌ `STRAPI_API_TOKEN` environment variable is not set on Railway
- ❌ The token is invalid or expired
- ❌ The token has insufficient permissions

## Solution

### Step 1: Verify Strapi is Running
Test locally first to ensure the token works:

```bash
cd cms/strapi-v5-backend
export STRAPI_API_TOKEN="your-full-access-token"
node scripts/seed-data.js
```

If this works locally, the token is valid and the issue is Railway-specific.

### Step 2: Set Environment Variable on Railway

1. Go to **Railway Dashboard**: https://railway.app/dashboard
2. Select your **Strapi project**
3. Click the **Strapi service**
4. Go to **Variables** tab
5. Add a new variable:
   - **Key**: `STRAPI_API_TOKEN`
   - **Value**: (your full-access token from Strapi admin)
6. Click **Save** or **Deploy**

### Step 3: Regenerate Token if Needed

If you don't have a valid token:

1. Log into Strapi admin: https://strapi-production-b234.up.railway.app/admin
2. Go to **Settings** → **API Tokens**
3. Create a new token:
   - **Name**: `Railway Seeder`
   - **Token type**: **Full access**
   - **Duration**: **Unlimited**
4. Click **Save**
5. Copy the full token string
6. Set it in Railway Variables (step 2 above)

### Step 4: Run the Seed Script

After setting the environment variable on Railway:

```bash
railway run node scripts/seed-data.js
```

## Alternative: Run Locally and Commit

If Railway environment variables are difficult to manage, you can:

1. **Run locally**:
   ```bash
   export STRAPI_API_TOKEN="your-token"
   cd cms/strapi-v5-backend
   npm run develop
   # Then in another terminal
   node scripts/seed-data.js
   ```

2. **Data syncs to PostgreSQL** on Railway automatically

3. **Commit any generated files** to Git (if needed)

4. **Redeploy Vercel** to fetch the data

## Debugging

If it still doesn't work, check:

```bash
# Verify the token is set on Railway
railway run env | grep STRAPI_API_TOKEN

# Check Strapi is accessible
railway run curl -I https://strapi-production-b234.up.railway.app

# Run script with verbose logging
railway run node -e "console.log('Token:', process.env.STRAPI_API_TOKEN); require('./scripts/seed-data.js')"
```

## Next Steps

After seeding completes successfully:

1. ✅ Verify data in Strapi admin
2. ✅ Redeploy Vercel
3. ✅ Test the Next.js build
4. ✅ Verify posts display on the site
