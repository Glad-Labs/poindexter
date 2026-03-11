# 🎛️ Oversight Hub

React admin dashboard for monitoring and controlling the Glad Labs AI Co-Founder system.

> **Documentation Update (Feb 21, 2026):** 18 legacy audit and refactoring docs have been moved to `archive/cleanup-feb2026/` for better organization. See [archive index](archive/cleanup-feb2026/INDEX.md) for access.

**Status:** ✅ Production Ready  
**Technology:** React 18 + Material-UI + Zustand  
**Port:** 3001

---

## 📖 Overview

The Oversight Hub is a React application that provides a comprehensive dashboard for:

- Monitoring the status of content creation tasks
- Viewing financial metrics and ROI calculations
- Interacting with the AI agents through a command interface
- Configuring AI models and API providers
- Tracking system health and performance
- Managing agent execution and workflows

---

## 🔧 Tech Stack

- **React 18:** Modern UI library with hooks
- **Material-UI:** Enterprise-grade component library
- **Zustand:** Lightweight state management
- **Tailwind CSS:** Utility-first styling
- **Axios:** HTTP client for API communication
- **Chat UI Kit:** Professional chat interface for agent interactions

---

## 🚀 Quick Start

### Prerequisites

- Node.js 18.x - 22.x
- npm 10+

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/glad-labs/glad-labs-website.git
   cd glad-labs-website
   ```

2. Navigate to the Oversight Hub directory:

   ```bash
   cd web/oversight-hub
   ```

3. Install the dependencies:

   ```bash
   npm install
   ```

### Running the Application

```bash
npm start
```

The application will be available at **[http://localhost:3001](http://localhost:3001)**

---

## 📊 Key Features

### Dashboard

- **System Health:** Real-time status of all services (Strapi, Backend, AI Agents)
- **Task Management:** Monitor all content creation tasks in progress
- **Performance Metrics:** View response times, error rates, and system health
- **Quick Actions:** Trigger new tasks, pause/resume agents, view logs

### Agent Monitor

- **Active Agents:** See all running agents and their status
- **Execution History:** View past agent executions and results
- **Error Tracking:** Monitor and debug agent failures
- **Performance Insights:** Agent speed, quality scores, self-critique loops

### Model Management

- **Available Models:** View all configured AI models
- **Provider Status:** Check connectivity to Ollama, OpenAI, Anthropic, Google
- **Model Selection:** Choose preferred model for each agent type
- **Fallback Chain:** See model prioritization (Ollama → Claude → GPT → Gemini)

### Financial Dashboard

- **Cost Tracking:** Monitor API usage costs by provider
- **ROI Calculations:** Track return on investment for content initiatives
- **Budget Alerts:** Set and monitor spending limits
- **Cost Trends:** Historical cost analysis and predictions

### Settings

- **Theme Settings:** Light/dark mode toggle
- **Auto-refresh Options:** Configure dashboard update intervals
- **API Key Management:** Safely store and manage provider API keys
- **System Configuration:** Adjust timeouts, rate limits, and other settings

### Command Pane

- **Chat Interface:** Interact with the AI Co-Founder agent
- **Natural Language:** Give commands in plain English
- **Feedback Loop:** Receive real-time responses and feedback
- **Command History:** View past commands and results

---

## 🏗️ Architecture

### Project Structure

```text
web/oversight-hub/
├── public/                     # Static files
│   ├── index.html
│   └── favicon.ico
├── src/
│   ├── components/
│   │   ├── Dashboard.jsx       # Main dashboard view
│   │   ├── TaskManager.jsx     # Task management UI
│   │   ├── AgentMonitor.jsx    # Agent status display
│   │   ├── ModelConfig.jsx     # Model configuration
│   │   ├── CommandPane.jsx     # Command interface
│   │   ├── Header.jsx          # Navigation header
│   │   └── Settings.jsx        # Settings page
│   ├── store/
│   │   └── useStore.js         # Zustand global state
│   ├── lib/
│   │   └── api.js              # API client
│   ├── styles/
│   │   └── index.css           # Global styles
│   ├── App.jsx                 # Main app component
│   └── index.js                # Entry point
├── package.json
└── README.md
```

### State Management (Zustand)

```javascript
// src/store/useStore.js
const useStore = create((set) => ({
  // UI State
  theme: 'light',
  setTheme: (theme) => set({ theme }),

  // Tasks
  tasks: [],
  setTasks: (tasks) => set({ tasks }),

  // Models
  activeModels: [],
  setActiveModels: (models) => set({ activeModels: models }),

  // Agents
  agentStatus: {},
  setAgentStatus: (status) => set({ agentStatus: status }),
}));
```

---

## 🔌 API Integration

### REST Endpoints

All data flows through the FastAPI backend:

```bash
# System Health
GET /api/health                    # System status
GET /api/agents/status             # All agents status
GET /api/models/status             # Model providers status

# Tasks
POST /api/tasks                    # Create new task
GET  /api/tasks/:id               # Get task details
GET  /api/tasks                   # List all tasks
PUT  /api/tasks/:id               # Update task
DELETE /api/tasks/:id             # Delete task

# Models
GET  /api/models                  # List available models
POST /api/models/test             # Test model connection
PUT  /api/models/:id/configure    # Configure model

