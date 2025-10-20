# Railway CLI Complete Setup Guide for GLAD Labs Strapi

## Project Overview

**Project Name:** glad-labs-strapi  
**Components:**

- Strapi v5.27.0 (Headless CMS)
- PostgreSQL (Database)
- Node.js 18+

**What's Included:**

- 7 Content Types (post, category, tag, author, about, content-metric, privacy-policy)
- Auto-generated REST API
- User permissions management
- Local file uploads

---

## Prerequisites

1. **Railway Account** - https://railway.app (sign up if needed)
2. **Railway CLI** - Install with: `npm install -g @railway/cli`
3. **Node.js 18+** - For running Strapi locally
4. **Git** - For version control

---

## Step 1: Install Railway CLI

### Windows (PowerShell)

```powershell
npm install -g @railway/cli
```

### macOS/Linux (Bash)

```bash
npm install -g @railway/cli
```

### Verify Installation

```bash
railway --version
```

---

## Step 2: Login to Railway

```bash
railway login
```

This opens a browser to authenticate with your Railway account.

---

## Step 3: Create New Project

Navigate to your Strapi project directory:

```bash
cd cms/strapi-v5-backend
```

Initialize a new Railway project:

```bash
railway init --name glad-labs-strapi
```

You'll be prompted to select or create a project. Choose "Create new project".

---

## Step 4: Add PostgreSQL Plugin

Railway automatically provisions databases when you add plugins:

```bash
railway add --plugin postgres
```

This creates a PostgreSQL database and automatically sets `DATABASE_URL` environment variable.

---

## Step 5: Configure Environment Variables

Set all required environment variables:

```bash
# Database Configuration (Auto-detected from DATABASE_URL)
railway variables set DATABASE_CLIENT=postgres

# Server Configuration
railway variables set HOST=0.0.0.0
railway variables set PORT=1337

# Security Keys (Generate new ones for production!)
railway variables set APP_KEYS="KVGQqa6VwePvks8tdkaH5w==,6ElFgh2NCH5u9jmoYCw4IQ==,SlMzleUfkELcbW2KbZNPxg==,5cHGp1K3ysmzSStnGJbHzw=="
railway variables set API_TOKEN_SALT="pwO5ldCP1ANUUcVu8EUzEg=="
railway variables set ADMIN_JWT_SECRET="nHC6Rtek+16MnucJ9WdUew=="
railway variables set TRANSFER_TOKEN_SALT="ATDiyx4XmcSfMwT4SqESEQ=="
railway variables set JWT_SECRET="u+q3dyJ0qDkmdu2Al58iWg=="

# Optional Settings
railway variables set STRAPI_TELEMETRY_DISABLED=true
```

### View All Variables

```bash
railway variables
```

---

## Step 6: Deploy to Railway

### Initial Deployment (Production Build)

```bash
railway deploy
```

This will:

1. Build Strapi (npm run build)
2. Install production dependencies (npm install --omit=dev)
3. Start Strapi server
4. Set up PostgreSQL connection

### Monitor Deployment

```bash
railway logs
```

### Watch Logs in Real-time

```bash
railway logs --follow
```

---

## Step 7: Verify Deployment

### Get Service URL

```bash
railway domain
```

This shows your Strapi URL: `https://your-project.railway.app`

### Test Admin Panel

```bash
railway open
```

Opens `https://your-project.railway.app/admin` in your browser

### Test API Endpoint

```bash
curl https://your-project.railway.app/api/posts
```

---

## Step 8: Create First Admin User

After deployment, you need to create your first admin user.

### Option 1: Via Admin Panel

1. Visit `https://your-project.railway.app/admin`
2. Follow the first-time setup wizard
3. Create your admin account

### Option 2: Via SSH/Remote Console

```bash
railway shell
npm run console
```

Then in the console:

```javascript
await strapi.db.query('admin::user').create({
  username: 'admin',
  email: 'admin@example.com',
  password: 'SecurePassword123!',
  isActive: true,
  roles: [1],
});
```

---

## Project Structure

