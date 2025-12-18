# Cloudinary Setup Guide

## 📋 Step 1: Gather Cloudinary Credentials

Go to your Cloudinary Dashboard and find these three pieces of information:

1. **Cloud Name**: Top of dashboard, looks like `abc123def`
2. **API Key**: Settings → API Keys → "Key"
3. **API Secret**: Settings → API Keys → "Secret"

Store these safely - you'll need them!

---

## 🔑 Step 2: Environment Variables

### For Local Development:

```bash
# In your .env file (or system environment)
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

# Keep these for local dev S3 if needed:
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# (optional - for fallback)
```

### For Railway Production:

```env
# In Railway Dashboard → Environment Variables
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

# Optional: Keep S3 for future use
# AWS_S3_BUCKET=...
# AWS_CLOUDFRONT_DOMAIN=...
```

---

## 💻 Step 3: Install Dependencies

Add to `src/cofounder_agent/requirements.txt`:

```
cloudinary>=1.36.0
```

Then install:

```bash
cd src/cofounder_agent
pip install -r requirements.txt
```

---

## 🎯 Dev vs Production Strategy

### Recommendation: Use Cloudinary for BOTH (for now)

**Why?**

- ✅ Free tier gives 75 GB/month
- ✅ No extra cost for local dev
- ✅ Same service in dev and prod (no surprises)
- ✅ Easy testing
- ✅ Images persist across dev/prod

**Timeline:**

```
NOW (Dec 2024):              Use Cloudinary free (75 GB/month)
                             ↓
WHEN YOU HIT 75 GB LIMIT:    Switch to S3 + CloudFront
(probably never for a blog)  or DigitalOcean Spaces
```

---

## 🔧 Step 4: Update Code

Replace the S3 functions in `media_routes.py` with Cloudinary equivalents.

### Option A: Pure Cloudinary (Recommended for Now)

Replace S3 code with Cloudinary:

```python
import cloudinary
import cloudinary.uploader
from typing import Optional

# Initialize Cloudinary
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

async def upload_to_cloudinary(file_path: str, task_id: Optional[str] = None) -> Optional[str]:
    """
    Upload generated image to Cloudinary and return public URL.

    Args:
        file_path: Local path to image file
        task_id: Task ID for metadata (optional)

    Returns:
        Public URL if successful, None if upload fails
    """
    try:
        # Upload to Cloudinary
        result = cloudinary.uploader.upload(
            file_path,
            folder="generated",  # Organize in folder
            resource_type="image",
            invalidate=True,  # Invalidate CDN cache
            tags=["blog-generated"] + ([task_id] if task_id else []),
            context={
                "task_id": task_id or "unknown",
                "generated_date": datetime.now().isoformat()
            }
        )

        public_url = result['secure_url']  # HTTPS URL
        logger.info(f"✅ Uploaded to Cloudinary: {public_url}")
        return public_url

    except Exception as e:
        logger.error(f"❌ Cloudinary upload failed: {e}", exc_info=True)
        return None
```

Then update the endpoint to use `upload_to_cloudinary` instead of `upload_to_s3`:

In `generate_featured_image()`, find this section:

```python
# Try S3 first (production)
task_id_str = request.task_id if request.task_id else None
s3_url = await upload_to_s3(output_path, task_id_str)
```

Replace with:

```python
# Upload to Cloudinary
task_id_str = request.task_id if request.task_id else None
cloudinary_url = await upload_to_cloudinary(output_path, task_id_str)
```

And update the logic:

```python
if cloudinary_url:
    # Use Cloudinary URL
    logger.info(f"✅ Using Cloudinary URL for image delivery")
    image_url_path = cloudinary_url
    image_source = "sdxl-cloudinary"
else:
    # Fallback to local filesystem (development)
    logger.info("ℹ️ Cloudinary not configured, using local filesystem fallback")
    image_filename = f"post-{uuid.uuid4()}.png"
    image_url_path = f"/images/generated/{image_filename}"
    full_disk_path = f"web/public-site/public{image_url_path}"

    # Ensure directory exists
    os.makedirs(os.path.dirname(full_disk_path), exist_ok=True)

    # Copy from temp location to persistent storage
    with open(output_path, 'rb') as f:
        image_bytes = f.read()

    with open(full_disk_path, 'wb') as f:
        f.write(image_bytes)

    logger.info(f"💾 Saved image to: {full_disk_path}")
    image_source = "sdxl-local"
```

---

### Option B: Cloudinary + S3 Fallback (Future-Proof)

Keep both, but prefer Cloudinary:

```python
async def upload_image(file_path: str, task_id: Optional[str] = None) -> Optional[str]:
    """
    Upload to Cloudinary first, fall back to S3, then local.
    """
    # Try Cloudinary first (free tier for now)
    cloudinary_url = await upload_to_cloudinary(file_path, task_id)
    if cloudinary_url:
        return cloudinary_url

    # Fall back to S3 (for production later)
    logger.info("ℹ️ Cloudinary failed, trying S3...")
    s3_url = await upload_to_s3(file_path, task_id)
    if s3_url:
        return s3_url

    # Fall back to local filesystem (development)
    logger.info("ℹ️ Cloud uploads failed, using local filesystem...")
    return None
```

---

## 🧪 Step 5: Test It

### Test 1: Check Credentials

```bash
python -c "
import cloudinary
import os

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

# If no error, credentials are valid
print('✅ Cloudinary credentials configured')
"
```

### Test 2: Test Upload

