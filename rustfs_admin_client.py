#!/usr/bin/env python3
"""RustFS-native admin HTTP client (plain JSON on /rustfs/admin/v3/).

MinIO's Python MinioAdmin expects encrypted responses on /minio/admin/v3/ with
AEAD IDs 0/1. RustFS returns plaintext JSON on /rustfs/admin/v3/ — using
minio-py's decrypt path fails with "Unknown AEAD ID 2".
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import urlunsplit

from minio import time as mtime
from minio.credentials import StaticProvider
from minio.error import MinioAdminException
from minio.helpers import _parse_url, queryencode, sha256_hash, url_replace
from minio.signer import sign_v4_s3
from urllib3._collections import HTTPHeaderDict
from urllib3.poolmanager import PoolManager

_ADMIN_PREFIX = "/rustfs/admin/v3"


class RustfsAdminClient:
    def __init__(self, endpoint_host: str, access_key: str, secret_key: str, *, secure: bool = False):
        scheme = "https" if secure else "http"
        self._base = _parse_url(f"{scheme}://{endpoint_host}")
        self._provider = StaticProvider(access_key, secret_key)
        self._http = PoolManager(cert_reqs="CERT_REQUIRED" if secure else "CERT_NONE")

    def _request(
        self,
        method: str,
        command: str,
        *,
        query: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        content_type: str = "application/json",
    ) -> Any:
        url = url_replace(url=self._base, path=f"{_ADMIN_PREFIX}/{command}")
        if query:
            q = "&".join(f"{queryencode(k)}={queryencode(v)}" for k, v in sorted(query.items()))
            url = url_replace(url=url, query=q)

        creds = self._provider.retrieve()
        payload = body or b""
        content_sha256 = sha256_hash(payload)
        date = mtime.utcnow()
        headers: Dict[str, str] = {
            "Host": url.netloc,
            "User-Agent": "rustfs-mcp-admin",
            "x-amz-date": mtime.to_amz_date(date),
            "x-amz-content-sha256": content_sha256,
            "Content-Type": content_type,
        }
        if creds.session_token:
            headers["X-Amz-Security-Token"] = creds.session_token
        if payload:
            headers["Content-Length"] = str(len(payload))

        headers = sign_v4_s3(
            method=method,
            url=url,
            region="",
            headers=headers,
            credentials=creds,
            content_sha256=content_sha256,
            date=date,
        )

        http_headers = HTTPHeaderDict()
        for key, value in headers.items():
            http_headers.add(key, value)

        response = self._http.request(method, urlunsplit(url), body=payload or None, headers=http_headers)
        data = response.data or b""
        if response.status >= 400:
            msg = data.decode("utf-8", errors="replace") or f"HTTP {response.status}"
            raise MinioAdminException(msg, response.status)

        if not data:
            return {}
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return {"raw": data.decode("utf-8", errors="replace")}

    def list_users(self) -> Dict[str, Any]:
        return self._request("GET", "list-users")

    def list_policies(self) -> Dict[str, Any]:
        return self._request("GET", "list-canned-policies")

    def list_service_accounts(self, user: str) -> Dict[str, Any]:
        return self._request("GET", "list-service-accounts", query={"user": user})

    def add_service_account(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._request("PUT", "add-service-account", body=body)

    def delete_service_account(self, access_key: str) -> None:
        self._request("DELETE", "delete-service-account", query={"accessKey": access_key})