# Agents
GET  /api/agents/{agent}/status   # Agent status
POST /api/agents/{agent}/command  # Send command to agent
GET  /api/agents/logs             # View agent logs
```

### Configuration

In `src/lib/api.js` (uses Vite conventions with `import.meta.env.VITE_*`):

```javascript
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export const createTask = async (taskData) => {
  const response = await axios.post(`${API_BASE_URL}/api/tasks`, taskData);
  return response.data;
};
```

**Note:** This application uses Vite, not Create React App, so environment variables use `VITE_*` prefix (accessed via `import.meta.env.*`), not `REACT_APP_*` prefix.

---

## 📈 Dashboard Sections

### System Health Panel

Shows real-time metrics:

- Service uptime (Strapi, Backend, Agents)
- Database connection status
- Memory and CPU usage
- Error rate and latency
- Model provider connectivity

### Tasks Panel

Displays all tasks with:

- Task ID, name, and type
- Status (pending, in-progress, completed, failed)
- Progress percentage
- Assigned agents
- Created/updated timestamps
- Action buttons (pause, retry, view details)

### Agents Panel

Shows all agents with:

- Agent name and type
- Current status (idle, working, error)
- Tasks completed/failed count
- Average execution time
- Last activity timestamp
- Real-time logs

### Models Panel

Displays:

- Available models by provider
- Connection status (online/offline)
- Current provider in use
- Fallback chain visualization
- Cost per request
- Model-specific settings

---

## 🧪 Development

### Available Scripts

```bash
# Start development server
npm start                          # With auto-reload

# Build for production
npm run build

# Run tests
npm test                           # Interactive watch mode
npm test:ci                        # CI mode (no watch)

# Code quality
npm run lint                       # ESLint check
npm run lint:fix                   # Auto-fix issues
npm run format                     # Prettier formatting
```

### Component Development

Create new components in `src/components/`:

```javascript
// src/components/MyComponent.jsx
import React from 'react';
import { Box, Card, Typography } from '@mui/material';
import useStore from '../store/useStore';

export default function MyComponent() {
  const { state, dispatch } = useStore();

  return (
    <Card>
      <Box p={2}>
        <Typography variant="h6">{state.title}</Typography>
      </Box>
    </Card>
  );
}
```

### Store Integration

Access global state in any component:

```javascript
import useStore from '../store/useStore';

function MyComponent() {
  const { tasks, setTasks } = useStore();

  // Component code
}
```

---

## 🚀 Deployment

### Build for Production

```bash
npm run build

# Output in build/ directory
# Ready to deploy to Vercel or any static host
```

### Environment Variables

Set in `.env.local` (uses Vite `VITE_*` convention):

```bash
VITE_API_BASE_URL=https://api.railway.app      # Backend URL
VITE_API_URL=https://api.railway.app           # Alternative backend URL
VITE_WS_BASE_URL=https://api.railway.app       # WebSocket URL
VITE_GH_OAUTH_CLIENT_ID=your_github_client_id  # GitHub OAuth
VITE_USE_MOCK_AUTH=false                       # Use mock auth (dev only)
```

**Note:** This is a Vite application, so use `VITE_*` prefix for environment variables (accessed via `import.meta.env.VITE_*` in code).

### Deploy to Vercel

```bash
npm install -g vercel
vercel --prod
```

---

## 🐛 Troubleshooting

### Cannot Connect to Backend

**Symptom:** API calls fail with "Connection refused"

**Solution:**

1. Verify backend is running: `curl http://localhost:8000/docs`
2. Check `REACT_APP_API_URL` in `.env`
3. Check CORS settings in backend

### Tasks Not Showing

**Symptom:** Dashboard loads but no tasks display

**Solution:**

1. Check browser console (F12) for errors
2. Verify database is connected: `GET /api/health`
3. Check user has permission to view tasks
4. Try refreshing page

### Models Not Connecting

**Symptom:** "Failed to connect to models"

**Solution:**

1. Check Ollama is running: `ollama serve`
2. Verify API keys in Settings
3. Test model connection: `GET /api/models/test-all`
4. Check backend logs for errors

---

## 📚 Additional Resources

- **Material-UI Docs:** https://mui.com/material-ui/
- **Zustand Docs:** https://github.com/pmndrs/zustand
- **React Docs:** https://react.dev
- **Glad Labs Architecture:** [docs/02-Architecture/System-Design.md](../../docs/02-Architecture/System-Design.md)
- **API Documentation:** [docs/reference/API_CONTRACT_CONTENT_CREATION.md](../../docs/reference/API_CONTRACT_CONTENT_CREATION.md)

---

## 📖 Resources & Documentation

- **[Setup Guide](../../docs/01-Getting-Started/Local-Development-Setup.md)** - Getting started
- **[Architecture](../../docs/02-Architecture/System-Design.md)** - System design
- **[Development Workflow](../../docs/04-Development/Development-Workflow.md)** - Testing & CI/CD
- **[Operations Guide](../../docs/05-Operations/Operations-Maintenance.md)** - Production support
- **[Testing Guide](../../docs/04-Development/Testing-Guide.md)** - Comprehensive test documentation
- **[API Documentation](../../docs/reference/API_CONTRACTS.md)** - REST API specifications
