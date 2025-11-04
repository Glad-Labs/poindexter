# CrewAI Tools Usage Guide for GLAD Labs

**Updated:** November 4, 2025  
**Phase:** Phase 1 - Core Tools Integration  
**Status:** ✅ Ready for Implementation

---

## 🚀 Quick Start

### Installation

```bash
# Install CrewAI with tools support
pip install 'crewai[tools]'

# Or upgrade if already installed
pip install --upgrade crewai crewai-tools
```

### Basic Usage

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# Get tools for your agent
tools = CrewAIToolsFactory.get_content_agent_tools()

# Use in a CrewAI agent
from crewai import Agent

researcher = Agent(
    role="Content Researcher",
    goal="Research topics thoroughly",
    tools=tools,
    verbose=True
)
```

---

## 📚 Available Tools

### 1. WebSearchTool - Real-time Web Search

**What it does:** Searches the web for real-time information via SerperDev API

**Requires:** `SERPER_API_KEY` environment variable

**Use Cases:**

- Market trends and news
- Competitor research
- Current events research
- Industry updates

**Example:**

```python
from src.agents.content_agent.utils.tools import WebSearchTool

tool = WebSearchTool()

# Used automatically by agents:
# Agent searches: "Latest AI trends 2025"
# Returns: Recent articles, news, trends
```

**Get for agent:**

```python
tool = CrewAIToolsFactory.get_web_search_tool()
```

---

### 2. CompetitorContentSearchTool - Website Content Analysis

**What it does:** Searches and analyzes content within websites using RAG (Retrieval-Augmented Generation)

**Requires:** None (uses local processing)

**Use Cases:**

- Analyze competitor websites
- Extract content from specific sites
- Find information within large websites
- Content benchmarking

**Example:**

```python
from src.agents.content_agent.utils.tools import CompetitorContentSearchTool

tool = CompetitorContentSearchTool()

# Used by agents:
# Agent searches: "Find pricing information on competitor website"
# Returns: Relevant pricing data extracted from website
```

**Get for agent:**

```python
tool = CrewAIToolsFactory.get_competitor_search_tool()
```

---

### 3. DocumentAccessTool - File Reading

**What it does:** Reads and extracts content from various file formats

**Supported Formats:** .txt, .md, .json, .csv, .pdf, .docx, .xlsx, and more

**Requires:** None (local files)

**Use Cases:**

- Read research documents
- Access configuration files
- Extract data from structured files
- Read documentation

**Example:**

```python
from src.agents.content_agent.utils.tools import DocumentAccessTool

tool = DocumentAccessTool()

# Read a file
content = tool.read_research_file("./research/market_analysis.md")
# Returns: File content or None if error

# Or use directly in agents:
# Agent reads: "./research/competitor_analysis.pdf"
# Returns: Extracted text from PDF
```

**Get for agent:**

```python
tool = CrewAIToolsFactory.get_document_tool()
```

---

### 4. DirectoryAccessTool - Directory Navigation

**What it does:** Navigates and analyzes directory structures to find files

**Requires:** None (local directories)

**Use Cases:**

- Find related files in directories
- Explore project structures
- Discover documentation
- Map file hierarchies

**Example:**

```python
from src.agents.content_agent.utils.tools import DirectoryAccessTool

# Default (current directory)
tool = DirectoryAccessTool()

# Specific directory
tool = DirectoryAccessTool(directory="./research_docs")

# Used in agents:
# Agent explores: "./research_docs"
# Returns: File listing and structure
```

**Get for agent:**

```python
tool = CrewAIToolsFactory.get_directory_tool("./research_docs")
```

---

### 5. DataProcessingTool - Python Code Execution

**What it does:** Executes Python code for data processing, calculations, and transformations

**Requires:** None (local Python execution)

**Use Cases:**

- Data transformation
- Statistical calculations
- Data cleaning
- Complex analysis
- Format conversion

**Example:**

```python
from src.agents.content_agent.utils.tools import DataProcessingTool

tool = DataProcessingTool()

