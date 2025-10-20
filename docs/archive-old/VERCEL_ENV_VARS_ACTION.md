# URGENT: Set Vercel Environment Variables Now

## Build Status

❌ **FAILED** - Still getting "Unauthorized" errors

## Why It's Failing

Even though we fixed the Strapi API URL in `.env.local`, **Vercel doesn't have the authentication token**.

Vercel environment is separate from your local `.env.local` file. Local files can't be deployed to Vercel.

## Immediate Action Required

### Step 1: Go to Vercel Dashboard

https://vercel.com/dashboard

### Step 2: Select Project

Click on: **glad-labs-website**

### Step 3: Navigate to Settings

- Click **Settings** (top menu)
- Click **Environment Variables** (left sidebar)

### Step 4: Add Two Variables

**COPY-PASTE THESE EXACTLY:**

#### Variable 1: API Token

- **Name:** `STRAPI_API_TOKEN`
- **Value:**

```
f96a8db7330483b6395666c96369a7a5b97214c734cda9ea958ce1edc97b43ea59cd46bef60a1fc82dbb38acfeb43a900b1b72010e9521978a76a6adaa302f70a2b0b67838b354785eaa8dab3c81111f21d2d2fda7c6c24d82707096e9f47aefe3b6e321b175d6a0cce19de9418eb71b0687a152c8f614b72781101ad1867c4b
```

- **Environment:** Select **Production** (checkbox only)
- Click **Save**

#### Variable 2: API URL

- **Name:** `NEXT_PUBLIC_STRAPI_API_URL`
- **Value:**

```
https://glad-labs-strapi-v5-backend-production.up.railway.app
```

- **Environment:** Select **All** (all checkboxes)
- Click **Save**

### Step 5: Redeploy

Go to **Deployments** tab → Find the failed build → Click **Redeploy**

## Expected Result

After redeploy, build should:

```
✓ Compiled successfully in 4.6s
✓ Collecting page data ...
✓ Generating static pages (POST) (123/123)
✓ Finalizing deployment
```

## Why This Works

| Environment         | API URL                 | Token           |
| ------------------- | ----------------------- | --------------- |
| Local (npm run dev) | `.env.local`            | `.env.local`    |
| Vercel Build        | Vercel env vars         | Vercel env vars |
| Browser             | Uses API URL from build | N/A             |

Vercel needs **both** because:

- It runs `getStaticProps()` during build
- Needs to authenticate with Strapi (token)
- Needs to know which Strapi instance (URL)

## Verification Checklist

- [ ] Logged into Vercel Dashboard
- [ ] Found glad-labs-website project
- [ ] Added `STRAPI_API_TOKEN` (Production only)
- [ ] Added `NEXT_PUBLIC_STRAPI_API_URL` (All environments)
- [ ] Clicked Redeploy on failed build
- [ ] Watched build logs for success

## Support

If build still fails:

1. Check variable names spelling (case-sensitive)
2. Verify token value (very long string, no spaces)
3. Check environment visibility (token = Production, URL = All)
4. Wait 2 minutes for Vercel to propagate changes
5. Try redeploy again

**This is the final step to get your site live!**
