# 🎯 Poindexter: Autonomous AI Orchestrator with smolagents

**Version:** 1.0  
**Status:** Design Phase  
**Date:** November 8, 2025  
**Architecture:** smolagents ReAct loops + MCP server discovery + Self-critique loops

---

## 🎨 Vision

Transform Glad Labs into a true autonomous orchestrator that:

```
Raw Text Command
    ↓
"Create a blog post about AI trends with images, publish to Twitter, track metrics"
    ↓
Poindexter (Reasoning Agent)
    ├─ What do I need? (tools: research, generate, critique, publish, track)
    ├─ What MCP servers are available? (discover: web-search, image-gen, twitter-api)
    ├─ What are my constraints? (budget: $5, models: Ollama-first, quality: 90%+)
    ├─ What's the best workflow? (research → generate → critique → refine → images → publish → metrics)
    └─ Execute with autonomy, self-correct, track everything
    ↓
Publication + Metrics + Cost Report
```

---

## 🏗️ Architecture Overview

### Layer 1: Request Processing

```
POST /api/v2/orchestrate
Body: {
  "command": "Create blog post about AI",
  "constraints": {
    "budget": 5.00,
    "preferred_models": ["ollama", "gpt-4"],
    "quality_threshold": 0.90,
    "max_runtime": 300
  },
  "context": {
    "user_id": "user123",
    "project_id": "proj456",
    "previous_results": [...]
  }
}
```

### Layer 2: Poindexter Reasoning Engine

```python
Poindexter Agent (ReAct Loop)
├─ Observation: Parse command & constraints
├─ Thought: "I need research, content generation, critique, images, publishing"
├─ Action: Call discover_tools(), discover_mcp_servers()
├─ Observation: Receive available tools & MCP servers
├─ Thought: "Best workflow is: research → generate → critique → refine → images → publish"
├─ Action: Execute workflow with error recovery
└─ Thought: "Quality check passed (0.93 > 0.90), publish result"
```

### Layer 3: Existing Agents as Tools

```
Poindexter calls:
├─ research_tool()     → ResearchAgent (semantic search, fact gathering)
├─ generate_tool()     → ContentAgent (generation phase)
├─ critique_tool()     → QAAgent (self-critique phase)
├─ refine_tool()       → ContentAgent + feedback (refinement)
├─ publish_tool()      → PublishingAgent (Strapi CMS)
├─ track_metrics_tool()→ FinancialAgent (cost tracking)
└─ fetch_images_tool()→ ImageAgent (visual assets)
```

### Layer 4: MCP Server Discovery & Bridging

```
MCP Registry
├─ web-search-server     → Google search, news, trends
├─ image-generation      → DALL-E, Stable Diffusion endpoints
├─ twitter-api           → Tweet publishing
├─ database-query        → Complex data retrieval
├─ analytics-server      → Real-time metrics
└─ custom-servers        → Your deployed MCP servers

Poindexter can discover & use any available MCP server as a tool
```

### Layer 5: Response & Tracking

```json
{
  "status": "success",
  "result": {
    "content_id": "post_789",
    "url": "https://blog.example.com/ai-trends",
    "published_to": ["web", "twitter"],
    "quality_score": 0.93,
    "images_count": 3
  },
  "workflow_trace": {
    "reasoning": ["researched AI trends", "generated 2000 word post", ...],
    "tools_used": ["research", "generate", "critique", "refine", "fetch_images", "publish"],
    "mcp_servers_used": ["web-search", "twitter-api"],
    "total_time": 87,
    "total_cost": 2.34,
    "critique_loops": 2
  }
}
```

---

## 🛠️ Core Components

### 1. Poindexter Agent Engine

**File:** `services/poindexter_orchestrator.py`

```python
class Poindexter:
    """
    Autonomous orchestrator using smolagents ReAct reasoning.

    Key features:
    - ReAct (Reasoning + Acting) decision loops
    - Dynamic tool discovery (agents + MCP servers)
    - Constraint-based reasoning
    - Self-critique loops
    - Multi-provider LLM support
    """

    def __init__(self, model_router, agent_factory):
        self.model_router = model_router  # Your existing router (Ollama-first)
        self.agent_factory = agent_factory  # Creates specialized agents
        self.mcp_registry = MCPRegistry()   # Discovers & caches MCP servers
        self.tool_registry = ToolRegistry() # Maps tools & constraints

    async def orchestrate(self, command: str, constraints: Dict) -> OrchestrationResult:
        """
        Main entry point. Takes raw command and executes autonomously.

        Poindexter's reasoning:
        1. Parse command → Extract intent & requirements
        2. Discover tools → What can I use? (agents + MCP servers)
        3. Reason about workflow → Best sequence?
        4. Check constraints → Budget, quality, time OK?
        5. Execute → With error recovery & self-critique
        6. Validate → Did it meet quality threshold?
        7. Return → Full trace + results
        """
        pass
```

