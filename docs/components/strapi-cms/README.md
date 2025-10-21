# 💾 Strapi CMS (Headless)

> Headless content management system powering the GLAD Labs platform

## 📍 Location

- **Source**: `cms/strapi-main/`
- **Main Entry**: `cms/strapi-main/README.md` (component-level)
- **Component Docs**: This folder (`docs/components/strapi-cms/`)

---

## 📚 Documentation

### Setup & Configuration

- See `README.md` in `cms/strapi-main/` for local development

### Database Configuration

- **`config/database.ts`** - PostgreSQL setup
- **`config/server.ts`** - Server configuration
- **`.env.example`** - Environment variables template

---

## 🎯 Key Features

- **Strapi v5** - Modern headless CMS
- **PostgreSQL Database** - Production-grade data storage
- **RESTful API** - Full REST API with automatic documentation
- **Role-Based Access** - Fine-grained permission control
- **Content Types** - Flexible schema definition
- **Media Management** - Asset uploading and optimization
- **Webhooks** - Event-driven integrations
- **Cloud Ready** - Railway deployment support

---

## 📂 Content Types

### Core Content Collections

1. **Posts** (`api/post/`)
   - Title, slug, excerpt, content
   - Featured image, author
   - Categories, tags
   - Publication date, featured flag
   - SEO metadata

2. **Categories** (`api/category/`)
   - Name, slug, description
   - Posts relation

3. **Tags** (`api/tag/`)
   - Name, slug, description
   - Posts relation

4. **Authors** (`api/author/`)
   - Name, email, bio
   - Posts relation

5. **Single Types** (One per site)
   - **About** - Company/site information
   - **Privacy Policy** - Privacy terms
   - **Terms of Service** - Terms
   - **Contact** - Contact information

### Metrics & Analytics

- **Content Metrics** (`api/content-metric/`)
  - Track content performance
  - Views, engagement, conversions

---

## 📂 Folder Structure

```
cms/strapi-main/
├── README.md                    ← Component README
├── .env                         ← Environment config
├── package.json                 ← Node dependencies
├── tsconfig.json               ← TypeScript config
├── config/
│   ├── database.ts             ← Database (PostgreSQL)
│   ├── server.ts               ← Server settings
│   ├── api.ts                  ← API configuration
│   ├── plugins.ts              ← Plugins setup
│   └── middlewares.ts          ← Custom middleware
├── src/
│   ├── index.ts                ← Entry point
│   ├── admin/                  ← Admin panel customization
│   ├── api/                    ← Content type APIs
│   │   ├── post/               ← Posts content type
│   │   │   ├── content-types/
│   │   │   │   └── post/schema.json
│   │   │   ├── controllers/
│   │   │   │   └── post.ts
│   │   │   ├── routes/
│   │   │   │   └── post.ts
│   │   │   └── services/
│   │   │       └── post.ts
│   │   ├── category/           ← Categories
│   │   ├── tag/                ← Tags
│   │   ├── about/              ← About (Single Type)
│   │   ├── privacy-policy/     ← Privacy (Single Type)
│   │   └── [other types]
│   ├── components/             ← Reusable components
│   │   ├── shared/
│   │   │   └── seo.json        ← SEO component
│   │   └── team/
│   │       └── team-member.json
│   ├── extensions/             ← Plugin extensions
│   └── middlewares/            ← Custom middleware
├── database/                   ← Database migrations
├── public/                     ← Static files
│   ├── robots.txt
│   └── uploads/               ← Media uploads
├── scripts/
│   ├── seed-data.js           ← Seed sample data
│   ├── create-admin.js        ← Admin user setup
│   └── reset-admin.js         ← Reset admin password
└── types/
    └── generated/             ← Auto-generated types
        ├── components.d.ts
        └── contentTypes.d.ts
```

---

## 🔗 Integration Points

### Database Configuration

**PostgreSQL** (via Railway in production):

