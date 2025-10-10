# 🧠 **AI Co-Founder Agent - Central Orchestrator**

## **Overview**

The AI Co-Founder Agent serves as the central "big brain" of GLAD Labs, orchestrating all business operations through intelligent command processing, task management, and agent coordination. Built with FastAPI and Google Cloud native services, it provides production-ready automation with comprehensive monitoring and real-time data operations.

**Status:** ✅ Production Ready v4.0  
**Technology:** Python 3.12+ with FastAPI  
**Port:** 8000  
**Architecture:** Google Cloud Native (Firestore + Pub/Sub)

---

## **🏗️ Architecture**

### **Core Components**

````text
┌─────────────────────────────────────────────────────┐
│                AI Co-Founder Agent                  │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────────┐    ┌──────────────┐              │
│  │ FastAPI     │◄──►│ Orchestrator │              │
│  │ Web Server  │    │ Logic        │              │
│  └─────────────┘    └──────────────┘              │
│         │                    │                     │
│         ▼                    ▼                     │
│  ┌─────────────┐    ┌──────────────┐              │
│  │ Performance │    │ Agent        │              │
│  │ Monitor     │    │ Managers     │              │
│  └─────────────┘    └──────────────┘              │
│         │                    │                     │
│         ▼                    ▼                     │
│  ┌─────────────┐    ┌──────────────┐              │
│  │ Firestore   │    │ Pub/Sub      │              │
│  │ Database    │    │ Messaging    │              │
│  └─────────────┘    └──────────────┘              │
│                                                     │
└─────────────────────────────────────────────────────┘
```bash

### **Key Features**

- **Dual Processing Modes**: Async for production, sync for development
- **Google Cloud Integration**: Native Firestore and Pub/Sub connectivity
- **Performance Monitoring**: Real-time metrics and health tracking
- **Structured Logging**: Production-ready logging with `structlog`
- **API Documentation**: Automatic OpenAPI/Swagger documentation
- **Error Handling**: Comprehensive error recovery and logging

---

## **🛠️ Installation & Setup**

### **Prerequisites**

```bash
# Python 3.12+
python --version

# Required packages (auto-installed)
pip install fastapi uvicorn structlog pydantic google-cloud-firestore google-cloud-pubsub
```text

### **Environment Configuration**

Create `.env` file in the project root:

```bash
# Google Cloud Configuration
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
GCP_PROJECT_ID=your-project-id
FIRESTORE_DATABASE=(default)
PUBSUB_TOPIC_CONTENT=content-creation-requests

# Development Mode (optional)
DEVELOPMENT_MODE=true  # Enables graceful fallback without Google Cloud
```text

### **Development Startup**

```bash
# From project root
cd src
python -m uvicorn cofounder_agent.main:app --reload --host 0.0.0.0 --port 8000

# Or using npm script
npm run dev:cofounder
````

### **Production Deployment**

```bash
# Google Cloud Run deployment
gcloud run deploy cofounder-agent \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GCP_PROJECT_ID=your-project-id
```

---

## **📚 API Documentation**

### **Base URLs**

- **Development:** `http://localhost:8000`
- **Production:** `https://your-domain.com`
- **Interactive Docs:** `/docs` (Swagger UI)
- **OpenAPI Schema:** `/openapi.json`

### **Core Endpoints**

#### **POST /command**

Process natural language commands through the AI Co-Founder.

**Request:**

```json
{
  "command": "Create blog post about AI automation trends",
  "context": {
    "priority": "high",
    "deadline": "2025-10-15"
  },
  "priority": "normal"
}
```

**Response:**

```json
{
  "response": "I'll create a comprehensive blog post about AI automation trends focusing on business transformation and emerging technologies...",
  "task_id": "task_abc123",
  "metadata": {
    "estimated_completion": "15-20 minutes",
    "agent_assigned": "content-creation-agent-v1",
    "priority": "normal",
    "processing_time_ms": 234
  }
}
```

#### **GET /status**

Retrieve comprehensive system health and service connectivity.

**Response:**

```json
{
  "status": "healthy",
  "data": {
    "service": "online",
    "google_cloud_available": true,
    "orchestrator_initialized": true,
    "timestamp": "1696851234.567",
    "firestore": {
      "status": "connected",
      "latency_ms": 45,
      "last_operation": "2025-10-09T14:30:00Z"
    },
    "pubsub": {
      "status": "connected",
      "topics_available": 3,
      "messages_processed": 157
    }
  }
}
```