### 2. Tool Definitions with Constraints

**File:** `services/poindexter_tools.py`

Each tool includes:

- Clear description (for LLM reasoning)
- Input validation
- Constraint checking (budget, time, quality)
- Error recovery
- Self-critique loop integration

```python
@tool
def research_tool(topic: str, depth: str = "comprehensive") -> str:
    """
    Research a topic using web search, semantic analysis, and knowledge bases.

    Constraints:
    - Cost: ~$0.01 per search
    - Time: ~5-10 seconds
    - Quality: Returns structured data with sources

    Args:
        topic: What to research
        depth: "quick" (1 source), "comprehensive" (5+ sources)

    Returns:
        JSON with research findings, sources, and confidence scores
    """
    pass

@tool
def generate_content_tool(topic: str, research: str, style: str, length: int) -> str:
    """
    Generate original content using self-critique loop.

    Self-critique process:
    1. Generate initial draft
    2. Critique for clarity, accuracy, engagement
    3. Refine based on feedback
    4. Final quality check

    Constraints:
    - Cost: Depends on model (Ollama free, others $0.02-0.10)
    - Time: 30-120 seconds depending on length
    - Quality: Uses self-critique to ensure 90%+ quality

    Args:
        topic: Blog post topic
        research: Research findings
        style: "professional", "casual", "technical"
        length: Word count target

    Returns:
        Markdown content with quality score
    """
    pass

@tool
def critique_content_tool(content: str, criteria: List[str]) -> Dict[str, Any]:
    """
    Critique content against specific criteria.

    Used by:
    - Self-critique loop in generate_content_tool
    - Quality validation before publishing
    - User feedback integration

    Constraints:
    - Cost: ~$0.01 per critique
    - Time: ~10 seconds
    - Quality: Returns structured feedback

    Args:
        content: Text to critique
        criteria: ["clarity", "accuracy", "engagement", "seo"]

    Returns:
        {
            "overall_score": 0.93,
            "feedback": {
                "clarity": {"score": 0.95, "notes": "..."},
                "accuracy": {"score": 0.91, "notes": "..."},
                ...
            },
            "suggestions": ["Add more examples", "Strengthen conclusion"]
        }
    """
    pass

@tool
def publish_tool(content: str, metadata: Dict, platforms: List[str]) -> Dict[str, str]:
    """
    Publish content to configured platforms (Strapi, Twitter, LinkedIn, etc).

    Dynamic MCP integration:
    - Poindexter discovers available publishing MCP servers
    - Selects appropriate platform based on content type
    - Publishes with platform-specific formatting

    Constraints:
    - Cost: Platform-specific (usually free)
    - Time: 5-30 seconds per platform
    - Quality: Validates content before publishing

    Args:
        content: Published content
        metadata: Title, excerpt, tags, etc.
        platforms: ["strapi", "twitter", "linkedin"]

    Returns:
        {"strapi": "https://...", "twitter": "tweet_id", ...}
    """
    pass

@tool
def discover_mcp_servers_tool(capability: str) -> List[Dict]:
    """
    Discover available MCP servers by capability.

    Poindexter uses this to find on-the-fly tools:
    - "web_search" → Returns available search servers
    - "image_generation" → Returns image gen servers
    - "social_media" → Returns Twitter, LinkedIn, etc. servers
    - "analytics" → Returns metrics & tracking servers

    Args:
        capability: What capability needed ("web_search", "image_gen", etc)

    Returns:
        [
            {
                "name": "serper-api",
                "type": "web_search",
                "cost": 0.05,
                "latency": "2s",
                "quality": 0.98
            },
            ...
        ]
    """
    pass

@tool
def track_metrics_tool(task_id: str, metrics: Dict) -> Dict:
    """
    Track business metrics: costs, quality, performance.

    Poindexter automatically tracks:
    - LLM calls (planning vs execution)
    - Tool usage patterns
    - Quality scores
    - Cost per task
    - Critique loop effectiveness

    Args:
        task_id: Unique task identifier
        metrics: {
            "llm_calls": {"planning": 3, "execution": 5},
            "tools_used": ["research", "generate", "critique"],
            "quality_score": 0.93,
            "total_cost": 2.34,
            "critique_loops": 2
        }

    Returns:
        {"tracked": True, "metrics_id": "metrics_789"}
    """
    pass
```

