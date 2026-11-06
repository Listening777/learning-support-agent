# -*- coding: utf-8 -*-
"""
安全工具：密码哈希（PBKDF2）+ JWT（HS256 自实现，零第三方依赖）。
JWT payload: {uid, role, name, exp}
"""
import hmac
import hashlib
import base64
import json
import time
import secrets
from .. import config


# ───────────────── 密码：PBKDF2-SHA256（带盐） ─────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_hex(8)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


# ───────────────── JWT：HS256 手工实现（header.payload.signature） ─────────────────
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def make_jwt(payload: dict) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = dict(payload)
    body["exp"] = int(time.time()) + config.JWT_EXPIRE_HOURS * 3600
    payload_b64 = _b64(json.dumps(body, ensure_ascii=False).encode())
    sig = hmac.new(config.JWT_SECRET.encode(),
                   f"{header}.{payload_b64}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload_b64}.{_b64(sig)}"


def verify_jwt(token: str) -> dict | None:
    """校验失败返回 None（过期/签名错误/格式错误统一处理）。"""
    try:
        header, payload_b64, sig = token.split(".")
        expected = hmac.new(config.JWT_SECRET.encode(),
                            f"{header}.{payload_b64}".encode(),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig)):
            return None
        payload = json.loads(_b64d(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, json.JSONDecodeError):
        return None
