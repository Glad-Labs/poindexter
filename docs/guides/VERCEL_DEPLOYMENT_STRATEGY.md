# 🚀 Vercel Deployment Strategy

Decision guide for deploying Public Site + Oversight Hub to Vercel

---

## 📊 Current Setup

### Monorepo Structure

```text
glad-labs-website/ (Root)
├── web/
│   ├── public-site/        (Next.js 15)
│   └── oversight-hub/      (React 18 with CRA)
├── cms/strapi-main/        (→ Railway)
├── src/cofounder_agent/    (→ Railway)
└── package.json            (npm workspaces)
```

### Build Characteristics

- **Public Site**: Next.js 15 (optimized for static/SSR)
- **Oversight Hub**: React 18 + Create React App (client-side app)
- **Monorepo**: npm workspaces for dependency management
- **Independent**: Both can be deployed separately

---

## 🤔 The Decision: One or Two Vercel Projects?

### Option 1: Two Separate Vercel Projects (✅ RECOMMENDED)

Deploy each application independently with separate build configurations.

```text
Vercel Project #1: glad-labs-public-site
├── Source: web/public-site/
├── Framework: Next.js
├── Domain: gladlabs.ai
└── Auto-deploy on changes to web/public-site/

Vercel Project #2: glad-labs-oversight-hub
├── Source: web/oversight-hub/
├── Framework: React (CRA)
├── Domain: hub.gladlabs.ai
└── Auto-deploy on changes to web/oversight-hub/
```

### Option 2: Single Monorepo Project (❌ NOT RECOMMENDED)

Deploy both from the repository root with custom build scripts.

```text
Problem: Builds BOTH apps on ANY change
Problem: Slower CI/CD (15-25 mins vs 5-10 mins)
Problem: Can't deploy one app independently
```

---

## ✅ Why Two Separate Projects is Better

### Independent Deployments

- Public site changes don't redeploy the hub
- Hub updates don't rebuild the public site
- Faster CI/CD pipeline
- Reduced build time

### Separate Domains & SSL

- `gladlabs.ai` → Public Site
- `hub.gladlabs.ai` → Oversight Hub
- Each gets independent SSL certificate
- Clean separation of concerns

### Independent Scaling

- Public site scales based on visitor traffic
- Hub scales based on concurrent users
- Different environments, rules, limits
- Separate analytics and monitoring

### Individual Environment Variables

- Public site: `NEXT_PUBLIC_STRAPI_URL`, etc.
- Hub: `REACT_APP_FIREBASE_*`, etc.
- No conflicts or shared secrets

### Build Optimization

```text
Two Projects (FAST):
├─ Detect change in web/public-site/
├─ Build ONLY web/public-site/
└─ Deploy in ~5-10 mins

Single Monorepo (SLOW):
├─ Detect ANY change in repo
├─ Run npm run build (builds ALL)
├─ Install deps for both apps
├─ Build public-site
├─ Build oversight-hub
└─ Deploy in ~15-25 mins
```

### Simpler Configuration

- Each project uses its own `vercel.json`
- No complex root-level build scripts
- Vercel's standard detection works
- No custom build commands needed

### Easy Rollback

- Rollback public site without affecting hub
- Rollback hub without touching public site
- Fine-grained control per application

---

## 🏗️ Implementation Steps

### Step 1: Push to GitHub

```bash
git remote add origin https://github.com/mattg-stack/glad-labs-website.git
git push -u origin main
```

### Step 2: Create Public Site Project

1. Go to vercel.com/dashboard
2. Click "Add New Project"
3. Select your GitHub repository

Configure the project settings:

```text
Framework Preset: Next.js
Project Name: glad-labs-public-site
Root Directory: web/public-site/
```

Add environment variables:

```bash
NEXT_PUBLIC_STRAPI_URL=https://strapi.railway.app
NEXT_PUBLIC_SITE_URL=https://gladlabs.ai
```

Set custom domain: `gladlabs.ai`, then deploy!

### Step 3: Create Oversight Hub Project

1. Go to vercel.com/dashboard
2. Click "Add New Project"
3. Select your GitHub repository

Configure the project settings:

```text
Framework Preset: Create React App
Project Name: glad-labs-oversight-hub
Root Directory: web/oversight-hub/
Build Command: npm run build
Output Directory: build/
```

Add environment variables:

