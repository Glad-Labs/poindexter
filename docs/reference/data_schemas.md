# **Data Schemas for Glad Labs Content Platform v2.0**

This document defines the data schemas for the content platform, including Strapi v5 content types, API structures, and supporting data models used throughout the system.

**Last Updated:** October 13, 2025  
**Version:** 2.0  
**Architecture:** Strapi v5 + Next.js + AI Content Agents

---

## **Strapi v5 Content Types**

### 1. `posts` Content Type

Represents blog posts and articles created by the content agents or manually.

**Strapi Schema:**

```json
{
  "id": "number", // Auto-generated primary key
  "documentId": "string", // Strapi v5 document identifier
  "title": "string", // Post title (required)
  "slug": "string", // URL-friendly identifier (required, unique)
  "content": "string", // Main post content in markdown format
  "excerpt": "string", // Short description/summary for previews
  "date": "datetime", // Publication date
  "featured": "boolean", // Whether post should be featured on homepage
  "coverImage": "media", // Featured image for the post
  "category": "relation", // Belongs to one category
  "tags": "relation", // Many-to-many relationship with tags
  "seo": {
    "metaTitle": "string",
    "metaDescription": "string",
    "metaKeywords": "string"
  },
  "createdAt": "datetime", // Auto-generated creation timestamp
  "updatedAt": "datetime", // Auto-generated update timestamp
  "publishedAt": "datetime" // Publication timestamp
}
```

**API Response Structure:**

```json
{
  "data": [
    {
      "id": 16,
      "documentId": "vl126xqnf9wf3wsvf5vgnqaz",
      "title": "Building Neural Networks for Computer Vision",
      "slug": "building-neural-networks-computer-vision",
      "content": "# Building Neural Networks...",
      "excerpt": "A comprehensive guide to implementing...",
      "date": "2025-02-01T00:00:00.000Z",
      "featured": true,
      "coverImage": null,
      "category": {
        "id": 4,
        "name": "AI & Machine Learning",
        "slug": "ai-machine-learning"
      },
      "tags": [
        {
          "id": 8,
          "name": "Neural Networks",
          "slug": "neural-networks"
        }
      ],
      "createdAt": "2025-10-13T03:58:25.579Z",
      "updatedAt": "2025-10-13T05:01:13.053Z",
      "publishedAt": "2025-10-13T05:01:13.062Z"
    }
  ],
  "meta": {
    "pagination": {
      "page": 1,
      "pageSize": 25,
      "pageCount": 1,
      "total": 5
    }
  }
}
```

### 2. `categories` Content Type

Organizes posts into topical categories.

**Strapi Schema:**

```json
{
  "id": "number",
  "documentId": "string",
  "name": "string", // Category name (required)
  "slug": "string", // URL-friendly identifier (required, unique)
  "description": "text", // Optional category description
  "posts": "relation", // One-to-many relationship with posts
  "createdAt": "datetime",
  "updatedAt": "datetime",
  "publishedAt": "datetime"
}
```

### 3. `tags` Content Type

Provides flexible tagging system for posts.

**Strapi Schema:**

```json
{
  "id": "number",
  "documentId": "string",
  "name": "string", // Tag name (required)
  "slug": "string", // URL-friendly identifier (required, unique)
  "posts": "relation", // Many-to-many relationship with posts
  "createdAt": "datetime",
  "updatedAt": "datetime",
  "publishedAt": "datetime"
}
```

### 4. `pages` Content Type (Optional)

For static pages like About, Privacy Policy, etc.

**Strapi Schema:**

```json
{
  "id": "number",
  "documentId": "string",
  "title": "string",
  "slug": "string",
  "content": "string", // Markdown content
  "seo": {
    "metaTitle": "string",
    "metaDescription": "string"
  },
  "createdAt": "datetime",
  "updatedAt": "datetime",
  "publishedAt": "datetime"
}
```

---

## **Content Agent Data Models**

