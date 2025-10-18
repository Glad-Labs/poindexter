# Railway DATABASE_PRIVATE_URL Setup Guide

## Why Use DATABASE_PRIVATE_URL?

Railway charges for **egress** (data leaving your service). When you use the public `DATABASE_URL`, traffic to your PostgreSQL database crosses the public internet and incurs egress fees.

**`DATABASE_PRIVATE_URL`** uses Railway's internal private network (no internet traffic) → **FREE** ✅

**Cost Impact:**
- With `DATABASE_URL`: ~$0.10 per GB of database traffic
- With `DATABASE_PRIVATE_URL`: $0 (internal network)

---

## How to Set Up DATABASE_PRIVATE_URL

### Step 1: Find Your DATABASE_PRIVATE_URL on Railway

1. Go to [railway.app](https://railway.app)
2. Navigate to your **Project** → **strapi-production** service
3. Click on the **PostgreSQL** database in the "Services" list
4. Go to the **"Variables"** tab
5. Look for `DATABASE_PRIVATE_URL` in the list
6. Copy the full connection string (looks like `postgresql://user:pass@postgres:5432/railway`)

### Step 2: Add DATABASE_PRIVATE_URL to Strapi Service Environment Variables

1. Go to your **strapi-production** service in Railway
2. Click the **"Variables"** tab
3. Click **"+ New Variable"**
4. Set the following:
   ```
   Variable: DATABASE_PRIVATE_URL
   Value: [paste the connection string from Step 1]
   ```
5. Click **"Add"** to save

### Step 3: Deploy

The Strapi service will automatically restart with the new configuration.

Your `database.ts` already prefers `DATABASE_PRIVATE_URL` if it exists, with automatic fallback to `DATABASE_URL`.

---

## How It Works (Technical Details)

### The Configuration Logic

Your `database.ts` now uses this priority:

```typescript
connectionString: env(
  'DATABASE_PRIVATE_URL',      // Try private URL first (FREE)
  env('DATABASE_URL')          // Fall back to public URL if needed
),
```

**If `DATABASE_PRIVATE_URL` is set:** 
- Strapi uses the private connection ✅
- Traffic stays within Railway's internal network
- No egress charges

**If `DATABASE_PRIVATE_URL` is missing:**
- Strapi falls back to `DATABASE_URL` 
- Still works, but may incur egress fees ⚠️

### Railway Network Architecture

```
┌─────────────────────────────────────────┐
│         Railway Project Network         │
│  (Internal - no egress charges)         │
├──────────────┬──────────────────────────┤
│  strapi      │     PostgreSQL           │
│  service     │     database             │
│              │                          │
│ Uses:        │     (Private IP)         │
│ PRIVATE_URL  │     postgres:5432        │
│              │                          │
│ (Free ✅)    ├──────────────────────────┤
│              │   Public IP for backup/  │
│              │   external access only   │
└──────────────┴──────────────────────────┘
```

---

## Verification Steps

### Check If PRIVATE_URL Is Being Used

1. **View Strapi Logs:**
   ```bash
   railway logs --service strapi-production
   ```
   Look for database connection messages (should show internal connection)

2. **Verify In Railway UI:**
   - Go to strapi service → Variables
   - Confirm both `DATABASE_PRIVATE_URL` and `DATABASE_CLIENT=postgres` are set

3. **Monitor Network Traffic:**
   - Railway Dashboard → Project → Analytics
   - Egress should decrease significantly if PRIVATE_URL is working

---

## Troubleshooting

### Issue: "Host name resolution failed"

**Cause:** `DATABASE_PRIVATE_URL` hostname (`postgres`) doesn't resolve outside Railway

**Solution:** Make sure you're using the exact value from Railway's PostgreSQL service variables, not modifying it

### Issue: Connection timeout errors

**Cause:** Might be using wrong connection string

**Solution:**
1. Double-check `DATABASE_PRIVATE_URL` matches PostgreSQL service's value
2. Verify `DATABASE_CLIENT=postgres` is set in Strapi service
3. Ensure `pg` package is in `package.json`: `npm list pg`

### Issue: Strapi still using public URL

**Cause:** Railway hasn't restarted the service after variable changes

**Solution:**
1. Go to Strapi service
2. Click **"Redeploy"** button
3. Wait for healthcheck to pass (~2 minutes)

---

## Environment Variable Summary

| Variable | Value | Purpose | Egress Cost |
|----------|-------|---------|-------------|
| `DATABASE_CLIENT` | `postgres` | Tell Strapi to use PostgreSQL driver | N/A |
| `DATABASE_PRIVATE_URL` | From PostgreSQL service vars | Internal private network connection | ✅ FREE |
| `DATABASE_URL` | From PostgreSQL service vars | Public connection (fallback) | ⚠️ Charged |

**Set both to maximize savings while maintaining fallback compatibility.**

---

## Related Configuration

Your Strapi configuration already supports:
- ✅ Multiple database clients (PostgreSQL, MySQL, SQLite)
- ✅ Connection pooling (min: 2, max: 10 connections)
- ✅ SSL/TLS for secure connections
- ✅ Automatic fallback from PRIVATE to PUBLIC URL

---

## More Resources

- [Railway Private URLs Documentation](https://docs.railway.app/guides/private-networking)
- [Strapi Database Configuration](https://docs.strapi.io/dev-docs/configurations/database)
- [PostgreSQL Connection Pooling Best Practices](https://www.postgresql.org/docs/current/runtime-config-connection.html)

---

## Summary

✅ **What you did:**
1. Updated `database.ts` to prioritize `DATABASE_PRIVATE_URL`
2. Configured Railway PostgreSQL's `DATABASE_PRIVATE_URL` variable

✅ **What you get:**
- Free internal network traffic (no egress charges)
- Automatic fallback to public URL if needed
- Same performance (actually slightly faster due to lower latency)
- Cost savings: ~$0.10/GB saved per month

🚀 **Next steps:**
1. Deploy your changes to Railway
2. Verify connection in Railway logs
3. Monitor analytics dashboard for reduced egress