# Execute Python code
code = """
import json
data = [{'name': 'A', 'value': 10}, {'name': 'B', 'value': 20}]
average = sum(d['value'] for d in data) / len(data)
average
"""
result = tool.process_data(code)
# Returns: 15.0
```

**Get for agent:**

```python
tool = CrewAIToolsFactory.get_data_processing_tool()
```

---

## 🏭 CrewAIToolsFactory - Tool Management

The factory provides easy access to tools with singleton pattern (cached instances).

### Getting Individual Tools

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# Each returns the same instance when called multiple times
web_search = CrewAIToolsFactory.get_web_search_tool()
competitor_search = CrewAIToolsFactory.get_competitor_search_tool()
document_tool = CrewAIToolsFactory.get_document_tool()
directory_tool = CrewAIToolsFactory.get_directory_tool()
data_processing = CrewAIToolsFactory.get_data_processing_tool()
```

### Getting Tool Collections by Agent Type

```python
# Content Agent - researches and creates content
content_tools = CrewAIToolsFactory.get_content_agent_tools()
# Returns: [WebSearchTool, CompetitorSearchTool, DocumentTool, DataProcessingTool]

# Research Agent - gathers and analyzes information
research_tools = CrewAIToolsFactory.get_research_agent_tools()
# Returns: [WebSearchTool, DocumentTool, DirectoryTool, DataProcessingTool]

# Market Agent - analyzes market data
market_tools = CrewAIToolsFactory.get_market_agent_tools()
# Returns: [WebSearchTool, CompetitorSearchTool, DataProcessingTool]
```

### Resetting Instances (Testing)

```python
# Clear all cached tool instances
CrewAIToolsFactory.reset_instances()

# Useful for testing to ensure fresh instances
```

---

## 🤖 Integrating with Agents

### Example 1: Content Agent with Tools

```python
from crewai import Agent
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# Create agent with tools
content_agent = Agent(
    role="Content Researcher and Writer",
    goal="Create well-researched, engaging content",
    backstory="An expert researcher and writer with access to web and file tools",
    tools=CrewAIToolsFactory.get_content_agent_tools(),
    verbose=True
)

# The agent now automatically uses tools when needed:
# - Web search for trends and information
# - Competitor site analysis for benchmarking
# - Document reading for research
# - Data processing for analysis
```

### Example 2: Market Analysis with Tools

```python
from crewai import Agent, Task, Crew
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

market_agent = Agent(
    role="Market Analyst",
    goal="Provide comprehensive market analysis",
    tools=CrewAIToolsFactory.get_market_agent_tools(),
    verbose=True
)

task = Task(
    description="Analyze the current AI market: size, trends, competitors, opportunities",
    agent=market_agent,
    expected_output="Market analysis report with statistics and insights"
)

crew = Crew(agents=[market_agent], tasks=[task], verbose=True)
result = crew.kickoff()
```

### Example 3: Custom Tool Selection

```python
from crewai import Agent
from src.agents.content_agent.utils.tools import (
    WebSearchTool,
    DocumentAccessTool,
    DataProcessingTool
)

# Create custom tool selection
custom_tools = [
    WebSearchTool(),              # Web search
    DocumentAccessTool(),         # File reading
    DataProcessingTool(),         # Data processing
    # Note: Not including CompetitorSearch or DirectoryAccess
]

analyst = Agent(
    role="Data Analyst",
    goal="Analyze data from various sources",
    tools=custom_tools,
    verbose=True
)
```

---

## 🔑 Environment Variables Setup

### Required for Web Search

```bash
# Create .env file
SERPER_API_KEY=your_serper_dev_api_key_here

# Or set in Python
import os
os.environ["SERPER_API_KEY"] = "your_key_here"
```

### Optional for Future Phases

```bash
# Phase 2+ - Image generation
OPENAI_API_KEY=your_openai_key

# Phase 2+ - GitHub search
GITHUB_TOKEN=your_github_token

# Phase 2+ - YouTube search
YOUTUBE_API_KEY=your_youtube_api_key
```

---

## 🧪 Testing Tools

### Run Tool Tests

```bash
# Run all tool integration tests
pytest tests/test_crewai_tools_integration.py -v

# Run specific test class
pytest tests/test_crewai_tools_integration.py::TestCrewAIToolsFactory -v

# Run with markers
pytest tests/test_crewai_tools_integration.py -m "unit" -v       # Unit tests
pytest tests/test_crewai_tools_integration.py -m "integration" -v # Integration tests
pytest tests/test_crewai_tools_integration.py -m "smoke" -v       # Quick checks
```

### Run Factory Tests

```bash
pytest tests/test_crewai_tools_integration.py::TestCrewAIToolsFactory -v
```

---

## 📊 Common Patterns

### Pattern 1: Tool Availability Check

