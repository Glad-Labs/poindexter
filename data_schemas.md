# Data Schemas for Glad Labs Firestore Collections

This document defines the JSON schemas for the core Firestore collections used by the Oversight Hub and the Content Creation Agent.

## 1. `tasks` Collection

Stores the real-time status of tasks assigned to the agent network.

**Schema:**

```json
{
  "taskId": "string", // Unique identifier for the task
  "agentId": "string", // ID of the agent responsible for the task
  "taskName": "string", // Human-readable name of the task (e.g., "Generate Weekly Content Brief")
  "status": "string", // Current status: "queued", "in_progress", "completed", "failed"
  "createdAt": "timestamp", // Timestamp of when the task was created
  "updatedAt": "timestamp", // Timestamp of the last status update
  "metadata": {
    "priority": "number", // Priority level (1-5)
    "relatedContentId": "string" // Optional: Link to a document in the `content_metrics` collection
  }
}
```

## 2. `financials` Collection

Tracks key financial metrics for the "Frontier Firm."

**Schema:**

```json
{
  "metricId": "string", // Unique identifier for the metric (e.g., "monthly_revenue")
  "metricName": "string", // Human-readable name (e.g., "Monthly Revenue")
  "value": "number", // The numerical value of the metric
  "currency": "string", // Currency code (e.g., "USD")
  "timestamp": "timestamp", // Timestamp of when the metric was recorded
  "metadata": {
    "source": "string", // Data source (e.g., "Stripe API", "Manual Entry")
    "isProjection": "boolean" // True if the value is a projection, false if it's actual
  }
}
```

## 3. `content_metrics` Collection

Monitors the performance and status of content created by the agents.

**Schema:**

```json
{
  "contentId": "string", // Unique identifier for the content piece
  "title": "string", // The title of the content
  "type": "string", // Type of content (e.g., "blog_post", "social_media_update")
  "status": "string", // Current status: "draft", "published", "archived"
  "publishedAt": "timestamp", // Timestamp of when the content was published
  "url": "string", // URL to the published content
  "performance": {
    "views": "number",
    "likes": "number",
    "shares": "number",
    "engagementRate": "number" // As a percentage
  },
  "metadata": {
    "strapiId": "string", // ID of the corresponding entry in the Strapi CMS
    "agentVersion": "string" // Version of the agent that created the content
  }
}
