# Visual Architecture Overview

**System Component Map & Data Flow Diagrams**

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         OVERSIGHT HUB FRONTEND                          │
│                          (React 18, React Router)                       │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                      LayoutWrapper Component                    │  │
│  │  ┌──────────────┐  ┌────────────────┐  ┌──────────────────┐   │  │
│  │  │   Navigation │  │   Page Router  │  │  Chat Panel (RHS)│   │  │
│  │  │   (12 items) │  │   (13+ pages)  │  │  (Always visible)│   │  │
│  │  └──────────────┘  └────────────────┘  └──────────────────┘   │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐      │
│  │   Tasks    │  │   Content  │  │   Social   │  │  Metrics   │      │
│  │  Management│  │ Management │  │ Publishing │  │ Dashboard  │      │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘      │
│                                                                         │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ⚠️ MISSING:         │
│  │   Agents   │  │   Models   │  │  Settings  │  ├─ Orchestrator    │
│  │  Monitoring│  │ (Ollama UI)│  │ Management │  ├─ Command Queue    │
│  └────────────┘  └────────────┘  └────────────┘  ├─ Webhooks Config  │
│                                                   ├─ Bulk Ops UI      │
│  ┌────────────────────────────────────────────┐  └─ Subtasks UI      │
│  │         Authentication (AuthContext)        │                      │
│  │  JWT Token Generation & Bearer Management   │                      │
│  └────────────────────────────────────────────┘                      │
│                                                                         │
│  🔑 STATE MANAGEMENT: Zustand (useStore)                              │
│  🔌 API CLIENT: cofounderAgentClient.js + fetch API                   │
│  🎨 STYLING: Tailwind CSS + OversightHub.css                          │
└─────────────────────────────────────────────────────────────────────────┘
                                 ▲
                    HTTP/HTTPS (Bearer Token)
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          FASTAPI BACKEND                                │
│                        (Python, Async/Await)                           │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    Middleware & Configuration                    │  │
│  │  ├─ CORS (localhost:3001)                                        │  │
│  │  ├─ JWT Validation (auth_unified.py)                             │  │
│  │  ├─ Error Handling (ErrorResponseBuilder)                        │  │
│  │  ├─ Request Logging                                              │  │
│  │  └─ Telemetry (OpenTelemetry, Sentry)                            │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │   Task      │  │   Content   │  │    Chat     │  │   Social    │  │
│  │   Routes    │  │   Routes    │  │    Routes   │  │   Routes    │  │
│  │ (7 endpoints)│ │(6 endpoints)│  │(4 endpoints)│  │(9 endpoints)│  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │   Metrics   │  │   Agents    │  │  Orchestr.  │  │   Settings  │  │
│  │   Routes    │  │   Routes    │  │   Routes    │  │   Routes    │  │
│  │(5 endpoints)│  │(6 endpoints)│  │(10 endp.)   │  │(11 endp.)   │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │   Ollama    │  │  Workflow   │  │  Subtasks   │  │   Command   │  │
│  │   Routes    │  │   History   │  │   Routes    │  │   Queue     │  │
│  │(5 endpoints)│  │(5 endpoints)│  │(5 endpoints)│  │(8 endpoints)│  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │   Bulk Ops  │  │  Webhooks   │  │     CMS     │  │    Auth     │  │
│  │   Routes    │  │   Routes    │  │   Routes    │  │   Routes    │  │
│  │(1 endpoint) │  │(1 endpoint) │  │(5 endpoints)│  │(3 endpoints)│  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                   Service Layer                                  │  │
│  │  ├─ DatabaseService (PostgreSQL + asyncpg)                       │  │
│  │  ├─ TaskExecutor (Task processing)                               │  │
│  │  ├─ ContentCritiqueLoop (Content quality)                         │  │
│  │  ├─ WorkflowHistoryService (Execution tracking)                   │  │
│  │  ├─ OllamaService (Local LLM management)                          │  │
│  │  ├─ ChatService (Conversation management)                         │  │
│  │  ├─ SocialService (Social media integration)                       │  │
│  │  └─ MetricsService (Usage tracking)                               │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  🔐 AUTHENTICATION: PyJWT (HS256)                                      │
│  📦 ORM: SQLAlchemy + asyncpg                                          │
│  ⚡ FRAMEWORK: FastAPI with async/await                                │
└─────────────────────────────────────────────────────────────────────────┘
                                 ▲
                      SQL Queries (asyncpg)
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       POSTGRESQL DATABASE                               │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │   tasks     │  │   users     │  │workflow_     │  │ chat_      │  │
│  │   (89 rows) │  │   (N/A)     │  │history (N/A) │  │history     │  │
│  │             │  │             │  │              │  │            │  │
│  │ id (UUID)   │  │ id (UUID)   │  │id (UUID)     │  │id (UUID)   │  │
│  │ task_name   │  │ email       │  │workflow_     │  │conv_id     │  │
│  │ status      │  │ name        │  │name          │  │user_id     │  │
│  │ created_at  │  │ auth_token  │  │executed_at   │  │message     │  │
│  │ content     │  │ avatar_url  │  │duration_ms   │  │role        │  │
│  │ quality_    │  │ settings    │  │result        │  │timestamp   │  │
│  │ score       │  │             │  │              │  │            │  │
│  │ task_       │  │             │  │              │  │            │  │
│  │ metadata    │  │             │  │              │  │            │  │
│  │ (JSONB)     │  │             │  │              │  │            │  │
│  └─────────────┘  └─────────────┘  └──────────────┘  └────────────┘  │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ social_     │  │  settings   │  │  commands_   │  │ Other      │  │
│  │ posts       │  │  (config)   │  │  queue       │  │ Tables     │  │
│  │             │  │             │  │              │  │            │  │
│  │ id (UUID)   │  │ id (UUID)   │  │id (UUID)     │  │ (indexed)  │  │
│  │ platform    │  │ key         │  │command       │  │            │  │
│  │ content     │  │ value       │  │status        │  │            │  │
│  │ posted_at   │  │ type        │  │created_at    │  │            │  │
│  │ analytics   │  │ user_id     │  │completed_at  │  │            │  │
│  │ (JSONB)     │  │             │  │result        │  │            │  │
│  └─────────────┘  └─────────────┘  └──────────────┘  └────────────┘  │
│                                                                         │
│  🗄️ PRIMARY DRIVER: PostgreSQL 14+                                    │
│  🔌 CONNECTION POOLING: asyncpg with connection pool                  │
│  📊 TOTAL TABLES: 7+ (all operational)                                │
│  ✅ VERIFIED: 89 tasks loaded successfully                             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Task Creation

