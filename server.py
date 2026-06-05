#!/usr/bin/env python3
"""
RustFS MCP Server — a Model Context Protocol server for RustFS (S3) object
storage, using FastMCP.

stackblaze rustfs-mcp: each tool resolves its S3Manager per request (the target
endpoint + credentials + access mode arrive as HTTP headers from the kubero chat
broker — see s3_connection.get_manager). Write tools refuse under restricted mode.
The kubero broker prefixes these tools as `s3__*` in the chat.
"""

import json
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

from admin_utils import get_admin
from s3_connection import get_manager
from s3_utils import S3_BEST_PRACTICES, RestrictedError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rustfs-mcp-server")

mcp = FastMCP("RustFS MCP Server")


def _err(e: Exception) -> str:
    if isinstance(e, RestrictedError):
        return f"Refused: {e}"
    return f"Error: {e}"


def _json(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


# ---------- Read tools (available in restricted mode) ----------

@mcp.tool()
def list_buckets() -> str:
    """List all buckets in the RustFS (S3) object store for the selected add-on."""
    try:
        return _json(get_manager().list_buckets())
    except Exception as e:
        return _err(e)


@mcp.tool()
def list_objects(
    bucket: str,
    prefix: Optional[str] = None,
    max_keys: int = 100,
    delimiter: Optional[str] = None,
) -> str:
    """List objects in a bucket (optionally under a key prefix). Pass delimiter='/'
    for folder-aware listing (returns `prefixes` = sub-folders + leaf `objects`)."""
    try:
        return _json(get_manager().list_objects(bucket, prefix, max_keys, delimiter))
    except Exception as e:
        return _err(e)


@mcp.tool()
def stat_object(bucket: str, key: str) -> str:
    """Get an object's metadata (size, content-type, etag, last-modified, user metadata)."""
    try:
        return _json(get_manager().stat_object(bucket, key))
    except Exception as e:
        return _err(e)


@mcp.tool()
def bucket_usage(bucket: str) -> str:
    """Total object count + byte size of a bucket (paginates the whole bucket)."""
    try:
        return _json(get_manager().bucket_usage(bucket))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_object_text(bucket: str, key: str, max_bytes: int = 65536) -> str:
    """Preview an object's content as UTF-8 text (capped; binary objects are rejected)."""
    try:
        return _json(get_manager().get_object_text(bucket, key, max_bytes))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_bucket_policy(bucket: str) -> str:
    """Get the bucket policy JSON (null if none is set)."""
    try:
        return _json(get_manager().get_bucket_policy(bucket))
    except Exception as e:
        return _err(e)


# ---------- Write tools (refused under restricted mode) ----------

@mcp.tool()
def create_bucket(bucket: str) -> str:
    """Create a new bucket. (Refused on read-only / production add-ons.)"""
    try:
        return _json(get_manager().create_bucket(bucket))
    except Exception as e:
        return _err(e)


@mcp.tool()
def delete_bucket(bucket: str) -> str:
    """Delete an empty bucket. (Refused on read-only / production add-ons.)"""
    try:
        return _json(get_manager().delete_bucket(bucket))
    except Exception as e:
        return _err(e)


@mcp.tool()
def put_object_text(bucket: str, key: str, content: str) -> str:
    """Write a UTF-8 text object. (Refused on read-only / production add-ons.)"""
    try:
        return _json(get_manager().put_object_text(bucket, key, content))
    except Exception as e:
        return _err(e)


@mcp.tool()
def delete_object(bucket: str, key: str) -> str:
    """Delete a single object. (Refused on read-only / production add-ons.)"""
    try:
        return _json(get_manager().delete_object(bucket, key))
    except Exception as e:
        return _err(e)


@mcp.tool()
def set_bucket_policy(bucket: str, policy: str) -> str:
    """Set a bucket policy (JSON string). (Refused on read-only / production add-ons.)"""
    try:
        return _json(get_manager().set_bucket_policy(bucket, policy))
    except Exception as e:
        return _err(e)


# ---------- Object copy / move / binary (S3 file browser) ----------

@mcp.tool()
def copy_object(src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> str:
    """Server-side copy an object. (Refused on read-only / production add-ons.)"""
    try:
        return _json(get_manager().copy_object(src_bucket, src_key, dst_bucket, dst_key))
    except Exception as e:
        return _err(e)


@mcp.tool()
def move_object(src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> str:
    """Move/rename an object (server-side copy + delete source). (Refused read-only.)"""
    try:
        return _json(get_manager().move_object(src_bucket, src_key, dst_bucket, dst_key))
    except Exception as e:
        return _err(e)


@mcp.tool()
def put_object_bytes(
    bucket: str, key: str, content_b64: str, content_type: Optional[str] = None
) -> str:
    """Upload an object from base64 bytes (browser file upload; ~10MiB cap). Empty
    content + a key ending in '/' creates a folder. (Refused read-only.)"""
    try:
        return _json(get_manager().put_object_bytes(bucket, key, content_b64, content_type))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_object_bytes(bucket: str, key: str, max_bytes: int = 10 * 1024 * 1024) -> str:
    """Download an object as base64 bytes (browser download; ~10MiB cap)."""
    try:
        return _json(get_manager().get_object_bytes(bucket, key, max_bytes))
    except Exception as e:
        return _err(e)


# ---------- IAM / access keys (admin API) ----------

@mcp.tool()
def list_users() -> str:
    """List IAM users in the RustFS tenant."""
    try:
        return _json(get_admin().list_users())
    except Exception as e:
        return _err(e)


@mcp.tool()
def list_access_keys(user: Optional[str] = None) -> str:
    """List service-account access keys for a user (defaults to the root user)."""
    try:
        return _json(get_admin().list_access_keys(user))
    except Exception as e:
        return _err(e)


@mcp.tool()
def add_access_key(
    user: Optional[str] = None,
    policy: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> str:
    """Create a scoped access key (service account) for a user. (Refused read-only.)"""
    try:
        return _json(
            get_admin().add_access_key(
                user=user,
                policy=policy,
                name=name,
                description=description,
                access_key=access_key,
                secret_key=secret_key,
            )
        )
    except Exception as e:
        return _err(e)


@mcp.tool()
def delete_access_key(access_key: str) -> str:
    """Delete a service-account access key. (Refused read-only.)"""
    try:
        return _json(get_admin().delete_access_key(access_key))
    except Exception as e:
        return _err(e)


@mcp.tool()
def list_policies() -> str:
    """List canned IAM policies available in RustFS."""
    try:
        return _json(get_admin().list_policies())
    except Exception as e:
        return _err(e)


# ---------- Bucket event notifications (write) ----------


@mcp.tool()
def set_bucket_notification(
    bucket: str,
    events: Optional[list] = None,
    target_arn: str = "arn:rustfs:sqs::kubero:webhook",
) -> str:
    """Emit this bucket's object events to the kubero event bus. `events` defaults
    to ['s3:ObjectCreated:*']. (Refused on read-only / production add-ons.)"""
    try:
        return _json(get_manager().set_bucket_notification(bucket, events, target_arn))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_bucket_notification(bucket: str) -> str:
    """Show the bucket's event-notification rules (which events → which target)."""
    try:
        return _json(get_manager().get_bucket_notification(bucket))
    except Exception as e:
        return _err(e)


@mcp.tool()
def remove_bucket_notification(bucket: str) -> str:
    """Stop emitting this bucket's events. (Refused on read-only / production add-ons.)"""
    try:
        return _json(get_manager().remove_bucket_notification(bucket))
    except Exception as e:
        return _err(e)


# ---------- Prompts ----------

@mcp.prompt()
def s3_best_practices() -> str:
    """Provide RustFS / S3 object-storage best-practices guidance."""
    return S3_BEST_PRACTICES


if __name__ == "__main__":
    mcp.run()
