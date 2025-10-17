# 📝 Strapi Content Types Setup Guide

**Date:** October 17, 2025  
**Environment:** Production (Railway)  
**Status:** Ready for configuration  

---

## Overview

This guide walks you through creating the content types needed for your automated content generation pipeline in your production Strapi instance.

**Access your admin panel:** https://glad-labs-strapi-v5-backend-production.up.railway.app/admin

---

## Step 1: Create "Blog Post" Content Type

This is your primary content type for generated articles.

### Via Admin Panel UI (Easy)

1. **Log in to Strapi Admin**
   - URL: https://glad-labs-strapi-v5-backend-production.up.railway.app/admin
   - Use your admin credentials

2. **Navigate to Content-Type Builder**
   - Left sidebar → "Content-Type Builder"
   - Click "Create new collection type"

3. **Basic Information**
   - Display name: `Blog Post`
   - API ID: `blog-post` (auto-filled)
   - Description: "Generated blog articles"
   - Click "Continue"

4. **Add Fields** (in this order)

**Field 1: Title**
- Type: `Text`
- Name: `title`
- ✅ Required
- ✅ Unique
- ✅ Private: No

**Field 2: Slug**
- Type: `Text`
- Name: `slug`
- ✅ Required
- ✅ Unique
- ✅ Private: No
- Help text: "URL-friendly version of title"

**Field 3: Content**
- Type: `Rich Text` (Markdown)
- Name: `content`
- ✅ Required
- Private: No

**Field 4: Excerpt**
- Type: `Text` (Long text)
- Name: `excerpt`
- Private: No
- Help text: "Brief summary for previews"

**Field 5: Featured Image**
- Type: `Media`
- Name: `featuredImage`
- Allow multiple: No
- Private: No

**Field 6: Category**
- Type: `Enumeration`
- Name: `category`
- Options:
  - `Technology`
  - `Business`
  - `AI & Automation`
  - `Productivity`
  - `Other`
- Default value: `Technology`

**Field 7: Tags**
- Type: `JSON`
- Name: `tags`
- Private: No
- Help text: "Array of tag strings"

**Field 8: SEO**
- Type: `Component`
- Name: `seo`
- ✅ Create a new component
- Component name: `seoMetadata`
- Fields in component:
  - `metaTitle` (Text, required)
  - `metaDescription` (Text, long)
  - `keywords` (Text, long)
  - `ogImage` (Media)

**Field 9: Status**
- Type: `Enumeration`
- Name: `status`
- Options:
  - `draft`
  - `published`
  - `scheduled`
- Default value: `draft`

**Field 10: Published At**
- Type: `DateTime`
- Name: `publishedAt`
- Private: No

5. **Save the Content Type**
   - Click "Save"
   - Wait for confirmation

---

## Step 2: Create "Content Topic" Content Type

For organizing and categorizing content generation topics.

1. **In Content-Type Builder**
   - Click "Create new collection type"

2. **Basic Information**
   - Display name: `Content Topic`
   - API ID: `content-topic`
   - Description: "Topics for content generation"

3. **Add Fields**

**Field 1: Name**
- Type: `Text`
- Name: `name`
- ✅ Required
- ✅ Unique

**Field 2: Description**
- Type: `Text` (Long)
- Name: `description`

**Field 3: Keywords**
- Type: `JSON`
- Name: `keywords`
- Help text: "Array of relevant keywords"

**Field 4: Icon**
- Type: `Media`
- Name: `icon`

**Field 5: Featured**
- Type: `Boolean`
- Name: `featured`
- Default value: false

4. **Save**

---

## Step 3: Create "Author" Content Type

For content attribution.

1. **Create new collection type**
   - Display name: `Author`
   - API ID: `author`

2. **Add Fields**

**Field 1: Name**
- Type: `Text`
- ✅ Required

**Field 2: Bio**
- Type: `Text` (Long)

**Field 3: Email**
- Type: `Email`

**Field 4: Avatar**
- Type: `Media`

3. **Save**

---

## Step 4: Configure Content Type Permissions

1. **Navigate to Settings → Roles**
   - Left sidebar → Settings → Roles

2. **Find "Public" Role**
   - Or create new if needed

3. **Grant Permissions**
   - Blog Post: ✅ Read (find, findOne)
   - Content Topic: ✅ Read (find, findOne)
   - Author: ✅ Read (find, findOne)

4. **Create "Content Pipeline" Role**
   - Click "Create new role"
   - Name: `Content Pipeline`
   - Description: "For automated content generation"