### 3. MCP Server Discovery Service

**File:** `services/mcp_discovery.py`

```python
class MCPRegistry:
    """
    Discovers, caches, and manages available MCP servers.

    Responsibilities:
    - Query MCP registry/servers
    - Parse capabilities
    - Cache with TTL
    - Expose as Poindexter tools
    - Handle failures gracefully
    """

    async def discover_servers(self, capability: str) -> List[MCPServer]:
        """
        Discover MCP servers that provide specific capability.

        Example flow:
        1. Poindexter: "I need web_search capability"
        2. MCPRegistry: Query registry for web_search servers
        3. Return: [serper-api, google-search, duckduckgo-api]
        4. Poindexter: Pick best (lowest cost, highest quality)
        5. Execute: Use selected server
        """
        pass

    async def get_server_capabilities(self, server_name: str) -> Dict:
        """Get detailed capabilities of specific server"""
        pass

    async def call_mcp_server(self, server_name: str, method: str, params: Dict) -> Any:
        """Execute method on MCP server with error handling"""
        pass
```

### 4. Constraint Reasoning Engine

**File:** `services/constraint_reasoner.py`

```python
class ConstraintReasoner:
    """
    Helps Poindexter reason about constraints.

    Constraints tracked:
    - Budget: Total cost limit for task
    - Quality threshold: Minimum acceptable quality
    - Time limit: Max seconds to complete
    - Model preferences: Ollama-first, then Claude, GPT, Gemini
    - Critique requirements: Must include self-critique loops
    """

    async def can_execute_workflow(
        self,
        workflow: List[str],
        constraints: Dict
    ) -> Tuple[bool, str]:
        """
        Check if proposed workflow meets constraints.

        Example:
        Workflow: [research, generate, critique, refine, publish]
        Constraints: budget=$5, max_time=300s, quality>=0.90

        Returns: (True, "Workflow OK: est. cost $2.34, quality 0.93")
        or
        Returns: (False, "Budget exceeded: est. cost $6.50 > $5.00")
        """
        pass

    async def estimate_cost(self, workflow: List[str]) -> float:
        """Estimate total cost for workflow"""
        pass

    async def estimate_time(self, workflow: List[str]) -> float:
        """Estimate execution time"""
        pass

    async def check_quality_feasibility(self, target_quality: float) -> bool:
        """Can we achieve target quality with available tools?"""
        pass
```

### 5. Self-Critique Loop Manager

**File:** `services/self_critique_manager.py`

```python
class SelfCritiqueManager:
    """
    Manages critique loops within Poindexter workflows.

    Key pattern:
    1. Generate content
    2. Critique automatically
    3. If score < threshold: Refine & retry
    4. Track loop iterations
    5. Stop after max iterations
    """

    async def critique_with_refinement(
        self,
        content: str,
        criteria: List[str],
        target_quality: float,
        max_iterations: int = 3
    ) -> Tuple[str, float, int]:
        """
        Run critique loop until quality target met or max iterations.

        Returns:
            content: Final refined content
            quality_score: Achieved quality
            iterations: Number of refinement loops
        """
        pass
```

---

## 🔄 Workflow Examples

### Example 1: Simple Blog Post Generation

```
Command: "Create a blog post about AI trends"

Poindexter Reasoning:
1. Intent: Generate blog content
2. Tools needed: research, generate, critique, publish
3. MCP servers: web_search for latest trends
4. Workflow:
   a. research_tool(topic="AI trends", depth="comprehensive")
   b. generate_tool(research=findings, style="professional")
   c. critique_tool(content=draft, criteria=[clarity, accuracy])
   d. IF score < 0.90: refine_tool(content=draft, feedback=critique)
   e. publish_tool(content=final)
5. Return: {content_id, url, quality_score, cost}
```

### Example 2: Complex Multi-Channel Campaign

