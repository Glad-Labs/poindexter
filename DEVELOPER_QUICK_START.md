# 🚀 **GLAD Labs Developer Quick Start Guide**

## **System Overview**

GLAD Labs AI Co-Founder is a production-ready Google Cloud native system with comprehensive AI automation, real-time monitoring, and enterprise security.

**Status:** ✅ **PRODUCTION READY v4.0**  
**Compliance:** GLAD-LABS-STANDARDS.md v4.0 ✅

---

## **Quick Start Commands**

### **Start All Services (Recommended)**

````bash
# Use VS Code task (preferred)
Ctrl+Shift+P → "Tasks: Run Task" → "Start All Services"

# Or start individually:
cd cms/strapi-v5-backend; npm run develop # Port 1337
cd web/public-site; npm run dev          # Port 3000
cd web/oversight-hub; npm start          # Port 3001
cd src; python -m uvicorn cofounder_agent.main:app --reload # Port 8000
```text

### **Development Environment Setup*

```bash
# Install dependencies
npm install  # Root level for workspace tools
cd web/public-site && npm install
cd web/oversight-hub && npm install
cd cms/strapi-v5-backend && npm install
pip install -r requirements.txt  # Python dependencies
```text

---

## **Key URLs & Access Points**

| Service               | URL                           | Purpose                       |
| --------------------- | ----------------------------- | ----------------------------- |
| **Public Site**       | `http://localhost:3000`       | Marketing website and blog    |
| **Oversight Hub**     | `http://localhost:3001`       | Command center dashboard      |
| **Strapi CMS**        | `http://localhost:1337/admin` | Content management            |
| **AI Co-Founder API** | `http://localhost:8000`       | Central orchestrator          |
| **API Docs**          | `http://localhost:8000/docs`  | Interactive API documentation |

---

## **Core Architecture**

```text
Public Site (Next.js) ──┐
Oversight Hub (React) ──┼──► AI Co-Founder (FastAPI) ──► Google Cloud
Strapi CMS (Headless) ──┘                              ├── Firestore
                                                        ├── Pub/Sub
                                                        └── Cloud Run
````

---

## **Essential API Endpoints**

### **AI Co-Founder (Port 8000)**

```bash
# Process natural language commands
POST /command
{
  "command": "create a blog post about AI trends",
  "mode": "async"  # or "sync" for development
}

# System health check
GET /status

# Performance metrics
GET /metrics/performance

# Create a task
POST /tasks
{
  "description": "Generate quarterly financial report",
  "assigned_agent": "financial_agent"
}
```

---

## **Development Workflow**

### **1. Code Changes**

```bash
# Frontend (React/Next.js)
cd web/oversight-hub
npm run dev  # Auto-reload enabled

# Backend (Python FastAPI)
cd src
python -m uvicorn cofounder_agent.main:app --reload

# CMS (Strapi)
cd cms/strapi-v5-backend
npm run develop
```

### **2. Testing**

```bash
# Python tests
cd src && pytest

# Frontend tests
cd web/oversight-hub && npm test
cd web/public-site && npm test

# Lint checks
ruff check src/         # Python
npm run lint           # JavaScript/Reacts
```

### **3. Environment Variables**

```bash
# Required for Google Cloud integration
export GOOGLE_APPLICATION_CREDENTIALS="path/to/service-account.json"
export GCP_PROJECT="your-project-id"
export FIRESTORE_DATABASE="(default)"

# Development overrides
export DEVELOPMENT_MODE="true"
export LOG_LEVEL="DEBUG"
```

---

## **Key Components**

### **AI Co-Founder (Central Orchestrator)**

- **File:** `src/cofounder_agent/main.py`
- **Purpose:** Central FastAPI application managing all AI agents
- **Features:** Dual async/sync modes, performance monitoring, Google Cloud integration

### **Oversight Hub (Command Interface)**

- **File:** `web/oversight-hub/src/components/dashboard/Dashboard.jsx`
- **Purpose:** Real-time dashboard with Firebase integration
- **Features:** Live metrics, task management, system health monitoring

### **Content Agent (Autonomous Creation)**

- **File:** `src/agents/content_agent/orchestrator.py`
- **Purpose:** Automated content creation and publishing
- **Features:** AI content generation, image creation, Strapi integration

---

## **Database Schema (Firestore)**

### **Key Collections**

```javascript
// Performance metrics
performance_metrics: {
  timestamp: DateTime,
  command_processing_time: Number,
  memory_usage: Number,
  success_rate: Number
}

