# GLAD Labs Website - Development Setup

## 🚀 Quick Start

This workspace is configured to automatically launch all services when opened in VS Code.

### Services Overview

| Service | Port | URL | Description |
|---------|------|-----|-------------|
| **Strapi CMS** | 1337 | http://localhost:1337 | Backend CMS and API |
| **Oversight Hub** | 3001 | http://localhost:3001 | React admin dashboard |
| **Public Site** | 3002 | http://localhost:3002 | Next.js public website |

## 🛠️ Manual Setup

If the auto-launch doesn't work, you can start services manually:

### Start All Services
```bash
npm run start:all
```

### Start Individual Services
```bash
# Strapi Backend
npm run start:strapi

# Oversight Hub (React)
npm run start:oversight

# Public Site (Next.js)
npm run start:public
```

## 📁 Project Structure

```
glad-labs-website/
├── cms/
│   └── strapi-backend/          # Strapi CMS (Port 1337)
├── web/
│   ├── oversight-hub/           # React Admin Dashboard (Port 3001)
│   └── public-site/             # Next.js Public Site (Port 3002)
├── agents/
│   └── content-agent/           # Python content generation agent
└── .vscode/                     # VS Code workspace configuration
```

## 🔧 Configuration

### VS Code Tasks

The workspace includes several pre-configured tasks:

- **Start All Services**: Launches all three web services simultaneously
- **Start Strapi Backend**: CMS only
- **Start Oversight Hub**: Admin dashboard only  
- **Start Public Site**: Public website only

Access via `Ctrl+Shift+P` → "Tasks: Run Task"

### Firebase Setup (Oversight Hub)

1. Copy `.env.example` to `.env` in `web/oversight-hub/`
2. Fill in your Firebase credentials:
   ```env
   REACT_APP_API_KEY=your_firebase_api_key
   REACT_APP_AUTH_DOMAIN=your_project.firebaseapp.com
   REACT_APP_PROJECT_ID=your_project_id
   # ... etc
   ```

## 🐛 Troubleshooting

### Port Conflicts
Each service is configured with a specific port. If you get port conflicts:
- Strapi: Edit `cms/strapi-backend/config/server.ts`
- Oversight Hub: Edit `web/oversight-hub/package.json` start script
- Public Site: Edit root `package.json` start:public script

### Strapi Issues
- Clear cache: Delete `cms/strapi-backend/.tmp` and `cms/strapi-backend/build`
- Reset database: Delete `cms/strapi-backend/database/migrations/*`

### Node Version
This project requires Node.js v20.11.1. Use `nvm` to switch versions:
```bash
nvm use 20.11.1
```

## 📋 Available Scripts

| Script | Description |
|--------|-------------|
| `npm run start:all` | Start all services concurrently |
| `npm run start:strapi` | Start Strapi CMS in development mode |
| `npm run start:oversight` | Start React oversight dashboard |
| `npm run start:public` | Start Next.js public site |
| `npm install` | Install all dependencies (workspaces) |

## 🎯 Development Workflow

1. **VS Code Auto-Launch**: Open the workspace and services start automatically
2. **Manual Launch**: Run `npm run start:all` in the terminal
3. **Individual Services**: Use the specific start scripts as needed

All services support hot-reload for efficient development.

## 🔐 Environment Variables

Each service may require specific environment variables:

- **Strapi**: Database and admin credentials
- **Oversight Hub**: Firebase configuration
- **Public Site**: API endpoints and keys

Check each service's `.env.example` file for required variables.