# Production Architecture: Image Storage & Blog Publishing

**Setup**: Railway (Backend) + PostgreSQL + Vercel (Frontend)  
**Goal**: Fast, scalable blog with generated images  
**Date**: December 17, 2025

---

## 🎯 Production Architecture Overview

```
┌─────────────────────┐
│  React App (Vercel) │
│  - UI for generation│
│  - Display blog     │
│  - Fast CDN delivery│
└──────────┬──────────┘
           │
           ├─ API calls (image generation)
           │
           ▼
┌──────────────────────────┐
│  FastAPI Backend         │
│  (Railway)               │
│  - Image generation      │
│  - Blog publishing       │
│  - Task management       │
└──────────┬───────────────┘
           │
           ├─ Store images in S3
           ├─ Store metadata in PostgreSQL
           │
           ▼
┌──────────────────────────┐
│  PostgreSQL (RDS)        │
│  - Blog posts metadata   │
│  - Featured image URLs   │
│  - Author/category info  │
└──────────────────────────┘
           ▲
           │
           └─ Read for display

┌──────────────────────────┐
│  S3 (or similar)         │
│  - Image files (.png)    │
│  - Served via CloudFront │
│  - Fast CDN delivery     │
└──────────────────────────┘
           ▲
           │
           └─ Referenced from PostgreSQL URLs
```

---

## 📋 Step-by-Step Flow

### 1. User Generates Image (React App → Railway Backend)

```
User clicks "Generate Image"
    ↓
POST /api/media/generate-image (from Vercel to Railway)
{
  "prompt": "AI gaming NPCs",
  "task_id": "blog-post-123",
  "publish_to_s3": true
}
    ↓
Backend receives request (Railway)
```

### 2. Backend Generates Image (GPU on Railway)

```
SDXL generates 1024x1024 PNG
    ↓
Save to temporary location: /tmp/generated_image.png
    ↓
Image is 1-3 MB binary PNG file
```

### 3. Upload to S3 (Railway → AWS S3)

```
s3_client.upload_file(
    '/tmp/generated_image.png',
    bucket='glad-labs-images',
    key=f'generated/blog-post-{uuid}.png'
)
    ↓
S3 URL: https://s3.amazonaws.com/glad-labs-images/generated/blog-post-123.png
    ↓
(or with CloudFront: https://cdn.glad-labs.com/generated/blog-post-123.png)
```

### 4. Store Metadata in PostgreSQL

```
INSERT INTO posts (
    featured_image_url = 'https://cdn.glad-labs.com/generated/blog-post-123.png',
    author_id = user_id,
    category_id = category_id,
    ...
)
    ↓
URL is 100 bytes (not 5 MB base64!)
```

### 5. Frontend Displays Image (Vercel)

```
Fetch from CDN: https://cdn.glad-labs.com/generated/blog-post-123.png
    ↓
Next.js Image component optimizes:
  - WebP for Chrome
  - Resize to device width
  - Cache for 1 year
    ↓
User sees fast-loading image ⚡
```

---

## 🏗️ Implementation: Three Options

### Option 1: AWS S3 + CloudFront (RECOMMENDED)

**Best for**: Production, scalable, fast globally

**Setup**:

```python
# requirements.txt
boto3==1.26.0

# .env
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_S3_BUCKET=glad-labs-images
AWS_S3_REGION=us-east-1

# Code in media_routes.py
import boto3
from botocore.config import Config

s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_S3_REGION'),
    config=Config(signature_version='s3v4')
)

async def upload_to_s3(file_path: str, task_id: str) -> str:
    """Upload generated image to S3"""
    import uuid

    # Generate unique key
    image_key = f"generated/blog-post-{uuid.uuid4()}.png"

    # Upload file
    s3_client.upload_file(
        file_path,
        os.getenv('AWS_S3_BUCKET'),
        image_key,
        ExtraArgs={
            'ContentType': 'image/png',
            'CacheControl': 'max-age=31536000',  # 1 year
        }
    )

    # Return S3 URL
    # If using CloudFront, use: https://cdn.glad-labs.com/{image_key}
    s3_url = f"https://s3.amazonaws.com/{os.getenv('AWS_S3_BUCKET')}/{image_key}"

    return s3_url
```

**Costs**:

- S3: ~$0.023 per GB
- CloudFront: ~$0.085 per GB
- Total for 1000 images/month: ~$5-10

**Pros**:

- ✅ Globally fast (CloudFront CDN)
- ✅ Highly scalable
- ✅ Cheap storage
- ✅ Industry standard
- ✅ Great for production

