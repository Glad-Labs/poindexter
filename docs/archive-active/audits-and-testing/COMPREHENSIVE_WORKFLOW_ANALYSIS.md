# 🔍 Comprehensive Workflow Analysis & Debugging Guide

## Glad Labs AI Co-Founder Agent (FastAPI Service)

**Last Updated:** February 10, 2026  
**Status:** Production-Ready | Multi-Agent Orchestration | Self-Critiquing Pipeline  
**Purpose:** Complete trace of workflows and debugging techniques for the FastAPI backend

---

## 📋 Table of Contents

1. [System Architecture Overview](#system-architecture-overview)
2. [Complete Request Flow Diagram](#complete-request-flow-diagram)
3. [Key Entry Points & Routes](#key-entry-points--routes)
4. [Core Services & Responsibilities](#core-services--responsibilities)
5. [Content Generation Pipeline (6-Phase)](#content-generation-pipeline-6-phase)
6. [Execution Workflows](#execution-workflows)
7. [Error Handling & Debugging](#error-handling--debugging)
8. [Database Interactions](#database-interactions)
9. [Quality Assessment Loop](#quality-assessment-loop)
10. [Status Lifecycle Management](#status-lifecycle-management)
11. [Agent Orchestration](#agent-orchestration)
12. [Performance Monitoring](#performance-monitoring)
13. [Debugging Techniques & Commands](#debugging-techniques--commands)

---

## System Architecture Overview

### Three-Tier Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Presentation Layer                                              │
│  - React Oversight Hub (port 3001)                               │
│  - Next.js Public Site (port 3000)                               │
└────────────────────────┬────────────────────────────────────────┘
                         │ REST API (HTTP/WebSocket)
┌────────────────────────▼────────────────────────────────────────┐
│  FastAPI Orchestrator (port 8000) - MAIN FOCUS                  │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Routes Layer (20+ modules)                               │   │
│  │ ├─ task_routes.py        (Task CRUD, status mgmt)       │   │
│  │ ├─ workflow_routes.py    (Workflow execution)           │   │
│  │ ├─ agents_routes.py      (Agent status/commands)        │   │
│  │ ├─ model_routes.py       (LLM provider selection)       │   │
│  │ ├─ chat_routes.py        (Real-time chat)              │   │
│  │ ├─ content_routes.py     (Content generation)           │   │
│  │ └─ [15+ other routes]    (Analytics, webhooks, etc.)    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                         ▲                                         │
│  ┌──────────────────────┴──────────────────────────────────┐   │
│  │ Services Layer (60+ modules)                            │   │
│  │                                                          │   │
│  │ ORCHESTRATION:                                           │   │
│  │ ├─ unified_orchestrator.py  (Master request router)    │   │
│  │ ├─ workflow_router.py       (Workflow execution)       │   │
│  │ ├─ task_intent_router.py    (NLP intent parsing)       │   │
│  │ └─ pipeline_executor.py     (Task chaining engine)     │   │
│  │                                                          │   │
│  │ EXECUTION:                                               │   │
│  │ ├─ task_executor.py         (Background task runner)    │   │
│  │ ├─ content_router_service.py(Content generation)        │   │
│  │ ├─ langgraph_orchestrator.py(LangGraph execution)      │   │
│  │ └─ prompt_manager.py        (Unified prompt library)    │   │
│  │                                                          │   │
│  │ QUALITY:                                                 │   │
│  │ ├─ quality_service.py       (QA framework, 7 criteria)  │   │
│  │ ├─ content_critique_loop.py (Self-critiquing)          │   │
│  │ └─ qa_agent_bridge.py       (QA agent integration)     │   │
│  │                                                          │   │
│  │ PERSISTENCE:                                             │   │
│  │ ├─ database_service.py      (Unified DB coordinator)    │   │
│  │ ├─ tasks_db.py              (Task CRUD)                │   │
│  │ ├─ content_db.py            (Content operations)        │   │
│  │ └─ users_db.py              (User management)           │   │
│  │                                                          │   │
│  │ ROUTING:                                                 │   │
│  │ ├─ model_router.py          (LLM provider selection)    │   │
│  │ └─ command_queue.py         (Task queueing)             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                         ▲                                         │
└────────────────────────┼─────────────────────────────────────────┘
                         │ SQL (asyncpg)
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│  Data Layer                                                       │
│                                                                   │
│  PostgreSQL Database (primary persistence)                       │
│  ├─ tasks table              (Task records, status)              │
│  ├─ content table            (Generated content)                 │
│  ├─ users table              (User accounts)                     │
│  ├─ task_status_history      (Audit trail)                       │
│  ├─ workflow_history         (Execution records)                 │
│  ├─ quality_scores           (Quality metrics)                   │
│  └─ [10+ other tables]       (Settings, webhooks, etc.)         │
│                                                                   │
│  Redis Cache (optional)                                           │
│  └─ Session cache, rate limiting                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Complete Request Flow Diagram

### Request Lifecycle (End-to-End)

```
User Request (REST/WebSocket)
        │
        ▼
    ┌─────────────────────┐
    │ Route Handler       │  (task_routes.py, etc.)
    │ - Authentication    │
    │ - Input validation  │
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────────────────────────┐
    │ UnifiedOrchestrator.process_request()   │
    │ Step 1: Parse & Route Request           │
    │ - Extract intent from user input        │
    │ - Determine request type                │
    │ - Route to appropriate handler          │
    └────────┬────────────────────────────────┘
             │
    ┌────────▼────────────────────────────────────┐
    │ Step 2: Extract Intent & Parameters        │
    │ - NLP parsing (TaskIntentRouter)           │
    │ - Pattern matching                         │
    │ - Parameter normalization                  │
    └────────┬────────────────────────────────────┘
             │
    ┌────────▼────────────────────────────────────┐
    │ Step 3: Create Task Record                  │
    │ - DatabaseService.create_task()            │
    │ - Initial status: "pending"                │
    │ - Store in PostgreSQL                      │
    └────────┬────────────────────────────────────┘
             │
    ┌────────▼────────────────────────────────────┐
    │ Step 4: Queue for Background Execution     │
    │ - TaskExecutor._process_loop() detects    │
    │ - Status → "in_progress"                   │
    └────────┬────────────────────────────────────┘
             │
    ┌────────▼─────────────────────────────────────────────┐
    │ PHASE 1: Content Generation                         │
    │ TaskExecutor._execute_task()                        │
    │ ├─ Research → Extract context, gather data          │
    │ ├─ Creative → Generate content with brand voice     │
    │ ├─ QA → Critique quality                            │
    │ ├─ Refine → Apply feedback                          │
    │ ├─ Images → Select/generate visuals                 │
    │ └─ Format → Prepare for publishing                  │
    │                                                       │
    │ Generated via: UnifiedOrchestrator                   │
    │ Fallback: AIContentGenerator (if orchestrator down) │
    └────────┬────────────────────────────────────────────┘
             │
    ┌────────▼────────────────────────────────────────────┐
    │ PHASE 2: Quality Validation (ContentCritiqueLoop)  │
    │ - Evaluate against 7 criteria:                      │
    │   1. Clarity & Readability                          │
    │   2. Brand Voice Match                              │
    │   3. SEO Optimization                               │
    │   4. Engagement Score                               │
    │   5. Fact Accuracy                                  │
    │   6. Grammar & Style                                │
    │   7. Length & Completeness                          │
    │                                                      │
    │ - Pass threshold (default 0.7)?                     │
    │   YES → Move to approval                            │
    │   NO  → Attempt refinement loop                     │
    └────────┬────────────────────────────────────────────┘
             │
    ┌────────▼────────────────────────────────────────────┐
    │ PHASE 3: Approval Gate                              │
    │ - Status → "awaiting_approval"                      │
    │ - Notify user via WebSocket/webhook                 │
    │ - Wait for user action:                             │
    │   ✓ Approve  → Move to publishing                   │
    │   ✗ Reject   → Store feedback, retrigger generation │
    │   ⚙ Modify   → Apply user changes                   │
    └────────┬────────────────────────────────────────────┘
             │
    ┌────────▼──────────────────────────────────────────┐
    │ PHASE 4: Publishing                               │
    │ - Format content per CMS requirements              │
    │ - Generate SEO metadata                            │
    │ - Convert to markdown/HTML                         │
    │ - Post to CMS (Strapi) via webhook                 │
    │ - Status → "published"                             │
    └────────┬──────────────────────────────────────────┘
             │
    ┌────────▼──────────────────────────────────────────┐
    │ PHASE 5: Training Data Capture                     │
    │ - Store input → output pair                        │
    │ - Record quality metrics                           │
    │ - Log refinement attempts                          │
    │ - Use for future model fine-tuning                 │
    └────────┬──────────────────────────────────────────┘
             │
    ┌────────▼──────────────────────────────────────────┐
    │ PHASE 6: Completion & Analytics                    │
    │ - Final status: "completed"                        │
    │ - Calculate costs (token usage)                     │
    │ - Record execution time                            │
    │ - Update user analytics                            │
    │ - Broadcast completion to clients                  │
    └──────────────────────────────────────────────────┘
```

---

## Key Entry Points & Routes

### Primary API Routes

| Route | Method | Purpose | Status Code | Handler |
|-------|--------|---------|-------------|---------|
| `/api/tasks` | POST | Create new task | 201 | `create_task()` |
| `/api/tasks` | GET | List tasks (paginated) | 200 | `list_tasks()` |
| `/api/tasks/{id}` | GET | Get task details | 200 | `get_task()` |
| `/api/tasks/{id}/status` | PUT | Update task status | 200 | `update_task_status_*()` |
| `/api/tasks/{id}/approve` | POST | Approve task | 200 | `approve_task()` |
| `/api/tasks/{id}/publish` | POST | Publish task | 200 | `publish_task()` |
| `/api/tasks/{id}/reject` | POST | Reject task | 200 | `reject_task()` |
| `/api/tasks/intent` | POST | Create from NL intent | 201 | `create_task_from_intent()` |
| `/api/tasks/confirm-intent` | POST | Confirm & execute task | 201 | `confirm_and_execute_task()` |
| `/api/health` | GET | Service health | 200 | FastAPI health check |
| `/api/models` | GET | Available LLM models | 200 | `get_available_models()` |
| `/ws/tasks/{id}` | WebSocket | Real-time task progress | - | `websocket_task_progress()` |

### Complete Route Files

```
routes/
├── task_routes.py              [2,623 lines] ← MAIN: Task CRUD + status lifecycle
├── agents_routes.py             [~500 lines] ← Agent status and commands
├── model_routes.py              [~300 lines] ← LLM model selection/health
├── chat_routes.py               [~400 lines] ← Real-time chat/streaming
├── content_routes.py            [~600 lines] ← Content generation shortcuts
├── workflow_history.py          [~500 lines] ← Execution history queries
├── analytics_routes.py          [~800 lines] ← Metrics and statistics
├── command_queue_routes.py      [~400 lines] ← Task queueing
├── bulk_task_routes.py          [~300 lines] ← Batch operations
├── cms_routes.py                [~500 lines] ← CMS (Strapi) integration
├── webhooks.py                  [~400 lines] ← External webhook handling
├── websocket_routes.py          [~300 lines] ← WebSocket connections
├── auth_unified.py              [~400 lines] ← Authentication/OAuth
├── settings_routes.py           [~500 lines] ← User settings
├── social_routes.py             [~400 lines] ← Social media publishing
├── newsletter_routes.py         [~300 lines] ← Email newsletter
├── media_routes.py              [~300 lines] ← File uploads/media
├── writing_style_routes.py      [~600 lines] ← RAG writing samples
├── privacy_routes.py            [~200 lines] ← GDPR/privacy endpoints
├── metrics_routes.py            [~400 lines] ← Real-time metrics
└── ollama_routes.py             [~200 lines] ← Local Ollama models
```

---

## Core Services & Responsibilities

### 1. Orchestration Services

#### `UnifiedOrchestrator` (1,066 lines)

**Location:** `services/unified_orchestrator.py`

**Primary Responsibility:** Master request router and executor

**Key Methods:**

- `process_request(user_input, context)` - Main entry point
- `process_command_async(command, context)` - Legacy command processing
- `_parse_request(user_input, request_id, context)` - Parse and route
- `_extract_intent_and_params(request)` - NLP intent extraction
- `_route_and_execute(request)` - Route to appropriate handler
- `_assess_quality(result)` - Quality evaluation
- `_refine_if_needed(result)` - Refinement loop
- `_store_training_data(result)` - Training capture

**Request Types Handled:**

```python
RequestType.CONTENT_CREATION          # Blog posts, articles, copy
RequestType.CONTENT_SUBTASK           # Individual content stages
RequestType.FINANCIAL_ANALYSIS        # Cost analysis, ROI
RequestType.COMPLIANCE_CHECK          # Legal/risk review
RequestType.TASK_MANAGEMENT           # Create/manage tasks
RequestType.INFORMATION_RETRIEVAL     # Data lookups
RequestType.DECISION_SUPPORT          # "What should I..."
RequestType.SYSTEM_OPERATION          # Status, health
RequestType.INTERVENTION              # Manual overrides
```

#### `WorkflowRouter` (varies)

**Location:** `services/workflow_router.py`

**Purpose:** Routes workflows to ModularPipelineExecutor

**Key Methods:**

- `execute_workflow()` - Execute workflow with custom pipeline
- `execute_from_natural_language()` - NL to workflow
- `_parse_intent()` - Intent extraction for workflows

#### `TaskIntentRouter` (varies)

**Location:** `services/task_intent_router.py`

**Purpose:** NLP parsing for user requests

**Key Methods:**

- `route_user_input()` - Main NLP entry point
- `_determine_subtasks()` - Break down task into stages
- `_should_confirm()` - User confirmation needed?
- `_determine_execution_strategy()` - Sequential vs parallel

#### `ModularPipelineExecutor` (472 lines)

**Location:** `services/pipeline_executor.py`

**Purpose:** Execute task pipelines with automatic chaining

**Key Features:**

- Automatic task chaining (output N → input N+1)
- Flexible error handling (fail/skip/retry)
- Checkpoint support for approval workflows
- Complete execution history

**Key Methods:**

- `execute(request: WorkflowRequest)` - Execute pipeline
- `resume_workflow()` - Resume from checkpoint
- `_get_pipeline()` - Get task sequence
- `_merge_task_output()` - Merge task results

---

### 2. Execution Services

#### `TaskExecutor` (1,015 lines)

**Location:** `services/task_executor.py`

**Primary Responsibility:** Background task processing pipeline

**Architecture:**

```
TaskExecutor
├── _process_loop()          ← Main loop (runs every 5 seconds)
│   └── Polls for pending tasks
│
├── _process_single_task()   ← Process one task
│   └── Update status → in_progress
│       └── _execute_task()
│           ├── PHASE 1: Content Generation
│           ├── PHASE 2: Quality Critique
│           ├── PHASE 3: Approval Gate
│           ├── PHASE 4: Publishing
│           ├── PHASE 5: Training Data
│           └── PHASE 6: Analytics
│
├── _execute_task()          ← Main execution handler
│   ├── Get model for execution
│   ├── Call UnifiedOrchestrator.process_request()
│   ├── Validate content via critique loop
│   ├── Attempt refinement if needed
│   ├── Store result
│   └── Update task status
│
├── _fallback_generate_content() ← Fallback if orchestrator unavailable
└── get_stats()              ← Execution statistics
```

**Key Constants:**

```python
TASK_TIMEOUT_SECONDS = 600  # 10 minutes per task
POLL_INTERVAL = 5           # Check for tasks every 5 seconds
MAX_REFINEMENT_ATTEMPTS = 3
QUALITY_THRESHOLD = 0.7     # Minimum quality score (70%)
```

#### `LangGraphOrchestrator` (varies)

**Location:** `services/langgraph_orchestrator.py`

**Purpose:** LangGraph-based workflow execution (advanced pipeline)

**Key Methods:**

- `execute_content_pipeline()` - Execute with/without streaming
- `_sync_execution()` - Synchronous execution
- `_stream_execution()` - Streaming execution

#### `ContentRouterService` (varies)

**Location:** `services/content_router_service.py`

**Purpose:** Multi-phase content generation orchestration

**Key Methods:**

- `process_content_generation_task()` - Main entry point
- Executes research → creative → qa → refine → images → publish

---

### 3. Quality & Critique Services

#### `ContentCritiqueLoop` (varies)

**Location:** `services/content_critique_loop.py`

**Purpose:** Self-critiquing quality assessment

**Evaluation Criteria (7):**

1. **Clarity & Readability** - Easy to understand?
2. **Brand Voice Match** - Consistent with style?
3. **SEO Optimization** - Good for search?
4. **Engagement Score** - Compelling content?
5. **Fact Accuracy** - Truthful claims?
6. **Grammar & Style** - Well-written?
7. **Length & Completeness** - Sufficient depth?

**Key Methods:**

- `critique()` - Perform quality assessment
- Returns: `{passed, score, feedback, needs_refinement}`

#### `UnifiedQualityService` (varies)

**Location:** `services/quality_service.py`

**Purpose:** Comprehensive QA framework

**Features:**

- Multi-criteria evaluation
- Custom scoring rubrics
- Feedback generation
- Threshold-based acceptance

---

### 4. Database Services

#### `DatabaseService` (coordination layer)

**Location:** `services/database_service.py`

**Purpose:** Unified database interface coordinator

**Delegates to:**

```python
DatabaseService
├── UsersDatabase          → User accounts, OAuth, auth
├── TasksDatabase          → Task CRUD, filtering, status
├── ContentDatabase        → Posts, quality scores, metrics
├── AdminDatabase          → Logging, finance, settings
├── WritingStyleDatabase   → Writing samples for RAG
└── [5+ other modules]     → Migrations, schema, health
```

**Key Methods:**

```python
# Task operations
await db.create_task(task_data)
await db.get_task(task_id)
await db.update_task_status(task_id, new_status)
await db.get_pending_tasks(limit=10)
await db.get_task_status_history(task_id)

# Content operations
await db.store_content(task_id, content_data)
await db.get_content_by_task(task_id)

# Quality operations
await db.store_quality_score(task_id, score, criteria)
await db.get_quality_by_task(task_id)
```

---

### 5. AI & Model Services

#### `ModelRouter` (varies)

**Location:** `services/model_router.py`

**Purpose:** Intelligent LLM provider selection with cost optimization

**Selection Priority (Fallback Chain):**

```
1. Ollama (local, ~$0, ~20ms latency)
   └─ If unavailable:
   
2. Anthropic Claude (configurable model)
   └─ If key missing or unavailable:
   
3. OpenAI GPT (configurable model)
   └─ If key missing or unavailable:
   
4. Google Gemini
   └─ If key missing or unavailable:
   
5. Echo/Mock Response (dev/demo)
```

**Key Methods:**

- `select_model(cost_tier)` - Get best model for tier
- `get_available_models()` - List all available
- `validate_model(model_name)` - Check availability
- `get_model_cost(model, tokens)` - Calculate cost

#### `PromptManager` (varies)

**Location:** `services/prompt_manager.py`

**Purpose:** Centralized prompt library and management

**Prompt Categories:**

```
Content Generation:
├─ research_prompt
├─ creative_prompt
├─ qa_prompt
├─ refine_prompt
├─ images_prompt
└─ format_prompt

System Prompts:
├─ quality_rubric
├─ critique_prompt
└─ refinement_prompt
```

---

## Content Generation Pipeline (6-Phase)

### Complete Phase Breakdown

```
REQUEST RECEIVED
      │
      ▼
═══════════════════════════════════════════════════════════════
PHASE 1: RESEARCH
═══════════════════════════════════════════════════════════════
  Responsibility: Gather background, identify key points
  
  Agent: Research Agent
  Time: ~5-10 seconds
  Cost: $0.05-0.10
  
  Inputs:
  - topic: str              (e.g., "AI Trends 2026")
  - target_length: int      (e.g., 2000)
  - target_audience: str    (e.g., "tech professionals")
  
  Process:
  1. Search for relevant information
  2. Gather context and sources
  3. Identify key points and themes
  4. Summarize findings
  
  Outputs:
  - research_data: Dict     (key findings, sources)
  - context_summary: str    (condensed research)
  - key_points: List[str]   (main takeaways)
  
  Error Handling:
  ├─ No sources found → Return generic research
  ├─ Search failed → Use cached knowledge
  └─ Timeout → Use fallback research
  
  Debug Points:
  └─ Research completeness check
  └─ Source availability validation


PHASE 2: CREATIVE GENERATION
═══════════════════════════════════════════════════════════════
  Responsibility: Generate initial draft with brand voice
  
  Agent: Creative Agent
  Time: ~10-15 seconds
  Cost: $0.10-0.20
  
  Inputs:
  - research_data: Dict     (from Phase 1)
  - style: str              (e.g., "informative, engaging")
  - tone: str               (e.g., "professional")
  - brand_voice: str        (RAG from writing samples)
  
  Process:
  1. Load brand voice guidelines from writing samples
  2. Generate initial outline
  3. Write full content with brand voice
  4. Add engaging hooks and transitions
  
  Outputs:
  - draft_content: str      (full article/post)
  - outline: List[str]      (structure)
  - metadata: Dict          (title, excerpt, etc.)
  
  Error Handling:
  ├─ Writing samples unavailable → Use default voice
  ├─ Generation timeout → Return partial content
  └─ Invalid parameters → Use defaults
  
  Debug Points:
  └─ Brand voice consistency
  └─ Content length validation
  └─ Prompt template rendering


PHASE 3: QA & CRITIQUE
═══════════════════════════════════════════════════════════════
  Responsibility: Evaluation without rewriting
  
  Service: ContentCritiqueLoop
  Time: ~8-12 seconds
  Cost: $0.05-0.15
  
  Inputs:
  - draft_content: str      (from Phase 2)
  - quality_threshold: float (default: 0.7)
  
  Process:
  1. Evaluate against 7 criteria:
     ├─ Clarity & Readability
     ├─ Brand Voice Match
     ├─ SEO Optimization
     ├─ Engagement Score
     ├─ Fact Accuracy
     ├─ Grammar & Style
     └─ Length & Completeness
  
  2. Generate score (0-1)
  3. Create feedback without rewrites
  4. Determine if needs refinement
  
  Outputs:
  - quality_score: float    (0-1)
  - passed: bool            (score >= threshold?)
  - feedback: str           (improvement suggestions)
  - needs_refinement: bool  (attempt refinement?)
  - criteria_scores: Dict   (breakdown by criterion)
  
  Error Handling:
  ├─ QA agent unavailable → Skip critique
  ├─ Scoring failed → Default to 0.5
  └─ Invalid feedback → Use generic feedback
  
  Debug Points:
  └─ Criterion scoring accuracy
  └─ Threshold comparison
  └─ Feedback relevance


PHASE 4: REFINEMENT (Conditional)
═══════════════════════════════════════════════════════════════
  Responsibility: Apply feedback, improve draft
  
  Agent: Creative Agent (again)
  Time: ~10-15 seconds (if needed)
  Cost: $0.10-0.20 (if needed)
  Triggered: Only if Phase 3 fails OR needs_refinement=true
  Max Attempts: 3
  
  Inputs:
  - original_content: str   (from Phase 2)
  - critique_feedback: str  (from Phase 3)
  
  Process:
  1. Read feedback from Phase 3
  2. Rewrite content incorporating suggestions
  3. Maintain brand voice
  4. Return refined version
  5. Loop back to Phase 3 if score still low
  
  Loop Logic:
  ├─ Attempt 1: Quality < 0.7
  │  ├─ Refine → GOTO Phase 3
  │  └─ Phase 3: Pass? → Proceed : Attempt 2
  ├─ Attempt 2: Still failing
  │  ├─ Stronger critique → Refine
  │  └─ GOTO Phase 3
  ├─ Attempt 3: Final attempt
  │  └─ Max retries reached → Continue (may publish with warning)
  
  Error Handling:
  ├─ Refinement timeout → Use original content
  ├─ Agent error → Skip refinement
  └─ Max retries exceeded → Continue anyway
  
  Debug Points:
  └─ Refinement quality improvements
  └─ Loop iteration count
  └─ Feedback application success


PHASE 5: IMAGE GENERATION & SELECTION
═══════════════════════════════════════════════════════════════
  Responsibility: Visuals for publication
  
  Agents: Image Agent + Selection Engine
  Time: ~15-30 seconds
  Cost: $0.20-1.00 (depending on generation)
  
  Process:
  1. Extract key topics from content
  2. Generate image description
  3. Search free image library (Pexels)
  4. If no match → Generate image (DALL-E/Midjourney)
  5. Select best result
  6. Generate alt-text (SEO + accessibility)
  7. Prepare metadata
  
  Inputs:
  - content: str            (from Phase 2/4)
  - topic: str             (original request)
  
  Outputs:
  - featured_image: str     (URL or path)
  - alt_text: str          (accessibility + SEO)
  - image_metadata: Dict    (title, description, credits)
  - thumbnail: str         (optional, for preview)
  
  Error Handling:
  ├─ No images found → Use default placeholder
  ├─ Generation failed → Use stock image
  ├─ Timeout → Use cached images
  └─ Invalid input → Skip images
  
  Debug Points:
  └─ Image quality and relevance
  └─ Alt-text generation quality
  └─ Generation vs. library search time


PHASE 6: FORMATTING & PUBLISHING
═══════════════════════════════════════════════════════════════
  Responsibility: CMS preparation and publishing
  
  Agent: Publishing Agent
  Time: ~5-10 seconds
  Cost: $0.05 (mostly metadata generation)
  
  Process:
  1. Validate content structure
  2. Convert to markdown (GitHub-flavored)
  3. Generate SEO metadata:
     ├─ Meta description (155 chars)
     ├─ Keywords (5-10)
     ├─ Open Graph tags
     └─ Twitter card tags
  4. Add structured data (JSON-LD)
  5. Format for CMS (Strapi)
  6. Queue for publishing
  
  Inputs:
  - content: str            (from Phase 2/4)
  - metadata: Dict          (title, excerpt, author)
  - featured_image: str     (from Phase 5)
  
  Outputs:
  - formatted_content: str  (markdown)
  - seo_metadata: Dict      (SEO fields)
  - cms_payload: Dict       (Strapi format)
  - published_url: str      (after publishing)
  
  Status Updates:
  └─ awaiting_approval → pending_publishing → published
  
  Error Handling:
  ├─ CMS connection failed → Queue for retry
  ├─ Invalid payload → Fix and retry
  ├─ Rate limited → Backoff and retry
  └─ Permanent error → Status = failed
  
  Debug Points:
  └─ CMS payload validation
  └─ SEO metadata quality
  └─ Publishing status tracking


═══════════════════════════════════════════════════════════════
COMPLETION
═══════════════════════════════════════════════════════════════
  Status: completed
  Metrics Recorded:
  ├─ Total execution time (Phase 1-6)
  ├─ Total cost (sum of all phases)
  ├─ Refinement attempts
  ├─ Quality score (final)
  ├─ Times consumed by each phase
  └─ Any errors or warnings
  
  Training Data Captured:
  ├─ User input → generated output
  ├─ All quality scores
  ├─ Refinement feedback
  └─ Final published content
  
  Stored in:
  └─ PostgreSQL for future model fine-tuning
```

---

## Execution Workflows

### Authentication & Request Validation

```python
# 1. Route Handler (task_routes.py)
@router.post("/api/tasks")
async def create_task(
    request_body: TaskCreateRequest,
    current_user: User = Depends(get_current_user)  # ← Auth check
) -> UnifiedTaskResponse:
    # 2. Input Validation (Pydantic)
    # - request_body automatically validated
    
    # 3. Check permissions
    # - current_user verified by get_current_user()
    
    # 4. Create initial task
    task_id = await database.create_task({
        'user_id': current_user.id,
        'topic': request_body.topic,
        'status': 'pending',
        ...
    })
    
    # 5. Return response
    return UnifiedTaskResponse(id=task_id, status='pending')
```

### Natural Language Intent Routing

```python
# From UnifiedOrchestrator.process_request()

async def process_request(user_input: str, context: Dict):
    # 1. Parse and Route
    request = await self._parse_request(user_input)
    
    # 2. Determine Type
    if "create" in user_input and "blog" in user_input:
        request.request_type = RequestType.CONTENT_CREATION
    elif "analyze" in user_input and "financial" in user_input:
        request.request_type = RequestType.FINANCIAL_ANALYSIS
    # ... etc
    
    # 3. Extract Intent & Parameters
    intent = await self._extract_intent_and_params(request)
    # → topic, style, tone, target_length, etc.
    
    # 4. Route to Handler
    if request.request_type == RequestType.CONTENT_CREATION:
        result = await self._handle_content_creation(intent)
    elif request.request_type == RequestType.FINANCIAL_ANALYSIS:
        result = await self._handle_financial_analysis(intent)
    # ... else branches
    
    # 5. Assess Quality
    quality = await self._assess_quality(result)
    
    # 6. Refine if Needed
    if quality['score'] < 0.7:
        result = await self._refine_if_needed(result, quality)
    
    # 7. Store Training Data
    await self._store_training_data(
        input=user_input,
        output=result,
        quality=quality
    )
    
    return result
```

### Background Task Processing Loop

```python
# From TaskExecutor._process_loop()

async def _process_loop():
    while self.running:
        try:
            # 1. Poll for pending tasks
            pending = await database.get_pending_tasks(limit=10)
            
            if pending:
                logger.info(f"Found {len(pending)} pending tasks")
                
                # 2. Process each task
                for task in pending:
                    await self._process_single_task(task)
            
            # 3. Await next poll
            await asyncio.sleep(self.poll_interval)  # 5 seconds
            
        except Exception as e:
            logger.error(f"Error in process loop: {e}")
            await asyncio.sleep(self.poll_interval)
```

---

## Error Handling & Debugging

### Error Hierarchy

```
Exception
├── HTTPException (FastAPI)
│   ├── 400 Bad Request         (invalid input)
│   ├── 401 Unauthorized        (authentication failed)
│   ├── 403 Forbidden           (permission denied)
│   ├── 404 Not Found           (resource missing)
│   ├── 409 Conflict            (status transition invalid)
│   └── 503 Service Unavailable (orchestrator down)
│
├── DatabaseError
│   ├── ConnectionError         (PostgreSQL unavailable)
│   ├── IntegrityError          (constraint violation)
│   └── OperationalError        (query failed)
│
├── OrchestrationError
│   ├── ModelNotAvailable       (no LLM providers)
│   ├── TimeoutError            (execution too long)
│   └── ParsingError            (intent extraction failed)
│
└── ApplicationError
    ├── ConfigError             (missing env vars)
    ├── ServiceInitError        (startup failed)
    └── ContentValidationError  (generated content invalid)
```

### Global Exception Handlers

```python
# From utils/exception_handlers.py
register_exception_handlers(app)

Registered Handlers:
├─ log_exceptions()           → Log all errors
├─ http_exception_handler()   → Return JSON errors
├─ validation_exception_handler() → Pydantic errors
├─ database_exception_handler() → SQL errors
├─ orchestrator_exception_handler() → Orchestration errors
└─ general_exception_handler() → Catch-all
```

---

## Database Interactions

### Table Structure

```sql
-- Core Tables
tasks
├─ id UUID PRIMARY KEY
├─ user_id UUID                    (owner)
├─ task_name VARCHAR                 (e.g., "Blog Post")
├─ topic VARCHAR                     (e.g., "AI Trends")
├─ status VARCHAR                    (pending, in_progress, etc.)
├─ created_at TIMESTAMP              (creation time)
├─ started_at TIMESTAMP              (execution start)
├─ completed_at TIMESTAMP            (execution end)
├─ task_metadata JSONB               (topic, style, tone, etc.)
├─ result JSONB                      (generated content)
├─ quality_score FLOAT               (0-1)
└─ error_message TEXT                (if failed)

task_status_history
├─ id UUID PRIMARY KEY
├─ task_id UUID FOREIGN KEY
├─ old_status VARCHAR
├─ new_status VARCHAR
├─ changed_by UUID                   (user who made change)
├─ reason TEXT                       (why changed)
└─ changed_at TIMESTAMP

content
├─ id UUID PRIMARY KEY
├─ task_id UUID FOREIGN KEY
├─ content_type VARCHAR              (blog_post, social, etc.)
├─ title VARCHAR
├─ body TEXT                         (markdown)
├─ summary TEXT
├─ featured_image VARCHAR            (URL)
├─ seo_metadata JSONB                (meta desc, keywords, etc.)
├─ quality_criteria JSONB            (7 criteria scores)
├─ created_at TIMESTAMP
└─ published_at TIMESTAMP

quality_scores
├─ id UUID PRIMARY KEY
├─ task_id UUID FOREIGN KEY
├─ criteria_scores JSONB             (clarity, brand_voice, etc.)
├─ overall_score FLOAT               (0-1)
├─ feedback TEXT
├─ timestamp TIMESTAMP
└─ version INT                       (which iteration)

workflow_history
├─ id UUID PRIMARY KEY
├─ user_id UUID
├─ workflow_type VARCHAR             (content_generation, etc.)
├─ start_time TIMESTAMP
├─ end_time TIMESTAMP
├─ status VARCHAR                    (completed, failed, etc.)
├─ task_results JSONB                (all task outputs)
├─ metrics JSONB                     (timing, costs, etc.)
└─ errors JSONB                      (any errors encountered)
```

### Common Queries

```python
# Get pending tasks
async def get_pending_tasks(limit=10):
    query = """
        SELECT * FROM tasks
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT $1
    """
    return await pool.fetch(query, limit)

# Update task status with history
async def update_task_status(task_id, new_status, reason=None):
    # 1. Get old status
    old_task = await get_task(task_id)
    
    # 2. Update task
    await db.execute("""
        UPDATE tasks SET status = $1,
        WHERE id = $2
    """, new_status, task_id)
    
    # 3. Record history
    await db.execute("""
        INSERT INTO task_status_history
        (task_id, old_status, new_status, reason)
        VALUES ($1, $2, $3, $4)
    """, task_id, old_task['status'], new_status, reason)

# Store content with quality metrics
async def store_content(task_id, content_data, quality_score):
    await db.execute("""
        INSERT INTO content
        (task_id, title, body, featured_image, seo_metadata)
        VALUES ($1, $2, $3, $4, $5::JSONB)
    """, task_id, content_data['title'], ...)
    
    await db.execute("""
        INSERT INTO quality_scores
        (task_id, overall_score, criteria_scores, feedback)
        VALUES ($1, $2, $3::JSONB, $4)
    """, task_id, quality_score['score'], ...)
```

---

## Quality Assessment Loop

### 7-Criteria Evaluation Framework

```python
QUALITY_CRITERIA = {
    'clarity': {
        'weight': 0.15,
        'threshold': 0.7,
        'description': 'Is content easy to understand?',
        'indicators': [
            'Clear sentence structure',
            'Logical paragraph flow',
            'Technical terms explained',
            'Short paragraphs (<100 words)'
        ]
    },
    'brand_voice': {
        'weight': 0.15,
        'threshold': 0.7,
        'description': 'Does it match brand voice?',
        'indicators': [
            'Uses characteristic phrases',
            'Maintains tone consistency',
            'Follows style guidelines',
            'References brand values'
        ]
    },
    'seo': {
        'weight': 0.15,
        'threshold': 0.7,
        'description': 'Is it SEO optimized?',
        'indicators': [
            'Keyword placement natural',
            'Meta description quality',
            'Heading structure proper',
            'Internal link opportunities'
        ]
    },
    'engagement': {
        'weight': 0.15,
        'threshold': 0.7,
        'description': 'Is it compelling?',
        'indicators': [
            'Strong opening hook',
            'Calls-to-action clear',
            'Examples and data included',
            'Visually scannable'
        ]
    },
    'accuracy': {
        'weight': 0.15,
        'threshold': 0.7,
        'description': 'Are facts correct?',
        'indicators': [
            'Claims verifiable',
            'Statistics cited',
            'No contradictions',
            'Current information'
        ]
    },
    'grammar': {
        'weight': 0.10,
        'threshold': 0.7,
        'description': 'Is writing quality high?',
        'indicators': [
            'No grammatical errors',
            'Proper punctuation',
            'Correct spelling',
            'Good word choice'
        ]
    },
    'completeness': {
        'weight': 0.15,
        'threshold': 0.7,
        'description': 'Is it sufficiently detailed?',
        'indicators': [
            'Meets length target',
            'Covers main topics',
            'Provides adequate depth',
            'Conclusion present'
        ]
    }
}

# Overall calculation
overall_score = (
    clarity_score * 0.15 +
    brand_voice_score * 0.15 +
    seo_score * 0.15 +
    engagement_score * 0.15 +
    accuracy_score * 0.15 +
    grammar_score * 0.10 +
    completeness_score * 0.15
)

# Pass/Fail
threshold = 0.7  # 70%
passed = overall_score >= threshold
```

### Refinement Loop Logic

```python
async def execute_with_refinement(task, max_attempts=3):
    """
    Execute content generation with automatic refinement.
    """
    attempt = 0
    current_content = None
    quality_history = []
    
    while attempt < max_attempts:
        attempt += 1
        logger.info(f"Refinement attempt {attempt}/{max_attempts}")
        
        # 1. Generate (or refine if iteration > 1)
        if attempt == 1:
            current_content = await orchestrator.generate_content(task)
        else:
            # Pass previous critique to guide refinement
            refinement_prompt = {
                'original': current_content,
                'feedback': quality_history[-1]['feedback'],
                'failed_criteria': quality_history[-1]['failed']
            }
            current_content = await orchestrator.refine_content(refinement_prompt)
        
        # 2. Critique
        quality = await critique_loop.critique(current_content)
        quality_history.append(quality)
        
        logger.info(f"Quality score: {quality['score']:.2f}")
        logger.info(f"Criteria: {quality['criteria_scores']}")
        
        # 3. Check if passed
        if quality['passed']:
            logger.info(f"✅ Content passed after {attempt} attempt(s)")
            return {
                'content': current_content,
                'quality': quality,
                'attempts': attempt,
                'history': quality_history
            }
        
        # 4. If final attempt, warn but continue
        if attempt == max_attempts:
            logger.warning(
                f"⚠️  Max refinement attempts ({max_attempts}) reached"
            )
            logger.warning(f"   Final score: {quality['score']:.2f}")
            logger.warning(f"   Continuing with current content")
            return {
                'content': current_content,
                'quality': quality,
                'attempts': attempt,
                'history': quality_history,
                'warning': 'Max refinements reached, publishing anyway'
            }
    
    return None  # Should not reach
```

---

## Status Lifecycle Management

### Valid Status Transitions

```
State Machine (Task Status)

         ┌─────────────┐
         │   pending   │  ← Created state
         └──────┬──────┘
                │ (auto: TaskExecutor picks up)
                ▼
         ┌──────────────────┐
         │  in_progress     │  ← Execution
         └───┬─────┬────┬───┘
             │     │    │
    ┌────────┘     │    └───────────┐
    ▼              ▼                 ▼
┌─────────┐  ┌─────────────┐  ┌────────────────┐
│ failed  │  │awaiting_    │  │ completed      │
│         │  │approval     │  │ (but not pub)  │
│(error)  │  └─┬───┬──┬────┘  └───────┬────────┘
└─────────┘    │   │  │            │
        │      │   │  └────────────┤
        │      │   │               │
┌───────▼──────┘   ▼               ▼
│         ┌─────────────────┐  ┌──────────┐
│         │  rejected       │  │published │
│         │ (user rejected) │  │(final)   │
│         └─────────────────┘  └──────────┘
│    ▲                            ▲
└────┘                            │
    (retry loop)       (publish endpoint)


Valid Transitions:
pending           → in_progress   (auto)
in_progress       → completed     (success)
in_progress       → failed        (error)
completed         → awaiting_approval (auto)
awaiting_approval → approved      (user action)
awaiting_approval → rejected      (user action)
approved          → pending_publishing (auto)
pending_publishing → published    (webhook)
rejected          → pending       (retry)
failed            → pending       (manual retry)
*                 → cancelled     (user cancel)
```

### Status Change Validation

```python
class StatusTransitionValidator:
    
    # Allowed transitions per status
    TRANSITIONS = {
        'pending': ['in_progress', 'cancelled'],
        'in_progress': ['completed', 'failed', 'cancelled'],
        'completed': ['awaiting_approval', 'cancelled'],
        'awaiting_approval': ['approved', 'rejected', 'cancelled'],
        'approved': ['pending_publishing', 'cancelled'],
        'pending_publishing': ['published', 'failed'],
        'published': ['cancelled'],
        'rejected': ['pending'],
        'failed': ['pending', 'cancelled'],
        'cancelled': []
    }
    
    @staticmethod
    def is_valid(current: str, target: str) -> bool:
        """Check if transition allowed"""
        allowed = TRANSITIONS.get(current, [])
        return target in allowed
    
    @staticmethod
    def get_reason(current: str, target: str) -> str:
        """Get human-readable reason for status change"""
        reasons = {
            ('pending', 'in_progress'): 'Executor picked up task',
            ('in_progress', 'completed'): 'Content generation complete',
            ('in_progress', 'failed'): 'Generation failed',
            ('completed', 'awaiting_approval'): 'Awaiting user approval',
            ('awaiting_approval', 'approved'): 'User approved content',
            ('approved', 'pending_publishing'): 'Publishing to CMS',
            ('pending_publishing', 'published'): 'Successfully published',
            # ... etc
        }
        return reasons.get((current, target), '')
```

---

## Agent Orchestration

### Agent Fleet Architecture

```
UnifiedOrchestrator (Master)
│
├─ Content Agent                    ├─ Research Sub-Agent
│  (6-stage pipeline)               ├─ Creative Sub-Agent
│                                   ├─ QA Sub-Agent
│                                   └─ Publishing Sub-Agent
│
├─ Financial Agent                  ├─ Cost Calculator
│  (ROI & budgeting)                ├─ Historical Analyzer
│                                   └─ Forecaster
│
├─ Market Insight Agent             ├─ Trend Analyzer
│  (industry analysis)              ├─ Competitor Scout
│                                   └─ Report Generator
│
└─ Compliance Agent                 ├─ Legal Checker
   (risk assessment)                ├─ Policy Validator
                                    └─ Recommendation Engine
```

### Agent Interface

```python
class Agent(ABC):
    """Base agent interface"""
    
    async def execute(self, request: Dict) -> Dict:
        """
        Execute agent task.
        
        Args:
            request: {
                'type': 'content_generation|financial_analysis|...',
                'parameters': {...},
                'context': {...}
            }
        
        Returns:
            {
                'status': 'success|failed',
                'output': {...},
                'error': '...',
                'cost_usd': 0.50,
                'duration_ms': 5000,
                'metadata': {...}
            }
        """
        pass
```

### Agent Execution Patterns

```python
# Parallel Execution (when independent)
results = await asyncio.gather(
    content_agent.execute(request),
    financial_agent.execute(financial_request),
    market_agent.execute(market_request),
)

# Sequential Execution (when dependent)
# 1. Research phase
research = await content_agent.research(topic)

# 2. Creative phase (uses research)
creative = await content_agent.create(
    research_data=research,
    style=style
)

# 3. QA phase (uses creative)
quality = await content_agent.critique(creative)

# 4. Refine phase (uses QA feedback)
if not quality['passed']:
    refined = await content_agent.refine(
        content=creative,
        feedback=quality['feedback']
    )
```

---

## Performance Monitoring

### Key Metrics

```python
# From TaskExecutor.get_stats()
{
    'task_count': 150,           # Total tasks processed
    'success_count': 145,        # Successfully completed
    'error_count': 5,            # Failed tasks
    'published_count': 140,      # Published to CMS
    'avg_execution_time': 45.2,  # Seconds per task
    'total_cost': 235.50,        # USD spent
    'quality_avg': 0.82,         # Average quality score
    'uptime_hours': 72.5,        # Hours running
    'queue_size': 3,             # Pending tasks
}

# Per-Phase Timing
phase_timing = {
    'research': 7500,            # ms
    'creative': 12000,           # ms
    'qa': 9500,                  # ms
    'refinement': 0,             # ms (if not needed)
    'images': 20000,             # ms
    'format': 3500,              # ms
    'total': 52500               # ms
}

# Cost Breakdown
costs = {
    'research': 0.07,
    'creative': 0.18,
    'qa': 0.08,
    'refinement': 0.00,
    'images': 0.50,
    'format': 0.02,
    'total': 0.85                # Total USD for task
}
```

### Monitoring Endpoints

```python
# Health Check
GET /api/health
Returns: {
    'status': 'healthy|degraded|unhealthy',
    'database': 'connected|disconnected',
    'orchestrator': 'initialized|failed',
    'task_executor': 'running|stopped',
    'pending_tasks': 3,
    'uptime_seconds': 86400,
}

# Executor Statistics
GET /api/executor/stats
Returns: (see above)

# System Metrics
GET /api/metrics
Returns: {
    'requests_total': 1500,
    'requests_active': 5,
    'latency_p95': 450,           # ms
    'latency_p99': 1200,          # ms
    'error_rate': 0.033,          # 3.3%
}

# Workflow History
GET /api/workflow-history?limit=100
Returns: [{
    'workflow_id': '...',
    'type': 'content_generation',
    'user_id': '...',
    'status': 'completed',
    'start_time': '2026-02-10T10:00:00Z',
    'end_time': '2026-02-10T10:02:30Z',
    'duration_seconds': 150,
    'task_count': 6,
    'total_cost': 0.85,
}]
```

---

## Debugging Techniques & Commands

### 1. Enable Debug Logging

```python
# In main.py or startup
import logging
logging.basicConfig(
    level=logging.DEBUG,        # Changed from INFO
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

# Or via environment variable
export LOG_LEVEL=debug
```

### 2. Tail Live Logs

**Terminal 1: Backend**

```bash
# If using npm run dev
npm run dev:cofounder 2>&1 | grep -E "TASK_EXECUTE|TASK_SINGLE|PHASE"

# Or with Python directly
python -m uvicorn main:app --log-level debug --reload
```

**Terminal 2: Grep specific task**

```bash
npm run dev:cofounder 2>&1 | grep "task_id_here"
```

### 3. Monitor Database Queries

```python
# Enable SQL query logging
export SQL_DEBUG=true

# In .env.local:
SQL_DEBUG=true
```

Watch logs for query patterns:

```
SELECT * FROM tasks WHERE status = 'pending'
UPDATE tasks SET status = 'in_progress' WHERE id = '...'
INSERT INTO task_status_history (task_id, old_status, new_status, ...)
```

### 4. Trace Workflow Execution

**Create a custom test task:**

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "task_name": "Debug Task",
    "topic": "Test Topic",
    "style": "informative",
    "tone": "professional",
    "target_length": 1000,
    "generate_featured_image": false
  }'

# Returns: {"id": "task_uuid", "status": "pending"}
```

**Poll task status:**

```bash
# Watch in real-time
while true; do
  curl -s http://localhost:8000/api/tasks/task_uuid \
    -H "Authorization: Bearer YOUR_TOKEN" | jq .status
  sleep 2
done
```

**Check execution logs:**

```bash
# Find in logs:
# [TASK_EXEC_LOOP] Found 1 pending task(s)
# [TASK_SINGLE] Starting task processing
# [TASK_EXECUTE] PHASE 1: Generating content via orchestrator...
# [TASK_EXECUTE] PHASE 2: Validating content through critique loop...
# [TASK_EXECUTE] PHASE 3: Content approved
# etc.
```

### 5. Check Orchestrator Status

```bash
# Get model availability
curl -s http://localhost:8000/api/models \
  -H "Authorization: Bearer YOUR_TOKEN" | jq .

# Returns: {
#   "providers": {
#     "ollama": "available",
#     "anthropic": "available",
#     "openai": "unavailable",
#     ...
#   },
#   "selected": "ollama"
# }

# Get orchestrator stats (if endpoint exists)
curl -s http://localhost:8000/api/executor/stats | jq .
```

### 6. Debug Critical Failures

**If tasks stuck in "in_progress":**

```python
# Check if TaskExecutor is running
# From logs: "✅ Task executor background processor started"

# If not running:
# 1. Check startup logs for errors
# 2. Look for: "🛑 StartupManager" errors
# 3. Verify orchestrator initialized

# Manual reset (development only):
# 1. Check database directly:
curl -s http://localhost:5432  # PostgreSQL

# 2. Find stuck task:
psql $DATABASE_URL -c "
SELECT id, status, started_at
FROM tasks
WHERE status = 'in_progress'
AND started_at < NOW() - INTERVAL '1 hour'
"

# 3. Reset manually (if needed):
psql $DATABASE_URL -c "
UPDATE tasks
SET status = 'failed',
    error_message = 'Manually reset - executor issue'
WHERE id = 'task_id'
"
```

**If orchestrator crashes:**

```python
# Look for in logs:
# "❌ Orchestrator initialization failed"
# "❌ Error initializing UnifiedOrchestrator"

# Check prerequisites:
# 1. Is database connected?
#    → Check DATABASE_URL in .env.local
#    → Verify PostgreSQL running

# 2. Are LLM keys set?
#    → Check OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
#    → At least one should be set

# 3. Is Ollama running (if using local)?
#    → ollama serve (in another terminal)
#    → curl http://localhost:11434/api/tags

# 4. Check service initialization order
#    → Look for startup logs showing each service init
```

### 7. Profile Execution Time

```python
# TaskExecutor logs timing per phase
# Look for in logs:
# ✅ [TASK_EXECUTE] PHASE 1 Complete (generation): Generated 1524 chars in 12.3s
# 🔍 [TASK_EXECUTE] PHASE 2 Complete (critique): Quality score 0.82
# 📝 [TASK_EXECUTE] PHASE 4 Complete (images): Selected/generated image in 18.5s
# etc.

# Calculate bottleneck:
# If PHASE 5 (images) takes 30+ seconds consistently
#  → Optimize image generation or use library instead

# If PHASE 1 (research) takes 20+ seconds
#  → May need cheaper/faster model or cached knowledge base
```

### 8. Test Individual Phases

**Create minimal test:**

```python
# In a test file or Python REPL

from services.task_executor import TaskExecutor
from services.database_service import DatabaseService
from services.unified_orchestrator import UnifiedOrchestrator

# Initialize services
db = DatabaseService()
orchestrator = UnifiedOrchestrator(db)
executor = TaskExecutor(db, orchestrator)

# Test Phase 1 (content generation)
task = {
    'id': 'test-task-1',
    'topic': 'Test Topic',
    'style': 'informative',
    'tone': 'professional',
    'target_length': 500
}

# Run generation
result = await orchestrator.process_request(
    topic=task['topic'],
    style=task['style'],
    tone=task['tone'],
    target_length=task['target_length']
)

print(result)  # See output, any errors, timing
```

### 9. WebSocket Debugging

**Connect to real-time progress:**

```javascript
// In browser console or test client

const taskId = 'your-task-uuid';
const ws = new WebSocket(`ws://localhost:8000/ws/tasks/${taskId}`);

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Progress update:', data);
    // Shows current phase, progress %, estimated time, etc.
};

ws.onerror = (error) => {
    console.error('WebSocket error:', error);
};
```

### 10. Database Inspection

```bash
# Connect to PostgreSQL
psql $DATABASE_URL

# See pending tasks and their status
SELECT id, task_name, status, created_at, started_at
FROM tasks
ORDER BY created_at DESC
LIMIT 10;

# Check task execution history
SELECT task_id, old_status, new_status, reason, changed_at
FROM task_status_history
WHERE task_id = 'your-task-id'
ORDER BY changed_at;

# Check quality scores
SELECT task_id, overall_score, criteria_scores, feedback
FROM quality_scores
WHERE task_id = 'your-task-id'
ORDER BY timestamp DESC;

# See workflow execution times
SELECT workflow_type, status,
       EXTRACT(EPOCH FROM (end_time - start_time)) as duration_seconds,
       metrics
FROM workflow_history
WHERE user_id = 'your-user-id'
ORDER BY start_time DESC
LIMIT 20;
```

---

## Quick Reference: Common Debug Scenarios

| Scenario | Issue | Debug Steps |
|----------|-------|------------|
| **Tasks stuck in pending** | Executor not running | Check logs for `Task executor background processor started` |
| **Very slow content gen** | Model selection issue | Check `/api/models` endpoint |
| **Quality always fails** | Threshold too high | Check ContentCritiqueLoop, reduce threshold temporarily |
| **Refinement loop endless** | Max retries not working | Check `MAX_REFINEMENT_ATTEMPTS` constant |
| **Database connection error** | PostgreSQL unavailable | Verify DATABASE_URL, check `psql $DATABASE_URL` |
| **Orchestrator crashes** | Service init failure | Check startup logs, verify all dependencies |
| **WebSocket disconnects** | Connection issue | Check CORS settings, WebSocket timeout |
| **Image gen timeout** | Image service slow | Check image provider status, use library instead |
| **CMS publish fails** | Strapi unavailable | Verify CMS_URL, check webhook auth |
| **Memory leak** | Long-running executor | Check for unclosed connections in loops |

---

## Summary

This comprehensive analysis provides:

✅ **Complete System Architecture** - Three-tier structure with all major components  
✅ **Request Flow Diagrams** - End-to-end request lifecycle  
✅ **6-Phase Pipeline** - Detailed breakdown of content generation  
✅ **Service Map** - 60+ services with responsibilities  
✅ **Error Handling** - Exception hierarchy and recovery  
✅ **Database Schema** - Table structure and queries  
✅ **Quality Framework** - 7-criteria evaluation system  
✅ **Debugging Techniques** - 10 practical debugging approaches  
✅ **Performance Metrics** - Monitoring and profiling  
✅ **Quick Reference** - Common scenarios and solutions  

Use this guide to trace any request through the system, identify bottlenecks, and debug issues systematically.

---

**For questions or updates, see:**

- `/docs/05-AI_AGENTS_AND_INTEGRATION.md` - Agent details
- `/docs/06-OPERATIONS_AND_MAINTENANCE.md` - Operations guide  
- `src/cofounder_agent/README.md` - Service README
- `src/cofounder_agent/DOCUMENTATION_INDEX.md` - Full documentation index