```
┌─────────────────────────────────────────────────────────────────┐
│ USER: Clicks "Create Task" button                              │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND: TaskManagement.jsx                                   │
│  ├─ Show modal with form fields                                │
│  ├─ Validate input (client-side)                               │
│  └─ Call createBlogPost() from cofounderAgentClient           │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ SERVICE: cofounderAgentClient.js                               │
│  ├─ Get auth token from localStorage                           │
│  ├─ Prepare JSON payload                                       │
│  ├─ Add Authorization header: "Bearer {token}"                │
│  └─ POST to http://localhost:8000/api/tasks                   │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ BACKEND: FastAPI Server                                        │
│  ├─ Router receives POST /api/tasks                            │
│  ├─ CORS middleware checks origin                              │
│  ├─ Extract & validate Bearer token                            │
│  ├─ Call get_current_user() dependency                         │
│  └─ Route handler: task_routes.py                              │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ AUTH: auth_unified.py                                          │
│  ├─ Extract token from Authorization header                    │
│  ├─ Verify JWT signature (HS256)                               │
│  ├─ Check token expiration                                     │
│  ├─ Extract user claims (user_id, email)                       │
│  └─ Return user object or raise 401                            │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ BACKEND: task_routes.py - POST /api/tasks                      │
│  ├─ Receive Pydantic model with validation                     │
│  ├─ Call DatabaseService.create_task()                         │
│  ├─ Generate UUID for task_id                                  │
│  ├─ Prepare INSERT query                                       │
│  └─ Return TaskResponse model                                  │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ DATABASE: PostgreSQL                                           │
│  ├─ Execute INSERT query via asyncpg                           │
│  ├─ Generate timestamps (created_at, updated_at)               │
│  ├─ Store JSONB metadata                                       │
│  ├─ Return inserted row                                        │
│  └─ Task now persisted ✅                                      │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ BACKEND: Return Response                                       │
│  ├─ Convert asyncpg row to TaskResponse                        │
│  ├─ Convert UUIDs to strings                                   │
│  ├─ Parse JSONB to dict                                        │
│  ├─ Return 201 Created with Location header                    │
│  └─ JSON body: { id, task_name, status, ... }                │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND: Response Handler                                     │
│  ├─ Receive 201 response                                       │
│  ├─ Parse JSON response                                        │
│  ├─ Update Zustand store with new task                         │
│  ├─ Close modal dialog                                         │
│  └─ Show success toast notification                            │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ UI: TaskManagement.jsx                                         │
│  ├─ Re-render with new task in list                            │
│  ├─ Sort/filter as needed                                      │
│  ├─ Update task count in header                                │
│  └─ NEW TASK NOW VISIBLE TO USER ✅                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Authentication Flow

```
┌──────────────────────────────────┐
│  App.jsx initializes             │
│  ↓                               │
│  AuthContext.jsx useEffect       │
│  ↓                               │
│  Check localStorage for token    │
│  ↓                               │
│  Token exists? ──NO──→ Call      │
│  │                    initializeDevToken()
│  │                    ↓
│  │                    mockTokenGenerator.js
│  │                    - Generate header
│  │                    - Create payload
│  │                    - Sign with HS256
│  │                    - Return 3-part JWT
│  │                    ↓
│  │                    Save to localStorage
│  │                    ↓
│  │                    authService.js
│  │
│  YES─→ Use existing token
│        ↓
│  Token stored in state ✓
│  │
│  └─→ Include in all API calls
│      Authorization: "Bearer {token}"
│      ↓
│      Backend receives request
│      ↓
│      auth_unified.py validates
│      - Extract token from header
│      - Verify signature (HS256)
│      - Check expiration
│      - Extract claims
│      ↓
│      Valid? ──YES→ Process request
│      │
│      └─NO→ Return 401 Unauthorized
│
│  User sees tasks, chat, etc ✓
│  Data loading confirmed (89 tasks) ✓
│
└──────────────────────────────────┘
```

---

## Route Structure (Frontend)

```
/api/tasks                    ← Core task management
  ├─ GET (list with pagination)
  ├─ POST (create)
  ├─ /{task_id} GET (detail)
  ├─ /{task_id} PATCH (update)
  ├─ /metrics/summary GET
  ├─ /intent POST
  └─ /confirm-intent POST

