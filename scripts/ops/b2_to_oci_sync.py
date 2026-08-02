"""One-time B2 -> OCI Object Storage sync for csi-youthmovement media.

Streams every object under the csi_youth_ prefix. Idempotent: skips keys
already present on OCI with the same size. Verifies counts+bytes at the end.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

CFG = Config(
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
    s3={"addressing_style": "path"},
    max_pool_connections=32,
)
PREFIX = "csi_youth_"

src = boto3.client(
    "s3",
    endpoint_url=os.environ["B2_ENDPOINT"],
    aws_access_key_id=os.environ["B2_KEY_ID"],
    aws_secret_access_key=os.environ["B2_APPLICATION_KEY"],
    region_name=os.environ["B2_REGION"],
    config=CFG,
)
dst = boto3.client(
    "s3",
    endpoint_url=os.environ["OCI_ENDPOINT"],
    aws_access_key_id=os.environ["OCI_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["OCI_SECRET_ACCESS_KEY"],
    region_name="us-ashburn-1",
    config=CFG,
)
SRC_BUCKET = os.environ["B2_BUCKET_NAME"]
DST_BUCKET = os.environ["OCI_BUCKET"]


def inventory(client, bucket):
    objs = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for o in page.get("Contents", []):
            objs[o["Key"]] = o["Size"]
    return objs


src_objs = inventory(src, SRC_BUCKET)
dst_objs = inventory(dst, DST_BUCKET)
todo = {k: v for k, v in src_objs.items() if dst_objs.get(k) != v}
print(f"source: {len(src_objs)} objects / {sum(src_objs.values())} bytes; "
      f"already on OCI: {len(src_objs) - len(todo)}; to copy: {len(todo)}")


def copy(key):
    obj = src.get_object(Bucket=SRC_BUCKET, Key=key)
    dst.put_object(
        Bucket=DST_BUCKET,
        Key=key,
        Body=obj["Body"].read(),
        ContentType=obj.get("ContentType") or "application/octet-stream",
    )
    return key


failed = []
with ThreadPoolExecutor(max_workers=8) as pool:
    for i, key in enumerate(pool.map(copy, sorted(todo)), 1):
        if i % 50 == 0 or i == len(todo):
            print(f"  copied {i}/{len(todo)}")

final = inventory(dst, DST_BUCKET)
missing = {k: v for k, v in src_objs.items() if final.get(k) != v}
print(f"final: OCI has {len(final)} objects / {sum(final.values())} bytes; mismatched/missing: {len(missing)}")
if missing:
    for k in list(missing)[:10]:
        print("  MISSING/MISMATCH:", k)
    sys.exit(1)
print("SYNC-VERIFIED-OK")
