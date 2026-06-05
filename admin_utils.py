#!/usr/bin/env python3
"""RustFS admin API helpers (IAM users, access keys / service accounts)."""

import json
import logging
import secrets
from typing import Any, Dict, Optional

from minio import minioadmin
from minio.credentials import StaticProvider
from minio.error import MinioAdminException

from s3_connection import get_manager
from s3_utils import RestrictedError

logger = logging.getLogger("rustfs-admin")


def _host_port() -> tuple[str, bool]:
    mgr = get_manager()
    endpoint = mgr.endpoint
    secure = endpoint.startswith("https://")
    host = endpoint.split("://", 1)[-1]
    return host, secure


def _admin() -> minioadmin.MinioAdmin:
    mgr = get_manager()
    host, secure = _host_port()
    # minio >=7.2.17: MinioAdmin is keyword-only (endpoint=..., credentials=...).
    return minioadmin.MinioAdmin(
        endpoint=host,
        credentials=StaticProvider(mgr.access_key, mgr.secret_key),
        secure=secure,
    )


def _ok(payload: Any) -> Dict[str, Any]:
    return {"status": "success", **payload} if isinstance(payload, dict) else {"status": "success", "data": payload}


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class RustfsAdmin:
    def list_users(self) -> Dict[str, Any]:
        try:
            raw = _admin().user_list()
            return _ok({"users": _parse_json_text(raw)})
        except MinioAdminException as e:
            return {"status": "error", "message": str(e)}

    def list_access_keys(self, user: Optional[str] = None) -> Dict[str, Any]:
        try:
            admin = _admin()
            parent = user or get_manager().access_key
            raw = admin.list_service_account(parent)
            parsed = _parse_json_text(raw)
            keys = []
            if isinstance(parsed, dict):
                for ak, info in parsed.items():
                    row = info if isinstance(info, dict) else {"info": info}
                    keys.append({"access_key": ak, **row})
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
        try:
            raw = _admin().add_service_account(
                access_key=ak,
                secret_key=sk,
                policy=policy_doc,
                name=name,
                description=description,
            )
            parsed = _parse_json_text(raw)
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
            raw = _admin().policy_list()
            return _ok({"policies": _parse_json_text(raw)})
        except MinioAdminException as e:
            return {"status": "error", "message": str(e)}


def get_admin() -> RustfsAdmin:
    return RustfsAdmin()
