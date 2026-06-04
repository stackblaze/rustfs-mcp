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
def list_objects(bucket: str, prefix: Optional[str] = None, max_keys: int = 100) -> str:
    """List objects in a bucket (optionally under a key prefix), newest page first."""
    try:
        return _json(get_manager().list_objects(bucket, prefix, max_keys))
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


# ---------- Prompts ----------

@mcp.prompt()
def s3_best_practices() -> str:
    """Provide RustFS / S3 object-storage best-practices guidance."""
    return S3_BEST_PRACTICES


if __name__ == "__main__":
    mcp.run()
