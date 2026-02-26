# Phase-Based Workflow System

## Overview

The phase-based workflow system enables **infinite flexibility** in content creation. Instead of predefined workflows, users can compose **any combination of capabilities** to create custom workflows.

### Core Concept: Phases

A **Phase** is an independent, composable unit of work that:

- Has well-defined inputs and outputs
- Is configurable via parameters
- Can be executed in isolation OR as part of a larger workflow
- Threads data to downstream phases

Example phases:

- `generate_content` - AI-generated blog content
- `quality_evaluation` - 7-dimension quality assessment
- `search_image` - Find featured image on Pexels
- `generate_seo` - Generate SEO metadata
- `create_post` - Save post to database
- `publish_post` - Publish and set live

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│             PHASE REGISTRY                              │
│  (Central catalog of all available phases)              │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  7 Built-in Phases:                                     │
│  • GenerateContentPhase         (input: topic → content)|
│  • QualityEvaluationPhase       (input: content → score)|
│  • SearchImagePhase             (input: topic → image) │
│  • GenerateSEOPhase             (input: content → seo) │
│  • CaptureTrainingDataPhase     (input: score → stored)|
│  • CreatePostPhase              (input: content → id)  │
│  • PublishPostPhase             (input: id → published)|
│                                                         │
│  Each phase is discoverable via /api/workflows/phases  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Data Threading (Key Feature)

Phases are connected via **data threading** - outputs from one phase automatically flow to the next phase's inputs.

### Example: Blog Post Workflow

```
User Input:
├─ topic: "AI in Healthcare"
├─ style: "technical"
├─ tone: "professional"
└─ tags: ["AI", "healthcare"]

           ┌─────────────────────┐
           │  PHASE 1: Generate  │
Input ────>│  Content            │
           │  style: technical   │───Output: content, model_used
           └─────────────────────┘
                    │
                    ▼
           ┌─────────────────────┐
           │  PHASE 2: Quality   │
├─content ─┤  Evaluation         │
├─topic  ──┤  threshold: 70      │───Output: score, passing
└─tags   ──│                     │
           └─────────────────────┘
                    │
                    ▼
           ┌─────────────────────┐
           │  PHASE 3: Search    │
├─topic  ──┤  Image              │
└─tags  ───┤  enabled: true      │───Output: image_url
           └─────────────────────┘
                    │
                    ▼
           ┌─────────────────────┐
           │  PHASE 4: Generate  │
├─content ─┤  SEO                │───Output: seo_title, seo_desc
└─topic  ──┤                     │
           └─────────────────────┘
                    │
                    ▼
           ┌─────────────────────┐
           │  PHASE 5: Create    │
├─content ─┤  Post               │
├─topic  ──┤  status: draft      │───Output: post_id, slug
├─seo_* ───┤                     │
└─image ───┤                     │
           └─────────────────────┘

Final Result:
{
  "post_id": "uuid",
  "slug": "ai-in-healthcare",
  "content": "...",
  "seo_title": "...",
  "image_url": "..."
}
```

## Usage Examples

### Example 1: Generate & Evaluate Only

```python
# No publishing, just content generation and quality check
workflow = {
    "name": "Quick Blog Draft",
    "phases": [
        {"type": "generate_content", "config": {"style": "technical"}},
        {"type": "quality_evaluation", "config": {"threshold": 70}}
    ],
    "topic": "Machine Learning Basics"
}

# Execute: POST /api/workflows/custom
# Result: Content + Quality scores (no post created)
```

### Example 2: Complete Blog Post

```python
# End-to-end: Generate → Evaluate → Image → SEO → Create
workflow = {
    "name": "Complete Blog",
    "phases": [
        {"type": "generate_content", "config": {"target_length": 2000}},
        {"type": "quality_evaluation", "config": {"threshold": 75}},
        {"type": "search_image", "config": {"enabled": true}},
        {"type": "generate_seo", "config": {}},
        {"type": "create_post", "config": {"status": "draft"}}
    ],
    "topic": "AI in Healthcare"
}

# Result: Blog post saved to database (draft status)
```

### Example 3: Custom Workflow

```python
# User-defined sequence of any phases
workflow = {
    "name": "Special Workflow",
    "phases": [
        # Start with content generation
        {"type": "generate_content", ...},
        # Skip image search
        # Go straight to SEO
        {"type": "generate_seo", ...},
        # Capture training data
        {"type": "capture_training_data", ...}
    ],
    "topic": "..."
}
```