```
cms/strapi-v5-backend/
├── config/
│   ├── database.js           # Database configuration (auto-detects PostgreSQL)
│   ├── server.js             # Server settings
│   ├── admin.js              # Admin panel config
│   ├── middlewares.js        # Middleware pipeline
│   └── plugins.js            # Plugin configuration
├── src/
│   ├── api/                  # Content types
│   │   ├── post/             # Post content type
│   │   ├── category/         # Category content type
│   │   ├── tag/              # Tag content type
│   │   ├── author/           # Author content type
│   │   ├── about/            # About page
│   │   ├── content-metric/   # Metrics tracking
│   │   └── privacy-policy/   # Privacy policy
│   ├── components/           # Reusable components
│   │   ├── shared/
│   │   │   └── seo.json      # SEO fields
│   │   └── team/
│   │       └── team-member.json
│   └── extensions/           # Plugin extensions
├── railway.json              # Railway deployment config
├── package.json              # Dependencies
└── README.md                 # Documentation
```

---

## Key Files Explained

### railway.json

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "buildCommand": "npm install --omit=dev && npm run build"
  },
  "deploy": {
    "startCommand": "npm run start",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

- **buildCommand**: Installs dependencies and builds Strapi
- **startCommand**: Starts Strapi in production mode
- **restartPolicy**: Auto-restarts on failure (up to 10 times)

### config/database.js

Automatically:

- Detects PostgreSQL from `DATABASE_URL`
- Falls back to SQLite if no database
- Validates database dialect
- Configures connection pooling

---

## Common Railway CLI Commands

```bash
# View project info
railway status

# View all environment variables
railway variables

# Set a variable
railway variables set KEY=value

# Unset a variable
railway variables unset KEY

# View logs
railway logs --follow

# Open service in browser
railway open

# SSH into container
railway shell

# Execute command in container
railway run "npm run console"

# View project dashboard (web)
railway dashboard

# List all services in project
railway services

# Delete service
railway remove --service <service-name>

# Logs for specific service
railway logs --service <service-name>

# Get resource metrics (CPU, memory)
railway monitor
```

---

## Troubleshooting

### "Unknown dialect" Error

**Cause**: DATABASE_CLIENT not set to "postgres"  
**Fix**:

```bash
railway variables set DATABASE_CLIENT=postgres
railway deploy
```

### Connection Refused to Database

**Cause**: PostgreSQL plugin not added  
**Fix**:

```bash
railway add --plugin postgres
```

### Admin Panel Shows White Page

**Cause**: Strapi build errors  
**Fix**:

```bash
railway logs --follow  # Check for errors
railway deploy         # Redeploy
```

### "Strapi started successfully" but still can't access

**Cause**: Service not ready  
**Fix**:

```bash
railway logs --follow  # Wait for "Server responded with 200"
```

---

## Production Checklist

- [ ] Change APP_KEYS to production values
- [ ] Update ADMIN_JWT_SECRET to production value
- [ ] Set up custom domain (Railway Dashboard → Settings)
- [ ] Enable HTTPS (automatic with Railway)
- [ ] Configure backups (Railway PostgreSQL → Backups)
- [ ] Monitor resource usage (railway monitor)
- [ ] Set up alerts (Railway Dashboard → Alerts)
- [ ] Document first admin credentials
- [ ] Test all content type APIs
- [ ] Verify file uploads work

---

## Next Steps

1. **Deploy Next.js Public Site to Vercel**

   ```bash
   cd web/public-site
   vercel deploy
   ```

2. **Deploy React Oversight Hub to Vercel**

   ```bash
   cd web/oversight-hub
   vercel deploy
   ```

3. **Deploy Python Cofounder to Railway**

   ```bash
   cd src/cofounder_agent
   railway add --name cofounder
   railway deploy
   ```

4. **Set up API Gateway** (optional)
   - Point all services to shared domain

---

## Support & Resources

- **Railway Docs**: https://docs.railway.app
- **Strapi Docs**: https://docs.strapi.io
- **PostgreSQL Docs**: https://www.postgresql.org/docs/

---

## Cost Estimate (Hybrid Strategy)

| Service   | Component        | Monthly Cost     |
| --------- | ---------------- | ---------------- |
| Railway   | Strapi Starter   | $5-10            |
| Railway   | PostgreSQL 1GB   | $15              |
| Railway   | Python Cofounder | $5-10            |
| Vercel    | Next.js (Free)   | $0               |
| Vercel    | React Hub (Free) | $0               |
| **TOTAL** |                  | **$25-35/month** |

**Scales to**: 100K+ monthly active users before needing upgrades
