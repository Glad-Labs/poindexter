# 🏗️ **GLAD Labs Platform Architecture v2.0**

![Architecture](https://img.shields.io/badge/Architecture-Production_Ready-brightgreen)
![Version](https://img.shields.io/badge/Version-2.0-blue)
![Status](https://img.shields.io/badge/Status-Fully_Operational-success)

> **Comprehensive architectural overview of the GLAD Labs AI-powered content platform, detailing component interactions, data flows, and deployment strategies.**

---

## **🎯 Executive Architecture Summary**

The GLAD Labs platform is a modern full-stack web application that combines AI-powered content generation with a high-performance content delivery system. The architecture emphasizes automation, scalability, and maintainability through clear separation of concerns and well-defined interfaces.

**Architecture Principles:**

- **API-First Design**: Headless CMS with RESTful APIs
- **Static Site Generation**: Optimal performance through pre-built pages
- **AI Automation**: Autonomous content creation and management
- **Component Modularity**: Reusable, testable components
- **Production Ready**: Scalable, secure, and monitorable

---

## **🔧 System Components**

### **Frontend Layer**

#### **Next.js Public Site** (`web/public-site/`)

- **Purpose**: Public-facing website and blog
- **Technology**: Next.js 14 with Static Site Generation
- **Port**: 3000
- **Status**: ✅ Production Ready

**Key Features:**

- Homepage with featured posts and content grid
- Individual post pages with full markdown rendering
- Category and tag-based content filtering
- SEO optimization with meta tags and Open Graph
- Responsive design with Tailwind CSS

#### **Oversight Hub** (`web/oversight-hub/`)

- **Purpose**: Admin interface for content agent management
- **Technology**: React 18 with Firebase integration
- **Port**: 3001
- **Status**: 🚧 Development Phase

**Key Features:**

- Real-time monitoring of content generation processes
- Agent control and configuration interface
- Task management and progress tracking
- Chat interface for conversational commands

### **Backend Layer**

#### **Strapi v5 CMS** (`cms/strapi-v5-backend/`)

- **Purpose**: Headless content management system
- **Technology**: Strapi v5 with SQLite/PostgreSQL
- **Port**: 1337
- **Status**: ✅ Production Ready

**Key Features:**

- Content types: Posts, Categories, Tags, Pages
- Automatic REST API generation
- Admin interface for content management
- Media library for image and file management
- Role-based permissions and API authentication

#### **Content Agent** (`src/agents/content_agent/`)

- **Purpose**: Autonomous AI content creation
- **Technology**: Python with OpenAI GPT integration
- **Port**: N/A (event-driven)
- **Status**: ✅ Production Ready

**Key Features:**

- Multi-agent pipeline for content creation
- Quality assurance and refinement loops
- Image sourcing and processing
- Automatic publishing to Strapi CMS

---

## **🔄 Data Flow Architecture**

### **Content Creation Flow**

```mermaid
sequenceDiagram
    participant OH as Oversight Hub
    participant CA as Content Agent
    participant S as Strapi CMS
    participant NS as Next.js Site
    participant U as User

    OH->>CA: Trigger Content Request
    CA->>CA: Research & Generate
    CA->>CA: QA Review & Refinement
    CA->>CA: Image Processing
    CA->>S: Publish Content
    S->>S: Store in Database
    NS->>S: Fetch Content (ISR)
    S->>NS: Return JSON Data
    NS->>NS: Generate Static Pages
    U->>NS: Browse Website
    NS->>U: Serve Optimized Content
```

### **API Integration Points**

```mermaid
graph TD
    A[Next.js Frontend] -->|REST API| B[Strapi v5 CMS]
    C[Content Agent] -->|REST API| B
    D[Oversight Hub] -->|Firebase| E[Cloud Database]
    C -->|Logging| E
    C -->|Images| F[Pexels API]
    C -->|AI| G[OpenAI GPT]

    style A fill:#000000
    style B fill:#4945ff
    style C fill:#4caf50
    style D fill:#61dafb
    style E fill:#ffa726
    style F fill:#ff6b6b
    style G fill:#10a37f
```

---

## **📊 Data Architecture**

### **Content Data Model**

#### **Primary Entities**

1. **Posts**: Blog articles with markdown content
2. **Categories**: Content organization taxonomy
3. **Tags**: Flexible content labeling system
4. **Pages**: Static content pages

#### **Relationships**

- Posts **belong to** one Category
- Posts **have many** Tags (many-to-many)
- Categories **have many** Posts
- Tags **belong to many** Posts

### **Database Schema**

```sql
-- Strapi v5 Structure (SQLite/PostgreSQL)
posts (
  id INTEGER PRIMARY KEY,
  document_id VARCHAR UNIQUE,
  title VARCHAR NOT NULL,
  slug VARCHAR UNIQUE NOT NULL,
  content TEXT,
  excerpt TEXT,
  featured BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  published_at TIMESTAMP
)

categories (
  id INTEGER PRIMARY KEY,
  name VARCHAR NOT NULL,
  slug VARCHAR UNIQUE NOT NULL
)

tags (
  id INTEGER PRIMARY KEY,
  name VARCHAR NOT NULL,
  slug VARCHAR UNIQUE NOT NULL
)

-- Relationship tables
posts_categories_links (post_id, category_id)
posts_tags_links (post_id, tag_id)
```

### **API Response Structure**

```json
{
  "data": [
    {
      "id": 16,
      "title": "Post Title",
      "slug": "post-slug",
      "content": "# Markdown content...",
      "excerpt": "Post summary...",
      "featured": true,
      "category": {
        "id": 4,
        "name": "Category Name",
        "slug": "category-slug"
      },
      "tags": [
        {
          "id": 8,
          "name": "Tag Name",
          "slug": "tag-slug"
        }
      ],
      "publishedAt": "2025-10-13T05:01:13.062Z"
    }
  ],
  "meta": {
    "pagination": {
      "page": 1,
      "pageSize": 25,
      "total": 5
    }
  }
}
```

---

## **🔐 Security Architecture**

### **Authentication & Authorization**

- **Strapi Admin**: JWT-based admin authentication
- **API Access**: Bearer token authentication for external applications
- **CORS Configuration**: Restricted cross-origin requests
- **Environment Security**: API keys and secrets in environment variables

### **Data Protection**

- **Input Validation**: Comprehensive input sanitization
- **SQL Injection Protection**: ORM-based database queries
- **XSS Prevention**: Content sanitization and output encoding
- **HTTPS Enforcement**: Encrypted communication in production

### **Access Control**

```yaml
Public API:
  - Read access to published content
  - No authentication required
  - Rate limiting applied

Admin API:
  - Full CRUD operations
  - JWT authentication required
  - Role-based permissions

Content Agent API:
  - Create/update permissions
  - API token authentication
  - Restricted to content operations
```

---

## **⚡ Performance Architecture**

### **Frontend Performance**

- **Static Site Generation**: Pre-built pages for instant loading
- **Incremental Static Regeneration**: Content updates without full rebuilds
- **Image Optimization**: Next.js automatic image optimization and lazy loading
- **Code Splitting**: Route-based automatic code splitting
- **CDN Integration**: Global content delivery network support

### **Backend Performance**

- **Database Optimization**: Indexed queries and efficient schema design
- **API Caching**: Strapi built-in caching mechanisms
- **Media Optimization**: Responsive image generation and delivery
- **Query Optimization**: Efficient relationship queries with populate

### **Monitoring & Observability**

```yaml
Application Metrics:
  - Response times and throughput
  - Error rates and success metrics
  - Database query performance
  - API endpoint usage patterns

Business Metrics:
  - Content generation success rates
  - Quality assurance scores
  - Publishing frequency and timing
  - User engagement and traffic patterns

System Health:
  - Server resource utilization
  - Database connection health
  - External API availability
  - Service dependency status
```

---

## **🤖 AI Model Provider Architecture**

### **Overview**

The GLAD Labs platform supports flexible AI model routing with **zero-cost local inference** (Ollama) and **cloud API providers** (OpenAI, Anthropic). This hybrid architecture enables cost-effective development while maintaining production-grade quality.

### **Model Provider Flow**

```mermaid
graph TD
    A[API Request] --> B{ModelRouter}
    B --> C{Check USE_OLLAMA}
    C -->|true| D[OllamaClient]
    C -->|false| E{Select Provider}
    E -->|OpenAI| F[OpenAI API]
    E -->|Anthropic| G[Anthropic API]

    D --> H[Local Inference]
    F --> I[Cloud Inference]
    G --> I

    H --> J[Response]
    I --> J

    style A fill:#e3f2fd
    style B fill:#fff3e0
    style C fill:#f3e5f5
    style D fill:#c8e6c9
    style E fill:#fff9c4
    style F fill:#bbdefb
    style G fill:#f8bbd0
    style H fill:#a5d6a7
    style I fill:#90caf9
    style J fill:#c5e1a5
```

### **Provider Selection Logic**

```python
# src/cofounder_agent/services/model_router.py

class ModelProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"

class ModelTier(str, Enum):
    FREE = "free"          # $0.00 - Ollama local models
    BUDGET = "budget"      # $0.15-0.60/1M tokens
    STANDARD = "standard"  # $2-3/1M tokens
    PREMIUM = "premium"    # $10-15/1M tokens
    FLAGSHIP = "flagship"  # $30-75/1M tokens

def route_request(task_type: str) -> dict:
    """Route request to appropriate model provider."""
    if os.getenv('USE_OLLAMA', '').lower() == 'true':
        return {
            'provider': ModelProvider.OLLAMA,
            'tier': ModelTier.FREE,
            'model': 'mistral',  # or llama2, codellama, etc.
            'cost_per_1k_tokens': 0.00
        }
    else:
        complexity = _assess_complexity(task_type)
        return MODEL_RECOMMENDATIONS[complexity]['primary']
```

### **Model Recommendations by Complexity**

| Task Type    | Local (Ollama)     | Cloud (OpenAI/Anthropic) | Cost Comparison          |
| ------------ | ------------------ | ------------------------ | ------------------------ |
| **Simple**   | `phi` (2.7B)       | `gpt-4o-mini`            | $0.00 vs $0.15/1M tokens |
| **General**  | `mistral` (7B)     | `gpt-4o`                 | $0.00 vs $2.50/1M tokens |
| **Code**     | `codellama` (7B)   | `claude-3-5-sonnet`      | $0.00 vs $3.00/1M tokens |
| **Complex**  | `mixtral` (8x7B)   | `gpt-4-turbo`            | $0.00 vs $10/1M tokens   |
| **Critical** | `llama2:70b` (70B) | `claude-opus-4`          | $0.00 vs $15/1M tokens   |

### **Integration Points**

#### **Content Agent Integration**

```python
# src/agents/content_agent/content_agent.py
from services.model_router import ModelRouter

router = ModelRouter(use_ollama=True)  # Enable zero-cost mode
response = router.route_request(task_type='content_generation')

# Automatic provider selection based on USE_OLLAMA
if router.use_ollama:
    client = OllamaClient()
    content = client.generate(prompt, model='mistral')
else:
    # Cloud API fallback
    content = openai_client.generate(prompt)
```

#### **Cost Tracking Integration**

```python
# src/cofounder_agent/services/cost_tracker.py
class CostTracker:
    def track_request(self, provider: str, tokens: int):
        if provider == ModelProvider.OLLAMA:
            # Zero cost for local inference
            self.total_cost += 0.00
            self.provider_costs[ModelProvider.OLLAMA] = 0.00
        else:
            # Track cloud API costs
            cost = tokens * PROVIDER_RATES[provider]
            self.total_cost += cost
```

### **Environment Configuration**

#### **Enable Zero-Cost Local Inference**

```bash
# PowerShell
$env:USE_OLLAMA = "true"

# Bash/Linux
export USE_OLLAMA=true

# .env file
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
```

#### **Cloud Provider Configuration**

```bash
# OpenAI
OPENAI_API_KEY=sk-proj-...

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Disable Ollama for cloud inference
USE_OLLAMA=false
```

### **Performance Benchmarks**

| Hardware                | Model          | Tokens/Sec | Latency (First Token) | Memory Usage |
| ----------------------- | -------------- | ---------- | --------------------- | ------------ |
| **Intel i7 + RTX 3060** | mistral (7B)   | 80-120     | ~200ms                | 6-8 GB       |
| **Intel i7 + RTX 3060** | mixtral (8x7B) | 40-60      | ~400ms                | 10-12 GB     |
| **Apple M2 Max**        | mistral (7B)   | 100-150    | ~150ms                | 8-10 GB      |
| **Apple M2 Max**        | llama2:70b     | 15-25      | ~800ms                | 40-45 GB     |
| **Cloud API**           | gpt-4o         | N/A        | ~300ms                | N/A          |

### **Cost Analysis**

#### **Monthly Cost Comparison (100K tokens/day)**

| Provider           | Model             | Cost/Month | Annual Cost |
| ------------------ | ----------------- | ---------- | ----------- |
| **Ollama (Local)** | mistral           | **$0.00**  | **$0.00**   |
| OpenAI             | gpt-4o-mini       | $4.50      | $54         |
| OpenAI             | gpt-4o            | $75        | $900        |
| Anthropic          | claude-3-5-sonnet | $90        | $1,080      |
| OpenAI             | gpt-4-turbo       | $300       | $3,600      |

**Savings with Ollama**: $54 - $3,600/year depending on usage

### **Provider Capabilities**

| Feature            | Ollama (Local)                       | OpenAI                    | Anthropic                |
| ------------------ | ------------------------------------ | ------------------------- | ------------------------ |
| **Cost**           | $0.00                                | $0.15-30/1M               | $3-15/1M                 |
| **Privacy**        | 100% local                           | Cloud                     | Cloud                    |
| **Latency**        | 50-200ms                             | 200-500ms                 | 200-500ms                |
| **Offline**        | ✅ Yes                               | ❌ No                     | ❌ No                    |
| **GPU Required**   | Recommended                          | N/A                       | N/A                      |
| **Context Length** | 2K-32K                               | 128K-200K                 | 200K                     |
| **Quality**        | Good                                 | Excellent                 | Excellent                |
| **Use Case**       | Development, testing, cost-sensitive | Production, complex tasks | Production, long context |

### **Hybrid Strategy**

**Recommended Approach:**

1. **Development**: Use Ollama (FREE tier) for rapid iteration
2. **Testing**: Validate with Ollama before cloud deployment
3. **Production**:
   - Non-critical tasks → Ollama (mistral)
   - User-facing content → Cloud APIs (gpt-4o, claude-3-5-sonnet)
   - Complex analysis → Cloud APIs (gpt-4-turbo, claude-opus-4)

**Cost Optimization Example:**

```python
def select_model_intelligently(task_type: str, is_production: bool):
    """Smart model selection based on environment and task."""
    if not is_production:
        # Always use Ollama in development
        return ModelProvider.OLLAMA, 'mistral'

    # Production: Use cloud for user-facing, Ollama for internal
    if task_type in ['user_query', 'content_generation']:
        return ModelProvider.OPENAI, 'gpt-4o'
    else:
        return ModelProvider.OLLAMA, 'mistral'
```

### **Monitoring & Observability**

```yaml
Model Provider Metrics:
  - Request volume by provider (Ollama vs Cloud)
  - Average latency by model
  - Cost per request
  - Provider error rates
  - Model fallback frequency

Cost Tracking:
  - Daily/monthly spend by provider
  - Cost per feature/agent
  - Budget alerts and thresholds
  - Savings from Ollama usage

Performance Metrics:
  - Tokens per second (local inference)
  - GPU utilization (Ollama)
  - Cache hit rates
  - Response quality scores
```

### **Setup Instructions**

For detailed setup instructions, see [Ollama Setup Guide](./OLLAMA_SETUP.md).

**Quick Start:**

1. Install Ollama: `https://ollama.ai/download`
2. Pull model: `ollama pull mistral`
3. Enable: `$env:USE_OLLAMA = "true"`
4. Start platform: `npm run dev`

---

## **🚀 Deployment Architecture**

### **Development Environment**

```yaml
Local Development:
  - Strapi v5: SQLite database on localhost:1337
  - Next.js: Development server on localhost:3000
  - Content Agent: Python process with local execution
  - Oversight Hub: React dev server on localhost:3001

Dependencies:
  - Node.js 20.11.1+
  - Python 3.12+
  - Environment variables configured locally
```

### **Production Deployment**

```yaml
Recommended Production Stack:
  - Frontend: Vercel or Netlify for Next.js
  - CMS: Railway or DigitalOcean for Strapi
  - Database: PostgreSQL on managed service
  - Content Agent: Google Cloud Run or AWS Lambda
  - Media Storage: AWS S3 or Google Cloud Storage
  - Monitoring: DataDog, New Relic, or Google Cloud Monitoring

Infrastructure Requirements:
  - SSL certificates for all services
  - Environment variable management
  - Database backup and recovery
  - CI/CD pipeline for automated deployment
```

### **Scaling Considerations**

- **Horizontal Scaling**: Multiple Strapi instances behind load balancer
- **Database Scaling**: Read replicas for content distribution
- **CDN Integration**: Global content delivery for static assets
- **Caching Strategy**: Redis for API response caching
- **Content Agent Scaling**: Serverless functions for demand-based scaling

---

## **📈 Operational Excellence**

### **Monitoring Strategy**

1. **Application Performance**: Response times, error rates, throughput
2. **Content Quality**: QA scores, refinement cycles, success rates
3. **Business Metrics**: Content publishing frequency, engagement rates
4. **Infrastructure Health**: Server resources, database performance

### **Backup & Recovery**

1. **Database Backups**: Automated daily backups with point-in-time recovery
2. **Media Backups**: Cloud storage with versioning and redundancy
3. **Code Repository**: Git with comprehensive version control
4. **Configuration Management**: Environment variable backup and recovery

### **Maintenance Procedures**

1. **Dependency Updates**: Regular security and feature updates
2. **Performance Optimization**: Ongoing performance monitoring and tuning
3. **Content Auditing**: Periodic review of generated content quality
4. **Security Updates**: Regular security patches and vulnerability assessments

---

## **🔮 Future Architecture Evolution**

### **Planned Enhancements**

1. **Microservices Migration**: Break down monolithic components
2. **Event-Driven Architecture**: Pub/Sub for all inter-service communication
3. **Vector Database Integration**: Content similarity and recommendation engine
4. **Multi-language Support**: Internationalization and localization
5. **Real-time Collaboration**: Live editing and collaborative content creation

### **Scalability Roadmap**

1. **Phase 1**: Current architecture optimization and monitoring
2. **Phase 2**: Microservices decomposition and containerization
3. **Phase 3**: Event-driven architecture and real-time features
4. **Phase 4**: AI enhancement and machine learning integration
5. **Phase 5**: Global distribution and multi-region deployment

---

**Architecture Documentation maintained by:** GLAD Labs Development Team  
**Contact:** Matthew M. Gladding (Glad Labs, LLC)  
**Last Review:** October 13, 2025  
**Next Review:** November 13, 2025  
**Architecture Status:** ✅ Production Ready v2.0
 
 

 
 