**Cons**:

- Requires AWS account
- Slight added complexity

---

### Option 2: Supabase Storage (PostgreSQL-Native)

**Best for**: PostgreSQL-centric, simpler setup

**Setup**:

```python
# requirements.txt
supabase==1.0.0

# .env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_anon_key

# Code
from supabase import create_client

supabase = create_client(
    os.getenv('SUPABASE_URL'),
    os.getenv('SUPABASE_KEY')
)

async def upload_to_supabase(file_path: str, task_id: str) -> str:
    """Upload generated image to Supabase Storage"""
    import uuid

    image_key = f"generated/blog-post-{uuid.uuid4()}.png"

    with open(file_path, 'rb') as f:
        response = supabase.storage.from_('images').upload(
            image_key,
            f.read()
        )

    # Get public URL
    public_url = supabase.storage.from_('images').get_public_url(image_key)

    return public_url
```

**Costs**:

- Free tier: 1 GB storage
- Paid: $5/month for 100 GB

**Pros**:

- ✅ Already using Supabase PostgreSQL
- ✅ Built-in storage service
- ✅ Simple integration
- ✅ CDN included
- ✅ Free tier available

**Cons**:

- Limited free storage
- Vendor lock-in with Supabase

---

### Option 3: Railway Persistent Volume (NO CDN)

**Best for**: Development/testing, not production

**Setup**:

```python
# In Railway, mount persistent volume to /data/images
# In railway.json:
{
  "volumes": {
    "images": {
      "mount": "/data/images"
    }
  }
}

# Code
async def upload_to_railway_volume(file_path: str, task_id: str) -> str:
    """Save to Railway persistent volume"""
    import uuid
    import shutil

    volume_path = "/data/images"
    os.makedirs(volume_path, exist_ok=True)

    image_filename = f"blog-post-{uuid.uuid4()}.png"
    destination = os.path.join(volume_path, image_filename)

    shutil.copy(file_path, destination)

    # Return relative URL (needs frontend on same domain)
    return f"/images/{image_filename}"
```

**Costs**:

- Railway volume: $0.50/GB/month
- 100 images × 2 MB = 200 GB/month = $100/month ❌ EXPENSIVE!

**Pros**:

- ✅ Simple setup

**Cons**:

- ❌ Very expensive for images
- ❌ Not globally fast
- ❌ Requires frontend on same domain
- ❌ Not production-ready

---

## 🚀 RECOMMENDED: Complete Production Implementation

### Step 1: Set Up AWS S3 & CloudFront

```bash
# Create S3 bucket
aws s3api create-bucket --bucket glad-labs-images --region us-east-1

# Set bucket CORS for public read
aws s3api put-bucket-cors \
  --bucket glad-labs-images \
  --cors-configuration file://cors.json
```

**cors.json**:

```json
{
  "CORSRules": [
    {
      "AllowedOrigins": ["https://glad-labs.com", "https://www.glad-labs.com"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "MaxAgeSeconds": 3000
    }
  ]
}
```

### Step 2: Update media_routes.py

