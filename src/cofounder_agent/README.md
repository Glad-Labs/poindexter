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

```text
src/cofounder_agent/
├── __init__.py
├── main.py                 # FastAPI application and routes
├── orchestrator_logic.py   # Core orchestration logic
├── services/
│   ├── __init__.py
│   ├── firestore_client.py # Firestore database operations
│   ├── pubsub_client.py    # Pub/Sub messaging
│   └── performance_monitor.py # Performance tracking
├── start_server.py         # Script to start the server
└── test_orchestrator.py    # Tests for the orchestrator
```

### **Key Features**

- **Dual Processing Modes**: Async for production, sync for development
- **Google Cloud Integration**: Native Firestore and Pub/Sub connectivity
- **Performance Monitoring**: Real-time metrics and health tracking
- **Structured Logging**: Production-ready logging with `structlog`
- **API Documentation**: Automatic OpenAPI/Swagger documentation
- **Error Handling**: Comprehensive error recovery and logging

---

## **🛠️ Installation & Setup**

For detailed instructions on how to set up the environment and install dependencies, please refer to the main [project README.md](../../README.md).

### **Development Startup**

```bash
# From project root
npm run dev:cofounder
```

---

## **📚 API Documentation**

### **Base URLs**

- **Development:** `http://localhost:8000`
- **Interactive Docs:** `/docs` (Swagger UI)
- **OpenAPI Schema:** `/openapi.json`

### **Core Endpoints**

(The API documentation remains the same as it is still accurate)

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

#### **Financial Operations (Planned)**

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

---

**Maintained by:** GLAD Labs Development Team  
**Last Updated:** October 11, 2025
