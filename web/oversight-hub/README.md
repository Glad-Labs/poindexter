# Oversight Hub

React admin dashboard for the Glad Labs AI Co-Founder system.

**Version:** 0.1.0
**Stack:** React 18 + Material-UI + Vite 5 + Zustand
**Port:** 3001

## Quick Start

```bash
# From monorepo root (installs all workspaces)
npm install

# Start all services
npm run dev

# Or run oversight hub only
npm run dev:oversight
```

The dashboard will be available at `http://localhost:3001`.

## Architecture

```
web/oversight-hub/
├── src/
│   ├── routes/                  # Page-level route components
│   │   ├── AppRoutes.jsx        # Router configuration
│   │   ├── TaskManagement.jsx   # Task management
│   │   ├── Content.jsx          # Content management
│   │   ├── AIStudio.jsx         # Workflow/AI studio
│   │   ├── CostMetricsDashboard.jsx
│   │   ├── PerformanceDashboard.jsx
│   │   └── Settings.jsx
│   ├── pages/                   # Standalone pages
│   │   ├── Login.jsx            # Login page
│   │   ├── AuthCallback.jsx     # OAuth callback handler
│   │   └── BlogWorkflowPage.jsx
│   ├── components/              # UI components (~40 files)
│   │   ├── common/              # Sidebar, CommandPane, ErrorMessage
│   │   ├── tasks/               # Task table, filters, modals, status
│   │   ├── notifications/       # NotificationCenter
│   │   ├── settings/            # Alert, General, ModelPreferences
│   │   └── pages/               # ExecutiveDashboard, UnifiedServicesPanel
│   ├── services/                # API clients (~20 files)
│   │   ├── cofounderAgentClient.js  # Main backend client (JWT auth)
│   │   ├── authService.js       # GitHub OAuth flow
│   │   ├── taskService.js       # Task CRUD
│   │   └── ...
│   ├── hooks/                   # Custom React hooks (~19 files)
│   ├── context/                 # AuthContext, WebSocketContext
│   ├── store/useStore.js        # Zustand global state
│   ├── lib/                     # Utilities (logger, error extraction, dates)
│   ├── config/apiConfig.js      # API URL configuration
│   └── Constants/               # Status enums, orchestrator constants
├── vitest.config.ts             # Test configuration
├── vite.config.ts               # Build configuration
└── Dockerfile                   # Production build (nginx)
```

## Key Features

- **Task Management** — Create, monitor, and manage content generation tasks
- **Workflow Studio** — Visual workflow builder with drag-and-drop phases
- **Cost Analytics** — Track LLM API costs by provider, model, and task
- **Real-time Updates** — WebSocket-powered live progress tracking
- **Agent Monitoring** — View agent status, execution history, and logs
- **GitHub OAuth** — Secure authentication with JWT tokens
- **Model Selection** — Configure LLM providers and fallback chains

## Environment Variables

Environment variables use the `REACT_APP_*` prefix (shimmed via `process.env` in `vite.config.ts`):

```env
# .env.local
REACT_APP_API_URL=http://localhost:8000
REACT_APP_GH_OAUTH_CLIENT_ID=your_github_client_id
REACT_APP_USE_MOCK_AUTH=true          # Set false in production
REACT_APP_LOG_LEVEL=debug
```

The Vite config (`vite.config.ts` lines 48-57) filters and exposes `REACT_APP_*` vars to the browser via a `process.env` define block.

## API Integration

All data flows through the FastAPI backend at `REACT_APP_API_URL`:

```
POST /api/tasks              # Create task
GET  /api/tasks              # List tasks (paginated)
GET  /api/tasks/:id          # Task details
GET  /api/health             # System health
GET  /api/models/status      # Model provider status
POST /api/auth/github/callback  # OAuth code exchange
WS   /api/workflow-progress/ws/:id  # Real-time progress
```

The primary API client is `src/services/cofounderAgentClient.js` which attaches JWT auth headers to all requests.

## Testing

```bash
# Run all tests
cd web/oversight-hub && npx vitest run

# Recommended flags for Windows stability
npx vitest run --pool=forks --poolOptions.forks.maxForks=4
```

- **Framework:** Vitest 2.1 with jsdom
- **Setup:** `src/test-setup.js` (mocks for scrollIntoView, getSelection, createRange)
- **Coverage thresholds:** 50% lines/functions/statements, 40% branches

## Build & Deployment

```bash
npm run build    # Output: build/
```

Production runs on nginx via Docker (`Dockerfile`). The nginx config includes SPA fallback (`try_files $uri $uri/ /index.html`) for client-side routing.

Deployed to Vercel via CI (`.github/workflows/deploy-production-with-environments.yml`).

## Resources

- [System Architecture](../../docs/02-Architecture/System-Design.md)
- [Development Workflow](../../docs/04-Development/Development-Workflow.md)
- [Testing Guide](../../docs/04-Development/Testing-Guide.md)
- [API Contracts](../../docs/reference/API_CONTRACTS.md)
