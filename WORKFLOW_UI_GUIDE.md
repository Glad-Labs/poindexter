# Blog Workflow UI Integration

## Overview

The Blog Workflow system has been successfully integrated into the Oversight Hub admin dashboard. Users can now create, execute, and monitor blog post generation workflows through an intuitive 4-step interface.

## What Was Added

### 1. **Backend API Endpoints** (in `src/cofounder_agent/services/workflow_executor.py`)
- Now dynamically loads and executes blog agents
- Handles both sync and async agents
- Supports phase sequencing and data threading
- Real-time progress tracking

### 2. **Frontend API Client Methods** (`web/oversight-hub/src/lib/apiClient.js`)
Added 5 new workflow endpoints:
- `getAvailablePhases()` - List all available workflow phases
- `executeWorkflow(workflow)` - Create and execute a workflow
- `getWorkflowProgress(executionId)` - Get real-time progress
- `getWorkflowResults(executionId)` - Get execution results
- `listWorkflowExecutions(params)` - View workflow history
- `cancelWorkflowExecution(executionId)` - Cancel running workflows

### 3. **UI Component** (`web/oversight-hub/src/pages/BlogWorkflowPage.jsx`)
A comprehensive 4-step workflow builder with:
- **Step 1: Design** - Select which phases to include
- **Step 2: Configure** - Set parameters (topic, style, tone, word count)
- **Step 3: Execute** - Run workflow and monitor progress
- **Step 4: Results** - View phase results and published content

### 4. **Navigation**
- Added "Workflows" link to sidebar (🔄 icon)
- Route: `/workflows`
- Protected by authentication

## How to Use

### 1. **Access the Workflow Builder**
1. Go to the Oversight Hub dashboard
2. Click "Workflows" in the left sidebar
3. You'll see the Blog Workflow Builder

### 2. **Design Your Workflow**
1. Select which phases to include:
   - ✓ **Blog Generate Content** - Create blog post using AI
   - ✓ **Blog Quality Evaluation** - Evaluate content quality (7 dimensions)
   - ✓ **Blog Search Image** - Find featured images from Pexels
   - ✓ **Blog Create Post** - Create and publish post to database

2. Click "Next: Configure Parameters"

### 3. **Configure Parameters**
Set the following:
- **Blog Topic** - What the post is about (e.g., "AI in Healthcare")
- **Content Style** - balanced, technical, narrative, listicle, thought-leadership
- **Content Tone** - professional, casual, academic, inspirational
- **Target Word Count** - 500-5000 words (default: 1500)

Click "Execute Workflow"

### 4. **Monitor Execution**
The system shows:
- Current phase being executed
- Progress percentage
- Estimated time remaining
- Option to cancel if needed

### 5. **View Results**
Once complete, you'll see:
- Status of each phase (✓ completed or ✗ failed)
- Execution time per phase
- Link to view the published blog post
- Recent workflow execution history

## Example Workflow Execution

```
Topic: "Artificial Intelligence in Healthcare"
Style: Technical
Tone: Professional
Word Count: 1500

Execution Flow:
1. blog_generate_content (2min 15s)
   └─ Generated 1,547 words

2. blog_quality_evaluation (5s)
   └─ Quality Score: 78/100 ✓ PASSED

3. blog_search_image (3s)
   └─ Found featured image from Pexels

4. blog_create_post (2s)
   └─ Post created: /posts/artificial-intelligence-in-healthcare

Total Time: 2min 25s
Result: ✓ Published successfully
```

## API Endpoints Used

The UI communicates with these backend endpoints:

```
GET  /api/workflows/phases
     └─ Get list of available phases

POST /api/workflows/custom
     └─ Create and execute a workflow

GET  /api/workflows/executions/{id}/progress
     └─ Get real-time execution progress

GET  /api/workflows/executions/{id}/results
     └─ Get complete execution results

GET  /api/workflows/executions
     └─ List workflow execution history

POST /api/workflows/executions/{id}/cancel
     └─ Cancel a running workflow
```

