# 📚 GLAD Labs - Consolidated Master Documentation

> **Last Updated:** October 18, 2025 | **Status:** Production Ready v3.0
>
> This is the single source of truth for all GLAD Labs documentation. All other docs should reference this index.

---

## 🎯 Quick Navigation by Role

### 👨‍💼 **Project Manager / Executive**

- [Executive Summary](#executive-summary) - 2 min read
- [System Status](#system-status) - Current state
- [Architecture Overview](./03-TECHNICAL_DESIGN.md) - How it all works

### 🚀 **New Developer**

- [Quick Start Guide](#quick-start-in-5-minutes) - Get running locally
- [Local Development Setup](./guides/LOCAL_SETUP_GUIDE.md) - Detailed setup
- [Architecture](./03-TECHNICAL_DESIGN.md) - Understand the system

### 🔧 **DevOps / Infrastructure**

- [Deployment Checklist](#deployment-checklist) - Ready to deploy?
- [Vercel Deployment](./VERCEL_DEPLOYMENT_GUIDE.md) - Frontend deployment
- [Railway Deployment](./guides/RAILWAY_DEPLOYMENT_COMPLETE.md) - Backend deployment
- [Environment Variables](./ENV_VARS_REFERENCE.md) - All env vars

### 👨‍💻 **Backend Developer (Strapi)**

- [Strapi Setup Guide](#strapi-setup) - Content management
- [Content Types](#content-types) - Data model
- [Local Dev Workflow](./STRAPI_LOCAL_DEV_WORKFLOW.md) - Development
- [API Reference](#api-endpoints) - GraphQL endpoints

### 🎨 **Frontend Developer (Next.js)**

- [Frontend Setup](./web/public-site/README.md) - Next.js config
- [API Integration](#api-endpoints) - Connecting to Strapi
- [Vercel Deployment](./VERCEL_DEPLOYMENT_GUIDE.md) - Deploy frontend

### 🤖 **AI/Agent Developer (Python)**

- [Agent Architecture](./src/cofounder_agent/README.md) - AI systems
- [Agents Setup](./src/agents/) - Agent configuration
- [AI Integration](#ai-integration) - How agents work together

---

## 📊 Executive Summary

### What is GLAD Labs?

GLAD Labs is an **AI-powered business co-founder system** combining:

- 📝 **Autonomous Content Creation** - Strapi CMS manages all content
- 🤖 **AI Co-Founder Agent** - Python-based intelligent orchestration
- 💼 **Business Intelligence** - Real-time dashboards and analytics
- 🌐 **Multi-Platform Web** - Next.js public site + admin dashboard
- 🔄 **Full CI/CD Pipeline** - Vercel (frontend) + Railway (backend)

### Current Status

| Component           | Status   | URL                                     | Last Update |
| ------------------- | -------- | --------------------------------------- | ----------- |
| **Public Website**  | ✅ Live  | TBD                                     | Today       |
| **Strapi CMS**      | ✅ Live  | `strapi-production-b234.up.railway.app` | Today       |
| **Admin Dashboard** | ✅ Ready | TBD                                     | Today       |
| **AI Co-Founder**   | ✅ Ready | TBD                                     | Today       |
| **CI/CD Pipeline**  | ✅ Ready | GitHub → Vercel/Railway                 | Today       |

### Key Metrics

- **Lines of Code:** 50K+ (Python, TypeScript, JavaScript)
- **Microservices:** 5 (Frontend, Backend CMS, Admin, AI Agent, Functions)
- **Content Types:** 7 (Post, Category, Tag, Author, About, Privacy Policy, ContentMetric)
- **Database:** PostgreSQL on Railway
- **API:** GraphQL + REST

---

## 🚀 Quick Start in 5 Minutes

### Prerequisites

```bash
# Verify you have these
node --version    # Should be 18+
python --version  # Should be 3.12+
git --version
```

### Installation

```bash
# 1. Clone repository
git clone <repo-url>
cd glad-labs-website

# 2. Install all dependencies
npm run setup:all

# 3. Start all services
npm run dev
```

### Access Everything

| Service       | URL                         | Purpose            |
| ------------- | --------------------------- | ------------------ |
| Public Site   | http://localhost:3000       | Next.js website    |
| Oversight Hub | http://localhost:3001       | React admin        |
| Strapi CMS    | http://localhost:1337/admin | Content management |
| AI Co-Founder | http://localhost:8000       | Python API         |

### First Steps

1. ✅ All services should be running
2. 🔓 Go to `http://localhost:1337/admin` and create admin account
3. 📝 Create a test Post in Strapi
4. 🌐 Visit `http://localhost:3000` to see it live
5. 🎉 You're ready to develop!

---

## 📋 Deployment Checklist

### Pre-Deployment (Local)

- [ ] Run tests: `npm test`
- [ ] Lint check: `npm run lint`
- [ ] Build locally: `npm run build`
- [ ] Check no errors in console

### Strapi Deployment (Railway)

1. **Create Railway Project**
   - Connect GitHub
   - Select `cms/strapi-v5-backend` directory

2. **Add PostgreSQL**
   - Railway will auto-provision
   - Variables automatically injected

3. **Verify Content Types**
   - Go to `https://strapi-production-[id].up.railway.app/admin`
   - Check all 7 content types exist

4. **Redeploy if Needed**
   - Push changes to main branch
   - Railway auto-deploys
   - Or manually click "Redeploy"

### Frontend Deployment (Vercel)

1. **Connect Repository**
   - Go to Vercel
   - Import `web/public-site` directory

2. **Set Environment Variables**

   ```
   NEXT_PUBLIC_STRAPI_API_URL=https://strapi-production-[id].up.railway.app
   ```

3. **Deploy**
   - Vercel auto-deploys on push
   - Or manually trigger deployment

4. **Verify**
   - Check home page loads
   - Verify posts display
   - Test navigation

### Post-Deployment

- [ ] Test frontend connectivity to Strapi
- [ ] Verify all content displays correctly
- [ ] Check SEO meta tags
- [ ] Monitor error logs

---

## 🏗️ System Architecture

### Project Structure

```
glad-labs-website/
├── web/                          # Frontend applications
│   ├── public-site/              # Next.js public website
│   └── oversight-hub/            # React admin dashboard
├── cms/
│   └── strapi-v5-backend/        # Headless CMS (Railway)
├── src/
│   ├── agents/                   # Specialized AI agents
│   ├── cofounder_agent/          # Main AI orchestrator
│   └── mcp/                      # Model Context Protocol
├── cloud-functions/
│   └── intervene-trigger/        # GCP serverless functions
├── docs/                         # Consolidated documentation
└── scripts/                      # Setup and deployment scripts
```

### Technology Stack

| Layer          | Technology       | Purpose                           |
| -------------- | ---------------- | --------------------------------- |
| **Frontend**   | Next.js 15       | Public website & content delivery |
| **Admin UI**   | React 18         | Admin dashboard                   |
| **CMS**        | Strapi v5        | Content management                |
| **Database**   | PostgreSQL       | Primary data store (Railway)      |
| **Backend**    | Python 3.12      | AI agents & orchestration         |
| **API**        | GraphQL + REST   | Content API                       |
| **Deployment** | Vercel + Railway | Cloud hosting                     |
| **CI/CD**      | GitHub Actions   | Automated testing & deployment    |

---

## ⚙️ Configuration & Environment

### Strapi Configuration Files

**Location:** `cms/strapi-v5-backend/config/`

| File             | Purpose                                      |
| ---------------- | -------------------------------------------- |
| `admin.ts`       | Admin panel settings (cookie security, auth) |
| `server.ts`      | Server configuration (host, port, proxy)     |
| `database.ts`    | Database connections (PostgreSQL, SQLite)    |
| `api.ts`         | API routes and middleware                    |
| `plugins.ts`     | Plugin configuration                         |
| `middlewares.ts` | Middleware setup (CORS, security)            |

### Key Configuration Settings

**Admin Cookie Configuration** (`config/admin.ts`)

```typescript
cookie: {
  secure: false,  // Railway proxy handles SSL wrapping
  httpOnly: true,
  sameSite: 'lax',
}
```

**Database Configuration** (`config/database.ts`)

```typescript
// Local development: SQLite (automatic)
// Production: PostgreSQL (Railway-provided DATABASE_URL)
```

**Server Configuration** (`config/server.ts`)

```typescript
proxy: true,  // Trust X-Forwarded-* headers from Railway proxy
```

---

## 🎯 Content Types

All content types stored in: `cms/strapi-v5-backend/src/api/*/content-types/*/schema.json`

### Content Types Overview

| Type               | Purpose            | Fields                                                                   |
| ------------------ | ------------------ | ------------------------------------------------------------------------ |
| **Post**           | Blog articles/news | title, slug, content, excerpt, coverImage, date, category, tags, metrics |
| **Category**       | Blog categories    | name, slug, description, posts                                           |
| **Tag**            | Content tags       | name, slug, posts                                                        |
| **Author**         | Content creators   | name, email, bio, posts                                                  |
| **About**          | About page content | title, content, teamMembers                                              |
| **Privacy Policy** | Legal content      | title, content, lastUpdated                                              |
| **ContentMetric**  | Analytics data     | views, clicks, shares, engagementScore                                   |

### Creating/Managing Content Types

**Local Development:**

1. Start Strapi: `npm run develop`
2. Go to `http://localhost:1337/admin`
3. Settings → Content-Type Builder
4. Create or edit content types
5. Commit changes (schema files auto-generated)

**Production Update:**

1. Create/edit content types locally
2. Commit to Git
3. Push to main branch
4. Railway automatically rebuilds with new schema

---

## 🔌 API Endpoints

### GraphQL API

**Base URL:** `https://strapi-production-[id].up.railway.app/graphql`

**Example Query:**

```graphql
query getPosts {
  posts {
    id
    title
    slug
    content
    excerpt
    date
    category {
      name
    }
    tags {
      name
    }
  }
}
```

### REST API

**Base URL:** `https://strapi-production-[id].up.railway.app/api`

**Endpoints:**

```
GET  /api/posts              # List all posts
GET  /api/posts/:id          # Get specific post
POST /api/posts              # Create post (with auth token)
PUT  /api/posts/:id          # Update post (with auth token)
DELETE /api/posts/:id        # Delete post (with auth token)

# Same for: categories, tags, authors, about, privacy-policy, content-metrics
```

### Authentication

**API Token:**

- Generate in Strapi Admin: Settings → API Tokens → Create new token
- Use in requests: `Authorization: Bearer YOUR_TOKEN_HERE`

**Required for:**

- Creating/updating content
- Accessing protected endpoints
- Webhooks and automation

---

## 🌐 Frontend Integration

### Next.js Configuration

**Strapi URL:**

```bash
# .env.local
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337         # Local
NEXT_PUBLIC_STRAPI_API_URL=https://strapi-production...  # Production
```

**Fetching Content:**

```typescript
// pages/posts.tsx
const { data } = await fetch(
  `${process.env.NEXT_PUBLIC_STRAPI_API_URL}/api/posts`
);
```

### Available Pages

- `/` - Homepage
- `/posts` - Blog archive
- `/posts/[slug]` - Individual post
- `/about` - About page
- `/privacy` - Privacy policy
- `/admin` - Admin dashboard

---

## 🤖 AI Integration

### AI Co-Founder Agent

**Location:** `src/cofounder_agent/`

**Features:**

- Autonomous content analysis
- Business insights generation
- Market opportunity identification
- Performance metrics tracking
- Voice interface support

**Starting the Agent:**

```bash
python -m uvicorn cofounder_agent.main:app --reload
```

### Specialized Agents

Located in `src/agents/`:

- **Compliance Agent** - Regulatory analysis
- **Content Agent** - Content generation
- **Financial Agent** - Financial modeling
- **Market Insight Agent** - Market research

### MCP Integration

**Model Context Protocol** enables:

- AI agents to access external tools
- Unified interface for agent communication
- Extensible architecture for new integrations

---

## 🔍 Development Workflow

### Local Development

```bash
# Install dependencies
npm run setup:all

# Start all services
npm run dev

# Run tests
npm test

# Build for production
npm run build

# Format code
npm run format

# Lint check
npm run lint
```

### Git Workflow

```bash
# Create feature branch
git checkout -b feature/your-feature

# Make changes
# ...

# Commit
git add .
git commit -m "feat: description"

# Push to develop
git push origin develop

# Create Pull Request on GitHub
# After review & approval, merge to main
```

### Deployment

```bash
# Push to main triggers:
# 1. GitHub Actions CI/CD
# 2. Tests run automatically
# 3. Vercel builds & deploys frontend
# 4. Railway rebuilds & deploys backend
# 5. All live in ~5 minutes

git push origin main
```

---

## 🚨 Troubleshooting

### Strapi Issues

**Admin login fails:**

- Check `secure: false` in `config/admin.ts`
- Verify `NODE_ENV` setting
- Clear Railway cache and redeploy

**Content types not loading:**

- Verify `npm run build` succeeds locally
- Check `src/api/*/content-types/` exists
- Ensure Procfile is present
- Redeploy Railway with cache cleared

**API returns 401 Unauthorized:**

- Check API token is correct
- Verify token has right permissions
- Generate new token if needed

### Vercel Issues

**Build fails:**

- Check `NEXT_PUBLIC_STRAPI_API_URL` is set
- Verify Strapi is accessible: `curl $NEXT_PUBLIC_STRAPI_API_URL/api`
- Check build logs in Vercel dashboard

**Content not displaying:**

- Verify API URL is correct
- Check browser console for fetch errors
- Confirm posts exist in Strapi

### Railway Issues

**Deployment fails:**

- Check build logs: `railway logs --service strapi-production`
- Clear build cache and retry
- Verify `Procfile` exists
- Check environment variables

---

## 📞 Support & Resources

### Documentation Files

Quick reference to key docs:

| Issue           | Document                                                 | Location        |
| --------------- | -------------------------------------------------------- | --------------- |
| Deploy frontend | [Vercel Guide](./VERCEL_DEPLOYMENT_GUIDE.md)             | `/docs/`        |
| Deploy backend  | [Railway Guide](./guides/RAILWAY_DEPLOYMENT_COMPLETE.md) | `/docs/guides/` |
| Local setup     | [Setup Guide](./01-SETUP_GUIDE.md)                       | `/docs/`        |
| Architecture    | [Technical Design](./03-TECHNICAL_DESIGN.md)             | `/docs/`        |
| Cookie issues   | [Cookie Fix](./FINAL_COOKIE_FIX.md)                      | `/docs/`        |
| Strapi workflow | [Local Dev](./STRAPI_LOCAL_DEV_WORKFLOW.md)              | `/docs/`        |

### External Resources

- [Strapi Documentation](https://docs.strapi.io/)
- [Next.js Documentation](https://nextjs.org/docs)
- [Railway Documentation](https://docs.railway.app/)
- [Vercel Documentation](https://vercel.com/docs)
- [Python Agents](./src/cofounder_agent/README.md)

---

## ✅ Checklist for New Team Members

- [ ] Clone repository
- [ ] Install Node.js 18+ and Python 3.12+
- [ ] Run `npm run setup:all`
- [ ] Run `npm run dev` to start all services
- [ ] Access http://localhost:3000
- [ ] Create Strapi admin account
- [ ] Create test post in Strapi
- [ ] Verify it appears on homepage
- [ ] Read [Architecture](./03-TECHNICAL_DESIGN.md)
- [ ] Understand your role (Frontend/Backend/AI/DevOps)
- [ ] Review relevant guides above

---

## 🎯 Next Steps

1. **Immediate:** Redeploy Railway if you made Strapi config changes
2. **Today:** Verify all services are accessible (Strapi, Vercel, admin)
3. **This Week:** Create sample content and test full pipeline
4. **Later:** Add webhooks, automate content publishing, integrate AI

---

## 📝 Version History

| Date       | Version | Changes                                                                   |
| ---------- | ------- | ------------------------------------------------------------------------- |
| 2025-10-18 | 3.0     | Consolidated master documentation, simplified config, fixed cookie issues |
| 2025-10-17 | 2.9     | Fixed admin cookie security, added Railway template comparison            |
| 2025-10-14 | 2.8     | Complete infrastructure setup, Vercel + Railway working                   |
| Earlier    | 1.0-2.7 | Initial setup and development                                             |

---

**Last Updated:** 2025-10-18 | **Maintained By:** GLAD Labs Team | **Status:** Production Ready ✅
