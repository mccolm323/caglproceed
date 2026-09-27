#!/usr/bin/env python3
"""One-time upload of the proceedings PDFs to Cloudflare R2.

Skips ' (N)'-suffix duplicate files and anything already in the bucket,
so it's safe to interrupt (Ctrl-C) and re-run.

Requires boto3 (pip install -r requirements-upload.txt) and R2 credentials
in a local .env file (see .env.example) — never commit that file.

Usage:
    python3 scripts/upload_pdfs.py [--source-dir DIR] [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from upload_plan import plan_uploads  # noqa: E402

DEFAULT_SOURCE_DIR = os.path.expanduser("~/Documents/California GL Proceedings")


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def list_local_pdfs(source_dir):
    return sorted(f for f in os.listdir(source_dir) if f.lower().endswith(".pdf"))


def list_remote_keys(client, bucket):
    keys = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--dry-run", action="store_true", help="print the plan, upload nothing")
    args = parser.parse_args()

    load_dotenv()
    bucket = os.environ.get("R2_BUCKET_NAME")
    if not bucket:
        sys.exit("R2_BUCKET_NAME is not set (check .env against .env.example)")

    local_files = list_local_pdfs(args.source_dir)
    print(f"Found {len(local_files)} PDF(s) in {args.source_dir}")

    if args.dry_run:
        to_upload = plan_uploads(local_files, already_uploaded=set())
        print(f"Would upload {len(to_upload)} file(s) (dedup only, remote state not checked):")
        for name in to_upload:
            print(f"  {name}")
        return

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )

    already_uploaded = list_remote_keys(client, bucket)
    to_upload = plan_uploads(local_files, already_uploaded)
    print(f"{len(already_uploaded)} already in bucket, {len(to_upload)} to upload")

    for i, name in enumerate(to_upload, start=1):
        path = os.path.join(args.source_dir, name)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"[{i}/{len(to_upload)}] Uploading {name} ({size_mb:.1f} MB)...")
        client.upload_file(path, bucket, name)

    print("Done.")


if __name__ == "__main__":
    main()
