# 🚀 **GLAD Labs AI Co-Founder System**

![Production Ready](https://img.shields.io/badge/Status-Production_Ready-brightgreen)
![GLAD Labs Standards](https://img.shields.io/badge/Standards-v2.0_Compliant-blue)
![Next.js](https://img.shields.io/badge/Frontend-Next.js_15-black)
![Strapi](https://img.shields.io/badge/CMS-Strapi_v5-blue)
![Python](https://img.shields.io/badge/Backend-Python_3.12-blue)
![AI Powered](https://img.shields.io/badge/AI-Powered_Co--Founder-purple)

> **Revolutionary AI-powered business co-founder system featuring autonomous agents, intelligent orchestration, and comprehensive business intelligence - delivering the world's first complete AI business partner.**

## **📚 Documentation Index**

| Document                                                        | Description                               | For        |
| --------------------------------------------------------------- | ----------------------------------------- | ---------- |
| [� **Master Documentation Index**](./MASTER_DOCS_INDEX.md) | Complete documentation hub with all links | Everyone   |
| [�🚀 **Quick Start Guide**](#-quick-start)                      | Get up and running in 5 minutes           | New Users  |
| [🏗️ **Architecture Overview**](./docs/ARCHITECTURE.md)               | System design and component interactions  | Developers |
| [📋 **Developer Guide**](./DEVELOPER_GUIDE.md)             | Technical documentation and APIs          | Developers |
| [⚙️ **Installation Guide**](./docs/INSTALLATION_SUMMARY.md)          | Dependency setup and configuration        | DevOps     |
| [🧪 **Testing Guide**](./TEST_IMPLEMENTATION_SUMMARY.md)   | Complete test coverage and execution      | Everyone   |
| [🔧 **CI/CD Review**](./CI_CD_TEST_REVIEW.md)              | Pipeline analysis and recommendations     | DevOps     |
| [📊 **System Standards**](./docs/GLAD-LABS-STANDARDS.md)             | Coding standards and best practices       | Team       |

## **🎯 Executive Summary**

GLAD Labs is a comprehensive AI Co-Founder ecosystem that combines autonomous content creation with intelligent business management. The system features a sophisticated AI Co-Founder that provides strategic insights, manages business operations, orchestrates specialized agents, and delivers real-time business intelligence through advanced dashboards and voice interfaces.

**Current Status:** ✅ **Production Ready v3.0** - Complete AI Co-Founder System  
**Last Updated:** October 14, 2025  
**Architecture:** Enterprise-grade monorepo with AI orchestration

---

## **🚀 Quick Start**

### **Prerequisites (Quick Start)**

- Node.js 18+ and Python 3.12+
- Git and a code editor

### **Installation**

```bash
# Clone the repository
git clone <repository-url>
cd glad-labs-website

# Install all dependencies (Python + Node.js)
npm run setup:all

# Start all services in development mode
npm run dev
```

### **Access Points**

| Service           | URL                     | Purpose               |
| ----------------- | ----------------------- | --------------------- |
| **Public Site**   | <http://localhost:3000> | Next.js website       |
| **Oversight Hub** | <http://localhost:3001> | React admin dashboard |
| **Strapi CMS**    | <http://localhost:1337> | Content management    |
| **AI Co-Founder** | <http://localhost:8000> | Python API server     |

### **Available Commands**

```bash
npm run dev           # Start all services
npm run build         # Build for production
npm test              # Run all tests
npm run lint          # Check code quality
```

## **🏗️ System Architecture**

The system is designed as a modern monorepo with clear separation of concerns and automated AI workflows.

| Service           | Technology  | Port | Status   | Description                       |
| ----------------- | ----------- | ---- | -------- | --------------------------------- |
| **Public Site**   | Next.js 15  | 3000 | ✅ Ready | High-performance public website   |
| **Oversight Hub** | React 18    | 3001 | ✅ Ready | Admin interface for AI management |
| **Strapi CMS**    | Strapi v5   | 1337 | ✅ Ready | Headless content management       |
| **AI Co-Founder** | Python 3.12 | 8000 | ✅ Ready | AI business intelligence system   |
| **Content Agent** | Python      | -    | ✅ Ready | Autonomous content creation       |

### **Workspace Structure**

```text
glad-labs-website/
├── 📁 web/
│ ├── public-site/ # Next.js 15 public website
│ └── oversight-hub/ # React admin dashboard
├── 📁 cms/
│ └── strapi-v5-backend/ # Strapi CMS backend
├── 📁 src/
│ ├── cofounder_agent/ # AI Co-Founder system
│ └── mcp/ # Model Context Protocol
├── 📁 agents/
│ └── content-agent/ # Content generation agents
└── 📁 docs/ # Documentation
```

---

## **⚡ Quick Start**

### **Prerequisites**

- **Node.js:** v20.11.1+
- **Python:** 3.12+
- **Git:** Latest stable

### **Installation & Setup**

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd glad-labs-website
   ```

1. **Install dependencies:**

   ```bash
   # Install root dependencies
   npm install

   # Install frontend dependencies
   cd web/public-site && npm install && cd ../..
   cd web/oversight-hub && npm install && cd ../..

   # Install CMS dependencies
   cd cms/strapi-v5-backend && npm install && cd ../..

   # Install Python dependencies for content agent
   cd src/agents/content_agent && pip install -r requirements.txt && cd ../../..
   ```

1. **Configure environment variables:**

   **Strapi CMS** (`cms/strapi-v5-backend/.env`):

   ```env
   NODE_ENV=development
   APP_KEYS="your-app-keys"
   API_TOKEN_SALT="your-api-token-salt"
   ADMIN_JWT_SECRET="your-admin-jwt-secret"
   TRANSFER_TOKEN_SALT="your-transfer-token-salt"
   JWT_SECRET="your-jwt-secret"
   ```

   **Next.js Frontend** (`web/public-site/.env.local`):

   ```env
   NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
   STRAPI_API_TOKEN=your-strapi-api-token
   ```

   **Content Agent** (`src/agents/content_agent/.env`):

   ```env
   OPENAI_API_KEY=your-openai-api-key
   PEXELS_API_KEY=your-pexels-api-key
   STRAPI_API_URL=http://localhost:1337
   STRAPI_API_TOKEN=your-strapi-api-token
   ```

1. **Start the development environment:**

   ```bash
   # Terminal 1: Start Strapi CMS
   cd cms/strapi-v5-backend
   npm run develop

   # Terminal 2: Start Next.js frontend
   cd web/public-site
   npm run dev

   # Terminal 3: Start content agent (optional)
   cd src/agents/content_agent
   python orchestrator.py
   ```

### **Development URLs**

- **Public Site:** <http://localhost:3000>
- **Strapi Admin:** <http://localhost:1337/admin>
- **Strapi API:** <http://localhost:1337/api>

---

## **🔧 Architecture Components**

### **1. Public Site (Next.js Frontend)**

- **Technology:** Next.js 14 with Static Site Generation (SSG)
- **Features:** Server-side rendering, SEO optimization, responsive design
- **API Integration:** Connects to Strapi v5 via REST API
- **Status:** ✅ Production ready with markdown content rendering

**Key Features:**

- Homepage with featured posts and recent content grid
- Individual post pages with full markdown rendering
- Category and tag-based content filtering
- Privacy policy and about pages
- SEO-optimized meta tags and Open Graph support

### **2. Content Management System (Strapi v5)**

- **Technology:** Strapi v5 with SQLite database
- **Features:** Headless CMS, API-first architecture, admin interface
- **Content Types:** Posts, Categories, Tags, Pages
- **Status:** ✅ Production ready with full CRUD operations

**Content Structure:**

- **Posts**: Title, slug, content (markdown), excerpt, featured flag, cover image
- **Categories**: Name, slug, description
- **Tags**: Name, slug
- **Relations**: Posts belong to categories and can have multiple tags

### **3. Content Agent (Autonomous AI)**

- **Technology:** Python with OpenAI GPT integration
- **Features:** Autonomous content creation, image sourcing, quality assurance
- **Workflow:** Research → Create → Review → Publish
- **Status:** ✅ Production ready with multi-agent pipeline

**Agent Pipeline:**

1. **Research Agent**: Gathers context and information
2. **Creative Agent**: Generates initial content drafts
3. **QA Agent**: Reviews content for quality and compliance
4. **Image Agent**: Sources and processes relevant images
5. **Publishing Agent**: Formats and publishes to Strapi

### **4. Oversight Hub (Admin Interface)**

- **Technology:** React 18 with Firebase integration
- **Features:** Real-time monitoring, agent control, chat interface
- **Status:** 🚧 Development phase

---

## **📚 Documentation**

| Document                                             | Description                          | Status     |
| ---------------------------------------------------- | ------------------------------------ | ---------- |
| [SYSTEM_DOCUMENTATION.md](./SYSTEM_DOCUMENTATION.md) | Complete system documentation        | ✅ Current |
| [data_schemas.md](./data_schemas.md)                 | Database and content schemas         | ✅ Current |
| [GLAD-LABS-STANDARDS.md](./GLAD-LABS-STANDARDS.md)   | Development standards and guidelines | ✅ Current |

### **Component Documentation**

- **[Public Site](./web/public-site/README.md)** - Next.js frontend documentation
- **[Strapi CMS](./cms/strapi-v5-backend/README.md)** - Content management system setup
- **[Content Agent](./src/agents/content_agent/README.md)** - Autonomous content creation
- **[Oversight Hub](./web/oversight-hub/README.md)** - Admin interface

---

## **🛠️ Development Workflow**

### **Content Creation Process**

1. **Manual Trigger**: Create content requests via Oversight Hub or direct API
2. **Agent Processing**: Content agent processes request through multi-agent pipeline
3. **Content Generation**: AI generates high-quality, SEO-optimized content
4. **Quality Assurance**: Automated review and refinement process
5. **Publication**: Content published to Strapi and available on public site

### **Code Quality Standards**

- **ESLint**: Frontend code linting and formatting
- **Prettier**: Code formatting consistency
- **React Markdown**: Markdown content rendering
- **Tailwind CSS**: Utility-first styling approach

---

## **🚀 Deployment**

### **Production Considerations**

- **Strapi**: Deploy to cloud hosting with PostgreSQL database
- **Next.js**: Deploy to Vercel, Netlify, or similar static hosting
- **Content Agent**: Deploy to Google Cloud Run or AWS Lambda
- **Environment Variables**: Secure API keys and database credentials

### **Performance Optimizations**

- **Static Site Generation**: Pre-built pages for optimal performance
- **Image Optimization**: Next.js automatic image optimization
- **API Caching**: Strapi content caching strategies
- **CDN Integration**: Global content delivery

---

## **🤝 Contributing**

### **Development Setup**

1. **Fork the repository**
2. **Create feature branch**: `git checkout -b feature/new-capability`
3. **Follow code standards**: ESLint, Prettier, component conventions
4. **Add comprehensive tests**: Unit and integration testing
5. **Update documentation**: Keep all docs current
6. **Create pull request**: Detailed description of changes

### **Testing Strategy**

- **Frontend**: Jest + React Testing Library
- **Backend**: Strapi built-in testing framework
- **Content Agent**: Python unittest framework
- **Integration**: End-to-end testing with Playwright

For step-by-step local testing instructions on Windows PowerShell (including virtualenv setup), see the Testing Guide: `TESTING.md`.

---

## **📞 Support & Contact**

**Project Owner:** Matthew M. Gladding  
**Organization:** Glad Labs, LLC  
**License:** MIT

**Architecture Status:** ✅ Production Ready v2.0  
**Last Documentation Update:** October 13, 2025