```python
# Add to imports
import boto3
from botocore.config import Config

# Initialize S3 client
s3_client = None

async def get_s3_client():
    """Get or create S3 client"""
    global s3_client
    if s3_client is None:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_S3_REGION', 'us-east-1'),
            config=Config(signature_version='s3v4')
        )
        logger.info("✅ S3 client initialized")
    return s3_client

async def upload_to_s3(file_path: str, task_id: str) -> str:
    """Upload generated image to S3 and return public URL"""
    try:
        s3 = await get_s3_client()

        image_key = f"generated/{int(time.time())}-{uuid.uuid4()}.png"

        # Read file
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Upload to S3
        s3.upload_fileobj(
            BytesIO(file_data),
            os.getenv('AWS_S3_BUCKET'),
            image_key,
            ExtraArgs={
                'ContentType': 'image/png',
                'CacheControl': 'max-age=31536000',  # Cache 1 year
                'Metadata': {
                    'task-id': task_id,
                    'generated-date': datetime.now().isoformat()
                }
            }
        )

        logger.info(f"✅ Uploaded to S3: s3://{os.getenv('AWS_S3_BUCKET')}/{image_key}")

        # Return CloudFront URL (if configured) or S3 URL
        cdn_domain = os.getenv('AWS_CLOUDFRONT_DOMAIN')
        if cdn_domain:
            public_url = f"https://{cdn_domain}/{image_key}"
        else:
            public_url = f"https://s3.amazonaws.com/{os.getenv('AWS_S3_BUCKET')}/{image_key}"

        logger.info(f"✅ Public URL: {public_url}")
        return public_url

    except Exception as e:
        logger.error(f"❌ S3 upload failed: {e}")
        raise

# Update generate_featured_image endpoint
@media_router.post("/generate-image")
async def generate_featured_image(request: ImageGenerationRequest):
    # ... generation code ...

    if success and os.path.exists(output_path):
        logger.info(f"✅ Generated image: {output_path}")

        # ✅ UPLOAD TO S3 (not local filesystem)
        try:
            public_url = await upload_to_s3(output_path, request.task_id)

            # Create metadata with S3 URL
            image = FeaturedImageMetadata(
                url=public_url,  # S3/CloudFront URL
                thumbnail=public_url,
                photographer="SDXL (Local Generation)",
                width=1024,
                height=1024,
                alt_text=request.prompt,
                source="sdxl",
            )

            logger.info(f"✅ Image ready at: {public_url}")

        except Exception as e:
            logger.error(f"❌ Failed to upload to S3: {e}")
            # Fallback to base64 if S3 fails
            with open(output_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')

            image = FeaturedImageMetadata(
                url=f"data:image/png;base64,{image_data}",
                # ... rest of metadata
            )

    # Return response
    return ImageGenerationResponse(
        success=True,
        image_url=image.url,
        image=ImageMetadata(
            url=image.url,
            source=image.source,
            photographer=image.photographer,
            width=image.width,
            height=image.height,
        ),
        message=f"✅ Image stored on S3",
    )
```

### Step 3: Environment Variables (Railway)

Set these in Railway dashboard:

```
AWS_ACCESS_KEY_ID=xxxxx
AWS_SECRET_ACCESS_KEY=xxxxx
AWS_S3_BUCKET=glad-labs-images
AWS_S3_REGION=us-east-1
AWS_CLOUDFRONT_DOMAIN=cdn.glad-labs.com  # Optional, if using CloudFront
```

### Step 4: Update Database Schema

Add S3 URL tracking to content_tasks:

```sql
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS s3_image_url VARCHAR(500);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS image_file_size INTEGER;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS image_s3_key VARCHAR(500);
```

### Step 5: Update Frontend (React/Next.js)

```jsx
// pages/blog/generate.tsx
import Image from 'next/image';

export default function GenerateBlog() {
  const [image, setImage] = useState(null);
  const [loading, setLoading] = useState(false);

  const generateImage = async () => {
    setLoading(true);
    try {
      const response = await fetch(
        'https://api.railway.app/api/media/generate-image',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: 'AI gaming NPCs futuristic',
            use_generation: true,
            task_id: 'blog-post-123',
          }),
        }
      );

      const data = await response.json();
      if (data.success) {
        // data.image_url is now S3/CloudFront URL
        setImage(data.image_url);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button onClick={generateImage} disabled={loading}>
        {loading ? 'Generating...' : 'Generate Image'}
      </button>

      {image && (
        <Image
          src={image}
          alt="Generated"
          width={1024}
          height={1024}
          quality={90}
          loading="lazy"
        />
      )}
    </div>
  );
}
```

---

## ⚡ Performance Optimization for Fast Blog

### 1. Database Queries (PostgreSQL)

```sql
-- Add indexes for fast queries
CREATE INDEX idx_posts_status_created ON posts(status, created_at DESC);
CREATE INDEX idx_posts_category ON posts(category_id);
CREATE INDEX idx_posts_author ON posts(author_id);
```

### 2. Image Optimization (Next.js)

```jsx
// Use Next.js Image with optimization
import Image from 'next/image';

<Image
  src={imageUrl}
  alt="Blog featured image"
  width={1024}
  height={1024}
  quality={85} // Reduce from 100 to 85 (imperceptible)
  placeholder="blur" // Blur while loading
  loading="lazy" // Load when visible
  onLoad={(result) => {
    if (result.naturalWidth < 1024) {
      // Handle responsive sizing
    }
  }}
/>;
```

### 3. Frontend Caching (Vercel)

```javascript
// vercel.json
{
  "headers": [
    {
      "source": "/images/:path*",
      "headers": [
        {
          "key": "Cache-Control",
          "value": "public, max-age=31536000, immutable"
        }
      ]
    }
  ]
}
```

### 4. Database Query Optimization