```
Command: "Create blog post about AI, make 10 social media variations, publish everywhere, track performance"

Poindexter Reasoning:
1. Intent: Multi-channel content campaign
2. Tools needed: research, generate, critique, refine, adapt, publish (multiple channels)
3. MCP servers: web_search, image_gen, twitter-api, linkedin-api
4. Constraints: Budget $20, Quality >= 0.85, complete in 10 minutes
5. Workflow:
   a. research_tool("AI trends")
   b. generate_tool(research=findings, style="professional", length=2000)
   c. critique_tool(content=post, criteria=[clarity, accuracy, engagement])
   d. IF score >= 0.85:
      - fetch_images_tool("AI trends", count=5)
      - publish_tool(platforms=["strapi"])
   e. FOR EACH social platform:
      - adapt_content_tool(post, platform="twitter")  # 280 char version
      - adapt_content_tool(post, platform="linkedin")  # Professional version
      - publish_tool(platforms=["twitter", "linkedin"])
   f. track_metrics_tool(campaign_id, metrics={cost, quality, reach})
6. Return: {strapi_url, social_posts, metrics, total_cost}
```

### Example 3: Dynamic Tool Discovery

```
Command: "Analyze social media sentiment about AI, create response blog post if positive"

Poindexter Reasoning:
1. Intent: Conditional multi-step task with discovery
2. Initial tools: research, generate
3. Dynamic discovery:
   - "I need sentiment analysis" → discover_mcp_servers("sentiment_analysis")
   - Returns: [twitter-sentiment, openai-moderation, huggingface-sentiment]
   - Pick best option (lowest cost, highest accuracy)
4. Workflow:
   a. discover_mcp_servers("sentiment_analysis") → Returns Twitter Sentiment MCP
   b. Call twitter_sentiment.analyze("AI sentiment", last_24h=True)
   c. IF sentiment_score > 0.7:
      - research_tool("AI positive news")
      - generate_tool(research=findings, tone="celebratory")
      - critique_tool(content=post)
      - publish_tool(platforms=["strapi", "twitter"])
   d. ELSE: Skip content generation
5. Return: {sentiment_score, decision, content_generated, actions_taken}
```

---

## 🔌 API Contract

### Request

```json
POST /api/v2/orchestrate

{
  "command": "Create a blog post about AI trends with images",
  "constraints": {
    "budget": 5.00,
    "quality_threshold": 0.90,
    "max_runtime": 300,
    "preferred_models": ["ollama", "gpt-4"],
    "critique_required": true
  },
  "context": {
    "user_id": "user123",
    "project_id": "proj456",
    "previous_commands": [
      "Create blog about climate change",
      "Publish 5 social posts"
    ],
    "metadata": {
      "industry": "technology",
      "target_audience": "developers"
    }
  }
}
```

### Response

```json
{
  "status": "success",
  "result": {
    "content_id": "content_abc123",
    "urls": {
      "strapi": "https://blog.example.com/ai-trends-nov2025",
      "twitter": "https://twitter.com/user/status/12345",
      "linkedin": "https://linkedin.com/feed/update/12345"
    },
    "metrics": {
      "quality_score": 0.93,
      "final_word_count": 2147,
      "images_included": 3,
      "social_variations_created": 3
    }
  },
  "workflow_trace": {
    "command_parsed": "Generate multi-channel AI content",
    "tools_discovered": [
      "research",
      "generate",
      "critique",
      "refine",
      "publish"
    ],
    "mcp_servers_discovered": [
      "web-search-serper",
      "image-gen-pexels",
      "twitter-api"
    ],
    "workflow_planned": [
      "research_tool → generate_tool → critique_tool → IF quality<0.90 refine → publish_tool"
    ],
    "steps_executed": [
      {
        "step": 1,
        "tool": "research_tool",
        "input": { "topic": "AI trends", "depth": "comprehensive" },
        "output": "Found 47 recent sources, compiled 15 key trends",
        "time": 8.2,
        "cost": 0.05
      },
      {
        "step": 2,
        "tool": "generate_tool",
        "input": { "research": "...", "style": "professional", "length": 2000 },
        "output": "Generated 2147 word post",
        "time": 45.3,
        "cost": 0.12
      },
      {
        "step": 3,
        "tool": "critique_tool",
        "input": {
          "content": "...",
          "criteria": ["clarity", "accuracy", "engagement"]
        },
        "output": "Quality score: 0.93",
        "time": 5.1,
        "cost": 0.03,
        "critique_feedback": {
          "clarity": 0.95,
          "accuracy": 0.91,
          "engagement": 0.93
        }
      },
      {
        "step": 4,
        "tool": "publish_tool",
        "input": { "platforms": ["strapi", "twitter", "linkedin"] },
        "output": "Published to 3 platforms",
        "time": 3.2,
        "cost": 0.0
      }
    ],
    "critique_loops": 0,
    "total_time": 62.8,
    "total_cost": 2.34,
    "constraints_met": true
  }
}
```

