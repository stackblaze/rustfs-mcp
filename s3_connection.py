#!/usr/bin/env python3
"""
Connection-from-request mode (stackblaze rustfs-mcp).

So ONE long-lived Deployment can serve every RustFS (S3) add-on, the target
endpoint + credentials + access mode arrive per request as HTTP headers set by
the kubero chat broker:

  X-Kubero-DB-URI       = s3://<accesskey>:<secretkey>@<host>:<port>
  X-Kubero-Access-Mode  = restricted | unrestricted

(the creds are URL-encoded by the broker). Read via the lowlevel request_ctx
contextvar so tools need no Context plumbing. On a transport with no HTTP request
(stdio) this is off and the S3_ENDPOINT_URL / AWS_* env vars are used.

In-cluster the RustFS S3 service is plain HTTP and requires path-style
addressing — both are baked into the boto3 client here.
"""

import os
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit

from s3_utils import S3Manager

connection_from_request = os.getenv("S3_CONNECTION_FROM_REQUEST", "") in (
    "1",
    "true",
    "t",
)

# The broker's generic connection-URI header carries `s3://ak:sk@host:port`.
HDR_URI = "x-kubero-db-uri"
HDR_ACCESS_MODE = "x-kubero-access-mode"

try:
    from mcp.server.lowlevel.server import request_ctx as _request_ctx
except Exception:  # pragma: no cover - defensive
    _request_ctx = None

# Per-(uri, restricted) manager cache so repeated requests reuse the boto3 client.
_managers: dict[str, S3Manager] = {}


def _request_headers():
    if _request_ctx is None:
        return None
    try:
        rc = _request_ctx.get()
    except LookupError:
        return None
    req = getattr(rc, "request", None)
    if req is None:
        return None
    return getattr(req, "headers", None)


def _request_target() -> Tuple[Optional[str], bool]:
    """(s3_uri, restricted) from the per-request headers. Missing/invalid access
    mode => restricted (fail safe)."""
    h = _request_headers()
    if not h:
        return None, True
    uri = h.get(HDR_URI)
    mode = (h.get(HDR_ACCESS_MODE) or "restricted").lower()
    return (uri.strip() if uri else None), mode != "unrestricted"


def _manager_for(uri: str, restricted: bool) -> S3Manager:
    key = f"{uri}|{restricted}"
    m = _managers.get(key)
    if m is None:
        if len(_managers) >= 32:  # bound: evict an arbitrary entry
            old = next(iter(_managers))
            try:
                _managers.pop(old).close()
            except Exception:  # pragma: no cover - best effort
                pass
        m = S3Manager.from_uri(uri, restricted)
        _managers[key] = m
    return m


def _from_request_enabled() -> bool:
    # Read live so a CLI flag that sets the env after import still takes effect.
    return connection_from_request or os.getenv(
        "S3_CONNECTION_FROM_REQUEST", ""
    ) in ("1", "true", "t")


def get_manager() -> S3Manager:
    """Resolve the S3Manager for the current request (or the env fallback)."""
    if _from_request_enabled():
        uri, restricted = _request_target()
        if not uri:
            raise ValueError(
                f"connection-from-request mode: missing {HDR_URI} header"
            )
        return _manager_for(uri, restricted)
    # stdio / local fallback: a single endpoint from env, full access.
    endpoint = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
    ak = os.getenv("AWS_ACCESS_KEY_ID", "")
    sk = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    # Re-encode as an s3:// uri so from_uri is the single parse path.
    host = endpoint.split("://", 1)[-1]
    return _manager_for(f"s3://{ak}:{sk}@{host}", False)


def parse_s3_uri(uri: str) -> Tuple[str, str, str]:
    """`s3://<ak>:<sk>@<host>:<port>` -> (endpoint_url, access_key, secret_key).

    The host is reached over plain HTTP in-cluster. Credentials are URL-decoded
    (the broker encodeURIComponent-s them)."""
    parts = urlsplit(uri)
    if parts.scheme not in ("s3", "http", "https"):
        raise ValueError(f"unsupported S3 URI scheme: {parts.scheme!r}")
    host = parts.hostname or "localhost"
    port = parts.port or 9000
    ak = unquote(parts.username or "")
    sk = unquote(parts.password or "")
    endpoint = f"http://{host}:{port}"
    return endpoint, ak, sk