```bash
REACT_APP_FIREBASE_API_KEY=your-api-key
REACT_APP_FIREBASE_AUTH_DOMAIN=your-domain.firebaseapp.com
REACT_APP_FIREBASE_PROJECT_ID=your-project-id
REACT_APP_FIREBASE_STORAGE_BUCKET=your-bucket
REACT_APP_FIREBASE_MESSAGING_SENDER_ID=your-sender-id
REACT_APP_FIREBASE_APP_ID=your-app-id
REACT_APP_STRAPI_URL=https://strapi.railway.app
REACT_APP_COFOUNDER_URL=https://cofounder.railway.app
```

Set custom domain: `hub.gladlabs.ai`, then deploy!

---

## 🔧 Configuration Files

### Public Site: web/public-site/vercel.json

```json
{
  "buildCommand": "next build",
  "outputDirectory": ".next",
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        {
          "key": "Cache-Control",
          "value": "public, max-age=3600, stale-while-revalidate=86400"
        }
      ]
    }
  ]
}
```

### Oversight Hub: web/oversight-hub/vercel.json

```json
{
  "buildCommand": "npm run build",
  "outputDirectory": "build"
}
```

---

## 🔌 Integration with Railway

### Data Flow

```text
Public Site (Vercel)
    ↓ (NEXT_PUBLIC_STRAPI_URL)
    └─→ Strapi (Railway:1337)

Oversight Hub (Vercel)
    ├─→ Strapi (Railway:1337)
    ├─→ Co-Founder Agent (Railway:8000)
    └─→ Firestore (Google Cloud)
```

### Railway Environment Variables

Already configured:

```bash
DATABASE_URL=postgresql://...
STRAPI_URL=https://strapi.railway.app
COFOUNDER_URL=https://cofounder.railway.app
```

---

## 📋 Deployment Checklist

### Pre-Deployment

- [ ] Both apps build locally
- [ ] Repository pushed to GitHub
- [ ] Custom domains registered
- [ ] Environment variables identified

### Vercel Project #1: Public Site

- [ ] Create Vercel project
- [ ] Set root directory to `web/public-site/`
- [ ] Add NEXT*PUBLIC*\* environment variables
- [ ] Set custom domain to `gladlabs.ai`
- [ ] Deploy and verify
- [ ] Test Strapi connection

### Vercel Project #2: Oversight Hub

- [ ] Create Vercel project
- [ ] Set root directory to `web/oversight-hub/`
- [ ] Set build command: `npm run build`
- [ ] Set output directory: `build/`
- [ ] Add REACT*APP*\* environment variables
- [ ] Set custom domain to `hub.gladlabs.ai`
- [ ] Deploy and verify
- [ ] Test Firebase, Strapi, and Co-Founder connections

### Post-Deployment

- [ ] Test public site functionality
- [ ] Test oversight hub functionality
- [ ] Verify SSL certificates
- [ ] Set up monitoring

---

## 🚨 Common Issues

### "Cannot find module" during build

**Solution**: Ensure root `package.json` has workspaces defined (already done).

### Environment variables not loading

**Solution**: Verify variable naming:

- `NEXT_PUBLIC_*` for Next.js
- `REACT_APP_*` for React/CRA

### Both apps deployed to same domain

**Solution**: Set separate custom domains in Vercel settings.

### Build fails with "root workspace not installed"

**Solution**: Root `package.json` must include both projects in `workspaces`.

---

## 📊 Comparison

| Aspect          | Two Projects (✅) | One Monorepo (❌)   |
| --------------- | ----------------- | ------------------- |
| **Deployments** | Independent       | Both together       |
| **Build Speed** | ~5-10 mins        | ~15-25 mins         |
| **Domains**     | Separate          | Shared/paths        |
| **Scaling**     | Independent       | Shared              |
| **Environment** | Separate vars     | Potential conflicts |
| **Rollback**    | Per-app           | Affects both        |

---

## 🎯 Recommendation

**Use two separate Vercel projects:**

1. Faster, independent deployments
2. Separate domains for better UX
3. Simpler configuration
4. Easier to maintain and scale

---

## 📚 Next Steps

1. Push code to GitHub
2. Create Vercel project #1 (Public Site)
3. Create Vercel project #2 (Oversight Hub)
4. Configure environment variables for each
5. Set custom domains
6. Deploy and test!
