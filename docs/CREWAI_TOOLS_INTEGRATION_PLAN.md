# CrewAI Tools Integration Plan for Glad Labs

**Date:** November 4, 2025  
**Status:** Planning Phase  
**Goal:** Integrate CrewAI's 30+ pre-built tools into Glad Labs agent ecosystem

---

## 🎯 Executive Summary

CrewAI provides **30+ pre-built, battle-tested tools** that can dramatically accelerate Glad Labs development:

- ✅ **Web Tools:** SerperDev (search), Firecrawl (scraping), WebsiteSearch (RAG)
- ✅ **File Tools:** DirectoryRead, FileRead, CSVSearch, PDFSearch, DOCXSearch, JSONSearch
- ✅ **Content Tools:** CodeDocs, GithubSearch, YoutubeSearch
- ✅ **Code Tools:** CodeInterpreter (Python execution), GithubSearch
- ✅ **Image Tools:** DALL-E integration
- ✅ **Database Tools:** PGSearch (PostgreSQL), RagTool (general)
- ✅ **Data Sources:** XML, TXT, MDX files
- ✅ **External APIs:** Apify, Composio, LlamaIndex
- ✅ **Web Scraping:** Firecrawl (intelligent), Browserbase, ScrapeWebsite

**Current Glad Labs Status:**

- ✅ Already using: `SerperDevTool` (web search)
- ⏳ Opportunity: Add 29+ more tools for rich agent capabilities
- 🎯 Focus: Content generation, market research, code analysis

---

## 📊 Available CrewAI Tools Reference

### Search & Research Tools

| Tool                         | Purpose                           | Best For                            | API Required    |
| ---------------------------- | --------------------------------- | ----------------------------------- | --------------- |
| **SerperDevTool**            | Web search with real-time results | Market trends, competitor research  | SERPER_API_KEY  |
| **WebsiteSearchTool**        | RAG search within websites        | Content analysis, competitor sites  | Optional        |
| **EXASearchTool**            | Exhaustive multi-source search    | Comprehensive research              | EXA_API_KEY     |
| **GithubSearchTool**         | Search GitHub repos & code        | Open-source research, code examples | GITHUB_TOKEN    |
| **YoutubeChannelSearchTool** | Search YouTube channels           | Video content analysis              | YOUTUBE_API_KEY |
| **YoutubeVideoSearchTool**   | Search within videos              | Video data extraction               | YOUTUBE_API_KEY |
| **CodeDocsSearchTool**       | Search code documentation         | Technical reference lookup          | Optional        |

### Web Scraping & Crawling Tools

| Tool                             | Purpose                        | Best For                         | API Required      |
| -------------------------------- | ------------------------------ | -------------------------------- | ----------------- |
| **FirecrawlScrapeWebsiteTool**   | Scrape single webpages         | Content extraction               | FIRECRAWL_API_KEY |
| **FirecrawlCrawlWebsiteTool**    | Crawl entire websites          | Sitemap mapping, bulk extraction | FIRECRAWL_API_KEY |
| **FirecrawlSearchTool**          | Search within crawled content  | Targeted page finding            | FIRECRAWL_API_KEY |
| **BrowserbaseLoadTool**          | Interactive browser automation | JavaScript-heavy sites           | BROWSERBASE_TOKEN |
| **ScrapeWebsiteTool**            | Basic website scraping         | Simple data extraction           | None              |
| **ScrapeElementFromWebsiteTool** | Scrape specific elements       | CSS selector targeting           | None              |
| **ApifyActorsTool**              | Web scraping & automation      | Complex scraping workflows       | APIFY_API_KEY     |

### File & Document Tools