/api/content                  ← Content pipeline
  ├─ GET (list)
  ├─ POST (create)
  ├─ /{id} GET
  ├─ /{id} POST (update)
  ├─ /{id} DELETE
  └─ /approve POST

/api/chat                     ← Chat interface
  ├─ POST (send message)
  ├─ /history/{id} GET
  ├─ /history/{id} DELETE
  └─ /models GET

/api/agents                   ← Agent monitoring
  ├─ /status GET (all)
  ├─ /{name}/status GET
  ├─ /{name}/command POST
  ├─ /logs GET
  ├─ /memory/stats GET
  └─ /health GET

/api/orchestrator             ← Advanced workflows
  ├─ /process POST
  ├─ /status/{id} GET
  ├─ /approval/{id} GET
  ├─ /approve/{id} POST
  ├─ /history GET
  ├─ /training-data/export POST
  ├─ /training-data/upload-model POST
  ├─ /learning-patterns GET
  ├─ /business-metrics-analysis GET
  └─ /tools GET

/api/social                   ← Social publishing
  ├─ /platforms GET
  ├─ /connect POST
  ├─ /posts GET
  ├─ /posts POST (create)
  ├─ /posts/{id} DELETE
  ├─ /posts/{id}/analytics GET
  ├─ /generate POST
  ├─ /trending GET
  └─ /cross-post POST

