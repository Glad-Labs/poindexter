# 🏗️ LangGraph Implementation Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Oversight Hub (React)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  BlogCreatorWithLangGraph Component                      │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ • TextField: topic input                                │  │
│  │ • Button: "Create with LangGraph"                       │  │
│  │ • POST /api/content/langgraph/blog-posts                │  │
│  └──────────────────────────────────────────────────────────┘  │
│         │                                                       │
│         │ requestId                                             │
│         ↓                                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  LangGraphStreamProgress Component                       │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ • useLangGraphStream(requestId)                         │  │
│  │ • Stepper: 5 phases                                     │  │
│  │ • LinearProgress: 0-100%                                │  │
│  │ • Quality card, content preview                         │  │
│  │ • onComplete callback                                   │  │
│  └──────────────────────────────────────────────────────────┘  │
│         │                                                       │
└─────────┼───────────────────────────────────────────────────────┘
          │ WebSocket stream
          │ /api/content/langgraph/ws/blog-posts/{request_id}
          │
          ↓
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend (8000)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  API Routes (content_routes.py)                         │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ • POST /langgraph/blog-posts                            │  │
│  │ • WebSocket /langgraph/ws/blog-posts/{id}               │  │
│  └──────────────────────────────────────────────────────────┘  │
│         │                                                       │
│         ↓                                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  LangGraphOrchestrator Service                          │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ • execute_content_pipeline()                            │  │
│  │ • _sync_execution() [HTTP]                              │  │
│  │ • _stream_execution() [WebSocket]                       │  │
│  │ • _calculate_progress()                                 │  │
│  └──────────────────────────────────────────────────────────┘  │
│         │                                                       │
│         ↓                                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  ContentPipelineGraph (LangGraph)                        │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ Graph with 6 Nodes + Decision Logic                     │  │
│  │                                                          │  │
│  │    START                                                │  │
│  │      │                                                  │  │
│  │  [1] research_phase                                    │  │
│  │      ↓                                                  │  │
│  │  [2] outline_phase                                     │  │
│  │      ↓                                                  │  │
│  │  [3] draft_phase                                       │  │
│  │      ↓                                                  │  │
│  │  [4] assess_quality                                    │  │
│  │      ↓                                                  │  │
│  │  [DECISION] should_refine()                            │  │
│  │      │                                                  │  │
│  │   ┌──┴──┐                                               │  │
│  │   ↓     ↓                                               │  │
│  │  YES   NO                                              │  │
│  │   │     │                                               │  │
│  │  [5]   [6] finalize_phase                              │  │
│  │  refine_phase                                           │  │
│  │   │     ↑                                               │  │
│  │   └─ → assess_quality (loop)                            │  │
│  │         │                                               │  │
│  │         ↓                                               │  │
│  │      [6] finalize_phase                                │  │
│  │          ↓                                              │  │
│  │        END                                              │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│         │                                                       │
└─────────┼───────────────────────────────────────────────────────┘
          │
          ↓
┌─────────────────────────────────────────────────────────────────┐
│                  Integrated Services                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │ ModelRouter     │  │ QualityService  │  │ MetadataService
│  │ (LLM Providers) │  │ (7-criteria)    │  │ (SEO + tags) │  │
│  │                 │  │                 │  │               │  │
│  │ • Ollama        │  │ • Scoring       │  │ • Generate    │  │
│  │ • OpenAI        │  │ • Feedback      │  │ • Extract     │  │
│  │ • Anthropic     │  │ • Assessment    │  │               │  │
│  │ • Gemini        │  │                 │  │               │  │
│  │ • HuggingFace   │  │                 │  │               │  │
│  └─────────────────┘  └─────────────────┘  └───────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
          │
          ↓
┌─────────────────────────────────────────────────────────────────┐
│              Persistence Layer (PostgreSQL)                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  • Tasks table                                                 │
│  • Content table                                               │
│  • Quality assessments                                         │
│  • Metadata                                                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Blog Creation

