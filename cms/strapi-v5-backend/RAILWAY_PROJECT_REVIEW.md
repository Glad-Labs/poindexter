# GLAD Labs Strapi Backend - Project Review & Railway Deployment

## 📋 Project Review Summary

### ✅ What's Good

1. **Strapi v5.27.0** - Latest stable version with all features
2. **Database Configuration** - Auto-detects PostgreSQL, falls back to SQLite
3. **7 Content Types** - Well-organized API structure
   - Post, Category, Tag, Author, About, Content-Metric, Privacy-Policy
4. **Components** - Reusable SEO and team member components
5. **User Permissions** - Built-in role-based access control
6. **Local File Uploads** - Configured and ready

### ⚠️ Items to Review

1. **Security Keys** - Using same keys for all environments (⚠️ Generate new for production)
2. **PostgreSQL Pool** - Min 2, Max 10 (good for small-medium projects)
3. **File Storage** - Local uploads (consider S3 for production)
4. **CORS** - Check if configured for frontend domains

### Dependencies Analysis

| Package | Version | Purpose | Status |
|---------|---------|---------|--------|
| @strapi/strapi | 5.27.0 | Core CMS | ✅ Current |
| @strapi/plugin-users-permissions | 5.27.0 | Auth/Permissions | ✅ Current |
| @strapi/provider-upload-local | 5.27.0 | File Uploads | ✅ Current |
| pg | 8.8.0 | PostgreSQL Driver | ✅ Current |
| axios | ^1.7.7 | HTTP Client | ✅ Current |
| bcryptjs | ^3.0.2 | Password Hashing | ✅ Current |

---

## 🚀 Railway CLI Deployment Steps

### Phase 1: Prerequisites (5 minutes)

```powershell
# 1. Install Railway CLI globally
npm install -g @railway/cli

# 2. Verify installation
railway --version

# 3. Navigate to Strapi project
cd cms/strapi-v5-backend
```

### Phase 2: Project Setup (10 minutes)

```powershell
# 1. Login to Railway
railway login

# 2. Create new Railway project
railway init --name glad-labs-strapi

# 3. Add PostgreSQL plugin
railway add --plugin postgres
```

### Phase 3: Environment Configuration (5 minutes)

```powershell
# Set all environment variables
railway variables set DATABASE_CLIENT=postgres
railway variables set HOST=0.0.0.0
railway variables set PORT=1337
railway variables set STRAPI_TELEMETRY_DISABLED=true

# Set security keys
railway variables set APP_KEYS="KVGQqa6VwePvks8tdkaH5w==,6ElFgh2NCH5u9jmoYCw4IQ==,SlMzleUfkELcbW2KbZNPxg==,5cHGp1K3ysmzSStnGJbHzw=="
railway variables set API_TOKEN_SALT="pwO5ldCP1ANUUcVu8EUzEg=="
railway variables set ADMIN_JWT_SECRET="nHC6Rtek+16MnucJ9WdUew=="
railway variables set TRANSFER_TOKEN_SALT="ATDiyx4XmcSfMwT4SqESEQ=="
railway variables set JWT_SECRET="u+q3dyJ0qDkmdu2Al58iWg=="
```

### Phase 4: Deployment (3 minutes)

```powershell
# 1. Deploy to Railway
railway deploy

# 2. Monitor logs
railway logs --follow

# 3. Once you see "Strapi started successfully", test
railway open
```

### Phase 5: Post-Deployment (5 minutes)

```powershell
# 1. Get your service URL
railway domain

# 2. Create first admin user via admin panel
# Visit: https://your-project.railway.app/admin

# 3. Test API
Invoke-WebRequest -Uri "https://your-project.railway.app/api/posts"
```

---

## 📊 Content Type Reference

### Post
```
- title (string, required)
- slug (string, unique)
- content (rich text)
- excerpt (string)
- featured_image (media)
- category (relation: many-to-one)
- tags (relation: many-to-many)
- author (relation: many-to-one)
- published (boolean)
- created_at (timestamp)
```

### Category
```
- name (string, required, unique)
- slug (string)
- description (text)
- posts (relation: one-to-many)
```

### Tag
```
- name (string, required, unique)
- slug (string)
- posts (relation: many-to-many)
```

### Author
```
- name (string, required)
- email (string)
- bio (text)
- avatar (media)
- posts (relation: one-to-many)
```

### About
```
- title (string)
- content (rich text)
- team_members (component: array)
```

### Content-Metric
```
- post (relation: many-to-one)
- views (integer)
- likes (integer)
- shares (integer)
- last_viewed (timestamp)
```

### Privacy-Policy
```
- title (string)
- content (rich text)
- last_updated (timestamp)
```

---

## 🔧 Configuration Files Explained

### config/database.js
**Purpose**: Database connection configuration

**Features**:
- Auto-detects PostgreSQL from DATABASE_URL
- Falls back to SQLite if no database configured
- Validates database dialect
- Connection pooling for performance
- SSL support for production

**Environment Variables**:
- `DATABASE_CLIENT` - Type: sqlite|postgres|mysql
- `DATABASE_URL` - Full connection string (PostgreSQL)
- `DATABASE_HOST`, `DATABASE_PORT`, etc. - Individual connection params