/api/metrics                  ← Analytics
  ├─ /usage GET
  ├─ /costs GET
  ├─ GET (all)
  ├─ /summary GET
  └─ /track-usage POST

/api/settings                 ← Configuration
  ├─ /general GET
  ├─ /system GET
  ├─ /create POST
  ├─ /{id} PUT
  ├─ /{id} DELETE
  ├─ /theme PUT
  ├─ /theme DELETE
  ├─ /api-keys GET
  ├─ /webhooks POST
  └─ /integrations GET

/api/workflow                 ← Execution history
  ├─ /history GET
  ├─ /{id}/details GET
  ├─ /statistics GET
  ├─ /performance-metrics GET
  └─ /{id}/history GET

/api/ollama                   ← Local LLM
  ├─ /health GET
  ├─ /models GET
  ├─ /warmup POST
  ├─ /status GET
  └─ /select-model POST

/api/subtasks                 ← Specialized tasks
  ├─ /research POST
  ├─ /creative POST
  ├─ /qa POST
  ├─ /images POST
  └─ /format POST

/api/commands                 ← Command queue
  ├─ POST (queue)
  ├─ /{id} GET
  ├─ GET (list)
  ├─ /{id}/complete POST
  ├─ /{id}/fail POST
  ├─ /{id}/cancel POST
  ├─ /stats/queue-stats GET
  └─ /cleanup/clear-old POST

/api/bulk                     ← Bulk operations
  └─ POST (bulk operation)

/api/webhooks                 ← External integration
  └─ / POST (webhook handler)

/api/auth                     ← Authentication
  ├─ /github/callback POST
  ├─ /logout POST
  └─ /me GET

/api/posts                    ← CMS (public)
  ├─ GET (list)
  ├─ /{slug} GET
  ├─ /categories GET
  ├─ /tags GET
  └─ /cms/status GET

/api/models                   ← Model info
  ├─ GET (list)
  ├─ /{name} GET
  ├─ /list GET
  ├─ /{name}/info GET
  └─ -list GET (alternate)
```

---

## Frontend Page-to-Backend Route Mapping

```
DASHBOARD (/)
  └─ Renders TaskManagement
     ├─ GET /api/tasks (list)
     └─ GET /api/tasks/metrics/summary

TASKS (/tasks)
  └─ TaskManagement.jsx
     ├─ GET /api/tasks (polling every 5s)
     ├─ POST /api/tasks (create)
     ├─ GET /api/tasks/{id} (detail)
     └─ PATCH /api/tasks/{id} (update)

CHAT (/chat)
  └─ ChatPage.jsx
     ├─ POST /api/chat (send)
     ├─ GET /api/chat/history/{id}
     ├─ DELETE /api/chat/history/{id}
     └─ GET /api/chat/models

AGENTS (/agents)
  └─ AgentsPage.jsx
     ├─ GET /api/agents/status
     ├─ GET /api/agents/{name}/status
     ├─ POST /api/agents/{name}/command
     ├─ GET /api/agents/logs
     ├─ GET /api/agents/memory/stats
     └─ GET /api/agents/health

ANALYTICS (/analytics)
  └─ AnalyticsPage.jsx
     ├─ GET /api/metrics/usage
     ├─ GET /api/metrics/costs
     └─ GET /api/metrics/summary

CONTENT (/content)
  └─ ContentManagementPage.jsx
     ├─ GET /api/content
     ├─ POST /api/content
     ├─ GET /api/content/{id}
     ├─ POST /api/content/{id}
     └─ POST /api/content/approve

SOCIAL (/social)
  └─ EnhancedSocialPublishingPage.jsx
     ├─ GET /api/social/platforms
     ├─ POST /api/social/connect
     ├─ GET /api/social/posts
     ├─ POST /api/social/posts
     ├─ DELETE /api/social/posts/{id}
     ├─ GET /api/social/posts/{id}/analytics
     ├─ POST /api/social/generate
     ├─ GET /api/social/trending
     └─ POST /api/social/cross-post