```python
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

try:
    tools = CrewAIToolsFactory.get_content_agent_tools()
    print(f"✅ Loaded {len(tools)} tools for content agent")
except Exception as e:
    print(f"⚠️ Error loading tools: {e}")
    # Fallback to basic agent without tools
```

### Pattern 2: Tool Error Handling

```python
from src.agents.content_agent.utils.tools import DocumentAccessTool

tool = DocumentAccessTool()

content = tool.read_research_file("research/market.md")
if content:
    print(f"✅ Read {len(content)} characters from file")
else:
    print("❌ Failed to read file")
```

### Pattern 3: Tool Chain (Sequential)

```python
from crewai import Task
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

# Tools will be used sequentially by the agent:
# 1. Search web for information
# 2. Read local documents
# 3. Process data
# 4. Return results

task = Task(
    description="Find market trends, compare with local data, and provide analysis",
    agent=my_agent,
    tools=CrewAIToolsFactory.get_research_agent_tools()
)
```

---

## 📈 Performance Tips

### 1. Use Factory Singleton

**Good:** Uses cached instances

```python
tool1 = CrewAIToolsFactory.get_web_search_tool()
tool2 = CrewAIToolsFactory.get_web_search_tool()
# tool1 is tool2 → True (same instance)
```

**Avoid:** Creating new instances repeatedly

```python
tool1 = WebSearchTool()
tool2 = WebSearchTool()
# tool1 is tool2 → False (different instances, wasteful)
```

### 2. Tool Collections for Agents

**Good:** Load tools once per agent

```python
tools = CrewAIToolsFactory.get_content_agent_tools()
agent1 = Agent(role="Writer", tools=tools)
```

**Avoid:** Creating tools for each task

```python
agent1 = Agent(role="Writer", tools=CrewAIToolsFactory.get_content_agent_tools())
agent2 = Agent(role="Editor", tools=CrewAIToolsFactory.get_content_agent_tools())
# Worse: Creates new instances each time
```

### 3. Error Handling

**Good:** Graceful degradation

```python
try:
    content = tool.read_research_file(path)
except Exception as e:
    logger.warning(f"Tool failed: {e}")
    content = None  # Continue with fallback
```

---

## 🚀 Next Steps

### Phase 1 (Current)

- ✅ WebSearchTool (SerperDev)
- ✅ CompetitorContentSearchTool (WebsiteSearch)
- ✅ DocumentAccessTool (FileRead)
- ✅ DirectoryAccessTool (DirectoryRead)
- ✅ DataProcessingTool (CodeInterpreter)

### Phase 2 (Upcoming)

- 📦 DALL-E Tool (image generation)
- 📦 PDFSearchTool (PDF analysis)
- 📦 CSVSearchTool (data queries)
- 📦 GithubSearchTool (code examples)
- 📦 YoutubeVideoSearchTool (video research)

### Phase 3 (Future)

- 📦 FirecrawlScrapeWebsiteTool (advanced scraping)
- 📦 PGSearchTool (database queries)
- 📦 ComposioTool (SaaS integrations)
- 📦 Advanced RAG tools

---

## 🔗 Resources

- **CrewAI Documentation:** https://docs.crewai.com/
- **CrewAI Tools GitHub:** https://github.com/joaomdmoura/crewai-tools
- **SerperDev API:** https://serper.dev/
- **Firecrawl (Phase 2):** https://www.firecrawl.dev/

---

## ❓ Troubleshooting

### Issue: "crewai_tools not installed"

```bash
# Solution
pip install crewai-tools
```

### Issue: "SERPER_API_KEY not set"

```bash
# Solution - get free API key from https://serper.dev/
export SERPER_API_KEY=your_key_here

# Or in Python
import os
os.environ["SERPER_API_KEY"] = "your_key_here"
```

### Issue: "Tool returns None"

```python
# Check if error occurred
result = tool.run(something)
if result is None:
    # Tool failed, check logs
    logger.error("Tool operation failed")
```

### Issue: "Factory returns different instances"

```python
# Don't reset unless testing
CrewAIToolsFactory.reset_instances()  # This clears cache

# Solution: Only reset in tests or debugging
# In production, keep cache active for performance
```

---

## 📞 Support

For tool-specific issues:

1. Check logs for error messages
2. Verify environment variables are set
3. Test tools independently first
4. Check CrewAI documentation
5. Review test cases for usage examples

---

**Ready to use Phase 1 tools? Start with `CrewAIToolsFactory.get_content_agent_tools()` in your agents!** 🚀