```
User Input
│
├─ POST /api/content/langgraph/blog-posts
│  └─ { topic, keywords, audience, tone, word_count }
│
├─ LangGraphOrchestrator.execute_content_pipeline()
│  ├─ Creates ContentPipelineState
│  └─ Returns request_id (202 Accepted)
│
├─ User connects to WebSocket
│  └─ ws://localhost:8000/api/content/langgraph/ws/blog-posts/{request_id}
│
├─ Graph execution starts
│  ├─ Phase 1: research_phase
│  │  ├─ Uses ModelRouter to call LLM
│  │  ├─ Streams: {"type": "progress", "node": "research", "progress": 15}
│  │  └─ Saves state.research_notes
│  │
│  ├─ Phase 2: outline_phase
│  │  ├─ Uses ModelRouter to call LLM
│  │  ├─ Streams: {"type": "progress", "node": "outline", "progress": 30}
│  │  └─ Saves state.outline
│  │
│  ├─ Phase 3: draft_phase
│  │  ├─ Uses ModelRouter to call LLM
│  │  ├─ Streams: {"type": "progress", "node": "draft", "progress": 50}
│  │  └─ Saves state.draft
│  │
│  ├─ Phase 4: assess_quality
│  │  ├─ Uses QualityService.evaluate()
│  │  ├─ Scores 0-100, provides feedback
│  │  └─ Streams: {"type": "progress", "node": "assess", "progress": 70, "quality_score": 78}
│  │
│  ├─ Decision: should_refine()?
│  │  ├─ If quality >= 80: Go to finalize
│  │  ├─ If quality < 80 AND attempts < 3: Go to refine
│  │  └─ If attempts >= 3: Go to finalize anyway
│  │
│  ├─ [If refining] Phase 5a: refine_phase
│  │  ├─ Uses ModelRouter to improve content
│  │  ├─ Increments refinement_count
│  │  └─ Loops back to assess_quality
│  │
│  ├─ Phase 6: finalize_phase
│  │  ├─ Uses MetadataService to generate SEO
│  │  ├─ Saves to PostgreSQL via db_service
│  │  ├─ Sets task_id from database
│  │  └─ Streams: {"type": "progress", "node": "finalize", "progress": 100}
│  │
│  └─ Graph complete
│
├─ WebSocket streams final message
│  └─ {"type": "complete", "status": "completed"}
│
└─ Frontend displays completion alert
   └─ Quality: 85/100, Refinements: 1
```

---

## Component Interaction

```
React Components
├─ useLangGraphStream Hook
│  ├─ Creates WebSocket connection
│  ├─ Listens for messages
│  ├─ Updates progress state
│  └─ Returns: { phase, progress, quality, refinements, error }
│
└─ LangGraphStreamProgress Component
   ├─ Receives requestId from parent
   ├─ Calls useLangGraphStream(requestId)
   ├─ Renders Stepper (5 phases)
   ├─ Renders LinearProgress (0-100%)
   ├─ Renders Quality Card (when score > 0)
   ├─ Renders Content Preview (when draft exists)
   ├─ Calls onComplete when status === "completed"
   └─ Calls onError when status === "error"
```

---

## State Evolution Through Graph

```
INITIAL STATE:
{
  topic: "AI Safety",
  keywords: ["AI", "safety"],
  research_notes: "",
  outline: "",
  draft: "",
  quality_score: 0,
  status: "in_progress"
}
          ↓ (research_phase)
{
  topic: "AI Safety",
  keywords: ["AI", "safety"],
  research_notes: "AI safety is...", ← FILLED
  outline: "",
  draft: "",
  quality_score: 0,
  status: "in_progress"
}
          ↓ (outline_phase)
{
  topic: "AI Safety",
  keywords: ["AI", "safety"],
  research_notes: "AI safety is...",
  outline: "1. Introduction\n2. Risks\n...", ← FILLED
  draft: "",
  quality_score: 0,
  status: "in_progress"
}
          ↓ (draft_phase)
{
  topic: "AI Safety",
  keywords: ["AI", "safety"],
  research_notes: "AI safety is...",
  outline: "1. Introduction\n2. Risks\n...",
  draft: "# AI Safety\n\nAI safety is...", ← FILLED
  quality_score: 0,
  status: "in_progress"
}
          ↓ (assess_quality)
{
  topic: "AI Safety",
  keywords: ["AI", "safety"],
  research_notes: "AI safety is...",
  outline: "1. Introduction\n2. Risks\n...",
  draft: "# AI Safety\n\nAI safety is...",
  quality_score: 75, ← FILLED
  quality_feedback: "Add more technical depth",
  passed_quality: false,
  status: "in_progress"
}
          ↓ should_refine? YES (75 < 80)
          ↓ (refine_phase)
{
  topic: "AI Safety",
  keywords: ["AI", "safety"],
  research_notes: "AI safety is...",
  outline: "1. Introduction\n2. Risks\n...",
  draft: "# AI Safety\n\nAI safety and technical aspects...", ← IMPROVED
  quality_score: 75,
  quality_feedback: "Add more technical depth",
  refinement_count: 1, ← INCREMENTED
  status: "in_progress"
}
          ↓ (assess_quality again)
{
  topic: "AI Safety",
  keywords: ["AI", "safety"],
  research_notes: "AI safety is...",
  outline: "1. Introduction\n2. Risks\n...",
  draft: "# AI Safety\n\nAI safety and technical aspects...",
  quality_score: 85, ← IMPROVED
  quality_feedback: "",
  passed_quality: true, ← NOW TRUE
  refinement_count: 1,
  status: "in_progress"
}
          ↓ should_refine? NO (85 >= 80)
          ↓ (finalize_phase)
{
  topic: "AI Safety",
  keywords: ["AI", "safety"],
  research_notes: "AI safety is...",
  outline: "1. Introduction\n2. Risks\n...",
  draft: "# AI Safety\n\nAI safety and technical aspects...",
  final_content: "# AI Safety\n\nAI safety and technical aspects...", ← FILLED
  quality_score: 85,
  passed_quality: true,
  refinement_count: 1,
  metadata: { title: "AI Safety", description: "...", keywords: [...] }, ← FILLED
  task_id: "task_abc123", ← FILLED
  status: "completed", ← FINAL STATE
  completed_at: "2025-12-18T..."
}
```