MODELS (/models)
  └─ EnhancedOllamaModelsPage.jsx
     ├─ GET /api/ollama/health
     ├─ GET /api/ollama/models
     ├─ POST /api/ollama/warmup
     ├─ GET /api/ollama/status
     └─ POST /api/ollama/select-model

WORKFLOW HISTORY (/workflow)
  └─ WorkflowHistoryPage.jsx
     ├─ GET /api/workflow/history
     ├─ GET /api/workflow/{id}/details
     ├─ GET /api/workflow/statistics
     ├─ GET /api/workflow/performance-metrics
     └─ GET /api/workflow/{id}/history

SETTINGS (/settings)
  └─ SettingsManager.jsx
     ├─ GET /api/settings/general
     ├─ GET /api/settings/system
     ├─ POST /api/settings/create
     ├─ PUT /api/settings/{id}
     ├─ DELETE /api/settings/{id}
     ├─ PUT /api/settings/theme
     ├─ GET /api/settings/api-keys
     ├─ POST /api/settings/webhooks
     └─ GET /api/settings/integrations

❌ MISSING PAGES (need to create):
  ORCHESTRATOR (/orchestrator) ← 10 endpoints available
  COMMAND QUEUE (/commands) ← 8 endpoints available
```

---

## Token Lifecycle

```
┌─────────────────────────────────────────┐
│ USER VISITS OVERSIGHT HUB               │
│ http://localhost:3001                   │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ App.jsx loads                           │
│ AuthContext initializes                 │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ Check localStorage['auth_token']        │
│                                         │
│ Exists? ────NO─→ Generate new token    │
│ │               mockTokenGenerator.js   │
│ │               Create 3-part JWT       │
│ │               Save to localStorage    │
│ │                                       │
│ └───YES──→ Use existing token          │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ Token in state (accessToken)            │
│ Ready for API calls                     │
│                                         │
│ Token Structure:                        │
│ {                                       │
│   "header": {                           │
│     "alg": "HS256",                     │
│     "typ": "JWT"                        │
│   },                                    │
│   "payload": {                          │
│     "sub": "user@example.com",          │
│     "user_id": "dev_user_local",        │
│     "type": "access",                   │
│     "exp": 1733872871,                  │
│     "iat": 1733872511                   │
│   },                                    │
│   "signature": "HMAC-SHA256"            │
│ }                                       │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ USER MAKES API REQUEST                  │
│ e.g., fetch tasks                       │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ cofounderAgentClient.js                 │
│ getAuthHeaders() reads localStorage     │
│ Authorization: "Bearer {token}"         │
│ Adds to request headers                 │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ HTTP REQUEST                            │
│ GET /api/tasks                          │
│ Authorization: Bearer eyJ...            │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ BACKEND: auth_unified.py                │
│ Extract bearer token from header        │
│ Verify JWT signature (HS256)            │
│ - Same secret as frontend               │
│ - Same algorithm                        │
│ - Signature must match                  │
│                                         │
│ If invalid → 401 Unauthorized           │
│ If valid → Extract user_id              │
│ If expired → 401 Unauthorized           │
│ If valid & not expired → Continue       │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ ROUTE HANDLER: task_routes.py           │
│ User is authenticated ✓                 │
│ Process request normally                │
│ Database query executed                 │
│ Results returned                        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ HTTP RESPONSE 200 OK                    │
│ { tasks: [...], total: 89 }             │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ FRONTEND: useTasks hook                 │
│ Parse response                          │
│ Update Zustand store                    │
│ Re-render component                     │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ USER SEES 89 TASKS LOADED ✅            │
│                                         │
│ Token remains valid for 15 minutes      │
│ After expiration, new token generated   │
└─────────────────────────────────────────┘
```

---

## Component Hierarchy

```
App.jsx (Root)
  ├─ AuthProvider
  │  └─ AuthContext with token state
  │
  ├─ Router
  │  └─ AppContent
  │     ├─ ProtectedRoute wrapper
  │     │
  │     └─ LayoutWrapper (for protected routes)
  │        ├─ Header
  │        │  ├─ Logo
  │        │  ├─ User info
  │        │  └─ Status indicators
  │        │
  │        ├─ Navigation Sidebar
  │        │  ├─ Dashboard (→ TaskManagement)
  │        │  ├─ Tasks
  │        │  ├─ Chat
  │        │  ├─ Agents
  │        │  ├─ Analytics
  │        │  ├─ Content
  │        │  ├─ Social
  │        │  ├─ Models
  │        │  ├─ Workflow History
  │        │  ├─ Settings
  │        │  └─ (5 missing pages)
  │        │
  │        ├─ Main Content Area (Router Outlet)
  │        │  ├─ TaskManagement.jsx
  │        │  │  ├─ Task List (useTasks hook)
  │        │  │  ├─ Status Filter
  │        │  │  ├─ Pagination
  │        │  │  ├─ Create Button
  │        │  │  ├─ TaskDetailModal
  │        │  │  │  └─ Task detail fields
  │        │  │  └─ Status Update UI
  │        │  │
  │        │  ├─ ChatPage.jsx
  │        │  │  ├─ Conversation Selector
  │        │  │  ├─ Message List
  │        │  │  ├─ Input Box
  │        │  │  └─ Model Selector
  │        │  │
  │        │  ├─ AgentsPage.jsx
  │        │  │  ├─ Agents List
  │        │  │  ├─ Status Display
  │        │  │  ├─ Command Interface
  │        │  │  └─ Logs Viewer
  │        │  │
  │        │  ├─ (other pages...)
  │        │  │
  │        │  └─ (5 missing pages)
  │        │
  │        └─ Chat Panel (RHS - Always Visible)
  │           ├─ Model Selector
  │           ├─ Message Input
  │           ├─ Chat History
  │           └─ Quick Actions
  │
  └─ Public Routes (Login, etc.)
