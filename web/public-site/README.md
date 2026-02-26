# 🌐 Glad Labs Public Site

![Next.js](https://img.shields.io/badge/Framework-Next.js_15-black)
![React](https://img.shields.io/badge/React-18.3-blue)
![Tailwind](https://img.shields.io/badge/Styling-Tailwind_CSS-38B2AC)
![SSG](https://img.shields.io/badge/Rendering-Static_Generation-green)

High-performance public website built with Next.js 15, featuring static site generation, SEO optimization, and seamless postgres DB integration.

> **Documentation Update (Feb 21, 2026):** 12 legacy implementation and testing docs have been moved to `archive/cleanup-feb2026/` for better organization. See [archive index](archive/cleanup-feb2026/INDEX.md) for access.

## Overview

The Glad Labs public site serves as the primary content distribution platform, consuming content from the postgres database and presenting it through a fast, SEO-optimized interface.

**Status:** ✅ Production Ready
**Version:** 3.0
**Last Updated:** February 25, 2026
**Technology:** Next.js 15.1.0 + React 18.3.1

---

## **🚀 Quick Start**

### **Prerequisites**

- Node.js 20.11.1+
- Running FastAPI backend on localhost:8000
- Configured environment variables

### **Development Setup**

```bash
# Navigate to public site directory
cd web/public-site

# Install dependencies
npm install

# Configure environment
cp .env.example .env.local
# Edit .env.local with your FastAPI URL

# Start development server
npm run dev
```

---

## **🏗️ Architecture**

### **Technology Stack**

- **Framework**: Next.js 15 with App Router
- **Rendering**: Static Site Generation (SSG) with Incremental Static Regeneration
- **Styling**: Tailwind CSS with Typography plugin
- **Content**: React Markdown for content rendering
- **API**: REST API integration with FastAPI backend
- **SEO**: Next.js Head component with Open Graph support

### **Project Structure**

```text
web/public-site/
├── components/           # Reusable React components
│   ├── Header.js        # Navigation header
│   ├── Layout.js        # Page layout wrapper
│   ├── PostCard.js      # Individual post card
│   └── PostList.js      # Post grid/list container
├── lib/                 # Utility functions and API helpers
│   └── api-fastapi.js  # FastAPI CMS integration functions
├── pages/              # Next.js pages (file-based routing)
│   ├── index.js        # Homepage with featured posts
│   ├── about.js        # About page
│   ├── privacy-policy.js # Privacy policy
│   ├── archive/        # Paginated post archives
│   ├── category/       # Category-filtered posts
│   ├── posts/          # Individual post pages
│   └── tag/            # Tag-filtered posts
├── scripts/            # Build and utility scripts
│   └── generate-sitemap.js # SEO sitemap generation
├── styles/             # Global styles and Tailwind config
└── .env.local          # Environment configuration
```

---

## **📝 Core Features**

### **Content Display**

- **Homepage**: Featured post hero section + recent posts grid
- **Post Detail Pages**: Full article display with markdown rendering
- **Category Pages**: Posts filtered by category with pagination
- **Tag Pages**: Posts filtered by tags with pagination
- **Archive Pages**: Chronological post browsing with pagination

### **SEO Optimization**

- **Meta Tags**: Dynamic title, description, and keywords
- **Open Graph**: Social media sharing optimization
- **Twitter Cards**: Enhanced Twitter sharing
- **Sitemap**: Automatically generated XML sitemap
- **Structured Data**: JSON-LD for rich search results

### **Performance Features**

- **Static Generation**: Pages pre-built at build time
- **Image Optimization**: Next.js automatic image optimization
- **API Caching**: FastAPI content caching with revalidation
- **Code Splitting**: Automatic code splitting and lazy loading

---

## **🔌 API Integration**

### **FastAPI Integration**

The site integrates with FastAPI CMS endpoints defined in `lib/api-fastapi.js`:

```javascript
// Key API functions
getFeaturedPost(); // Get featured post for homepage
getPaginatedPosts(); // Get paginated posts with filtering
getPostBySlug(slug); // Get individual post by slug
getCategories(); // Get all categories
getTags(); // Get all tags
```

### **Data Flow**

1. **Build Time**: Next.js calls FastAPI to generate static pages
2. **Runtime**: ISR (Incremental Static Regeneration) updates content
3. **Client Side**: No client-side API calls, all content server-rendered

### **Environment Configuration**

```env
# .env.local
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000
```

---

## **🧩 Component Reference**

### **Page Components**

- **Homepage** (`pages/index.js`): Featured post + recent posts grid
- **Post Detail** (`pages/posts/[slug].js`): Individual article display
- **Category** (`pages/category/[slug].js`): Category-filtered posts
- **Tag** (`pages/tag/[slug].js`): Tag-filtered posts
- **Archive** (`pages/archive/[page].js`): Paginated post archives

### **Reusable Components**

- **Header** (`components/Header.js`): Navigation with modern Link syntax
- **Layout** (`components/Layout.js`): Page wrapper with header/footer
- **PostCard** (`components/PostCard.js`): Post preview card with validation
- **PostList** (`components/PostList.js`): Grid container for multiple posts

### **Utilities**

- **API Helpers** (`lib/api-fastapi.js`): Centralized FastAPI CMS communication
- **URL Helpers**: getStrapiURL for asset URL construction (handles both relative and absolute URLs)

---

## **🛠️ Development**

### **Available Scripts**

```bash
npm run dev         # Start development server
npm run build       # Build for production
npm run start       # Start production server
npm run lint        # Run ESLint
npm run test        # Run Jest tests
```

### **Build Process**

1. **Static Generation**: Pages pre-built using getStaticProps
2. **Sitemap Generation**: Automatic sitemap creation post-build
3. **Optimization**: Automatic code splitting and optimization
4. **Asset Processing**: Image optimization and static asset handling

### **Testing Strategy**

- **Unit Tests**: Jest + React Testing Library
- **Component Tests**: Individual component functionality
- **Integration Tests**: API integration and data flow
- **SEO Tests**: Meta tag and structured data validation

---

## **🚀 Deployment**

### **Production Considerations**

- **Static Hosting**: Deploy to Vercel, Netlify, or similar
- **Environment Variables**: Secure API tokens and URLs
- **Database**: Point to production FastAPI instance
- **CDN**: Global content delivery for optimal performance
- **Analytics**: Google Analytics or similar tracking

### **Performance Optimizations**

- **Image Optimization**: Next.js Image component with lazy loading
- **Code Splitting**: Automatic route-based code splitting
- **Static Generation**: Pre-built pages for instant loading
- **Caching**: ISR for content updates without full rebuilds

---

## **🔧 Troubleshooting**

### **Common Issues**

1. **Content Not Displaying**: Check FastAPI API connection and data structure
2. **Build Failures**: Verify all environment variables are set
3. **Image Loading Issues**: Confirm image URLs and Next.js config
4. **SEO Problems**: Validate meta tags and sitemap generation

### **Debug Mode**

Enable detailed logging by adding to your environment:

```env
NEXT_PUBLIC_DEBUG=true
```

---

**Component Documentation maintained by:** Glad Labs Development Team  
**Contact:** Matthew M. Gladding (Glad Labs, LLC)  
**Last Review:** October 13, 2025  
**Architecture Status:** ✅ Production Ready