5. **Grant Permissions for Pipeline Role**
   - Blog Post: ✅ Create, Read, Update, Delete
   - Content Topic: ✅ Read
   - Author: ✅ Read

---

## Step 5: Create API Token for Pipeline

1. **Navigate to Settings → API Tokens**
   - Left sidebar → Settings → API Tokens

2. **Create New Token**
   - Click "Create new API token"
   - Name: `Content Generation Pipeline`
   - Description: "Token for automated content creation"
   - Token duration: No expiration (or set to yearly)

3. **Select Permissions**
   - Go to "Content" tab
   - Grant "Content Pipeline" role permissions
   - Or manually select:
     - Blog Post: All permissions
     - Content Topic: Read-only
     - Author: Read-only

4. **Copy Token**
   - Click "Save"
   - Copy the token (appears once)
   - ⚠️ **IMPORTANT:** Save this securely!

5. **Add to Environment Variables**
   ```
   STRAPI_API_TOKEN=<your-copied-token>
   STRAPI_API_URL=https://glad-labs-strapi-v5-backend-production.up.railway.app
   ```

---

## Step 6: Test API Connection

### Using curl

```bash
# Test reading blog posts (public)
curl "https://glad-labs-strapi-v5-backend-production.up.railway.app/api/blog-posts"

# Test creating a blog post (with API token)
curl -X POST "https://glad-labs-strapi-v5-backend-production.up.railway.app/api/blog-posts" \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "title": "Test Post",
      "slug": "test-post",
      "content": "This is a test post",
      "category": "Technology",
      "status": "draft"
    }
  }'
```

### Using Python

```python
import requests

STRAPI_URL = "https://glad-labs-strapi-v5-backend-production.up.railway.app"
API_TOKEN = "your-api-token"

# Create blog post
response = requests.post(
    f"{STRAPI_URL}/api/blog-posts",
    headers={
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    },
    json={
        "data": {
            "title": "Generated Article",
            "slug": "generated-article",
            "content": "# Article Content\n\nThis is generated content.",
            "excerpt": "Brief summary",
            "category": "AI & Automation",
            "status": "draft",
            "seo": {
                "metaTitle": "Generated Article",
                "metaDescription": "A generated article about AI",
                "keywords": "AI, automation, content generation"
            }
        }
    }
)

print(response.json())
```

---

## Step 7: Publish Content Type

After creating fields, you must **publish** the content type:

1. **Click the "Publish" button** (top right)
2. Confirm the publication
3. Content type becomes available in the API

---

## Next Steps

1. **Create a few test posts** manually to verify everything works
2. **Update your content generation pipeline** to use the Strapi API
3. **Connect the CoFounder Agent** to create posts automatically
4. **Set up scheduling** for automated publishing
5. **Monitor and optimize** post generation

---

## API Endpoints Reference

After publishing, your content will be available at:

```
GET  /api/blog-posts              # List all posts
GET  /api/blog-posts/:id          # Get single post
POST /api/blog-posts              # Create post (needs API token)
PUT  /api/blog-posts/:id          # Update post (needs API token)
DELETE /api/blog-posts/:id        # Delete post (needs API token)

GET  /api/content-topics          # List topics
GET  /api/authors                 # List authors
```

### Query Parameters

```
# Get with relationships
GET /api/blog-posts?populate=*

# Filter by status
GET /api/blog-posts?filters[status][$eq]=published

# Pagination
GET /api/blog-posts?pagination[page]=1&pagination[pageSize]=10

# Sorting
GET /api/blog-posts?sort[0]=publishedAt:desc

# Select specific fields
GET /api/blog-posts?fields[0]=title&fields[1]=slug
```

---

## Troubleshooting

### Issue: "Content type not found"
- Make sure you **Published** the content type
- Check API ID matches in your requests

### Issue: "Unauthorized" error
- Verify your API token is correct
- Check that the token hasn't expired
- Verify permissions are set correctly in the role

### Issue: "Field validation failed"
- Check required fields are included
- Verify field types match your schema

### Issue: "Slug must be unique"
- Each blog post needs a unique slug
- Include timestamp or UUID to ensure uniqueness

---

## Documentation References

- **Strapi Content-Type Builder:** https://docs.strapi.io/user-docs/content-manager/content-types-builder
- **Strapi REST API:** https://docs.strapi.io/dev-docs/rest-api
- **API Tokens:** https://docs.strapi.io/dev-docs/configurations/api-tokens

---

**Status:** Ready to proceed  
**Time to complete:** 30-45 minutes  
**Next:** Connect content generation pipeline to use these content types
