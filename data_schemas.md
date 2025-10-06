# **Data Schemas for GLAD Labs Firestore Collections v1.1**

This document defines the JSON schemas for the core Firestore collections used by the Oversight Hub and the Content Creation Agent. Clear and consistent data structures are essential for the reliability of the automated systems.

---

## 1. `tasks` Collection

Stores the real-time status of tasks assigned to the agent network. This collection acts as the primary command and control log.

**Schema:**

```json
{
  "taskId": "string", // (Document ID) Unique identifier for the task, typically a UUID.
  "agentId": "string", // ID of the agent responsible for the task (e.g., "content-creation-agent-v1").
  "taskName": "string", // Human-readable name of the task (e.g., "Generate Weekly Content Brief", "Publish Article to Strapi").
  "status": "string", // Current status: "queued", "in_progress", "completed", "failed", "pending_review".
  "createdAt": "timestamp", // Firestore timestamp of when the task was created.
  "updatedAt": "timestamp", // Firestore timestamp of the last status update.
  "metadata": {
    "priority": "number", // Priority level (e.g., 1-High, 2-Medium, 3-Low).
    "relatedContentId": "string", // Optional: Link to a document in the `content_metrics` collection.
    "trigger": "string" // How the task was initiated (e.g., "manual_intervention", "scheduled", "api_call").
  }
}
```

---

## 2. `financials` Collection

Tracks key financial metrics for the "Frontier Firm" project, providing a real-time view of the budget burn rate and operational costs.

**Schema:**

```json
{
  "metricId": "string", // (Document ID) Unique identifier for the metric (e.g., "monthly_cloud_spend").
  "metricName": "string", // Human-readable name (e.g., "Monthly Google Cloud Spend").
  "value": "number", // The numerical value of the metric.
  "currency": "string", // Currency code (e.g., "USD").
  "timestamp": "timestamp", // Firestore timestamp of when the metric was recorded.
  "metadata": {
    "source": "string", // Data source (e.g., "GCP Billing API", "Manual Entry", "Stripe API").
    "isProjection": "boolean" // `true` if the value is a forecast, `false` if it's a historical fact.
  }
}
```

---

## 3. `content_metrics` Collection

Monitors the performance and status of content created by the agents. This collection is vital for measuring the ROI of the content engine.

**Schema:**

```json
{
  "contentId": "string", // (Document ID) Unique identifier for the content piece.
  "title": "string", // The title of the content.
  "type": "string", // Type of content (e.g., "blog_post", "social_media_update", "technical_doc").
  "status": "string", // Current status: "draft", "published", "archived", "error".
  "publishedAt": "timestamp", // Firestore timestamp of when the content was published.
  "url": "string", // Direct URL to the published content.
  "performance": {
    "views": "number",
    "likes": "number",
    "shares": "number",
    "comments": "number",
    "engagementRate": "number" // A calculated percentage.
  },
  "metadata": {
    "strapiId": "string", // ID of the corresponding entry in the Strapi CMS.
    "agentVersion": "string", // Version of the agent that created the content (e.g., "content-agent-v1.2").
    "generationTimeMs": "number" // Time taken for the agent to generate the content, in milliseconds.
  }
}
```

---

## 4. `agent_logs` Collection (New)

Provides detailed, structured logs from agent operations for debugging and performance analysis.

**Schema:**

```json
{
  "logId": "string", // (Document ID) Unique identifier for the log entry.
  "agentId": "string", // ID of the agent that produced the log.
  "taskId": "string", // Optional: The taskId this log relates to.
  "level": "string", // Log level: "INFO", "WARNING", "ERROR", "DEBUG".
  "message": "string", // The primary log message.
  "timestamp": "timestamp", // Firestore timestamp when the log was created.
  "payload": {
    // A flexible object for structured data, e.g., API responses, error traces, etc.
    "step": "string", // e.g., "Fetching Pexels Image"
    "durationMs": "number", // e.g., 1500
    "error": "string" // e.g., "API key invalid"
  }
}
```
