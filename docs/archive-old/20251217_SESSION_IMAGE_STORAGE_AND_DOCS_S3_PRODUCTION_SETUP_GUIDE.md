# S3 + CloudFront Production Setup Guide

## Overview
Your application now supports two image storage modes:
- **S3 + CloudFront** (Production): Fast, scalable, CDN-backed
- **Local Filesystem** (Development/Fallback): For local testing

## Step 1: Install Dependencies

Add boto3 and botocore to your Python environment on Railway:

```bash
# In src/cofounder_agent directory
pip install -r requirements.txt
```

This installs (among other things):
```
boto3>=1.28.0
botocore>=1.31.0
```

## Step 2: Create AWS S3 Bucket

### Via AWS Console:
1. Go to [AWS S3 Console](https://s3.console.aws.amazon.com/)
2. Click "Create bucket"
3. **Bucket name**: `glad-labs-images` (must be globally unique, modify as needed)
4. **Region**: `us-east-1` (or your preferred region)
5. Click through to create

### Public Access Settings:
For CloudFront distribution to work:
1. Go to Bucket → Permissions
2. Under "Block public access", disable all (CloudFront will handle access)
3. Click "Save"

### Bucket Policy:
Add this policy to allow CloudFront access:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFront",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::cloudfront:user/CloudFront Origin Access Identity/OACXXXXXXXX"
      },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::glad-labs-images/*"
    }
  ]
}
```

Replace `OACXXXXXXXX` with your CloudFront Origin Access Identity (created in step 3).

## Step 3: Create CloudFront Distribution

1. Go to [CloudFront Console](https://console.aws.amazon.com/cloudfront/)
2. Click "Create distribution"
3. **Origin domain**: Select your S3 bucket (`glad-labs-images.s3.amazonaws.com`)
4. **Origin access**: Create new Origin Access Identity (OAI)
5. **Viewer protocol policy**: Redirect HTTP to HTTPS
6. **Allowed HTTP methods**: GET, HEAD
7. **Cache policy**: CachingOptimized (or create custom for `max-age=31536000`)
8. Click "Create distribution"

**Wait for deployment**: Status will show "Deploying" → "Enabled" (5-10 minutes)

Once enabled, note your **CloudFront domain** (e.g., `d123abc.cloudfront.net`)

## Step 4: Configure Railway Environment Variables

In [Railway Dashboard](https://railway.app/):

1. Go to your project → Co-founder Agent service
2. Variables tab
3. Add these environment variables:

```env
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
AWS_S3_REGION=us-east-1
AWS_S3_BUCKET=glad-labs-images
AWS_CLOUDFRONT_DOMAIN=d123abc.cloudfront.net
```

### Getting AWS Credentials:

1. Go to [IAM Console](https://console.aws.amazon.com/iam/)
2. Users → Create user (e.g., `railway-uploader`)
3. Permissions → Attach policy → Create inline policy
4. Service: S3
5. Actions: `PutObject`, `PutObjectAcl`
6. Resources: `arn:aws:s3:::glad-labs-images/*`
7. Review → Create policy
8. Security credentials → Create access key
9. **Save the Access Key ID and Secret Access Key**

Add these to Railway environment variables.

## Step 5: Update Railway Deployment

Deploy the updated code to Railway:

```bash
git add .
git commit -m "feat: Add S3 + CloudFront image storage"
git push
```

Railway will automatically redeploy with the new code.

## Step 6: Test the Setup

### Test 1: Basic Connectivity
```bash
curl -X GET "http://your-railway-app/api/media/health"
# Expected: 200 OK with S3 status
```

### Test 2: Generate Image
```bash
curl -X POST "http://your-railway-app/api/media/generate-image" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A beautiful sunset over mountains",
    "task_id": "test-001",
    "use_generation": true,
    "num_inference_steps": 20
  }'
```

Expected response:
```json
{
  "success": true,
  "image_url": "https://d123abc.cloudfront.net/generated/1702851234-abc123.png",
  "generation_time": 25.5,
  "message": "✅ Image found via sdxl-s3"
}
```

### Test 3: Verify S3 Upload
Go to [AWS S3 Console](https://s3.console.aws.amazon.com/):
1. Navigate to `glad-labs-images` bucket
2. Look for `generated/` folder with PNG files
3. Verify file size and upload time

### Test 4: Verify CloudFront Delivery
```bash
curl -I "https://d123abc.cloudfront.net/generated/YOUR-IMAGE-NAME.png"
# Expected: 200 OK with cache headers
```

## Step 7: Verify in Frontend

In your React app (public-site):

1. Generate a blog post with image
2. Verify `featured_image_url` contains CloudFront URL (not S3 direct URL)
3. Image loads quickly (CloudFront cached globally)
4. Check browser DevTools Network tab for cache hits

Expected URL format:
```
https://d123abc.cloudfront.net/generated/1702851234-abc123.png
```

## Cost Estimation

| Component | Monthly Cost | Notes |
|-----------|--------------|-------|
| S3 Storage | $0.02 per GB | 1,000 images ≈ $2.30 |
| CloudFront | $0.085 per GB | 100 GB downloads ≈ $8.50 |
| **Total** | **~$45** | For 100 GB traffic/month |

Scale increases linearly:
- 10,000 images: +$23/month (S3)
- 1 TB downloads: ~$80/month (CloudFront)

## Troubleshooting

### Images Not Uploading to S3
- ✅ Check AWS credentials in Railway environment
- ✅ Verify S3 bucket name matches environment variable
- ✅ Confirm IAM user has `PutObject` permission
- ✅ Check S3 bucket public access isn't blocked

### CloudFront Returns 403 Forbidden
- ✅ Verify Origin Access Identity is created
- ✅ Check S3 bucket policy has correct OAI ARN
- ✅ Wait for CloudFront distribution to fully deploy
- ✅ Clear CloudFront cache manually

### Images Not Displaying in Frontend
- ✅ Verify CloudFront domain is correct in Railway environment
- ✅ Check browser console for CORS errors
- ✅ Verify S3 bucket CORS policy (if needed)

### S3 Upload Slow
- ✅ CloudFront caching should speed up subsequent requests
- ✅ Consider enabling S3 Transfer Acceleration for faster uploads
- ✅ Use multi-part upload for large files (>100 MB)

## Code Implementation Summary

### What Changed in `media_routes.py`:

1. **Added S3 Client Management**:
   - `get_s3_client()`: Lazy-load and cache S3 client
   - Graceful fallback if AWS credentials not configured

2. **Added Upload Function**:
   - `upload_to_s3()`: Upload file to S3 and return CloudFront URL
   - Automatic retry logic
   - Full error logging

3. **Updated Image Generation Endpoint**:
   - Try S3 upload first (production)
   - Fall back to local filesystem if S3 not configured (development)
   - Automatically set correct image source in metadata

### Fallback Behavior:

```
If S3 configured:     Image → S3 → CloudFront URL → PostgreSQL → Frontend
If S3 not configured: Image → Local Filesystem → File URL → PostgreSQL → Frontend
```

This allows development without AWS setup while enabling production deployment.

## Performance Optimizations

### Cache Headers
- **1 year expiration**: `max-age=31536000, immutable`
- Images are uniquely named: Changing image doesn't break old URLs
- Perfect for blog posts (images don't change after publication)

### CDN Distribution
CloudFront automatically caches in 200+ edge locations globally:
- **US**: 50ms response time
- **Europe**: 100ms response time
- **Asia**: 150ms response time
- **Australia**: 200ms response time

### Recommended Reading
- [AWS S3 Best Practices](https://docs.aws.amazon.com/AmazonS3/latest/userguide/BestPractices.html)
- [CloudFront Optimization Guide](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html)
- [boto3 S3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)

## Next Steps

1. ✅ S3 code integration complete
2. ⏳ Create AWS S3 bucket (estimated: 5 minutes)
3. ⏳ Create CloudFront distribution (estimated: 15 minutes, wait 10+ for deployment)
4. ⏳ Configure Railway environment variables (estimated: 2 minutes)
5. ⏳ Deploy code to Railway (estimated: 5 minutes)
6. ⏳ Test end-to-end flow (estimated: 10 minutes)
7. ⏳ Monitor production for 24 hours

**Estimated Total Setup Time**: 45 minutes - 1 hour

## Support

For issues:
1. Check CloudWatch logs in Railway dashboard
2. Verify S3 bucket policy and OAI configuration
3. Test CloudFront distribution directly with curl
4. Check AWS billing for unexpected charges
