from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, HTTPException


class OriginGuard:
    def __init__(self, token: str, origin: str = "http://tauri.localhost"):
        self._token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        self.origin = origin

    def check(
        self,
        authorization: str | None = Header(default=None),
        origin: str | None = Header(default=None),
    ):
        if origin != self.origin:
            raise HTTPException(
                403,
                detail={
                    "code": "SECURITY_ORIGIN_REJECTED",
                    "user_message": "来源被拒绝。",
                    "recoverable": False,
                },
            )
        if (
            not authorization
            or not authorization.startswith("Bearer ")
            or not hmac.compare_digest(
                hashlib.sha256(authorization[7:].encode("utf-8")).digest(), self._token_digest
            )
        ):
            raise HTTPException(
                401,
                detail={
                    "code": "SECURITY_AUTH_REQUIRED",
                    "user_message": "认证失败。",
                    "recoverable": False,
                },
            )


class HostControlGuard:
    """Separates native file-picker authority from the WebView bearer session."""

    def __init__(self, token: str):
        self._token_digest = hashlib.sha256(token.encode("utf-8")).digest()

    def check(self, x_host_control_token: str | None = Header(default=None)):
        if not x_host_control_token or not hmac.compare_digest(
            hashlib.sha256(x_host_control_token.encode("utf-8")).digest(), self._token_digest
        ):
            raise HTTPException(
                403,
                detail={
                    "code": "SECURITY_CAPABILITY_INVALID",
                    "user_message": "Native file authority is required.",
                    "recoverable": False,
                },
            )