#### **POST /tasks**

Create new tasks for content creation or business operations.

**Request:**

```json
{
  "topic": "AI automation in small businesses",
  "task_type": "content_creation",
  "metadata": {
    "priority": 1,
    "target_audience": "Small business owners",
    "content_type": "blog_post",
    "word_count_target": 1500
  }
}
```

**Response:**

```json
{
  "task_id": "task_def456",
  "status": "created",
  "message": "Task created for 'AI automation in small businesses'"
}
```

#### **GET /tasks/pending**

Retrieve pending tasks from the task queue.

**Query Parameters:**

- `limit` (optional): Number of tasks to return (default: 10)

**Response:**

```json
{
  "tasks": [
    {
      "taskId": "task_abc123",
      "taskName": "Create blog post about AI trends",
      "agentId": "content-creation-agent-v1",
      "status": "queued",
      "createdAt": "2025-10-09T14:00:00Z",
      "metadata": {
        "priority": 2,
        "estimated_duration_minutes": 45
      }
    }
  ],
  "count": 1
}
```

### **Performance Monitoring Endpoints**

#### **GET /metrics/performance**

Get comprehensive performance metrics and analytics.

**Query Parameters:**

- `hours` (optional): Time window for metrics (default: 24)

**Response:**

```json
{
  "metrics": {
    "command_count": 47,
    "average_response_time": 0.234,
    "peak_response_time": 1.456,
    "error_count": 2,
    "error_rate": 0.043,
    "database_operations": 156,
    "average_db_latency": 0.045,
    "memory_usage_mb": 234.5,
    "active_operations": 3
  },
  "status": "success"
}
```

#### **GET /metrics/health**

Get current system health metrics and status.

**Response:**

```json
{
  "health": {
    "overall_health": "excellent",
    "health_score": 95,
    "services": {
      "fastapi": "healthy",
      "firestore": "healthy",
      "pubsub": "healthy",
      "performance_monitor": "healthy"
    },
    "last_check": "2025-10-09T14:30:00Z"
  },
  "status": "success"
}
```

#### **POST /metrics/reset**

Reset session-level performance metrics (admin endpoint).

**Response:**

```json
{
  "message": "Performance metrics reset successfully",
  "status": "success"
}
```

---

## **🔧 Command Processing**

### **Supported Command Types**

#### **Content Creation**

- `"Create blog post about [topic]"`
- `"Write article on [subject]"`
- `"Generate content for [theme]"`

#### **Task Management**

- `"Show pending tasks"`
- `"Get task status"`
- `"Create task for [objective]"`

#### **Financial Operations**

- `"Show financial summary"`
- `"Get budget status"`
- `"Track expenses"`

#### **System Operations**

- `"System status"`
- `"Health check"`
- `"Performance metrics"`

#### **Agent Management**

- `"Run content pipeline"`
- `"Trigger content agent"`
- `"Agent status"`

### **Command Processing Flow**

1. **Command Reception**: FastAPI receives command via POST /command
2. **Command Analysis**: Natural language processing and intent recognition
3. **Route Determination**: Command routed to appropriate handler
4. **Agent Coordination**: Specialized agents invoked as needed
5. **Database Operations**: Firestore operations for task management
6. **Response Generation**: Structured response with metadata
7. **Performance Tracking**: Metrics collection and monitoring

---

## **📊 Performance Monitoring**

### **Key Metrics Tracked**

#### **System Performance**

- **Response Times**: Command processing latency (average, min, max)
- **Throughput**: Commands processed per minute/hour
- **Error Rates**: Failed operations and recovery times
- **Resource Usage**: Memory consumption and CPU utilization

#### **Database Performance**

- **Firestore Latency**: Read/write operation times
- **Connection Health**: Database connectivity status
- **Operation Counts**: Database operations per time period
- **Error Tracking**: Database operation failures

#### **Agent Performance**

- **Task Completion**: Agent processing times and success rates
- **Queue Depth**: Pending task accumulation
- **Agent Utilization**: Resource consumption per agent
- **Communication Latency**: Pub/Sub messaging performance

### **Health Monitoring**

#### **Service Health Checks**

- **FastAPI Service**: Web server responsiveness
- **Firestore Connection**: Database connectivity
- **Pub/Sub Connection**: Messaging service status
- **Agent Availability**: Specialized agent health

#### **Performance Thresholds**