## Workflow Data Flow

```
User Input (UI)
    ↓
Build Workflow Definition
    ↓
POST /api/workflows/custom (Execute)
    ↓
WorkflowExecutor._execute_phase()
    ↓
Load Agent (blog_content_generator_agent, etc)
    ↓
Agent.run(phase_inputs)
    ↓
Existing Services:
  - ai_content_generator
  - quality_service
  - image_service
  - database_service
    ↓
Phase Output
    ↓
Pass to Next Phase (data threading)
    ↓
Return Results to UI
```

## Key Features

✅ **Multi-Phase Workflows** - Combine any phases in any order
✅ **Real-Time Progress** - See execution status as it happens
✅ **Error Handling** - Clear error messages and retry options
✅ **Workflow History** - View all past executions
✅ **Direct Publishing** - Blog posts are automatically created
✅ **Extensible** - Easy to add new phases or workflows

## Configuration Options

### Content Generation Phase
- Topic (required)
- Style (balanced, technical, narrative, listicle, thought-leadership)
- Tone (professional, casual, academic, inspirational)
- Target Length (500-5000 words)
- Tags (optional)

### Quality Evaluation Phase
- Evaluation Method (pattern-based, llm-based, hybrid)
- Quality Threshold (default: 70/100)

### Image Search Phase
- Orientation (landscape, portrait, square)
- Number of Images (1-5)

### Post Creation Phase
- Publish Immediately (true/false, default: true)
- Category (default: "News")

## Next Steps

1. **Start the Server**
   ```bash
   npm run dev
   ```

2. **Navigate to Workflows**
   - Dashboard → Workflows

3. **Create Your First Workflow**
   - Select all phases
   - Configure for your topic
   - Click "Execute Workflow"

4. **Monitor Progress**
   - Watch real-time updates
   - View results when complete

5. **Create More Workflows**
   - Different topics
   - Different configurations
   - Different phase combinations

## Troubleshooting

### Workflow won't execute
- Check that topic is not empty
- Verify at least one phase is selected
- Check browser console for errors

### Phases failing
- Check backend logs: `npm run dev:cofounder`
- Verify Pexels API key is set for image search
- Check database connection for post creation

### Progress not updating
- Refresh the page
- Check that WebSocket connection is working
- Look at browser network tab for `/api/workflows/progress` calls

## Architecture

```
Oversight Hub (React)
    ↓
BlogWorkflowPage.jsx
    ↓
apiClient (Workflow endpoints)
    ↓
FastAPI Backend
    ↓
WorkflowExecutor
    ↓
PhaseRegistry (blog_generate_content, blog_quality_evaluation, etc)
    ↓
Blog Bridge Agents
    ↓
Existing Services
    ↓
PostgreSQL Database
```

## Performance Notes

- Typical blog generation: 2-3 minutes
- Quality evaluation: <10 seconds
- Image search: 3-5 seconds
- Post creation: <5 seconds
- **Total time for complete workflow: ~2.5 minutes**

## Files Modified/Created

### Created:
- `web/oversight-hub/src/pages/BlogWorkflowPage.jsx` - Main UI component
- `src/cofounder_agent/test_blog_workflow.py` - Integration tests
- 4 Blog Bridge Agents in `src/cofounder_agent/agents/`

### Modified:
- `web/oversight-hub/src/lib/apiClient.js` - Added workflow endpoints
- `web/oversight-hub/src/routes/AppRoutes.jsx` - Added /workflows route
- `web/oversight-hub/src/components/common/Sidebar.jsx` - Added navigation link
- `src/cofounder_agent/services/workflow_executor.py` - Implemented agent dispatch
- `src/cofounder_agent/services/phase_registry.py` - Registered blog phases

## Support

For issues or questions:
1. Check backend logs: `npm run dev:cofounder`
2. Check browser console (F12)
3. Review workflow execution history for error details