### 1. `BlogPost` Model (Python)

Used by content agents during the creation process.

```python
class BlogPost(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    raw_content: Optional[str] = None
    excerpt: Optional[str] = None
    slug: Optional[str] = None
    primary_keyword: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = []
    images: List[ImageDetails] = []
    body_content_blocks: Optional[List[Dict[str, Any]]] = None
    strapi_id: Optional[int] = None
    strapi_url: Optional[str] = None
    qa_feedback: List[str] = []
```

### 2. `ImageDetails` Model (Python)

Represents images processed by the content agent.

```python
class ImageDetails(BaseModel):
    query: Optional[str] = None
    source: str = "pexels"  # Default to Pexels
    path: Optional[str] = None  # Local path or GCS blob name
    public_url: Optional[str] = None
    alt_text: Optional[str] = None
    caption: Optional[str] = None
    description: Optional[str] = None
    strapi_image_id: Optional[int] = None
```

---

## **Firebase Collections (Oversight Hub)**

### 1. `tasks` Collection

Tracks content generation tasks and agent activities.

```json
{
  "taskId": "string", // Document ID - UUID
  "agentId": "string", // "content-agent"
  "taskName": "string", // "Generate Blog Post: [Topic]"
  "status": "string", // "queued", "in_progress", "completed", "failed"
  "createdAt": "timestamp",
  "updatedAt": "timestamp",
  "metadata": {
    "topic": "string",
    "priority": "number", // 1-High, 2-Medium, 3-Low
    "strapiId": "number", // Link to created post
    "trigger": "string" // "manual", "scheduled", "api"
  }
}
```

### 2. `agent_logs` Collection

Detailed logging from content agent operations.

```json
{
  "logId": "string", // Document ID
  "agentId": "string",
  "taskId": "string",
  "level": "string", // "INFO", "WARNING", "ERROR", "DEBUG"
  "message": "string",
  "timestamp": "timestamp",
  "payload": {
    "step": "string", // "Research", "Generation", "QA", "Publishing"
    "durationMs": "number",
    "error": "string", // Optional error details
    "metadata": "object" // Flexible additional data
  }
}
```

### 3. `content_metrics` Collection

Performance tracking for published content.

```json
{
  "contentId": "string", // Document ID
  "strapiId": "number", // Link to Strapi post
  "title": "string",
  "type": "string", // "blog_post"
  "status": "string", // "published", "draft", "archived"
  "publishedAt": "timestamp",
  "url": "string",
  "performance": {
    "views": "number",
    "engagement": "number",
    "socialShares": "number"
  },
  "metadata": {
    "agentVersion": "string",
    "generationTimeMs": "number",
    "aiModel": "string" // "gpt-4", "gpt-3.5-turbo"
  }
}
```

---

## **API Endpoints Reference**

### Strapi v5 REST API

- **Posts**: `/api/posts` (GET, POST, PUT, DELETE)
- **Categories**: `/api/categories` (GET, POST, PUT, DELETE)
- **Tags**: `/api/tags` (GET, POST, PUT, DELETE)
- **Upload**: `/api/upload` (POST for media files)

### Common Query Parameters

- `populate=*` - Include all relations
- `filters[field][$eq]=value` - Filter by field value
- `sort[field]=asc|desc` - Sort by field
- `pagination[page]=1&pagination[pageSize]=25` - Pagination

### Example API Calls

```bash
# Get all posts with relations
GET /api/posts?populate=*

# Get featured posts
GET /api/posts?filters[featured][$eq]=true&populate=*

# Get posts by category
GET /api/posts?filters[category][slug][$eq]=ai-machine-learning&populate=*

# Get single post by slug
GET /api/posts?filters[slug][$eq]=post-slug&populate=*
```

---

**Schema Documentation maintained by:** Glad Labs Development Team  
**Contact:** Matthew M. Gladding (Glad Labs, LLC)  
**Last Review:** October 13, 2025