---

## 📊 Performance Tracking

### Metrics Captured

```python
{
    "orchestration_metrics": {
        "llm_calls": {
            "planning": 3,      # ReAct reasoning steps
            "tool_selection": 2, # Tool selection reasoning
            "execution": 12,    # LLM calls within tools
            "total": 17
        },
        "tools_used": ["research", "generate", "critique", "publish"],
        "mcp_servers_used": ["web-search", "twitter-api"],
        "critique_loops": 1,
        "total_cost": 2.34,
        "cost_breakdown": {
            "planning": 0.03,
            "research": 0.05,
            "generation": 0.12,
            "critique": 0.03,
            "publishing": 0.00
        },
        "quality_scores": {
            "clarity": 0.95,
            "accuracy": 0.91,
            "engagement": 0.93,
            "overall": 0.93
        },
        "performance": {
            "total_time": 62.8,
            "time_per_step": {
                "research": 8.2,
                "generate": 45.3,
                "critique": 5.1,
                "publish": 3.2
            }
        }
    }
}
```

---

## 🛡️ Error Recovery

Poindexter handles failures gracefully:

```python
# If tool fails: Try alternative tool or MCP server
if research_tool() fails:
    alternative_sources = discover_mcp_servers("research")
    try alternative with next best server

# If cost exceeds budget: Adjust scope
if estimated_cost > budget:
    reduce_research_depth()
    use_faster_model()
    skip_optional_critique_loop()

# If quality below threshold: Trigger refinement
if quality_score < threshold:
    refine_tool(content, feedback)
    retry critique
    if still below after 3 loops: Escalate or fail gracefully

# If timeout approaching: Save progress & return partial result
if remaining_time < 10s:
    save_partial_workflow()
    return intermediate_result_with_note()
```

---

## 🚀 Implementation Phases

### Phase 1: Core Poindexter Engine (Week 1)

- [ ] Set up smolagents integration
- [ ] Define tool registry
- [ ] Implement ReAct reasoning loop
- [ ] Build constraint checker

### Phase 2: Agent-to-Tool Conversion (Week 2)

- [ ] Wrap existing agents as tools
- [ ] Integrate self-critique loops
- [ ] Test tool chaining

### Phase 3: MCP Discovery (Week 2-3)

- [ ] Build MCP discovery service
- [ ] Integrate with Poindexter
- [ ] Add dynamic tool selection

### Phase 4: Proof of Concept (Week 3)

- [ ] Create /api/v2/orchestrate endpoint
- [ ] E2E test with real commands
- [ ] Performance tracking

### Phase 5: Production Ready (Week 4)

- [ ] Error recovery & fallbacks
- [ ] Comprehensive testing
- [ ] Documentation & deployment

---

## 📋 Success Criteria

- ✅ Poindexter accepts raw text commands
- ✅ Autonomous workflow generation (no manual routing)
- ✅ Self-critique loops improve quality
- ✅ MCP server discovery works on-the-fly
- ✅ Cost tracking accurate & helpful
- ✅ Quality metrics > 0.90
- ✅ Complete < 2 minutes for typical tasks
- ✅ Graceful error recovery
- ✅ Full workflow trace for debugging

---

## 🔗 Related Files

- `src/cofounder_agent/services/poindexter_orchestrator.py` (new)
- `src/cofounder_agent/services/poindexter_tools.py` (new)
- `src/cofounder_agent/services/mcp_discovery.py` (new)
- `src/cofounder_agent/services/constraint_reasoner.py` (new)
- `src/cofounder_agent/services/self_critique_manager.py` (new)
- `src/cofounder_agent/routes/poindexter_routes.py` (new)
- `src/cofounder_agent/tests/test_poindexter_*.py` (new)

---

✅ **Ready for implementation!**
