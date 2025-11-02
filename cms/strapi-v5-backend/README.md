# 🗄️ Strapi v5 CMS Backend

![Strapi](https://img.shields.io/badge/CMS-Strapi_v5-2F2E8B)
![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-336791)
![TypeScript](https://img.shields.io/badge/Language-TypeScript-3178c6)

Headless content management system serving GLAD Labs content and data infrastructure.

**Status:** ✅ Production Ready  
**Version:** 5.0  
**Last Updated:** October 26, 2025  
**Technology:** Strapi v5 + PostgreSQL + TypeScript

---

## 📖 Overview

The Strapi v5 CMS backend provides:

- **Headless API:** RESTful endpoints for all content types
- **Content Management:** User-friendly admin interface
- **Media Management:** Upload and organize images, videos, files
- **Relational Data:** Posts, Categories, Tags, Pages, and Custom Collections
- **TypeScript Plugins:** Custom business logic and extensions
- **Database:** PostgreSQL for production, SQLite for development

**Content served to:**
- Public Site (Next.js) - https://example.com
- Oversight Hub (React) - https://admin.example.com/dashboard
- AI Agents (FastAPI) - For content publishing and metadata

---

## 🚀 Quick Start

### Prerequisites

- Node.js 20.x+
- npm 10+
- PostgreSQL 13+ (production) or SQLite (development)

### Local Development

```bash
# Navigate to CMS directory
cd cms/strapi-v5-backend

# Install dependencies
npm install

# Configure database (see section below)
cp .env.example .env
# Edit .env with your database credentials

# Build Strapi
npm run build

# Start development server
npm run develop
```

Access Strapi Admin at: **http://localhost:1337/admin**

### First-Time Setup

1. Navigate to http://localhost:1337/admin
2. Create admin account (email, username, password)
3. Strapi will create default database tables
4. Create API Token:
   - Settings → API Tokens → Create new API Token
   - Name: `Next.js Public Site`
   - Type: `Full access` (development) or scoped (production)
   - Copy token and add to frontend `.env` files

---

## 🏗️ Architecture

### Content Types

```text
Content Types (Collections)
├── Posts
│   ├── title, slug, content
│   ├── excerpt, featured_image, cover_image
│   ├── category (single relation)
│   ├── tags (many relation)
│   ├── author, published_at
│   ├── seo_title, seo_description, seo_keywords
│   └── status (draft, published, archived)
│
├── Categories
│   ├── name, slug, description
│   ├── featured_image
│   ├── meta_description
│   └── posts (reverse relation)
│
├── Tags
│   ├── name, slug, description
│   ├── color, icon
│   └── posts (reverse relation)
│
├── Pages
│   ├── title, slug, content
│   ├── featured_image
│   ├── seo_title, seo_description
│   └── visibility (public, draft, archived)
│
└── Tasks (Custom)
    ├── title, description
    ├── type (content_generation, etc)
    ├── status (pending, in-progress, completed, failed)
    ├── assigned_agents (JSON)
    ├── result_data (JSON)
    ├── error_message
    └── timestamps (created_at, updated_at, completed_at)
```

### API Endpoints

**Base URL:** `http://localhost:1337/api` (development) or `https://cms.railway.app/api` (production)

**Content Endpoints:**

```bash
# Posts
GET    /posts                      # List all posts with pagination
GET    /posts/:id                  # Get single post by ID
POST   /posts                      # Create new post (requires auth)
PUT    /posts/:id                  # Update post (requires auth)
DELETE /posts/:id                  # Delete post (requires auth)

# Categories
GET    /categories                 # List all categories
GET    /categories/:id             # Get single category
POST   /categories                 # Create category (requires auth)

# Tags
GET    /tags                       # List all tags
GET    /tags/:id                   # Get single tag

# Pages
GET    /pages                      # List all pages
GET    /pages/:slug               # Get page by slug

# Tasks
POST   /tasks                      # Create content task
GET    /tasks/:id                  # Get task status
PUT    /tasks/:id                  # Update task status
```

**Query Parameters:**

```bash
# Pagination
?pagination[page]=1&pagination[pageSize]=25

# Filtering
?filters[status][$eq]=published
?filters[category][id][$eq]=1

# Population (relations)
?populate[category]=true
?populate[tags]=true
?populate[author]=true

# Sorting
?sort[0]=published_at:desc
?sort[0]=title:asc
```

**Examples:**

```bash
# Get 10 published posts with categories
curl "http://localhost:1337/api/posts?pagination[pageSize]=10&filters[status][$eq]=published&populate[category]=true"

# Search posts by slug
curl "http://localhost:1337/api/posts?filters[slug][$eq]=my-post-slug"
```

---

## ⚙️ Configuration

### Environment Variables (.env)

```bash
# Database
DATABASE_CLIENT=postgres                              # postgres or sqlite
DATABASE_HOST=localhost                               # DB hostname
DATABASE_PORT=5432                                    # DB port
DATABASE_NAME=glad_labs                               # DB name
DATABASE_USERNAME=postgres                            # DB username
DATABASE_PASSWORD=your-password                       # DB password

# Strapi
HOST=0.0.0.0                                          # Server host
PORT=1337                                             # Server port
APP_KEYS=key1,key2,key3,key4                          # Encryption keys (generate random)
API_TOKEN_SALT=your-random-salt                       # API token salt (generate random)
ADMIN_JWT_SECRET=your-secret-here                     # JWT secret (generate random)
JWT_SECRET=your-jwt-secret                            # JWT secret (generate random)

# Environment
NODE_ENV=development                                  # development or production
DEBUG=true                                            # Enable debug logging

# Frontend
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337      # Frontend Strapi URL
```

### Generate Secure Keys

```bash
# Generate random secure strings
openssl rand -base64 32           # For app keys and secrets
openssl rand -base64 16           # For salt values

# Or in PowerShell
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Minimum 0 -Maximum 256 }))
```

### Database Setup

**PostgreSQL (Production):**

```bash
# Create database
createdb glad_labs

# Update .env
DATABASE_CLIENT=postgres
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs
DATABASE_USERNAME=postgres
DATABASE_PASSWORD=your-password

# Start Strapi (creates tables automatically)
npm run develop
```

**SQLite (Development):**

```bash
# Create .tmp directory
mkdir -p .tmp

# Set in .env
DATABASE_CLIENT=sqlite

# Strapi creates database automatically
npm run develop
```

---

## 🔧 Development

### Project Structure

```text
cms/strapi-v5-backend/
├── src/
│   ├── admin/                     # Strapi admin customizations
│   ├── api/                       # Content type definitions
│   │   ├── post/
│   │   │   ├── controllers/
│   │   │   ├── routes/
│   │   │   ├── services/
│   │   │   ├── policies/
│   │   │   └── models/
│   │   ├── category/
│   │   ├── tag/
│   │   ├── page/
│   │   └── task/
│   ├── components/                # Reusable field components
│   ├── config/                    # Configuration files
│   │   ├── admin.ts              # Admin panel config
│   │   ├── api.ts                # API config
│   │   ├── database.ts           # Database config
│   │   ├── logger.ts             # Logging config
│   │   ├── plugins.ts            # Plugin setup
│   │   ├── server.ts             # Server config
│   │   └── middlewares/          # Custom middlewares
│   ├── extensions/                # Strapi plugins extensions
│   ├── middleware/                # Express middlewares
│   └── index.ts                   # Entry point
├── database/                       # Database migrations and seeds
├── public/                         # Static files
├── types/                          # TypeScript type definitions
├── package.json
├── tsconfig.json
└── .env
```

### Scripts

```bash
# Development
npm run develop                     # Start with auto-reload
npm run develop -- --watch         # Watch mode

# Production
npm run build                       # Build for production
npm run start                       # Start production server
npm run start:prod                  # Start with production flag

# Database
npm run migrations:run              # Run pending migrations
npm run migrations:rollback         # Rollback last migration
npm run seed                        # Run database seeders

# Utilities
npm run seeds                       # Manage database seeds
npm run policies                    # Generate policies
npm run services                    # Generate services

# Testing
npm run test                        # Run Jest tests
npm test -- --coverage             # With coverage report
npm test -- --watch                # Watch mode
```

### Creating New Content Types

1. **Via Admin Interface:**
   - Settings → Content-Types Builder
   - Click "Create new collection type"
   - Add fields (text, image, relation, etc)
   - Save and publish

2. **Via Code (TypeScript):**
   ```typescript
   // src/api/my-collection/models/my-collection.ts
   export default {
     attributes: {
       title: { type: 'string', required: true },
       description: { type: 'richtext' },
       featured_image: { type: 'media' },
     },
   };
   ```

### Custom Plugins

Example: Add a middleware for logging API calls

```typescript
// src/config/middlewares.ts
export default [
  'strapi::errors',
  'strapi::security',
  'strapi::cors',
  'strapi::poweredBy',
  'strapi::logger',
  'strapi::query',
  'strapi::body',
  'strapi::session',
  'strapi::favicon',
  'strapi::public',
  {
    name: 'custom::logging',
    config: {
      enabled: true,
    },
  },
];
```

---

## 🚀 Deployment

### Railway Deployment (Recommended)

**Option 1: Use Railway Template (Fastest)**

1. Visit: [Railway Strapi Template](https://railway.com/template/strapi)
2. Click "Deploy Now"
3. Connect GitHub and select repository
4. Railway will create PostgreSQL database automatically
5. Configure environment variables
6. Deploy

**Option 2: Manual Railway Deployment**

```bash
# Install Railway CLI
npm install -g railway

# Login
railway login

# Deploy
cd cms/strapi-v5-backend
railway up

# Set environment variables in Railway dashboard
# Add DATABASE_URL (auto-created if using Railway template)
# Add other secrets (APP_KEYS, ADMIN_JWT_SECRET, etc)
```

### Docker Deployment

```dockerfile
# Dockerfile (in project root or cms/strapi-v5-backend/)
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --only=production

COPY . .
RUN npm run build

EXPOSE 1337

CMD ["npm", "run", "start"]
```

```bash
# Build and run
docker build -t glad-labs-strapi:latest .
docker run -p 1337:1337 \
  -e DATABASE_URL=postgresql://user:pass@host/db \
  -e ADMIN_JWT_SECRET=your-secret \
  glad-labs-strapi:latest
```

### Environment Variables (Production)

Add to Railway/hosting provider secrets:

```bash
# Database (typically auto-created by Railway)
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Strapi secrets (generate secure values)
APP_KEYS=key1,key2,key3,key4
API_TOKEN_SALT=random-salt-value
ADMIN_JWT_SECRET=secure-secret-here
JWT_SECRET=secure-jwt-here

# Environment
NODE_ENV=production
DEBUG=false

# API URLs
NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
```

---

## 🔐 Security

### API Authentication

All write operations require authentication:

```bash
# Create API Token
1. Admin Panel → Settings → API Tokens → Create new API Token
2. Name it (e.g., "Next.js Public Site")
3. Choose scope (Full access for dev, scoped for production)
4. Copy token

# Use in requests
curl -H "Authorization: Bearer YOUR_TOKEN" \
     -X POST http://localhost:1337/api/posts \
     -H "Content-Type: application/json" \
     -d '{"data": {"title": "New Post"}}'
```

### Role-Based Access Control

- **Admin:** Full access to CMS
- **Authenticated:** Can create/edit own content
- **Public:** Read-only access (configured per endpoint)

### CORS Configuration

**For development (all origins):**

```typescript
// config/middlewares.ts
{
  name: 'strapi::cors',
  config: {
    enabled: true,
    origin: ['*'],
    methods: ['GET', 'POST', 'PUT', 'DELETE'],
  },
}
```

**For production (restricted origins):**

```typescript
{
  name: 'strapi::cors',
  config: {
    enabled: true,
    origin: [
      'https://example.com',
      'https://admin.example.com',
      'https://api.example.com',
    ],
    methods: ['GET', 'POST', 'PUT', 'DELETE'],
  },
}
```

---

## 🐛 Troubleshooting

### Issue: Admin Won't Load

**Symptom:** Blank screen or 404 at `/admin`

**Solution:**

```bash
cd cms/strapi-v5-backend

# Clear cache
rm -rf .cache build node_modules

# Rebuild
npm install
npm run build
npm run develop
```

### Issue: Database Connection Error

**Symptom:** `Error: Connection refused to localhost:5432`

**Solution:**

```bash
# Check PostgreSQL is running
psql -U postgres -l

# If not running, start it
# Windows: services.msc (search PostgreSQL)
# macOS: brew services start postgresql
# Linux: sudo systemctl start postgresql

# Or use SQLite for development
# Edit .env: DATABASE_CLIENT=sqlite
```

### Issue: API Endpoint 404

**Symptom:** `POST /api/posts` returns 404

**Solution:**

```bash
# 1. Verify content type is published
# Admin → Content-Type Builder → Post → Publish

# 2. Check Routes are registered
# Admin → Settings → Roles & Permissions → Public

# 3. Restart Strapi
npm run develop
```

### Issue: Media Upload Fails

**Symptom:** File upload returns 413 or timeout

**Solution:**

```bash
# Increase file size limits in config/server.ts
const config = {
  http: {
    maxFileSize: 250 * 1024 * 1024,  // 250MB
  },
};

# Or use cloud storage (S3, Google Cloud Storage)
# See plugin configuration in src/config/plugins.ts
```

---

## 📚 Useful Resources

- **Strapi Documentation:** https://docs.strapi.io
- **Strapi API Reference:** https://docs.strapi.io/dev-docs/api
- **Content Type Builder:** https://docs.strapi.io/user-docs/content-manager/content-types-builder
- **Database Guide:** https://docs.strapi.io/dev-docs/setup-deployment-guides/databases

---

## 📞 Support & Issues

- **Logs:** `npm run develop` output in terminal
- **Debug Mode:** Add `DEBUG=true` to `.env`
- **Admin Logs:** Admin Panel → Plugins → Logs

---

**Maintained by:** GLAD Labs Development Team  
**Last Updated:** October 26, 2025  
**Status:** ✅ Production Ready