- **Response Time**: Warning >2s, Critical >5s
- **Error Rate**: Warning >5%, Critical >10%
- **Memory Usage**: Warning >80%, Critical >95%
- **Database Latency**: Warning >500ms, Critical >1000ms

---

## **🔒 Security & Configuration**

### **Authentication**

- **Google Cloud Service Accounts**: Secure API authentication
- **Environment Variables**: Credential isolation and security
- **CORS Configuration**: Restricted frontend origins

### **Input Validation**

- **Pydantic Models**: Request/response validation
- **Command Sanitization**: Safe command processing
- **Error Handling**: Secure error messages

### **Logging & Auditing**

- **Structured Logging**: JSON-formatted logs with `structlog`
- **Operation Tracking**: Comprehensive audit trail
- **Error Monitoring**: Detailed error capture and analysis

---

## **🛠️ Development**

### **Local Development**

```bash
# Start with hot reloading
uvicorn cofounder_agent.main:app --reload

# Development mode (without Google Cloud)
DEVELOPMENT_MODE=true uvicorn cofounder_agent.main:app --reload

# Run tests
pytest tests/

# Lint code
ruff check .
```

### **Testing**

```bash
# Unit tests
pytest tests/unit/

# Integration tests
pytest tests/integration/

# API tests
pytest tests/api/

# Performance tests
pytest tests/performance/
```

### **Debugging**

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Health check
curl http://localhost:8000/status

# Performance metrics
curl http://localhost:8000/metrics/performance
```

---

## **📁 File Structure**

```text
src/cofounder_agent/
├── main.py                 # FastAPI application and routes
├── orchestrator_logic.py   # Core orchestration logic
├── services/
│   ├── firestore_client.py # Firestore database operations
│   ├── pubsub_client.py    # Pub/Sub messaging
│   └── performance_monitor.py # Performance tracking
├── tests/
│   ├── unit/              # Unit tests
│   ├── integration/       # Integration tests
│   └── api/               # API endpoint tests
└── README.md              # This documentation
```

---

## **🚀 Production Deployment**

### **Google Cloud Run**

```bash
# Build and deploy
gcloud run deploy cofounder-agent \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --concurrency 100 \
  --set-env-vars GCP_PROJECT_ID=your-project-id
```

### **Environment Variables (Production)**

```bash
# Required
GCP_PROJECT_ID=your-production-project
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# Optional
FIRESTORE_DATABASE=(default)
PUBSUB_TOPIC_CONTENT=content-creation-requests
LOG_LEVEL=INFO
```

### **Monitoring Setup**

```bash
# Enable monitoring APIs
gcloud services enable monitoring.googleapis.com
gcloud services enable logging.googleapis.com

# Create alerting policies
gcloud alpha monitoring policies create --policy-from-file=alerting-policy.yaml
```

---

## **🔧 Troubleshooting**

### **Common Issues**

#### **Google Cloud Connection**

```bash
# Check authentication
gcloud auth application-default login

# Verify project access
gcloud projects list

# Test Firestore
gcloud firestore operations list
```

#### **Service Startup**

```bash
# Check Python environment
python --version
pip list | grep -E "(fastapi|structlog)"

# Verify dependencies
pip install -r requirements.txt
```

#### **Performance Issues**

```bash
# Monitor metrics
curl http://localhost:8000/metrics/health

# Check logs
tail -f logs/cofounder_agent.log

# Reset metrics
curl -X POST http://localhost:8000/metrics/reset
```

### **Log Analysis**

```json
{
  "timestamp": "2025-10-09T14:30:00Z",
  "level": "INFO",
  "logger_name": "cofounder_agent.main",
  "message": "Command processed successfully",
  "command": "system status",
  "duration_ms": 234,
  "status": "success"
}
```

---

## **📈 Version History**

### **v4.0 (October 2025) - Production Ready**

✅ Complete Google Cloud integration  
✅ Comprehensive performance monitoring  
✅ Production deployment configuration  
✅ Enhanced security and error handling

### **v3.5 (September 2025) - Enhanced Integration**

✅ Dual async/sync architecture  
✅ Real Firestore and Pub/Sub integration  
✅ Structured logging implementation

### **v3.0 (August 2025) - Core Implementation**

✅ FastAPI foundation  
✅ Basic orchestration logic  
✅ Agent management framework

---

**Maintained by:** GLAD Labs Development Team  
**Last Updated:** October 9, 2025  
**Next Review:** November 9, 2025
