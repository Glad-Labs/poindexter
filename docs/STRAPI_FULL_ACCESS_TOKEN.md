# Fix: Generate Full-Access API Token for Strapi

## The Problem

You generated a **read-only** API token, but Strapi GraphQL queries require **full-access** permissions.

Error: Permission denied / Unauthorized

## Solution: Generate Full-Access Token

### Step 1: Log into Strapi Admin
Go to: `https://glad-labs-strapi-v5-backend-production.up.railway.app/admin`

### Step 2: Navigate to API Tokens
1. Click **Settings** (⚙️ icon, bottom left)
2. Click **API Tokens** (left sidebar under "Administration")

### Step 3: Create New Token
1. Click **Create new API token** button
2. Fill in the form:
   - **Name:** `Vercel Production` (or similar)
   - **Description:** `For Vercel build-time data fetching`
   - **Token duration:** `Unlimited` (recommended for production)
   - **Token type:** Select **Full access** (NOT "Read-only")
   
3. Click **Save**

### Step 4: Copy the Token
- A popup will show the full token string
- **Copy the entire token** (it's very long)
- ⚠️ **IMPORTANT:** You can only see this token once! Copy it now.

### Step 5: Update Vercel
1. Go to: https://vercel.com/dashboard/glad-labs-website/settings/environment-variables
2. Find the `STRAPI_API_TOKEN` variable
3. Click the **Edit** button (pencil icon)
4. **Replace the value** with your new full-access token
5. Click **Save**

### Step 6: Delete Old Token (Optional but Recommended)
Back in Strapi admin:
1. Go to **Settings → API Tokens**
2. Find your old read-only token
3. Click the trash icon to delete it
4. This prevents accidental use of the wrong token

### Step 7: Redeploy in Vercel
1. Go to: https://vercel.com/dashboard/glad-labs-website/deployments
2. Find the failed deployment
3. Click the three dots (**...**)
4. Select **Redeploy**

## Why Full-Access is Required

| Token Type | Can Do |
|---|---|
| **Read-only** | View public content (if any) |
| **Full access** | Query all content, execute GraphQL mutations |

Your site needs to query:
- Posts (title, slug, content, etc.)
- Categories (name, slug, posts)
- Tags (name, slug, posts)
- Pages (about, privacy policy)

These require **full-access** permissions even though you're just reading data.

## Expected Build Result

After updating the token and redeploy:

```
✓ Compiled successfully
✓ Collecting page data ...
✓ Generating static pages (archive) (100/100)
✓ Generating static pages (category) (25/25)
✓ Generating static pages (tag) (50/50)
✓ Generating static pages (posts) (75/75)
✓ Finalizing deployment
```

## Troubleshooting

### Still Getting Permission Errors?

1. **Verify you copied the entire token** (it's ~100+ characters)
2. **Check for extra spaces** at beginning/end (common copy-paste mistake)
3. **Wait 2-3 minutes** for Vercel to register the new env var
4. **Try redeploy again**

### Can't Find API Tokens in Strapi?

Make sure you're:
- ✅ Logged in as admin
- ✅ In the correct Strapi instance (Railway production)
- ✅ Clicking Settings (⚙️ icon)
- ✅ Looking in left sidebar for "API Tokens"

### Token Disappeared After Creating?

This is normal - Strapi only shows new tokens once. If you missed it:
1. Delete the token (trash icon)
2. Create a new one
3. Copy it immediately

## Security Note

- ✅ **Safe to use:** Production full-access tokens are fine for build-time access
- ✅ **No exposure:** Token never reaches client browsers (build-only)
- ⚠️ **Keep secure:** Don't commit to Git or share publicly
- ✅ **Rotate regularly:** Consider regenerating quarterly

## Verification

After redeploy succeeds, check:
1. ✅ Homepage loads with recent posts
2. ✅ Archive page shows paginated posts
3. ✅ Category pages display filtered posts
4. ✅ Individual post pages render content
5. ✅ No console errors in browser DevTools
