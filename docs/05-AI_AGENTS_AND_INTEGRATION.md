# 05 - AI Agents & Integration

**Role**: AI Engineers, Backend Developers  
**Reading Time**: 18-22 minutes  
**Last Updated**: October 18, 2025

---

## 🚀 Quick Navigation

- **[← Back to Docs](./00-README.md)** | **[↑ Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** | **[↑ Development](./04-DEVELOPMENT_WORKFLOW.md)** | **Next: [Operations](./06-OPERATIONS_AND_MAINTENANCE.md) →**

---

## Overview

GLAD Labs features a sophisticated multi-agent system powered by AI. This document covers the agent architecture, integration points, deployment, and customization. Agents handle specialized business functions including compliance, content, financial analysis, and market insights.

---

## 📋 Table of Contents

1. [Agent Architecture](#agent-architecture)
2. [Available Agents](#available-agents)
3. [Co-founder Agent](#co-founder-agent)
4. [Agent Integration](#agent-integration)
5. [MCP (Model Context Protocol)](#mcp-model-context-protocol)
6. [Testing & Debugging](#testing--debugging)
7. [Deployment](#deployment)

---

## Agent Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    GLAD LABS AI SYSTEM                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  User Interface Layer                                        │
│  ├─ Dashboard (React)                                       │
│  └─ API Endpoints (REST/GraphQL)                            │
│                                                               │
│  Orchestration Layer                                        │
│  ├─ Multi-Agent Orchestrator                               │
│  ├─ Agent Router                                            │
│  └─ Context Manager                                         │
│                                                               │
│  Agent Layer                                                │
│  ├─ Co-founder Agent (Main intelligence)                   │
│  ├─ Compliance Agent (Regulatory checks)                   │
│  ├─ Content Agent (Content generation)                     │
│  ├─ Financial Agent (Analysis & forecasting)               │
│  └─ Market Insight Agent (Market analysis)                 │
│                                                               │
│  Integration Layer                                          │
│  ├─ MCP Servers (Tool access)                              │
│  ├─ External APIs                                          │
│  └─ Data Sources                                           │
│                                                               │
│  Storage & Memory                                          │
│  ├─ Memory System (Short-term & long-term)                │
│  ├─ PostgreSQL Database                                    │
│  └─ Vector Store (for embeddings)                          │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Agent Communication Flow

```
1. User Request
   ↓
2. API Endpoint receives request
   ↓
3. Orchestrator analyzes request type
   ↓
4. Routes to appropriate agent(s)
   ↓
5. Agent accesses tools via MCP
   ↓
6. Agent processes with memory context
   ↓
7. Response formatted and returned
   ↓
8. User sees result
```

---

## Available Agents

### Agent Specifications

| Agent                    | Purpose                | Input                    | Output                     | Skills                       |
| ------------------------ | ---------------------- | ------------------------ | -------------------------- | ---------------------------- |
| **Co-founder Agent**     | Main AI decision maker | Business questions, data | Actionable recommendations | Strategy, analysis, planning |
| **Compliance Agent**     | Regulatory adherence   | Business rules, updates  | Compliance status, alerts  | Legal, regulatory, audit     |
| **Content Agent**        | Content generation     | Topic, style, format     | Articles, posts, copy      | Writing, SEO, formatting     |
| **Financial Agent**      | Financial analysis     | Data, forecasts, reports | Projections, advice        | Accounting, forecasting      |
| **Market Insight Agent** | Market analysis        | Market data, trends      | Insights, opportunities    | Analysis, research           |

### Quick Reference

```python
# Using agents programmatically
from cofounder_agent import agents

# Get co-founder recommendation
response = agents.cofounder.ask("Should we enter the European market?")

# Check compliance
status = agents.compliance.check_compliance(business_rules)

# Generate content
article = agents.content.generate_article(
    topic="AI in business",
    style="professional",
    length="medium"
)

# Analyze market
analysis = agents.market.analyze_market(region="US", sector="tech")
```

---

## Co-founder Agent

### Purpose & Capabilities

The Co-founder Agent serves as your AI business partner, providing:

- **Strategic Planning**: Long-term business strategy and roadmaps
- **Decision Support**: Analysis and recommendations on key decisions
- **Performance Analysis**: KPI tracking and improvement suggestions
- **Market Intelligence**: Competitive analysis and opportunities
- **Operational Optimization**: Efficiency improvements and automation

### Starting the Agent

```bash
# Terminal 1: Start the co-founder agent server
cd src/cofounder_agent
python -m uvicorn cofounder_agent.main:app --reload

# Access at: http://localhost:8000
# API docs: http://localhost:8000/docs
# WebSocket: ws://localhost:8000/ws
```

### Using the Agent API

#### REST API

```bash
# Ask a question
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is our market positioning?",
    "context": "We are a B2B SaaS company in the HR tech space"
  }'

# Get response
{
  "id": "req-123",
  "answer": "Based on current market analysis...",
  "confidence": 0.92,
  "sources": ["market-data", "financial-records"],
  "next_steps": ["increase marketing", "expand features"]
}
```

#### WebSocket (Real-time)

```javascript
// JavaScript client
const ws = new WebSocket('ws://localhost:8000/ws');

ws.onopen = () => {
  ws.send(
    JSON.stringify({
      type: 'ask',
      question: 'Analyze our customer churn',
      stream: true, // Stream response
    })
  );
};

ws.onmessage = (event) => {
  const response = JSON.parse(event.data);
  console.log(response.chunk); // Streaming response
};
```

#### Python Client

```python
import requests

# Ask question
response = requests.post(
    'http://localhost:8000/api/ask',
    json={
        'question': 'What are our top growth opportunities?',
        'context': 'Enterprise software company'
    }
)

result = response.json()
print(f"Answer: {result['answer']}")
print(f"Confidence: {result['confidence']}")
```

### Agent Memory System

The co-founder maintains persistent memory:

```python
# Memory is automatically maintained
# Short-term: Current session context
# Long-term: Historical decisions and learnings

# Access memory
from cofounder_agent import memory

# Add memory
memory.add("We decided to focus on US market in Q2")

# Query memory
relevant = memory.search("market decisions")

# Clear memory (if needed)
memory.clear()
```

### Advanced Configuration

```python
# In cofounder_agent/config.py
AGENT_CONFIG = {
    # Model configuration
    'model': 'gpt-4',  # or 'gpt-3.5-turbo'
    'temperature': 0.7,  # Creativity (0=deterministic, 1=random)
    'max_tokens': 2000,

    # Memory configuration
    'memory_type': 'persistent',  # or 'session'
    'memory_limit': 10000,  # Number of context items

    # Tool configuration
    'enable_mcp': True,  # Enable tool access
    'enable_web_search': True,
    'enable_data_analysis': True,

    # Behavior
    'response_style': 'professional',  # or 'casual', 'technical'
    'include_sources': True,
    'include_confidence': True,
}
```

---

## Agent Integration

### Using Agents in Your Application

#### React Component Example

```javascript
// src/components/AgentChat.tsx
import { useState } from 'react';
import { useAgent } from '@/hooks/useAgent';

export default function AgentChat() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const { ask, isLoading } = useAgent();

  const handleAsk = async () => {
    const response = await ask(input);
    setMessages([
      ...messages,
      {
        role: 'user',
        content: input,
      },
      {
        role: 'assistant',
        content: response.answer,
        confidence: response.confidence,
      },
    ]);
    setInput('');
  };

  return (
    <div>
      <div>
        {messages.map((msg, i) => (
          <div key={i} className="message">
            <strong>{msg.role}:</strong> {msg.content}
            {msg.confidence && (
              <small>Confidence: {msg.confidence.toFixed(2)}</small>
            )}
          </div>
        ))}
      </div>
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="Ask the co-founder..."
      />
      <button onClick={handleAsk} disabled={isLoading}>
        {isLoading ? 'Thinking...' : 'Ask'}
      </button>
    </div>
  );
}
```

#### Strapi Integration

```javascript
// cms/strapi-main/src/api/agent/routes/agent.js
module.exports = {
  routes: [
    {
      method: 'POST',
      path: '/agent/ask',
      handler: 'agent.ask',
      config: { auth: false },
    },
  ],
};

// controllers/agent.js
module.exports = {
  async ask(ctx) {
    const { question } = ctx.request.body;

    // Call co-founder agent
    const response = await fetch('http://localhost:8000/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });

    ctx.body = await response.json();
  },
};
```

### Event-Driven Agent Actions

```python
# Listen for business events and trigger agents
from cofounder_agent.events import EventBus

event_bus = EventBus()

@event_bus.on('sales_spike')
async def on_sales_spike(data):
    """Triggered when sales spike detected"""
    analysis = await agents.cofounder.analyze_spike(data)
    return analysis

@event_bus.on('customer_churn')
async def on_customer_churn(data):
    """Triggered when customer churn detected"""
    action = await agents.compliance.check_churn_concerns(data)
    return action

# Emit events
event_bus.emit('sales_spike', { 'revenue': 50000 })
```

---

## MCP (Model Context Protocol)

### What is MCP?

MCP enables agents to access tools and data sources securely. It's like giving AI access to your business tools.

### Available MCP Servers

```
┌─ MCP Server 1: Data Access
│  ├─ Query databases
│  ├─ Fetch reports
│  └─ Get real-time data
│
├─ MCP Server 2: Content Management
│  ├─ Create posts
│  ├─ Edit articles
│  └─ Publish content
│
├─ MCP Server 3: External APIs
│  ├─ Call webhooks
│  ├─ Integrate services
│  └─ Fetch external data
│
└─ MCP Server 4: Business Tools
   ├─ Accounting software
   ├─ CRM systems
   └─ Analytics platforms
```

### Using MCP Tools

```python
# In your agent code
from mcp_integration import mcp_tools

# Get available tools
tools = mcp_tools.list_tools()
# Returns: ['query_database', 'fetch_report', 'send_email', ...]

# Use a tool
result = await mcp_tools.call('query_database', {
    'query': 'SELECT * FROM customers WHERE status = active',
    'limit': 100
})

# Check result
if result.success:
    data = result.data
else:
    error = result.error
```

### Creating Custom MCP Servers

```python
# src/mcp/servers/custom_server.py
from mcp_base_server import BaseMCPServer

class CustomToolServer(BaseMCPServer):
    async def initialize(self):
        """Register your tools"""
        self.register_tool(
            name='analyze_customer',
            handler=self.analyze_customer,
            description='Analyze customer data'
        )

    async def analyze_customer(self, customer_id: str):
        """Implementation"""
        # Your logic here
        return {
            'status': 'active',
            'lifetime_value': 5000,
            'recommendation': 'upsell opportunity'
        }

# Register
server = CustomToolServer()
server.start()
```

---

## Testing & Debugging

### Test Agent Responses

```python
# tests/test_cofounder_agent.py
import pytest
from cofounder_agent import agents

@pytest.mark.asyncio
async def test_market_analysis():
    """Test market analysis capability"""
    result = await agents.cofounder.analyze_market(
        region="US",
        sector="SaaS"
    )

    assert result.success
    assert 'opportunities' in result.data
    assert result.confidence > 0.8

@pytest.mark.asyncio
async def test_strategic_planning():
    """Test strategic recommendations"""
    result = await agents.cofounder.plan_strategy(
        business_type="B2B SaaS",
        stage="Series A"
    )

    assert result.success
    assert 'roadmap' in result.data
    assert len(result.data['roadmap']) > 0

# Run tests
pytest tests/test_cofounder_agent.py -v
```

### Debug Agent Thinking

```python
# Enable debug logging
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('cofounder_agent')

# Now all agent operations are logged:
# - Tool calls
# - Reasoning steps
# - Memory access
# - Response generation

# Run your agent call
result = agents.cofounder.ask("What should we do?")

# Check logs for detailed trace
```

### Monitor Agent Performance

```python
# Get agent statistics
from cofounder_agent import monitoring

stats = monitoring.get_stats()
print(f"Average response time: {stats.avg_response_time}ms")
print(f"Success rate: {stats.success_rate}%")
print(f"Cache hit rate: {stats.cache_hit_rate}%")
print(f"Cost per request: ${stats.avg_cost}")

# Get recent requests
history = monitoring.get_request_history(limit=10)
for req in history:
    print(f"Q: {req.question}")
    print(f"  → Time: {req.duration}ms, Cost: ${req.cost}")
```

---

## Deployment

### Local Development

```bash
# 1. Start the agent server
cd src/cofounder_agent
python -m uvicorn cofounder_agent.main:app --reload --port 8000

# 2. Verify it's running
curl http://localhost:8000/health
# Returns: {"status": "healthy", "agents": 5}

# 3. Open API documentation
# Visit: http://localhost:8000/docs
```

### Production Deployment

#### Option 1: Railway

```bash
# 1. Add service to Railway
railway add

# 2. Set environment variables
# DATABASE_URL, OPENAI_API_KEY, etc.

# 3. Deploy
git push origin main

# 4. Monitor
railway logs -f
```

#### Option 2: Docker

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/cofounder_agent .

CMD ["uvicorn", "cofounder_agent.main:app", "--host", "0.0.0.0"]
```

```bash
# Build and run
docker build -t glad-cofounder .
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=sk-xxx \
  -e DATABASE_URL=postgresql://... \
  glad-cofounder
```

### Environment Variables

```env
# Required
OPENAI_API_KEY=sk-xxx (get from openai.com)
DATABASE_URL=postgresql://user:pass@host/db

# Optional
AGENT_MODEL=gpt-4 (or gpt-3.5-turbo)
LOG_LEVEL=DEBUG (or INFO, WARN, ERROR)
CACHE_ENABLED=true
ENABLE_WEB_SEARCH=true
MEMORY_SIZE=10000
```

### Monitoring in Production

```python
# Health check endpoint
GET /health
Response: {
  "status": "healthy",
  "uptime_seconds": 3600,
  "agents_active": 5,
  "memory_usage_mb": 256,
  "requests_processed": 1234,
  "average_response_time_ms": 245
}

# Error tracking
GET /metrics
Response: {
  "errors_last_hour": 2,
  "error_rate": 0.15,
  "average_cost_per_request": 0.08,
  "cache_hit_rate": 72.5
}
```

---

## Best Practices

### ✅ Do

- [ ] Test agents before production
- [ ] Monitor cost per request
- [ ] Cache frequently asked questions
- [ ] Log all agent decisions
- [ ] Use MCP for data access
- [ ] Validate agent outputs
- [ ] Set confidence thresholds
- [ ] Track agent performance metrics

### ❌ Don't

- [ ] Expose API keys in code
- [ ] Call agents synchronously for large operations
- [ ] Trust agent outputs without validation
- [ ] Leave debug logging in production
- [ ] Ignore agent error rates
- [ ] Overload agents with requests
- [ ] Store sensitive data in memory

---

## Troubleshooting

### Agent Not Responding

```bash
# 1. Check if server is running
curl http://localhost:8000/health

# 2. Check logs
tail -f logs/cofounder_agent.log

# 3. Verify API key
echo $OPENAI_API_KEY

# 4. Restart
pkill -f uvicorn
python -m uvicorn cofounder_agent.main:app --reload
```

### High Response Latency

```
✅ Enable caching
✅ Optimize prompts (shorter = faster)
✅ Use gpt-3.5-turbo instead of gpt-4
✅ Reduce context size
✅ Check network connection
```

### High Costs

```
✅ Enable response caching
✅ Use cheaper model (gpt-3.5-turbo)
✅ Limit token usage with max_tokens
✅ Cache embeddings
✅ Monitor per-request costs
```

---

## Next Steps

1. **[← Back to Documentation](./00-README.md)**
2. **Read**: [06 - Operations & Maintenance](./06-OPERATIONS_AND_MAINTENANCE.md)
3. **Try**: Start the co-founder agent and test the API
4. **Integrate**: Add an agent call to your frontend

---

**Last Updated**: October 18, 2025 | **Version**: 1.0
