# 🌐 Public Site (Next.js)

> Frontend application for public-facing content delivery

## 📍 Location

- **Source**: `web/public-site/`
- **Main Entry**: `web/public-site/README.md` (component-level)
- **Component Docs**: This folder (`docs/components/public-site/`)

---

## 📚 Documentation

### Quick Links

- **Deployment**: See [Operations-Maintenance.md](../../05-Operations/Operations-Maintenance.md) for deployment process
- **Architecture**: See [System-Design.md](../../02-Architecture/System-Design.md) for system design
- **Troubleshooting**: See [troubleshooting/](./troubleshooting/) for common issues
- **Source Code**: See `web/public-site/README.md` for detailed implementation

### Configuration

- **`.env.example`** - Environment variables template
- **`vercel.json`** - Vercel deployment configuration
- **`tailwind.config.js`** - Tailwind CSS configuration
- **`jest.config.js`** - Testing configuration

---

## 🎯 Key Features

- **Next.js 15** - React framework with server-side rendering and API routes
- **Markdown-based Content** - Static markdown files stored in git for versioning and offline availability
- **PostgreSQL Fallback** - Dynamic content via FastAPI endpoints when needed (posts, metadata, analytics)
- **ISR Support** - Incremental Static Regeneration for fresh content without full rebuilds
- **Blog System** - Posts, categories, tags with full-text search via PostgreSQL backend
- **Responsive Design** - Mobile-first TailwindCSS styling for all screen sizes
- **SEO Optimized** - Meta tags, structured data, sitemap generation for search engines

---

## 🧪 Testing

```bash
# Run all tests
npm test

# Run specific tests
npm test -- components/__tests__/PostCard.test.js

# Run with coverage
npm test -- --coverage
```

**Test Files:**

- `components/__tests__/PostCard.test.js` - 39 tests
- `components/__tests__/Pagination.test.js` - 31 tests
- `lib/__tests__/api.test.js` - 25 tests
- `components/Header.test.js` - ✅ Passing
- `components/Footer.test.js` - ✅ Passing
- `components/Layout.test.js` - ✅ Passing
- `components/PostList.test.js` - ✅ Passing

**Total**: 100+ tests passing ✅

---

## 📂 Folder Structure

```
web/public-site/
├── README.md                    ← Component README
├── app/                         ← Next.js 15 app router
│   ├── page.js                ← Homepage
│   ├── layout.js              ← Root layout (wraps all pages in <main>)
│   ├── about/page.js          ← About page
│   ├── posts/[slug]/page.tsx  ← Post detail page
│   ├── category/[slug]/page.tsx ← Category archive
│   ├── tag/[slug]/page.tsx    ← Tag archive
│   ├── archive/[page]/page.tsx ← Paginated archive
│   ├── author/[id]/page.tsx   ← Author page
│   └── legal/layout.tsx       ← Legal pages layout
├── components/                ← React components
│   ├── __tests__/             ← Component tests
│   ├── PostCard.js            ← Blog post card
│   ├── Pagination.js          ← Pagination component
│   ├── TopNav.js              ← Header navigation
│   ├── Footer.js              ← Footer
│   └── [other components]
├── lib/                       ← Utilities
│   ├── api.js                 ← FastAPI client
│   └── __tests__/
│       └── api.test.js        ← API tests
└── public/                    ← Static assets
```

---

## 🔗 Integration Points

### FastAPI Integration

**API Client**: `lib/api.js`

Key functions:

- `getPaginatedPosts()` - Fetch blog posts with pagination
- `getFeaturedPost()` - Get featured blog post
- `getPostBySlug()` - Get single post by slug
- `getCategories()` - Fetch all categories
- `getTags()` - Fetch all tags

### Environment Variables

Required in `web/public-site/.env.local`:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SITE_URL=http://localhost:3000
```

---

## 🚀 Development Workflow

### Local Development

```bash
# Start Next.js dev server
npm run dev

# Run tests
npm test

# Build for production
npm run build

# Start production server
npm start
```

### Deployment to Vercel

```bash
# Environment variables set in Vercel dashboard
# NEXT_PUBLIC_API_BASE_URL
# NEXT_PUBLIC_SITE_URL

git push origin main  # Auto-deploys to Vercel
```

---

## 📋 Related Documentation

**In this component docs:**

- Setup: See `README.md` in `web/public-site/`
- Deployment: See `DEPLOYMENT_READINESS.md` (this folder)
- Vercel config: See `VERCEL_DEPLOYMENT.md` (this folder)

**In main docs hub:**

- Frontend Architecture: `docs/02-Architecture/System-Design.md`
- Testing Guide: `docs/04-Development/Testing-Guide.md`
- Deployment: `docs/05-Operations/Operations-Maintenance.md`

---

## ✅ Quick Links

- **Development**: Local setup in `web/public-site/README.md`
- **Deployment**: `VERCEL_DEPLOYMENT.md`
- **Readiness**: `DEPLOYMENT_READINESS.md`
- **Testing**: `docs/04-Development/Testing-Guide.md`
- **Architecture**: `docs/02-Architecture/System-Design.md`
