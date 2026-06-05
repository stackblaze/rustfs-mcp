#!/usr/bin/env python3
"""
S3 Utilities — core object-storage operations for the RustFS add-on MCP.

stackblaze rustfs-mcp: an S3Manager is built from an `s3://ak:sk@host:port` URI
(S3Manager.from_uri) and carries a `restricted` flag. In restricted (read-only)
mode every mutating operation refuses BEFORE touching S3 — the fail-safe surface
the kubero chat broker grants on production add-ons.

Targets RustFS (an S3-compatible store) over plain HTTP in-cluster with path-style
addressing.
"""

import base64
import json
import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger("s3-utils")

# Cap object-text previews so a huge object can't blow the chat context.
DEFAULT_MAX_BYTES = 64 * 1024
# Cap object listings.
DEFAULT_MAX_KEYS = 1000
# Cap base64 binary transfer through the MCP (upload/download). The store is
# in-cluster (no presigned URLs reachable from a browser), so file bytes tunnel
# as base64 in the JSON body — fine for modest files, not multi-GB.
DEFAULT_MAX_OBJECT_BYTES = 10 * 1024 * 1024  # 10 MiB


class RestrictedError(PermissionError):
    """Raised when a write op is attempted on a read-only (restricted) add-on."""


class S3Manager:
    """Manages an S3 (RustFS) connection and bucket/object operations."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, restricted: bool = False):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.restricted = restricted
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},  # RustFS requires path-style
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=20,
            ),
        )

    @classmethod
    def from_uri(cls, uri: str, restricted: bool = False) -> "S3Manager":
        """Build a manager from an `s3://ak:sk@host:port` URI (connection-from-request)."""
        from s3_connection import parse_s3_uri  # local import avoids a cycle at import time

        endpoint, ak, sk = parse_s3_uri(uri)
        return cls(endpoint, ak, sk, restricted=restricted)

    def _require_unrestricted(self) -> None:
        if self.restricted:
            raise RestrictedError(
                "This add-on is attached read-only (restricted mode); "
                "writes (create/delete bucket, put/delete object, set policy) are not allowed."
            )

    # ---------- Reads (allowed in restricted mode) ----------

    def list_buckets(self) -> Dict[str, Any]:
        resp = self._client.list_buckets()
        buckets = [
            {"name": b["Name"], "creation_date": str(b.get("CreationDate", ""))}
            for b in resp.get("Buckets", [])
        ]
        return {"status": "success", "endpoint": self.endpoint, "buckets": buckets}

    def list_objects(
        self,
        bucket: str,
        prefix: Optional[str] = None,
        max_keys: int = 100,
        delimiter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List a bucket. With `delimiter` (e.g. '/') the result is folder-aware:
        `prefixes` holds the common prefixes (sub-folders) under `prefix`, and
        `objects` holds only the leaf objects at this level — the shape an S3
        file-browser needs to render one directory at a time."""
        kwargs: Dict[str, Any] = {
            "Bucket": bucket,
            "MaxKeys": max(1, min(int(max_keys), DEFAULT_MAX_KEYS)),
        }
        if prefix:
            kwargs["Prefix"] = prefix
        if delimiter:
            kwargs["Delimiter"] = delimiter
        resp = self._client.list_objects_v2(**kwargs)
        objects = [
            {
                "key": o["Key"],
                "size": o.get("Size", 0),
                "last_modified": str(o.get("LastModified", "")),
                "etag": (o.get("ETag") or "").strip('"'),
            }
            for o in resp.get("Contents", [])
            # Skip the folder placeholder object itself (key == the prefix).
            if not (delimiter and o["Key"] == (prefix or ""))
        ]
        prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
        return {
            "status": "success",
            "bucket": bucket,
            "prefix": prefix or "",
            "delimiter": delimiter or "",
            "count": len(objects),
            "truncated": bool(resp.get("IsTruncated")),
            "prefixes": prefixes,
            "objects": objects,
        }

    def stat_object(self, bucket: str, key: str) -> Dict[str, Any]:
        resp = self._client.head_object(Bucket=bucket, Key=key)
        return {
            "status": "success",
            "bucket": bucket,
            "key": key,
            "size": resp.get("ContentLength", 0),
            "content_type": resp.get("ContentType", ""),
            "etag": (resp.get("ETag") or "").strip('"'),
            "last_modified": str(resp.get("LastModified", "")),
            "metadata": resp.get("Metadata", {}),
        }

    def bucket_usage(self, bucket: str) -> Dict[str, Any]:
        """Object count + total size (paginates the whole bucket)."""
        paginator = self._client.get_paginator("list_objects_v2")
        count = 0
        total = 0
        for page in paginator.paginate(Bucket=bucket):
            for o in page.get("Contents", []):
                count += 1
                total += o.get("Size", 0)
        return {
            "status": "success",
            "bucket": bucket,
            "object_count": count,
            "total_bytes": total,
        }

    def get_object_text(
        self, bucket: str, key: str, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> Dict[str, Any]:
        cap = max(1, min(int(max_bytes), DEFAULT_MAX_BYTES))
        resp = self._client.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{cap - 1}")
        body = resp["Body"].read()
        try:
            text = body.decode("utf-8")
            truncated = resp.get("ContentLength", len(body)) > len(body)
            return {
                "status": "success",
                "bucket": bucket,
                "key": key,
                "bytes_returned": len(body),
                "truncated": truncated,
                "text": text,
            }
        except UnicodeDecodeError:
            return {
                "status": "error",
                "message": "object is not valid UTF-8 text (binary); preview unavailable",
                "bucket": bucket,
                "key": key,
            }

    def get_bucket_policy(self, bucket: str) -> Dict[str, Any]:
        try:
            resp = self._client.get_bucket_policy(Bucket=bucket)
            policy = resp.get("Policy", "")
            try:
                policy = json.loads(policy)
            except Exception:
                pass
            return {"status": "success", "bucket": bucket, "policy": policy}
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchBucketPolicy", "NoSuchBucket"):
                return {"status": "success", "bucket": bucket, "policy": None}
            raise

    # ---------- Writes (refused in restricted mode) ----------

    def create_bucket(self, bucket: str) -> Dict[str, Any]:
        self._require_unrestricted()
        try:
            self._client.create_bucket(Bucket=bucket)
            return {"status": "success", "message": f"Bucket '{bucket}' created"}
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                return {"status": "success", "message": f"Bucket '{bucket}' already exists"}
            return {"status": "error", "message": f"Failed to create bucket: {e}"}

    def delete_bucket(self, bucket: str) -> Dict[str, Any]:
        self._require_unrestricted()
        try:
            self._client.delete_bucket(Bucket=bucket)
            return {"status": "success", "message": f"Bucket '{bucket}' deleted"}
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "BucketNotEmpty":
                return {
                    "status": "error",
                    "message": f"Bucket '{bucket}' is not empty; delete its objects first",
                }
            return {"status": "error", "message": f"Failed to delete bucket: {e}"}

    def put_object_text(self, bucket: str, key: str, content: str) -> Dict[str, Any]:
        self._require_unrestricted()
        resp = self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
        return {
            "status": "success",
            "message": f"Wrote {len(content.encode('utf-8'))} bytes to s3://{bucket}/{key}",
            "etag": (resp.get("ETag") or "").strip('"'),
        }

    def delete_object(self, bucket: str, key: str) -> Dict[str, Any]:
        self._require_unrestricted()
        self._client.delete_object(Bucket=bucket, Key=key)
        return {"status": "success", "message": f"Deleted s3://{bucket}/{key}"}

    def set_bucket_policy(self, bucket: str, policy: Any) -> Dict[str, Any]:
        self._require_unrestricted()
        policy_str = policy if isinstance(policy, str) else json.dumps(policy)
        # Validate it parses as JSON before sending.
        try:
            json.loads(policy_str)
        except Exception as e:
            return {"status": "error", "message": f"policy is not valid JSON: {e}"}
        self._client.put_bucket_policy(Bucket=bucket, Policy=policy_str)
        return {"status": "success", "message": f"Set bucket policy on '{bucket}'"}

    # ---------- Object copy / move / binary (S3 file browser) ----------

    def copy_object(
        self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
    ) -> Dict[str, Any]:
        """Server-side copy (no data leaves the store)."""
        self._require_unrestricted()
        self._client.copy_object(
            Bucket=dst_bucket,
            Key=dst_key,
            CopySource={"Bucket": src_bucket, "Key": src_key},
        )
        return {
            "status": "success",
            "message": f"Copied s3://{src_bucket}/{src_key} -> s3://{dst_bucket}/{dst_key}",
        }

    def move_object(
        self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
    ) -> Dict[str, Any]:
        """Move/rename = server-side copy then delete the source."""
        self._require_unrestricted()
        self._client.copy_object(
            Bucket=dst_bucket,
            Key=dst_key,
            CopySource={"Bucket": src_bucket, "Key": src_key},
        )
        self._client.delete_object(Bucket=src_bucket, Key=src_key)
        return {
            "status": "success",
            "message": f"Moved s3://{src_bucket}/{src_key} -> s3://{dst_bucket}/{dst_key}",
        }

    def put_object_bytes(
        self,
        bucket: str,
        key: str,
        content_b64: str,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload an object from base64-encoded bytes (browser file upload).
        Empty content + a key ending in '/' creates a folder placeholder."""
        self._require_unrestricted()
        try:
            raw = base64.b64decode(content_b64 or "", validate=False)
        except Exception as e:
            return {"status": "error", "message": f"content_b64 is not valid base64: {e}"}
        if len(raw) > DEFAULT_MAX_OBJECT_BYTES:
            return {
                "status": "error",
                "message": f"object is {len(raw)} bytes; exceeds the {DEFAULT_MAX_OBJECT_BYTES}-byte upload cap",
            }
        kwargs: Dict[str, Any] = {"Bucket": bucket, "Key": key, "Body": raw}
        if content_type:
            kwargs["ContentType"] = content_type
        resp = self._client.put_object(**kwargs)
        return {
            "status": "success",
            "message": f"Wrote {len(raw)} bytes to s3://{bucket}/{key}",
            "size": len(raw),
            "etag": (resp.get("ETag") or "").strip('"'),
        }

    def get_object_bytes(
        self, bucket: str, key: str, max_bytes: int = DEFAULT_MAX_OBJECT_BYTES
    ) -> Dict[str, Any]:
        """Download an object as base64-encoded bytes (browser download)."""
        head = self._client.head_object(Bucket=bucket, Key=key)
        size = head.get("ContentLength", 0)
        cap = max(1, min(int(max_bytes), DEFAULT_MAX_OBJECT_BYTES))
        if size > cap:
            return {
                "status": "error",
                "message": f"object is {size} bytes; exceeds the {cap}-byte download cap",
                "size": size,
            }
        resp = self._client.get_object(Bucket=bucket, Key=key)
        raw = resp["Body"].read()
        return {
            "status": "success",
            "bucket": bucket,
            "key": key,
            "size": len(raw),
            "content_type": resp.get("ContentType", "application/octet-stream"),
            "content_b64": base64.b64encode(raw).decode("ascii"),
        }

    # ---------- Bucket event notifications (S3 -> kubero event bus) ----------

    def set_bucket_notification(
        self,
        bucket: str,
        events: Optional[List[str]] = None,
        target_arn: str = "arn:rustfs:sqs::kubero:webhook",
        notif_id: str = "kubero",
    ) -> Dict[str, Any]:
        """Route the bucket's object events to a registered RustFS notification
        target (the kubero webhook). The target itself is registered out-of-band
        (the Tenant's spec.env); here we only set the per-bucket rule."""
        self._require_unrestricted()
        evs = events or ["s3:ObjectCreated:*"]
        try:
            self._client.put_bucket_notification_configuration(
                Bucket=bucket,
                NotificationConfiguration={
                    "QueueConfigurations": [
                        {"Id": notif_id, "QueueArn": target_arn, "Events": evs}
                    ]
                },
                SkipDestinationValidation=True,
            )
            return {
                "status": "success",
                "message": f"Bucket '{bucket}' will emit {evs} to {target_arn}",
            }
        except ClientError as e:
            return {"status": "error", "message": f"Failed to set notification: {e}"}

    def get_bucket_notification(self, bucket: str) -> Dict[str, Any]:
        resp = self._client.get_bucket_notification_configuration(Bucket=bucket)
        return {
            "status": "success",
            "bucket": bucket,
            "queue_configurations": resp.get("QueueConfigurations", []),
        }

    def remove_bucket_notification(self, bucket: str) -> Dict[str, Any]:
        self._require_unrestricted()
        self._client.put_bucket_notification_configuration(
            Bucket=bucket, NotificationConfiguration={}, SkipDestinationValidation=True
        )
        return {"status": "success", "message": f"Cleared notifications on '{bucket}'"}

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best effort
            pass


# Guidance content surfaced as MCP prompts.
S3_BEST_PRACTICES = """
RustFS / S3 best practices: use lowercase, DNS-safe bucket names (3-63 chars);
prefer many small prefixes over deep nesting for listing performance; set bucket
policies least-privilege; never store secrets unencrypted; lifecycle-expire
temporary objects; in-cluster RustFS is plain HTTP with path-style addressing
(virtual-host buckets are NOT supported).
"""