// Task management
tasks: {
  id: String,
  description: String,
  status: String,  // pending, processing, completed, failed
  assigned_agent: String,
  created_at: DateTime,
  completed_at: DateTime
}

// Agent activities
agent_activities: {
  agent_id: String,
  activity_type: String,
  details: Object,
  timestamp: DateTime
}
```

---

## **Common Development Tasks**

### **Add New API Endpoint**

```python
# In src/cofounder_agent/main.py
@app.post("/your-endpoint")
async def your_endpoint(request: YourRequestModel):
    """Your endpoint description"""
    try:
        result = await your_logic(request)
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Error in your_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

### **Add New Dashboard Component**

```jsx
// In web/oversight-hub/src/components/
import React, { useState, useEffect } from 'react';
import { db } from '../firebase';
import { collection, onSnapshot } from 'firebase/firestore';

export default function YourComponent() {
  const [data, setData] = useState([]);

  useEffect(() => {
    const unsubscribe = onSnapshot(
      collection(db, 'your-collection'),
      (snapshot) => {
        const newData = snapshot.docs.map((doc) => ({
          id: doc.id,
          ...doc.data(),
        }));
        setData(newData);
      }
    );
    return () => unsubscribe();
  }, []);

  return (
    <div className="bg-white p-6 rounded-lg shadow">
      {/* Your component JSX */}
    </div>
  );
}
```

### **Add New Agent**

```python
# In src/agents/your_agent/
class YourAgent:
    def __init__(self):
        self.name = "your_agent"

    async def process_command(self, command: str) -> dict:
        """Process command and return result"""
        try:
            # Your agent logic here
            result = await self.your_logic(command)
            return {
                "status": "success",
                "result": result,
                "agent": self.name
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "agent": self.name
            }
```

---

## **Debugging & Troubleshooting**

### **Check System Health**

```bash
# API health check
curl http://localhost:8000/status

# Database connectivity
curl http://localhost:8000/metrics/health

# Performance metrics
curl http://localhost:8000/metrics/performance
```

### **Log Analysis**

```bash
# View logs in development
tail -f logs/app.log

# Check specific component logs
grep "ERROR" logs/app.log
grep "cofounder_agent" logs/app.log
```

### **Common Issues**

| Issue               | Solution                               |
| ------------------- | -------------------------------------- |
| Port already in use | `lsof -ti:8000 \| xargs kill -9`       |
| Google Cloud auth   | Check `GOOGLE_APPLICATION_CREDENTIALS` |
| Database connection | Verify Firestore rules and project ID  |
| Module not found    | `pip install -r requirements.txt`      |

---

## **Production Deployment**

### **Google Cloud Setup**

```bash
# Authenticate with Google Cloud
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Deploy to Cloud Run
gcloud run deploy ai-cofounder \
  --source=./src \
  --port=8000 \
  --memory=2Gi \
  --cpu=2 \
  --max-instances=10
```

### **Environment Configuration**

```bash
# Production environment variables
export DEVELOPMENT_MODE="false"
export LOG_LEVEL="INFO"
export FIRESTORE_DATABASE="(default)"
export ENABLE_PERFORMANCE_MONITORING="true"
```

---

## **Useful Resources**

### **Documentation**

- [Complete System Docs](./SYSTEM_DOCUMENTATION.md)
- [Production Deployment](./PRODUCTION_DEPLOYMENT_GUIDE.md)
- [Architecture Standards](./GLAD-LABS-STANDARDS.md)
- [Database Schemas](./data_schemas.md)

### **Component READMEs**

- [AI Co-Founder](./src/cofounder_agent/README.md)
- [Content Agent](./src/agents/content_agent/README.md)
- [Oversight Hub](./web/oversight-hub/README.md)
- [Public Site](./web/public-site/README.md)

---

## **Support & Maintenance**

### **Code Quality**

- **Python:** `ruff check src/` and `ruff format src/`
- **JavaScript:** `npm run lint` and `npm run format`
- **Markdown:** `markdownlint *.md`

### **Testing**

- **Unit Tests:** `pytest src/tests/`
- **Integration:** `npm test` in web directories
- **E2E:** Manual testing checklist in deployment guide

### **Performance Monitoring**

- **Health Dashboard:** `http://localhost:3001`
- **API Metrics:** `http://localhost:8000/metrics/performance`
- **Google Cloud Monitoring:** Cloud Console

---

**🚀 You're ready to start developing with GLAD Labs AI Co-Founder!**

For detailed implementation guidance, see the complete documentation suite.
