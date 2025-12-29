#!/usr/bin/env python3
"""
Test S3 Integration for Image Upload
Tests S3 connectivity and CloudFront URL generation
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_s3_setup():
    """Test S3 configuration and connectivity"""

    print("\n" + "=" * 70)
    print("🧪 S3 INTEGRATION TEST")
    print("=" * 70)

    # Test 1: Check environment variables
    print("\n📋 Test 1: Environment Variables")
    print("-" * 70)

    required_vars = {
        "AWS_ACCESS_KEY_ID": "AWS Access Key",
        "AWS_SECRET_ACCESS_KEY": "AWS Secret Key",
        "AWS_S3_BUCKET": "S3 Bucket Name",
        "AWS_S3_REGION": "AWS Region",
    }

    optional_vars = {"AWS_CLOUDFRONT_DOMAIN": "CloudFront Domain"}

    all_vars_ok = True
    for var, label in required_vars.items():
        value = os.getenv(var)
        if value:
            # Mask sensitive values
            display = (
                "***"
                if var == "AWS_SECRET_ACCESS_KEY"
                else value[:20] + "..." if len(value) > 20 else value
            )
            print(f"  ✅ {label}: {display}")
        else:
            print(f"  ❌ {label}: NOT SET")
            all_vars_ok = False

    print("\n📋 Optional Variables:")
    for var, label in optional_vars.items():
        value = os.getenv(var)
        if value:
            print(f"  ✅ {label}: {value}")
        else:
            print(f"  ⚠️  {label}: Not set (will use S3 direct URL)")

    if not all_vars_ok:
        print("\n⚠️  Required environment variables not set!")
        print("   Please configure AWS credentials before testing S3 upload.")
        return False

    # Test 2: Import boto3
    print("\n📋 Test 2: boto3 Module Import")
    print("-" * 70)

    try:
        import boto3
        from botocore.config import Config

        print(f"  ✅ boto3 version: {boto3.__version__}")
        print(f"  ✅ botocore imported successfully")
    except ImportError as e:
        print(f"  ❌ Import failed: {e}")
        print("   Please run: pip install boto3 botocore")
        return False

    # Test 3: S3 Client Creation
    print("\n📋 Test 3: S3 Client Creation")
    print("-" * 70)

    try:
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_S3_REGION", "us-east-1"),
            config=Config(signature_version="s3v4"),
        )
        print(f"  ✅ S3 client created successfully")
        print(f"  ✅ Region: {os.getenv('AWS_S3_REGION', 'us-east-1')}")
    except Exception as e:
        print(f"  ❌ Client creation failed: {e}")
        return False

    # Test 4: S3 Bucket Connectivity
    print("\n📋 Test 4: S3 Bucket Connectivity")
    print("-" * 70)

    bucket = os.getenv("AWS_S3_BUCKET")
    try:
        response = s3_client.head_bucket(Bucket=bucket)
        print(f"  ✅ Bucket '{bucket}' exists and is accessible")
        print(f"  ✅ HTTP Status: {response['ResponseMetadata']['HTTPStatusCode']}")
    except s3_client.exceptions.NoSuchBucket:
        print(f"  ❌ Bucket '{bucket}' does not exist")
        return False
    except s3_client.exceptions.Forbidden:
        print(f"  ❌ Access denied to bucket '{bucket}'")
        print("     Check AWS credentials and IAM permissions")
        return False
    except Exception as e:
        print(f"  ❌ Bucket check failed: {e}")
        return False

    # Test 5: Test Upload Simulation
    print("\n📋 Test 5: Test Upload Simulation")
    print("-" * 70)

    try:
        import tempfile
        from io import BytesIO

        # Create test image
        test_key = "test-connection/test-image.txt"
        test_content = b"S3 Connectivity Test - " + str(os.getenv("AWS_S3_BUCKET")).encode()

        s3_client.upload_fileobj(
            BytesIO(test_content),
            bucket,
            test_key,
            ExtraArgs={
                "ContentType": "text/plain",
                "Metadata": {"test": "true", "timestamp": str(Path.ctime(Path(__file__)))},
            },
        )
        print(f"  ✅ Test upload successful")
        print(f"  ✅ S3 key: s3://{bucket}/{test_key}")

        # Verify we can read it back
        response = s3_client.get_object(Bucket=bucket, Key=test_key)
        content = response["Body"].read()
        print(f"  ✅ Verification read successful ({len(content)} bytes)")

        # Clean up
        s3_client.delete_object(Bucket=bucket, Key=test_key)
        print(f"  ✅ Test file cleaned up")

    except Exception as e:
        print(f"  ❌ Upload simulation failed: {e}")
        return False

    # Test 6: CloudFront URL Generation
    print("\n📋 Test 6: CloudFront URL Generation")
    print("-" * 70)

    cdn_domain = os.getenv("AWS_CLOUDFRONT_DOMAIN")
    if cdn_domain:
        example_key = "generated/1702851234-example.png"
        cdn_url = f"https://{cdn_domain}/{example_key}"
        s3_url = f"https://s3.amazonaws.com/{bucket}/{example_key}"
        print(f"  ✅ CloudFront domain configured: {cdn_domain}")
        print(f"  📍 Example CloudFront URL: {cdn_url}")
        print(f"  📍 Fallback S3 URL: {s3_url}")
    else:
        print(f"  ⚠️  CloudFront domain not configured")
        print(f"  📍 Will use direct S3 URLs (slower globally)")
        example_key = "generated/1702851234-example.png"
        s3_url = f"https://s3.amazonaws.com/{bucket}/{example_key}"
        print(f"  📍 Example S3 URL: {s3_url}")

    # Test 7: Import routes module
    print("\n📋 Test 7: Routes Module Import")
    print("-" * 70)

    try:
        from routes.media_routes import get_s3_client, upload_to_s3

        print(f"  ✅ media_routes module imported successfully")
        print(f"  ✅ get_s3_client function available")
        print(f"  ✅ upload_to_s3 function available")
    except ImportError as e:
        print(f"  ⚠️  Could not import media_routes: {e}")
        print(f"     (This is OK if running from different directory)")

    return True


def main():
    """Run all tests"""
    print("\n🚀 Starting S3 Integration Tests...")

    success = asyncio.run(test_s3_setup())

    print("\n" + "=" * 70)
    if success:
        print("✅ ALL TESTS PASSED - S3 Integration Ready!")
        print("\nNext steps:")
        print("  1. Deploy updated code to Railway: git push")
        print("  2. Generate a test image to verify upload")
        print("  3. Check CloudFront for cached images")
    else:
        print("❌ TESTS FAILED - Please fix issues before proceeding")
        print("\nCommon issues:")
        print("  • AWS credentials not set in environment")
        print("  • S3 bucket doesn't exist")
        print("  • IAM user lacks S3 permissions")
        print("  • boto3 not installed (pip install boto3)")
    print("=" * 70 + "\n")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