```typescript
// config/database.ts
export default ({ env }) => ({
  connection: {
    client: 'postgres',
    connection: {
      host: env('DATABASE_HOST', 'localhost'),
      port: env.int('DATABASE_PORT', 5432),
      database: env('DATABASE_NAME', 'strapi'),
      user: env('DATABASE_USERNAME', 'strapi'),
      password: env('DATABASE_PASSWORD'),
      ssl: env.bool('DATABASE_SSL', false),
    },
  },
});
```

### API Endpoints

**Base URL**: `http://localhost:1337`

Available endpoints:

- `GET /api/posts` - Get all posts
- `GET /api/posts?populate=*` - Get posts with relations
- `GET /api/posts/:id` - Get single post
- `POST /api/posts` - Create post (auth required)
- `PUT /api/posts/:id` - Update post (auth required)
- `DELETE /api/posts/:id` - Delete post (auth required)

Similar patterns for all content types.

### Frontend Integration

**Query Pattern** (from `lib/api.js`):

```javascript
const url = `${process.env.NEXT_PUBLIC_STRAPI_API_URL}/api/posts?populate=*&sort[publishedAt]=desc`;
const response = await fetch(url, {
  headers: { Authorization: `Bearer ${token}` },
});
```

---

## 🚀 Development Workflow

### Local Development

```bash
# Install dependencies
cd cms/strapi-main
npm install

# Start dev server
npm run develop

# Admin panel
# Open: http://localhost:1337/admin
```

### Database Setup

```bash
# Create admin user
npm run setup

# Seed sample data
npm run seed

# Reset admin password
npm run reset-admin
```

---

## 🔑 Environment Variables

Required in `.env`:

```bash
# Database
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=strapi
DATABASE_USERNAME=strapi
DATABASE_PASSWORD=<password>
DATABASE_SSL=false

# Admin
ADMIN_JWT_SECRET=<random-secret>
API_TOKEN_SALT=<random-secret>

# Server
HOST=0.0.0.0
PORT=1337
APP_KEYS=<comma-separated-secrets>
NODE_ENV=development

# Cloud Storage (Optional)
STRAPI_PLUGIN_UPLOAD_PROVIDER=cloudinary
STRAPI_PLUGIN_UPLOAD_PROVIDER_KEY=<key>
```

---

## 🐳 Docker Deployment

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build
EXPOSE 1337
CMD ["npm", "run", "start"]
```

### Railway Deployment

1. Connect repository to Railway
2. Set environment variables in Railway dashboard
3. Configure PostgreSQL plugin
4. Deploy automatically on push

---

## 📊 Content Seeding

Populate with sample data:

```bash
npm run seed-data
```

Creates:

- Sample posts with categories and tags
- Author profiles
- Metadata and metrics
- About, Privacy, Terms pages

---

## 🔐 Security

### API Authentication

```bash
# Create API token in admin panel
# Settings → API Tokens → Create new token

# Use in requests
curl -H "Authorization: Bearer <token>" \
  http://localhost:1337/api/posts
```

### Middleware

- **CORS** - Configured for frontend origins
- **HTTPS Redirect** - Force HTTPS in production
- **Security Headers** - X-Frame-Options, X-Content-Type-Options

---

## 📋 Related Documentation

**In this component docs:**

- Setup: See `README.md` in `cms/strapi-main/`

**In main docs hub:**

- CMS Architecture: `docs/02-ARCHITECTURE_AND_DESIGN.md#cms-layer`
- Strapi Integration: `docs/guides/STRAPI_BACKED_PAGES_GUIDE.md`
- Content Setup: `docs/guides/CONTENT_POPULATION_GUIDE.md`
- Deployment: `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`

---

## ✅ Quick Links

- **Development**: Local setup in `cms/strapi-main/README.md`
- **Admin Panel**: http://localhost:1337/admin
- **API Docs**: http://localhost:1337/documentation
- **Architecture**: `docs/02-ARCHITECTURE_AND_DESIGN.md`
- **Deployment**: `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`