| Tool                    | Purpose                   | Best For                    | Notes                |
| ----------------------- | ------------------------- | --------------------------- | -------------------- |
| **FileReadTool**        | Read any file format      | General file access         | Supports all formats |
| **DirectoryReadTool**   | Read directory structures | Traverse folder hierarchies | Local/remote         |
| **CSVSearchTool**       | RAG search CSV data       | Structured data queries     | Ideal for tables     |
| **PDFSearchTool**       | Search PDF documents      | Document analysis           | OCR-capable          |
| **DOCXSearchTool**      | Search Word documents     | Rich text analysis          | Formatting preserved |
| **JSONSearchTool**      | Search JSON files         | Configuration analysis      | Nested support       |
| **XMLSearchTool**       | Search XML files          | Structured data queries     | Schema-aware         |
| **TXTSearchTool**       | Search text files         | Unstructured data           | Full-text search     |
| **MDXSearchTool**       | Search Markdown/MDX       | Documentation search        | Code blocks parsed   |
| **DirectorySearchTool** | RAG directory search      | Hierarchical queries        | File path search     |

### Code & Technical Tools

| Tool                    | Purpose                | Best For                       | Dependencies |
| ----------------------- | ---------------------- | ------------------------------ | ------------ |
| **CodeInterpreterTool** | Execute Python code    | Script execution, calculations | Python 3.9+  |
| **GithubSearchTool**    | Search GitHub repos    | Open-source code discovery     | GITHUB_TOKEN |
| **CodeDocsSearchTool**  | RAG code documentation | API reference lookup           | Optional     |
| **LlamaIndexTool**      | Advanced RAG framework | Complex document indexing      | llama-index  |

### Image & Media Tools

| Tool            | Purpose          | Best For                        | API Required   |
| --------------- | ---------------- | ------------------------------- | -------------- |
| **DALL-E Tool** | Image generation | Content creation, illustrations | OPENAI_API_KEY |
| **Vision Tool** | Image analysis   | Image understanding             | OPENAI_API_KEY |

### Database Tools

| Tool             | Purpose               | Best For             | Connection     |
| ---------------- | --------------------- | -------------------- | -------------- |
| **PGSearchTool** | PostgreSQL RAG search | Database queries     | PostgreSQL URL |
| **RagTool**      | General RAG framework | Multi-source queries | Configurable   |

### Integration Tools

| Tool             | Purpose               | Best For                      | Notes        |
| ---------------- | --------------------- | ----------------------------- | ------------ |
| **ComposioTool** | 50+ SaaS integrations | Slack, Jira, Salesforce, etc. | Composio API |

---

## 🎯 Integration Priority for Glad Labs

### Phase 1: HIGH PRIORITY (Implement First)

**Why:** Direct impact on core content generation pipeline

```python
# Already have:
from crewai_tools import SerperDevTool  ✅

# Add immediately:
from crewai_tools import (
    WebsiteSearchTool,           # Research competitor sites
    FirecrawlScrapeWebsiteTool,  # Extract content from URLs
    FileReadTool,                # Read research documents
    DirectoryReadTool,           # Access content directories
    CodeInterpreterTool,         # Execute data processing
)
```

**Use Cases:**

- Content Agent: Research + scrape competitor content
- Market Agent: Search web for trends + scrape market data
- Financial Agent: Extract pricing data from websites
- Memory System: Search local documents for context

### Phase 2: MEDIUM PRIORITY (Implement Week 2)

```python
from crewai_tools import (
    PDFSearchTool,               # Analyze research PDFs
    CSVSearchTool,               # Query data files
    GithubSearchTool,            # Find code examples
    YoutubeVideoSearchTool,      # Find relevant videos
    DALL_E_Tool,                 # Generate images
    CodeDocsSearchTool,          # API reference
)
```

**Use Cases:**

- Content Agent: Generate images for blog posts
- Research: Find technical documentation
- Market Agent: Video content analysis

### Phase 3: NICE-TO-HAVE (Implement Later)

```python
from crewai_tools import (
    BrowserbaseLoadTool,         # JavaScript-heavy sites
    ApifyActorsTool,             # Complex scraping
    YoutubeChannelSearchTool,    # Channel-level search
    EXASearchTool,               # Exhaustive search
    ComposioTool,                # SaaS integrations
    XMLSearchTool,               # Data parsing
    LlamaIndexTool,              # Advanced indexing
)
```

---

## 📦 Installation