### railway.json
**Purpose**: Railway deployment configuration

**Build Step**:
- Installs production dependencies
- Runs `npm run build` to build Strapi

**Deploy Step**:
- Runs `npm run start` to start server
- Auto-restarts on failure (max 10 times)

### config/server.js
**Purpose**: Server configuration

**Settings**:
- HOST: 0.0.0.0 (listen on all interfaces)
- PORT: 1337 (Railway assigns via PORT env var)
- APP_KEYS: Session encryption keys (required for strapi::session middleware)

---

## 🔐 Security Recommendations

### Before Production Deployment

1. **Generate New Security Keys**
   ```bash
   # Generate strong random keys
   node -e "console.log(require('crypto').randomBytes(16).toString('base64'))"
   ```
   
   Update:
   - APP_KEYS (4 values)
   - API_TOKEN_SALT
   - ADMIN_JWT_SECRET
   - TRANSFER_TOKEN_SALT
   - JWT_SECRET

2. **Configure CORS**
   Edit `config/middlewares.js`:
   ```javascript
   {
     name: 'strapi::cors',
     config: {
       origin: ['https://your-frontend-domain.com'],
       methods: ['GET', 'POST', 'PUT', 'DELETE'],
       headers: ['Content-Type', 'Authorization'],
     },
   }
   ```

3. **Enable HTTPS** - Railway does this automatically ✅

4. **Set Up Database Backups** - Railway PostgreSQL plugin includes daily backups ✅

5. **Configure Monitoring** - Set up Railway alerts for:
   - High CPU usage
   - Memory spike
   - Deployment failures

---

## 📈 Monitoring & Maintenance

### View Project Status
```bash
railway status
```

### Monitor Resources
```bash
railway monitor
```

### View Logs
```bash
# Real-time logs
railway logs --follow

# Last 50 lines
railway logs

# Logs for specific service
railway logs --service postgres
```

### Manage Environment Variables
```bash
# View all variables
railway variables

# Set new variable
railway variables set KEY=value

# Unset variable
railway variables unset KEY
```

---

## 🔄 Updating Strapi

If you need to update Strapi version:

```bash
# Locally first
npm update @strapi/strapi @strapi/plugin-users-permissions @strapi/provider-upload-local

# Test locally
npm run dev

# Commit and push
git add -A
git commit -m "chore: update Strapi to latest"
git push

# Railway auto-redeploys
```

---

## 🐛 Troubleshooting Deployment

### Error: "Unknown dialect"
```
Cause: DATABASE_CLIENT not set to "postgres"
Fix: railway variables set DATABASE_CLIENT=postgres
     railway deploy
```

### Error: "Connection refused to database"
```
Cause: PostgreSQL plugin not added
Fix: railway add --plugin postgres
    railway deploy
```

### Error: "Admin panel shows white page"
```
Cause: Strapi build errors (shouldn't happen with v5.27.0)
Fix: railway logs --follow (check for errors)
    railway deploy (redeploy)
```

### Error: "Service crashes immediately"
```
Cause: Missing environment variables or invalid config
Fix: railway logs --follow (read error message)
    railway variables set KEY=value (add missing var)
    railway deploy
```

---

## ✅ Deployment Checklist

- [ ] Railway CLI installed and logged in
- [ ] Railway project created
- [ ] PostgreSQL plugin added
- [ ] DATABASE_CLIENT=postgres set
- [ ] All security keys configured
- [ ] railway.json in project root
- [ ] Code pushed to GitHub
- [ ] First deployment complete
- [ ] Admin panel accessible
- [ ] Create first admin user
- [ ] Test API endpoints
- [ ] Set up custom domain (optional)
- [ ] Configure backups (PostgreSQL plugin)
- [ ] Monitor resources for first week

---

## 📞 Quick Reference

| Task | Command |
|------|---------|
| Login | `railway login` |
| Create project | `railway init --name <name>` |
| Add service | `railway add --plugin <name>` |
| Deploy | `railway deploy` |
| View logs | `railway logs --follow` |
| Environment vars | `railway variables` |
| Open in browser | `railway open` |
| Project status | `railway status` |
| Monitor resources | `railway monitor` |
| SSH into container | `railway shell` |
| View domain | `railway domain` |

---

## 🎉 What's Next

Once Strapi is deployed:

1. **Deploy Frontend Services** (Vercel Free Tier)
   - Next.js Public Site
   - React Oversight Hub

2. **Deploy Python Cofounder** (Railway Starter)
   - Create Python service
   - Connect to shared PostgreSQL

3. **Set Up API Gateway** (Optional)
   - Route all services through single domain
   - Configure load balancing

4. **Monitor & Optimize**
   - Watch resource usage
   - Scale up if needed
   - Optimize queries for performance

---

## Cost Summary

| Service | Cost | Notes |
|---------|------|-------|
| Railway Strapi | $5-10/month | Starts at $5 |
| Railway PostgreSQL | $15/month | 1GB included, auto-backups |
| Railway Python Agent | $5-10/month | Optional, scales as needed |
| Vercel Next.js | $0/month | Free tier |
| Vercel React Hub | $0/month | Free tier |
| **Total** | **$25-35/month** | Scales to 100K+ users |

**Savings vs Heroku**: ~$50-100/month on similar setup