---

## Error Handling Paths

```
Graph Execution Begins
        │
        ├─ LLM Call Fails
        │  └─ catch → state["errors"].append()
        │     └─ Continue with previous state
        │        └─ May go to finalize with partial content
        │
        ├─ Database Save Fails
        │  └─ catch → state["errors"].append()
        │     └─ Still return task_id (in-memory)
        │     └─ Retry on next poll
        │
        ├─ Quality Assessment Fails
        │  └─ catch → state["quality_score"] = 50
        │     └─ Allow refinement attempt
        │     └─ Continue normally
        │
        ├─ WebSocket Connection Drops
        │  └─ Frontend: Automatic reconnect
        │     └─ Resume from last known progress
        │
        └─ Graph Execution Completes Successfully
           └─ Stream "complete" event
              └─ Frontend shows completion alert
```

---

## Performance Characteristics

```
PHASE                  TIME         TOKENS      NODES
────────────────────────────────────────────────────
Research               30-60s       200-300     1 (LLM)
Outline                20-40s       100-200     1 (LLM)
Draft                  60-120s      500-1000    1 (LLM)
─────────────────────────────────────────────────── SUBTOTAL: 2-4 min, 800-1500 tokens
Quality Assessment     10-30s       50-100      1 (Scoring)
───────────────────────────────────────────────────
[Refinement Loop]
  └─ Refine            30-60s       100-200     1 (LLM)
  └─ Reassess          10-30s       50-100      1 (Scoring)
─────────────────────────────────────────────────── (repeat 0-3 times, add 40-90s per loop)
Finalize               5-10s        10-50       2 (Metadata + DB)
───────────────────────────────────────────────────
TOTAL                  2.5-5.5 min  900-1800    7-10 LLM calls

BOTTLENECK: LLM calls (dependent on provider)
  • Ollama: 5-10s per token (local, unlimited)
  • OpenAI: 1-2s per token (fast, paid)
  • Anthropic: 2-3s per token (accurate, paid)
```

---

## Deployment Architecture

```
DEVELOPMENT (Local)
├─ FastAPI: http://localhost:8000
├─ React: http://localhost:3001
├─ PostgreSQL: localhost:5432
└─ LLM: Ollama on localhost:11434

STAGING (Docker)
├─ FastAPI: cofounder-agent:8000
├─ React: oversight-hub:3000
├─ PostgreSQL: postgres:5432
└─ LLM: ollama:11434

PRODUCTION (Kubernetes/Railway)
├─ FastAPI: cofounder-agent-prod
├─ React: oversight-hub-prod
├─ PostgreSQL: Tier-1 managed DB
└─ LLM: OpenAI/Anthropic APIs (no local fallback)
```

---

## Testing Strategy

```
UNIT TESTS
├─ test_content_pipeline.py
│  ├─ Test each node in isolation
│  ├─ Mock LLM, quality service
│  └─ Verify state transformations
│
├─ test_langgraph_orchestrator.py
│  ├─ Test sync execution path
│  ├─ Test stream execution path
│  └─ Test error handling
│
└─ test_api_endpoints.py
   ├─ Test POST /langgraph/blog-posts
   └─ Test WebSocket connection

INTEGRATION TESTS
├─ Full pipeline with mock LLM
├─ WebSocket streaming simulation
├─ Database persistence verification
└─ Error recovery scenarios

E2E TESTS
├─ UI: Create blog via React component
├─ Streaming: Verify progress updates in real-time
├─ Quality: Check refinement loops work
└─ Database: Verify content saved correctly
```

---

**Architecture is production-ready and fully documented.** ✅