```

---

## Request/Response Cycle

```
FRONTEND          NETWORK            BACKEND         DATABASE
   │                                    │                │
   │─ Get auth token from store         │                │
   │  (3-part JWT format verified ✓)    │                │
   │                                    │                │
   │─ Prepare request payload           │                │
   │                                    │                │
   │─ Add Authorization header          │                │
   │  "Bearer {token}"                  │                │
   │                                    │                │
   │─ Fetch API call ────────────────→  │                │
   │  POST /api/tasks                   │                │
   │                                    │                │
   │                                    │─ Extract token │
   │                                    │  from header   │
   │                                    │                │
   │                                    │─ Verify JWT   │
   │                                    │  signature    │
   │                                    │  (HS256)      │
   │                                    │                │
   │                                    │─ Extract user │
   │                                    │  claims       │
   │                                    │                │
   │                                    │─ Validate     │
   │                                    │  request body │
   │                                    │  (Pydantic)   │
   │                                    │                │
   │                                    │─ Generate    │
   │                                    │  UUID for     │
   │                                    │  task_id      │
   │                                    │                │
   │                                    │─ Call service │
   │                                    │  layer        │
   │                                    │                │
   │                                    │             ┌─ INSERT task
   │                                    │             │  into tasks
   │                                    │             │
   │                                    │             │  Generate
   │                                    │             │  timestamps
   │                                    │             │
   │                                    │             │  Store JSONB
   │                                    │             │
   │                                    │         ←───┤ Return row
   │                                    │             │
   │                                    │─ Convert row│
   │                                    │  to response│
   │                                    │             │
   │  ←───────────────────────────────── 201 Created
   │  {                                 │
   │    "id": "uuid",                   │
   │    "task_name": "...",             │
   │    "status": "pending"             │
   │  }                                 │
   │                                    │
   │─ Parse response                    │
   │                                    │
   │─ Update Zustand store              │
   │                                    │
   │─ Re-render component               │
   │                                    │
   └─ User sees new task ✓
```

---

**Visual documentation complete** ✅

These diagrams provide:

- System architecture overview
- Data flow visualization
- Component hierarchy
- Authentication lifecycle
- Request/response cycle
- Route structure

Print this page for team reference!