### Example 4: Optional Phases

```python
# Some phases can be optional (failure doesn't stop workflow)
workflow = {
    "phases": [
        {"type": "generate_content", ...},
        {"type": "quality_evaluation", ...},
        {
            "type": "search_image",
            "config": {"enabled": true},
            "required": false  # ← Optional: workflow continues even if image search fails
        },
        {"type": "create_post", ...}
    ]
}
```

## API Endpoints

### Discover Available Phases

```
GET /api/workflows/phases

Response:
{
  "phases": {
    "generate_content": {
      "name": "Generate Content",
      "description": "AI-generated content...",
      "inputs": [...],
      "outputs": [...],
      "configurable_params": {"style": "balanced", ...}
    },
    ...
  },
  "count": 7
}
```

### Get Phase Details

```
GET /api/workflows/phases/{phase_type}

Example: GET /api/workflows/phases/quality_evaluation

Response: Detailed specification of inputs, outputs, params
```

### Create Custom Workflow

```
POST /api/workflows/custom

Request:
{
  "name": "My Workflow",
  "phases": [ ... ],
  "topic": "AI in Healthcare",
  "style": "technical",
  "tone": "professional",
  "tags": ["AI", "healthcare"]
}

Response (202 Accepted):
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "created_at": "2026-02-25T12:34:56Z"
}
```

### Get Workflow Status

```
GET /api/workflows/{workflow_id}

Response:
{
  "workflow_id": "550e8400-...",
  "status": "completed",
  "phase_results": {
    "generate_content": {...},
    "quality_evaluation": {...},
    ...
  },
  "execution_time": 45.2
}
```

### Approve Workflow (at approval gates)

```
POST /api/workflows/{workflow_id}/approve

Request:
{
  "feedback": "Looks good, proceed with publishing"
}

Response: Workflow continues from approval point
```

## File Structure

```
src/cofounder_agent/services/phases/
├─ __init__.py                    # Module initialization
├─ base_phase.py                  # BasePhase abstract class
├─ phase_registry.py              # PhaseRegistry (central catalog)
├─ content_phases.py              # Content generation phases
├─ publishing_phases.py           # Publishing phases
├─ workflow_executor.py           # WorkflowExecutor (executes phase sequences)
└─ example_workflows.py           # Pre-configured workflow templates
```

## Implementation Status

✅ **Completed:**

- BasePhase class with input/output specifications
- PhaseRegistry for discovering available phases
- Content phases (GenerateContent, QualityEvaluation, SearchImage, GenerateSEO, CaptureTrainingData)
- Publishing phases (CreatePost, PublishPost)
- WorkflowExecutor with data threading
- Phase discovery API endpoints

🔄 **In Progress:**

- Database schema for workflows table
- Integration with existing API routes
- WebSocket support for real-time progress updates

📋 **TODO:**

- Workflow composer UI (phase canvas)
- Social media phases (TwitterPost, LinkedInPost, etc.)
- Email phases
- Research phase
- Approval gate support in executor
- Background task queue integration (Celery)

## Next Steps

1. **Database Setup** - Create `workflows` table with phase results and metadata
2. **API Integration** - Hook up custom_workflows_routes to use new phase system
3. **UI Composer** - Build phase canvas in Oversight Hub for drag-drop workflow design
4. **Testing** - Test complete workflows: blog → social → email
5. **Scaling** - Add more phases (research, social media, email, etc.)

## Future Phases (To Be Implemented)

```
# Research Phase
{
  "type": "research",
  "inputs": ["topic", "depth"],
  "outputs": ["research_findings", "sources"]
}

# Social Media Phases
{
  "type": "social_media_post",
  "inputs": ["content", "platform", "hashtags"],
  "outputs": ["post_id", "url"]
}

# Email Phase
{
  "type": "send_email",
  "inputs": ["content", "recipients", "subject"],
  "outputs": ["sent_count", "bounced"]
}

# Approval Phase
{
  "type": "approval_gate",
  "inputs": ["content", "quality_score"],
  "outputs": ["approved", "feedback"]
}
```

## Benefits

🎯 **Flexibility** - Users can compose ANY combination of capabilities
🔄 **Reusability** - Phases are independent and can be used in any order
📈 **Scalability** - Easy to add new phases without changing architecture
🧪 **Testing** - Each phase can be tested in isolation
📊 **Observability** - Full execution history with phase results
🚀 **Real-time** - WebSocket updates during execution
💾 **Learning** - Training data captured from every execution