```bash
# Install CrewAI with all tools
pip install 'crewai[tools]'

# Or individual tools
pip install crewai-tools

# For Firecrawl (optional, high-value)
pip install firecrawl-py

# For specific integrations
pip install browserbase          # For BrowserbaseLoadTool
pip install apify-client         # For ApifyActorsTool
pip install composio-core        # For ComposioTool
```

---

## 🔧 Integration Examples

### Example 1: Enhance Content Agent with Research Tools

**Current Implementation:**

```python
# src/agents/content_agent/content_agent.py
class ContentAgent(BaseAgent):
    def __init__(self):
        self.llm = LLMClient()
        # No tools currently
```

**Enhanced Implementation:**

```python
from crewai_tools import (
    SerperDevTool,
    WebsiteSearchTool,
    FileReadTool,
    CodeInterpreterTool,
)

class ContentAgent(BaseAgent):
    def __init__(self):
        self.llm = LLMClient()
        self.tools = [
            SerperDevTool(),              # Search web
            WebsiteSearchTool(),          # RAG search sites
            FileReadTool(),               # Read research docs
            CodeInterpreterTool(),        # Process data
        ]

    async def research_topic(self, topic: str) -> Dict:
        """Research topic using CrewAI tools"""
        # Tool 1: Search web for trending info
        search_results = self.tools[0].run(topic)

        # Tool 2: Extract from competitor sites
        competitor_content = self.tools[1].run(
            f"Find competitor content about {topic}"
        )

        # Tool 3: Read local research files
        local_research = self.tools[2].run(
            "research_docs/market_trends.md"
        )

        return {
            "search_results": search_results,
            "competitor_content": competitor_content,
            "local_research": local_research,
        }
```

### Example 2: Market Research with Multiple Tools

```python
from crewai_tools import (
    SerperDevTool,
    FirecrawlScrapeWebsiteTool,
    YoutubeVideoSearchTool,
    CSVSearchTool,
)

class MarketInsightAgent(BaseAgent):
    def __init__(self):
        self.serper = SerperDevTool()
        self.firecrawl = FirecrawlScrapeWebsiteTool()
        self.youtube = YoutubeVideoSearchTool()
        self.csv = CSVSearchTool()

    async def analyze_market(self, market: str) -> Dict:
        """Comprehensive market analysis"""

        # 1. Web search for market trends
        trends = self.serper.run(f"Latest {market} trends 2025")

        # 2. Scrape competitor websites
        competitors = self.firecrawl.run(
            "https://competitor.com/market-analysis"
        )

        # 3. Find relevant videos
        videos = self.youtube.run(f"Best practices in {market}")

        # 4. Query internal market data CSV
        internal_data = self.csv.run(
            "historical_data.csv",
            f"Find growth trends in {market}"
        )

        return {
            "trends": trends,
            "competitors": competitors,
            "videos": videos,
            "internal_data": internal_data,
        }
```

### Example 3: Image Generation for Blog Posts

```python
from crewai_tools import DALL_E_Tool

class ContentAgent(BaseAgent):
    def __init__(self):
        self.dall_e = DALL_E_Tool()

    async def generate_blog_post_images(self, topic: str, count: int = 3):
        """Generate images for blog post"""
        images = []

        prompts = [
            f"Professional blog header image about {topic}",
            f"Infographic illustrating {topic}",
            f"Modern web design element for {topic}",
        ]

        for prompt in prompts:
            image = self.dall_e.run(prompt)
            images.append(image)

        return images
```

### Example 4: Database Queries with PGSearch

```python
from crewai_tools import PGSearchTool

class AnalyticsAgent(BaseAgent):
    def __init__(self):
        self.pg_search = PGSearchTool(
            connection_string="postgresql://user:pass@localhost/glad_labs"
        )

    async def get_cost_breakdown(self) -> Dict:
        """Query database for cost analysis"""
        query = self.pg_search.run(
            "Find total API costs by model for the last 30 days"
        )
        return query
```

---

## 📋 Implementation Roadmap

### Week 1 (Days 4-5): Core Tools Integration

