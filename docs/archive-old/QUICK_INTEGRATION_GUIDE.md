# 🚀 Quick Integration Guide - Training System

## Step 1: Database Setup (5 minutes)

```bash
# Connect to PostgreSQL
psql -U postgres -d glad_labs

# Run migration to create all tables
\i src/cofounder_agent/migrations/003_training_data_tables.sql

# Verify tables created (should show 8+ tables)
\dt orchestrator_* social_* web_* financial_*

# Exit
\q
```

## Step 2: Backend Service Setup (10 minutes)

### File: `src/cofounder_agent/main.py`

Add these imports at the top:

```python
from services.training_data_service import TrainingDataService
from services.fine_tuning_service import FineTuningService
from services.legacy_data_integration import LegacyDataIntegrationService
from routes.training_routes import router as training_router, set_services
```

In your app initialization (after creating `app = FastAPI()`):

```python
# Initialize services
training_service = TrainingDataService(db_pool)
fine_tuning_service = FineTuningService()
legacy_service = LegacyDataIntegrationService(db_pool)

# Register services with routes
set_services(training_service, fine_tuning_service)

# Mount router
app.include_router(training_router, prefix="/api/orchestrator")
```

### File: `src/cofounder_agent/intelligent_orchestrator.py`

Add imports:

```python
from services.training_data_service import TrainingDataService
from services.legacy_data_integration import LegacyDataIntegrationService
```

After execution completes, save training example:

```python
# Calculate quality score (0.0-1.0)
quality_score = calculate_quality(execution_result)

# Save training example
await training_service.save_training_example(
    execution_id=execution_id,
    user_request=user_request,
    intent=detected_intent,
    execution_plan=execution_plan,
    execution_result=execution_result,
    quality_score=quality_score,
    success=True  # or False based on result
)
```

## Step 3: Environment Variables

### File: `.env` or environment setup

```bash
# Optional: API Keys for fine-tuning (get from providers)
GOOGLE_API_KEY=your_google_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
OPENAI_API_KEY=your_openai_api_key_here

# Optional: Ollama configuration
OLLAMA_BASE_URL=http://localhost:11434

# Database (should already exist)
DATABASE_URL=postgresql://postgres:password@localhost:5432/glad_labs
```

## Step 4: Frontend Routes

### Already Done ✅

- `AppRoutes.jsx` - Routes added
- `LayoutWrapper.jsx` - Navigation updated
- Pages created:
  - `/training` → TrainingDataDashboard
  - `/queue` → CommandQueuePage
  - `/orchestrator` → OrchestratorPage

Just restart your frontend dev server!

## Step 5: Testing

### Test Database

```bash
# Check if data exists
psql -U postgres -d glad_labs -c "SELECT COUNT(*) FROM orchestrator_training_data;"

# Check tables
psql -U postgres -d glad_labs -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
```

### Test API Endpoints

```bash
# List training data
curl http://localhost:8000/api/orchestrator/training/data

# Get statistics
curl http://localhost:8000/api/orchestrator/training/stats

# List datasets
curl http://localhost:8000/api/orchestrator/training/datasets

# List fine-tuning jobs
curl http://localhost:8000/api/orchestrator/training/jobs
```

### Test Frontend Pages

1. **Training Dashboard** - http://localhost:3000/training
   - Should show stats, filtering options, dataset creation
2. **Command Queue** - http://localhost:3000/queue
   - Should show command history (empty initially)
3. **Orchestrator** - http://localhost:3000/orchestrator
   - Should show form to submit requests

## Step 6: First Data Upload

### Option A: Manual via API

```python
import asyncio
import httpx

async def upload_training_example():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/orchestrator/training/data",
            json={
                "user_request": "Draft a tweet about AI",
                "intent": "content_creation",
                "execution_plan": "Use content agent → Generate tweet → Format",
                "execution_result": "Tweet generated successfully",
                "quality_score": 0.9,
                "success": True,
                "tags": ["PRODUCTION", "MANUAL_APPROVED"]
            }
        )
        print(response.json())

asyncio.run(upload_training_example())
```

### Option B: Via Training Dashboard

1. Navigate to `/training`
2. Manually add training data through API (data populates from database)
3. Create dataset from filtered data
4. Select dataset and start fine-tuning job

## Step 7: Run Ollama (if using local fine-tuning)

```bash
# Start Ollama
ollama serve

# In another terminal, pull a model
ollama pull mistral

# Verify it's working
ollama list
```

## Troubleshooting

| Problem                                                        | Solution                                                                                  |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `ModuleNotFoundError: No module named 'training_data_service'` | Verify file is in `src/cofounder_agent/services/`                                         |
| `Database table does not exist`                                | Run migration: `psql -U postgres -d glad_labs -f migrations/003_training_data_tables.sql` |
| `Connection refused: Ollama`                                   | Start Ollama with `ollama serve`                                                          |
| API returns 404                                                | Verify `app.include_router(training_router)` is in main.py                                |
| Frontend shows 404 for routes                                  | Restart frontend dev server after AppRoutes changes                                       |
| "Cannot find module" in React                                  | Clear node_modules and reinstall: `npm install`                                           |

## File Checklist

Backend Services:

- [x] `src/cofounder_agent/services/training_data_service.py` (400 lines)
- [x] `src/cofounder_agent/services/fine_tuning_service.py` (350 lines)
- [x] `src/cofounder_agent/services/legacy_data_integration.py` (350 lines)
- [x] `src/cofounder_agent/routes/training_routes.py` (400 lines)

Frontend Pages:

- [x] `web/oversight-hub/src/pages/TrainingDataDashboard.jsx` (600 lines)
- [x] `web/oversight-hub/src/pages/CommandQueuePage.jsx` (400 lines)
- [x] `web/oversight-hub/src/pages/OrchestratorPage.jsx` (500 lines)

Routes & Navigation:

- [x] `web/oversight-hub/src/routes/AppRoutes.jsx` (updated)
- [x] `web/oversight-hub/src/components/LayoutWrapper.jsx` (updated)

Database:

- [x] `src/cofounder_agent/migrations/003_training_data_tables.sql` (300 lines)

Bulk Operations:

- [x] `web/oversight-hub/src/components/tasks/TaskManagement.jsx` (updated)

---

## Typical Flow

```
1. User submits request via OrchestratorPage
   ↓
2. Orchestrator processes and saves training example
   ↓
3. Training example visible in TrainingDataDashboard
   ↓
4. User filters and creates dataset
   ↓
5. Dataset exported as JSONL
   ↓
6. User selects fine-tuning model
   ↓
7. Job tracked in CommandQueuePage
   ↓
8. Model deployed after completion
   ↓
9. Learning patterns extracted
   ↓
10. Patterns improve future orchestrations
```

---

**✅ You're ready to go!** Follow steps 1-7 and the system will be fully operational.
