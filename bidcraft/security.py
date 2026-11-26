"""Password hashing, session tokens and secret encryption.

Only the Python standard library plus ``cryptography`` are used. There is no
external identity provider and no telemetry.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_PREFIX = "scrypt"


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("密码不能为空")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "$".join(
        (
            _PREFIX,
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        prefix, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if prefix != _PREFIX:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _key_path() -> Path:
    return settings.home / "secret.key"


def _fernet_key() -> bytes:
    """Fernet 密钥字节串。

    默认来自 ``BIDCRAFT_HOME/secret.key``（0600）。多实例 PostgreSQL 部署没有共享
    文件卷时，统一设置 ``BIDCRAFT_SECRET_KEY``（32 字节密钥的 base64 编码）即可让
    所有进程共用同一把主密钥；否则加密数据/签名状态在实例间互不相认。
    """

    env_key = (settings.secret_key or "").strip()
    if env_key:
        try:
            decoded = base64.urlsafe_b64decode(env_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("BIDCRAFT_SECRET_KEY 必须是 32 字节 Fernet 密钥的 base64 编码") from exc
        if len(decoded) != 32:
            raise ValueError("BIDCRAFT_SECRET_KEY 必须是 32 字节 Fernet 密钥的 base64 编码")
        return env_key.encode("ascii")
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_bytes().strip()
    raw = Fernet.generate_key()
    # O_EXCL avoids two workers racing to create different keys.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return raw


def _fernet() -> Fernet:
    return Fernet(_fernet_key())


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def _signing_key() -> bytes:
    return hashlib.sha256(_fernet_key()).digest()


def sign_state(payload: dict, ttl_seconds: int = 600) -> str:
    import json
    import time

    body = {**payload, "exp": int(time.time()) + ttl_seconds}
    encoded = base64.urlsafe_b64encode(
        json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(_signing_key(), encoded, hashlib.sha256).digest()
    return encoded.decode("ascii") + "." + base64.urlsafe_b64encode(signature).decode("ascii")


def verify_state(token: str) -> dict | None:
    import json
    import time

    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(_signing_key(), encoded.encode("ascii"), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature.encode("ascii"))
        if not hmac.compare_digest(expected, actual):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


REDACTED = "***REDACTED***"

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"authorization|credential|private[_-]?key|cookie|session|otp|mfa)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;}\"']{6,}"),
    re.compile(r"(?i)(access[_-]?token\s*[:=]\s*)[^\s,;}\"']{6,}"),
    re.compile(r"(?i)(password\s*[:=]\s*)[^\s,;}\"']{4,}"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)[^\s,;}\"']{6,}"),
    re.compile(r"\bsk-[a-z0-9_-]{12,}\b", re.IGNORECASE),
)


def redact_text(value: str) -> str:
    """遮蔽文本中的 Bearer/API Key/密码等敏感片段；保留非敏感内容。"""

    text = str(value or "")

    def replace(match: re.Match[str]) -> str:
        if match.lastindex:
            return match.group(1) + REDACTED
        return REDACTED

    for pattern in _SENSITIVE_VALUE_PATTERNS:
        text = pattern.sub(replace, text)
    return text


def redact_mapping(value: Any, *, depth: int = 0) -> Any:
    """递归遮蔽映射中的敏感键值；执行器仍拿到原始参数，只有轨迹被脱敏。"""

    if depth > 6:
        return REDACTED
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            redacted[name] = (
                REDACTED if _SENSITIVE_KEY_RE.search(name) else redact_mapping(item, depth=depth + 1)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_mapping(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
