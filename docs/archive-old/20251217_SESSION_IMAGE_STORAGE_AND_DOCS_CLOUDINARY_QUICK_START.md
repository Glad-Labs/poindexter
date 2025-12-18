# Quick Cloudinary Setup Steps

## ✅ Complete Checklist

### Step 1: Get Cloudinary Credentials (2 min)

```bash
1. Go to https://cloudinary.com/console
2. Find these values:
   - Cloud Name: (at top of dashboard)
   - API Key: (Settings → API Keys)
   - API Secret: (Settings → API Keys)
3. Save them securely
```

### Step 2: Local Development Setup (5 min)

#### A. Create .env file

```bash
cd src/cofounder_agent
cat > .env << 'EOF'
CLOUDINARY_CLOUD_NAME=your_cloud_name_here
CLOUDINARY_API_KEY=your_api_key_here
CLOUDINARY_API_SECRET=your_api_secret_here
EOF

# Don't commit .env to git!
echo ".env" >> .gitignore
```

#### B. Install dependencies

```bash
cd src/cofounder_agent
pip install -r requirements.txt

# This will install:
# - cloudinary
# - boto3
# - botocore
# (and all other deps)
```

#### C. Verify installation

```bash
python -c "
import cloudinary
import os
print('✅ Cloudinary installed')
print(f'✅ Cloud Name: {os.getenv(\"CLOUDINARY_CLOUD_NAME\", \"not set\")}')
"
```

### Step 3: Test Locally (5 min)

#### A. Generate test image

```bash
cd src/cofounder_agent

# Start the app
python main.py

# In another terminal, test the endpoint:
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A beautiful sunset",
    "use_generation": true,
    "num_inference_steps": 20
  }'

# Expected response:
# {
#   "success": true,
#   "image_url": "https://res.cloudinary.com/...",
#   "source": "sdxl-cloudinary"
# }
```

#### B. Verify in Cloudinary Dashboard

```
1. Go to https://cloudinary.com/console
2. Click "Media Library"
3. Look for "generated" folder
4. You should see your test image!
```

### Step 4: Deploy to Railway (5 min)

#### A. Update Railway environment variables

```
Railway Dashboard:
→ Your Project
→ Co-founder Agent service
→ Variables tab

Add:
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

(Remove S3 variables if you don't need them)
```

#### B. Deploy code

```bash
git add .
git commit -m "feat: Add Cloudinary image storage (free tier)"
git push origin main

# Railway auto-deploys
# Check logs to verify it worked
```

#### C. Test in production

```bash
# Get your Railway app URL
curl -X POST https://your-railway-url/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Your prompt here",
    "use_generation": true
  }'
```

---

## 🎯 Priority Choice: Dev vs Production

### Option A: Use Cloudinary EVERYWHERE (Recommended)

```
Local dev:  Use Cloudinary (free)
Production: Use Cloudinary (free)

Pros:
✅ Same service in both environments
✅ No surprises between dev and prod
✅ Free tier is generous (75 GB/month)
✅ Easy to test

Cons:
❌ If you exceed 75 GB/month, you pay $0.16/GB overage
   (But this is unlikely for a blog)
```

### Option B: Mix Cloudinary (Dev) + S3 (Production)

```
Local dev:  Use Cloudinary (free)
Production: Use S3 + CloudFront ($45/month)

Pros:
✅ Free development (no AWS costs)
✅ Production is enterprise-grade
✅ Can scale without limits

Cons:
❌ Different services in dev vs prod
❌ Need to manage two sets of credentials
❌ Testing production config locally is harder
```

### My Recommendation: Start with Option A

Use Cloudinary for everything. If you ever hit 75 GB/month, switch to S3 then. For a blog, this is unlikely to ever happen.

---

## 📊 What's Configured

After these steps, your system will:

```
Image Generation Flow:
1. User generates image in Oversight Hub
2. SDXL creates PNG (20-30 seconds)
3. Automatically uploads to Cloudinary
4. Returns Cloudinary URL (https://res.cloudinary.com/...)
5. Stores URL in PostgreSQL
6. Public site displays image from Cloudinary CDN
7. Global users see fast delivery (50-200ms)
```

**Cost**: FREE (until 75 GB/month)

---

## 🆘 Troubleshooting

### Issue: "Cloudinary not configured"

```bash
Fix:
1. Check .env file exists in src/cofounder_agent/
2. Check CLOUDINARY_CLOUD_NAME is set
3. Check there are no typos
4. Restart the app
```

### Issue: Upload fails "Authentication failed"

```bash
Fix:
1. Double-check API Key and Secret are correct
2. Go to Cloudinary dashboard to verify values
3. Make sure you copied the full key (no spaces)
```

### Issue: Image URL broken in production

```bash
Fix:
1. Check Railway environment variables are set
2. Verify they match Cloudinary dashboard values
3. Check Railway logs for error messages
```

### Issue: "Image uploads but is slow"

```bash
This is normal on first upload
Cloudinary needs 1-2 seconds to optimize the image
After that, CDN cache makes it instant
```

---

## ✨ Code Changes Summary

What we updated:

1. ✅ Added cloudinary to requirements.txt
2. ✅ Added cloudinary imports with fallback
3. ✅ Added upload_to_cloudinary() function
4. ✅ Updated media_routes.py endpoint to use Cloudinary first
5. ✅ Kept S3 as fallback option
6. ✅ Kept local filesystem as last resort

Result: **Triple-layer fallback** for reliability

```
Try Cloudinary (fast, free)
  → Fallback to S3 (reliable, paid)
    → Fallback to local (always works)
```

---

## 📚 Next Steps

1. ✅ Get Cloudinary credentials
2. ✅ Create .env file locally
3. ✅ Install dependencies
4. ✅ Test locally
5. ✅ Deploy to Railway
6. ✅ Test in production

**Total time: 20-30 minutes**

---

## 🎉 You're Ready!

After completing these steps:

- Images generate automatically
- Upload to Cloudinary (free tier)
- Display globally at CDN speeds
- Cost: $0 (unless you exceed 75 GB/month)

Questions? Check `CLOUDINARY_SETUP_GUIDE.md` for detailed information.