```python
# Only fetch what you need
async def get_blog_posts(limit=10, offset=0):
    return await db.fetch("""
        SELECT
            id, title, slug, excerpt, featured_image_url,
            author_id, category_id, created_at
        FROM posts
        WHERE status = 'published'
        ORDER BY created_at DESC
        LIMIT $1 OFFSET $2
    """, limit, offset)
```

### 5. API Response Caching

```python
from fastapi_cache2 import FastAPICache2
from fastapi_cache2.backends.redis import RedisBackend
from fastapi_cache2.decorators import cache

@router.get("/api/posts")
@cache(expire=3600)  # Cache for 1 hour
async def get_posts():
    # This will be cached
    return await db.fetch("SELECT * FROM posts WHERE status='published'")
```

### 6. CDN for Static Assets (CloudFront)

```yaml
# CloudFront Distribution Settings
Origin: s3://glad-labs-images
Behaviors:
  - Path: /generated/*
    Compress: Yes
    Cache TTL: 31536000 (1 year)
    Query String: No
    Viewer Protocol: HTTPS only

Restrictions:
  Geo-restriction: None
```

---

## 📊 Architecture Comparison

| Aspect                 | Option 1 (S3+CloudFront) | Option 2 (Supabase) | Option 3 (Railway Vol) |
| ---------------------- | ------------------------ | ------------------- | ---------------------- |
| **Image Speed**        | 50-100ms (global CDN)    | 100-200ms           | 500-2000ms             |
| **Cost/1000 images**   | $10-15                   | $5-20               | $100+ ❌               |
| **Scalability**        | Unlimited                | Good                | Limited                |
| **Setup Time**         | 30 min                   | 10 min              | 5 min                  |
| **Production Ready**   | ✅ YES                   | ✅ YES              | ❌ NO                  |
| **Global Performance** | ✅ YES                   | ✅ YES              | ❌ NO                  |

---

## 🚀 Complete Flow (Production)

```
1. User in React app clicks "Generate Blog Post"
   ↓
2. POST /api/media/generate-image
   From: Vercel (https://glad-labs.com)
   To: Railway backend (https://api.glad-labs.com)
   ↓
3. Railway backend generates SDXL image
   ↓
4. Railway uploads to S3
   ↓
5. S3 returns public URL
   https://cdn.glad-labs.com/generated/blog-post-123.png
   ↓
6. Backend stores in PostgreSQL:
   featured_image_url = "https://cdn.glad-labs.com/generated/blog-post-123.png"
   ↓
7. React app receives URL, displays image
   ↓
8. Image loaded from CloudFront (50-100ms globally)
   ↓
9. User publishes blog post
   ↓
10. Vercel public site queries PostgreSQL for posts
    ↓
11. Next.js displays image via CloudFront
    ✅ Fast, scalable, production-ready!
```

---

## 📋 Deployment Checklist

- [ ] Create S3 bucket (glad-labs-images)
- [ ] Create CloudFront distribution
- [ ] Get AWS credentials
- [ ] Update media_routes.py with S3 upload code
- [ ] Add boto3 to requirements.txt
- [ ] Set environment variables in Railway
- [ ] Test image generation
- [ ] Verify S3 URL returned
- [ ] Test image display in React app
- [ ] Verify CloudFront caching working
- [ ] Test blog post publishing flow
- [ ] Monitor costs and performance

---

## 🔧 Troubleshooting

**Image upload fails to S3?**

- Check AWS credentials in Railway
- Verify bucket name is correct
- Check S3 bucket policy allows uploads

**CloudFront not delivering images?**

- Wait 15 minutes for CloudFront distribution to be ready
- Invalidate cache: AWS Console → CloudFront → Create Invalidation
- Test: Visit image URL directly in browser

**Slow image loading?**

- Check CloudFront cache hit ratio
- Enable compression in CloudFront
- Reduce image quality (85% vs 100%)

---

## 💡 Cost Summary (Monthly)

| Service     | Usage       | Cost    |
| ----------- | ----------- | ------- |
| S3 Storage  | 100 GB      | $2.30   |
| S3 Requests | 1M          | $0.40   |
| CloudFront  | 500 GB      | $42.50  |
| Total       | 1000 images | ~$45-50 |

_This is cheap for a production blog with high traffic!_

---

## ✅ Next Steps

1. Create AWS S3 bucket
2. Create CloudFront distribution
3. Update media_routes.py (copy code above)
4. Set Railway environment variables
5. Test image generation
6. Verify S3 upload working
7. Check CloudFront URL working
8. Deploy to production

This will give you a **fast, scalable, production-ready blog platform**! 🚀