**Tasks:**

1. ✅ Add `WebsiteSearchTool` to Content Agent
2. ✅ Add `FirecrawlScrapeWebsiteTool` for content extraction
3. ✅ Add `FileReadTool` for document access
4. ✅ Add `CodeInterpreterTool` for data processing
5. ✅ Update tests for new tools

**Files to Create/Modify:**

- `src/agents/content_agent/tools_config.py` - Tool definitions
- `src/agents/*/tools_*.py` - Agent-specific tools
- `tests/test_crewai_tools_integration.py` - Tool integration tests
- `docs/CREWAI_TOOLS_USAGE.md` - Tool usage guide

**Estimated Time:** 4-6 hours

### Week 2: Extended Tools

**Tasks:**

1. Add image generation (DALL-E)
2. Add PDF/CSV search
3. Add GitHub search for code examples
4. Add YouTube search for research

**Estimated Time:** 6-8 hours

### Week 3: Advanced Integration

**Tasks:**

1. Firecrawl advanced crawling
2. Database tool integration
3. Composio SaaS integrations
4. Performance optimization

**Estimated Time:** 8-10 hours

---

## ✅ Environment Variables Required

### Essential (Phase 1)

```bash
SERPER_API_KEY=...              # Already have
FIRECRAWL_API_KEY=...           # For scraping
```

### Phase 2

```bash
OPENAI_API_KEY=...              # Already have (DALL-E)
GITHUB_TOKEN=...                # GitHub search
YOUTUBE_API_KEY=...             # YouTube search
```

### Phase 3 (Optional)

```bash
BROWSERBASE_TOKEN=...           # JavaScript sites
APIFY_API_KEY=...               # Complex scraping
COMPOSIO_API_KEY=...            # SaaS integrations
```

---

## 🎯 Next Steps

### Immediate (Do Now)

1. **Add tools config file:**

```bash
touch src/agents/content_agent/tools_config.py
```

2. **Create enhanced content agent with tools:**

```python
# Modify: src/agents/content_agent/content_agent.py
# Add: Tool initialization and methods
```

3. **Create integration tests:**

```bash
touch tests/test_crewai_tools_integration.py
```

### Decision Points

**Q1: Which tools should we prioritize first?**

- Answer: WebsiteSearchTool + FileReadTool + FirecrawlScrapeWebsiteTool (highest ROI)

**Q2: Do we need all 30 tools?**

- Answer: No, but having the infrastructure makes adding them trivial

**Q3: How do we avoid vendor lock-in?**

- Answer: Abstract tools behind our own BaseTool interface (already done!)

---

## 🔗 Resources

- **CrewAI Tools Docs:** https://docs.crewai.com/en/concepts/tools
- **CrewAI Tools GitHub:** https://github.com/joaomdmoura/crewai-tools
- **Firecrawl Docs:** https://www.firecrawl.dev/
- **SerperDev Docs:** https://serper.dev/
- **OpenAI DALL-E:** https://platform.openai.com/docs/guides/vision

---

## 💡 Key Insights

1. **CrewAI tools are production-ready** - Used by thousands of agents in production
2. **Most tools have caching** - Reduces API costs and improves performance
3. **Async support** - All tools work with our async/await architecture
4. **Error handling built-in** - Graceful degradation and retries
5. **Easy to extend** - Can create custom tools using same patterns

---

## 🚀 Recommendation

**Start with this minimal set immediately (Phase 1):**

```python
from crewai_tools import (
    SerperDevTool,              # ✅ Already have
    WebsiteSearchTool,          # 📦 Add ASAP
    FileReadTool,               # 📦 Add ASAP
    DirectoryReadTool,          # 📦 Add ASAP
    CodeInterpreterTool,        # 📦 Add ASAP
)
```

**This alone will:**

- Enhance research capabilities 10x
- Enable document analysis
- Support data processing
- Cost: Only API calls (mostly free tier available)
- Time to implement: 2-3 hours

Then iteratively add more tools as needed!

---

**Ready to implement? Let's start with Phase 1! 🚀**
