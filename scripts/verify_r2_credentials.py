#!/usr/bin/env python3
"""Verify the configured R2/S3 credentials actually work — read AND write.

Run before and after a credential rotation. Exercises the same settings the
upload path uses (storage_endpoint / storage_bucket / storage_access_key /
storage_secret_key), so a pass here means the pipeline can publish.

    docker exec poindexter-worker python /app/scripts/verify_r2_credentials.py

Never prints secret values — only whether each step worked.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime


async def main() -> int:
    sys.path.insert(0, "/app")
    import os

    import asyncpg

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("LOCAL_DATABASE_URL")
    if not dsn:
        sys.path.insert(0, "/opt/poindexter")
        from brain.bootstrap import resolve_database_url
        dsn = resolve_database_url()
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        from services.site_config import SiteConfig

        sc = SiteConfig(pool=pool)
        await sc.load(pool)
        endpoint = sc.get("storage_endpoint", "")
        bucket = sc.get("storage_bucket", "")
        access = sc.get("storage_access_key", "")
        secret = await sc.get_secret("storage_secret_key", "")
    finally:
        await pool.close()

    missing = [
        n for n, v in (
            ("storage_endpoint", endpoint), ("storage_bucket", bucket),
            ("storage_access_key", access), ("storage_secret_key", secret),
        ) if not v
    ]
    if missing:
        print(f"FAIL  unset: {', '.join(missing)}")
        return 2

    print(f"config  endpoint={endpoint}  bucket={bucket}  access_key_id={access[:6]}…{access[-4:]}")

    import boto3
    from botocore.exceptions import ClientError

    s3 = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=access, aws_secret_access_key=secret,
        region_name="auto",
    )
    key = f"_healthcheck/rotation-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.txt"
    ok = True

    try:
        s3.head_bucket(Bucket=bucket)
        print("PASS  head_bucket      — credentials accepted, bucket reachable")
    except ClientError as exc:
        print(f"FAIL  head_bucket      — {exc.response['Error'].get('Code')}: {exc.response['Error'].get('Message')}")
        return 1

    try:
        s3.put_object(Bucket=bucket, Key=key, Body=b"rotation healthcheck", ContentType="text/plain")
        print(f"PASS  put_object       — wrote {key}")
    except ClientError as exc:
        print(f"FAIL  put_object       — {exc.response['Error'].get('Code')} (token may lack write)")
        ok = False

    if ok:
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            print(f"PASS  get_object       — read back {len(body)} bytes")
        except ClientError as exc:
            print(f"FAIL  get_object       — {exc.response['Error'].get('Code')}")
            ok = False
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            print("PASS  delete_object    — cleaned up")
        except ClientError as exc:
            print(f"WARN  delete_object    — {exc.response['Error'].get('Code')} (leftover: {key})")

    print("\nRESULT: R2 credentials fully functional" if ok else "\nRESULT: credentials INCOMPLETE — do not revoke the old token")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