```python
import cloudinary.uploader
import os

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

# Create a test image
from PIL import Image
import io

img = Image.new('RGB', (100, 100), color='red')
img.save('/tmp/test.png')

# Upload it
result = cloudinary.uploader.upload('/tmp/test.png', folder='test')
print(f"✅ Test upload successful: {result['secure_url']}")

# Clean up
cloudinary.api.delete_resources(['test/test.png'])
print("✅ Test image cleaned up")
```

### Test 3: Generate Real Image

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A sunset over mountains",
    "use_generation": true,
    "num_inference_steps": 20
  }'

# Check response - should have Cloudinary URL:
# "image_url": "https://res.cloudinary.com/..."
```

---

## 📊 What You Get with Cloudinary Free Tier

### Limits:

- 75 GB storage + downloads per month
- 100,000 assets maximum
- Unlimited uploads (in free tier)
- Unlimited transformations (resize, crop, etc.)

### Included Features:

- Global CDN (fast everywhere)
- Image optimization (automatic compression)
- Image transformations API
- HTTPS delivery
- Versioning (keep old versions)

### Not Included:

- Custom domain (uses `res.cloudinary.com`)
- Advanced security features
- Priority support

---

## 🔄 Monitoring Your Usage

In Cloudinary Dashboard:

```
Settings → Billing → Usage

Shows:
- Bandwidth used (of 75 GB)
- Storage used
- Transformations
- API calls
```

Set email alerts to warn before you hit 75 GB limit.

---

## ⚠️ When 75 GB Limit is Hit

Two options:

### Option 1: Keep Using Cloudinary (Pay)

```
75+ GB/month = $0.16 per GB overage
So if you use 100 GB: $4/month extra
Still cheaper than S3 + CloudFront ($45)
```

### Option 2: Switch to S3 (One-Time Code Change)

```
Just swap the upload function back to S3
Your images on Cloudinary stay there forever
(can keep using old Cloudinary URLs or migrate them)

New images go to S3
Cost: ~$2-10/month depending on usage
```

**My recommendation**: Cloudinary will probably NEVER hit 75 GB/month for a blog. That's:

- 25,000 images at 3 MB each
- Or 7.5 TB of downloads monthly
- Extremely unlikely for a blog

---

## 🚀 Local Dev Setup

### For Local Development:

```bash
# 1. Create .env file in src/cofounder_agent/
cat > .env << 'EOF'
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret
EOF

# 2. Install cloudinary
pip install cloudinary

# 3. Test
python -c "import cloudinary; print('✅ Cloudinary ready')"

# 4. Run your app
npm start  # or python main.py
```

### Using with your current setup:

```python
# In media_routes.py, you can load from .env:
from dotenv import load_dotenv
load_dotenv()

# Then use:
CLOUDINARY_CLOUD_NAME = os.getenv('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = os.getenv('CLOUDINARY_API_KEY')
CLOUDINARY_API_SECRET = os.getenv('CLOUDINARY_API_SECRET')
```

---

## 🎯 Quick Deployment to Railway

### Step 1: Add to Requirements

```bash
# In src/cofounder_agent/requirements.txt
cloudinary>=1.36.0
```

### Step 2: Set Environment Variables

```
Railway Dashboard
→ Your Project → Co-founder Agent
→ Variables tab
→ Add:

CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

# Remove S3 variables (optional):
# Remove AWS_ACCESS_KEY_ID
# Remove AWS_SECRET_ACCESS_KEY
# etc.
```

### Step 3: Deploy Code

```bash
git add .
git commit -m "feat: Switch to Cloudinary for image hosting"
git push origin main
```

Railway auto-deploys.

---

## ✅ Verification Checklist

After setup:

- [ ] Created Cloudinary account
- [ ] Saved Cloud Name, API Key, API Secret
- [ ] Added `cloudinary>=1.36.0` to requirements.txt
- [ ] Updated `media_routes.py` with Cloudinary upload function
- [ ] Set environment variables (local or Railway)
- [ ] Tested upload locally
- [ ] Deployed to Railway
- [ ] Generated test image
- [ ] Verified image in Cloudinary dashboard
- [ ] Checked image appears in public site

---

## 🔍 Troubleshooting

### Upload fails: "Invalid API Key"

```
Fix: Check CLOUDINARY_API_KEY in environment
     Verify it's the actual key, not the Cloud Name
```

### Upload succeeds but image URL is broken

```
Fix: Make sure HTTPS URL is being used (result['secure_url'])
     Not HTTP (result['url'])
```

### "Unable to connect to Cloudinary"

```
Fix: Check internet connection
     Verify no firewall blocking uploads
     Check Railway logs for network errors
```

### Image appears but loads slowly

```
This is normal on first load
Cloudinary needs 1-2 seconds to optimize
After that, CDN cache makes it instant
```

---

## 📚 Resources

- [Cloudinary Python SDK](https://cloudinary.com/documentation/python_integration)
- [Cloudinary Upload API](https://cloudinary.com/documentation/image_upload_api_reference)
- [Cloudinary Dashboard](https://cloudinary.com/console)

---

## 💡 Pro Tips

1. **Keep images organized**: Use folders in Cloudinary (`folder="generated"`)
2. **Tag your images**: Makes it easier to find later
3. **Monitor usage**: Check dashboard weekly
4. **Set expiration**: Old images can be auto-deleted if needed
5. **Use transformations**: Cloudinary can resize, crop, optimize automatically

---

## 🎉 You're Ready!

Once you've completed the steps above:

1. ✅ Cloudinary is set up for local dev
2. ✅ Code is updated to use Cloudinary
3. ✅ Environment variables are configured
4. ✅ Images upload automatically
5. ✅ Everything works (free!)

**Next**: Follow the code changes in Step 4 and test locally.
