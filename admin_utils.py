#!/usr/bin/env python3
"""RustFS admin API helpers (IAM users, access keys / service accounts)."""

import json
import logging
import secrets
from typing import Any, Dict, Optional

from minio.error import MinioAdminException

from rustfs_admin_client import RustfsAdminClient
from s3_connection import get_manager
from s3_utils import RestrictedError

logger = logging.getLogger("rustfs-admin")


def _host_port() -> tuple[str, bool]:
    mgr = get_manager()
    endpoint = mgr.endpoint
    secure = endpoint.startswith("https://")
    host = endpoint.split("://", 1)[-1]
    return host, secure


def _admin() -> RustfsAdminClient:
    mgr = get_manager()
    host, secure = _host_port()
    return RustfsAdminClient(host, mgr.access_key, mgr.secret_key, secure=secure)


def _ok(payload: Any) -> Dict[str, Any]:
    return {"status": "success", **payload} if isinstance(payload, dict) else {"status": "success", "data": payload}


class RustfsAdmin:
    def list_users(self) -> Dict[str, Any]:
        try:
            return _ok({"users": _admin().list_users()})
        except MinioAdminException as e:
            return {"status": "error", "message": str(e)}

    def list_access_keys(self, user: Optional[str] = None) -> Dict[str, Any]:
        try:
            parent = user or get_manager().access_key
            parsed = _admin().list_service_accounts(parent)
            keys = []
            for row in parsed.get("accounts") or []:
                if not isinstance(row, dict):
                    continue
                keys.append(
                    {
                        "access_key": row.get("accessKey") or row.get("access_key") or "",
                        "name": row.get("name"),
                        "description": row.get("description"),
                        "status": row.get("accountStatus") or row.get("status"),
                    }
                )
            return _ok({"access_keys": keys, "parent_user": parent})
        except MinioAdminException as e:
            return {"status": "error", "message": str(e)}

    def add_access_key(
        self,
        user: Optional[str] = None,
        policy: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        mgr = get_manager()
        mgr._require_unrestricted()
        parent = user or mgr.access_key
        ak = access_key or secrets.token_hex(10)
        sk = secret_key or secrets.token_hex(20)
        policy_doc = None
        if policy:
            policy_doc = json.loads(policy) if isinstance(policy, str) else policy
        payload: Dict[str, Any] = {
            "accessKey": ak,
            "secretKey": sk,
        }
        if policy_doc is not None:
            payload["policy"] = policy_doc
        if name:
            payload["name"] = name
        if description:
            payload["description"] = description
        try:
            parsed = _admin().add_service_account(payload)
            creds = parsed.get("credentials", parsed) if isinstance(parsed, dict) else parsed
            return _ok(
                {
                    "access_key": creds.get("accessKey", ak) if isinstance(creds, dict) else ak,
                    "secret_key": creds.get("secretKey", sk) if isinstance(creds, dict) else sk,
                    "parent_user": parent,
                    "raw": parsed,
                }
            )
        except MinioAdminException as e:
            return {"status": "error", "message": str(e)}

    def delete_access_key(self, access_key: str) -> Dict[str, Any]:
        get_manager()._require_unrestricted()
        if not access_key:
            return {"status": "error", "message": "access_key is required"}
        try:
            _admin().delete_service_account(access_key)
            return _ok({"message": f"Deleted access key '{access_key}'"})
        except MinioAdminException as e:
            return {"status": "error", "message": str(e)}

    def list_policies(self) -> Dict[str, Any]:
        try:
            return _ok({"policies": _admin().list_policies()})
        except MinioAdminException as e:
            return {"status": "error", "message": str(e)}


def get_admin() -> RustfsAdmin:
    return RustfsAdmin()
