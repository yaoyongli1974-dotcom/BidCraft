"""插件包（``.bidplugin``）的受控导出与导入。

一个插件包就是一个 zip，里面固定放一份 ``manifest.json``：插件的可移植配置
（传输方式、command/args/env/url、工具白名单、沙箱限额）。包里**不含**任何
运行态数据（测试结果、失败计数、密钥以外的东西一律不导出），导入端按同一套
校验规则规范化后落库，同名插件按 ``on_conflict`` 决定覆盖还是拒绝。

边界：

* 包体上限 :data:`MAX_PACKAGE_BYTES`，超限直接拒绝，避免内存/磁盘放大；
* manifest 只认 :data:`FORMAT` 与 ``version <= FORMAT_VERSION``；
* 校验失败一律抛 ``ValueError``（API 转 400），同名且 skip 抛
  :class:`PluginPackConflict`（API 转 409）。

可选签名：manifest 里可带 ``signature``（Ed25519）。导出端配置了签名私钥就自动
签名；导入端配置了信任公钥后会自动验签，签名存在但验签失败一律拒绝。未签名的
包默认仍可导入（兼容既有部署），需要强制签名可设 ``BIDCRAFT_PLUGIN_REQUIRE_SIGNATURE=1``。

只有超级管理员能导入：插件会以当前系统用户身份启动子进程，导入即安装。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import select

from . import plugins
from .config import settings
from .models import Plugin, User

FORMAT = "bidcraft-plugin"
FORMAT_VERSION = 1
MAX_PACKAGE_BYTES = 5_000_000
#: zip 内 manifest 的固定路径。
MANIFEST_PATH = "manifest.json"

#: 导入比在线表单更紧：插件包是外部输入，超限一律拒绝而不是静默截断。
MAX_TEXT_CHARS = 500
MAX_ARGS = 20
MAX_ARG_CHARS = 200
MAX_ENV_KEYS = 40
MAX_ENV_KEY_CHARS = 60
MAX_ENV_VALUE_CHARS = 200
MAX_ALLOWLIST = 200
MAX_ALLOWLIST_CHARS = 120


class PluginPackConflict(ValueError):
    """同名插件已存在且 ``on_conflict="skip"``；API 转 409。"""


class PluginPackSignatureError(ValueError):
    """签名缺失/非法/验签失败；API 转 400。"""


SIGNATURE_KEY = "signature"
SIGNATURE_ALG = "ed25519"
#: 私钥：环境变量（PEM 文本或文件路径）优先，其次 settings.home 下的固定文件。
PRIVATE_KEY_ENV = "BIDCRAFT_PLUGIN_SIGNING_KEY"
PRIVATE_KEY_FILE = "plugin-signing-key.pem"
#: 信任公钥：逗号分隔的 base64 原始公钥或 PEM，也可放 settings.home 下 JSON 数组。
TRUSTED_KEYS_ENV = "BIDCRAFT_PLUGIN_TRUSTED_KEYS"
TRUSTED_KEYS_FILE = "plugin-trusted-keys.json"
REQUIRE_SIGNATURE_ENV = "BIDCRAFT_PLUGIN_REQUIRE_SIGNATURE"


def require_signature() -> bool:
    """是否强制要求包带有效签名（默认关闭以兼容既有导出包）。"""
    return os.getenv(REQUIRE_SIGNATURE_ENV, "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _key_material(value: str) -> bytes:
    """环境变量值既可是 PEM 文本，也可是密钥文件路径。"""
    text = (value or "").strip()
    if not text:
        return b""
    if "PRIVATE KEY" in text or "PUBLIC KEY" in text:
        return text.encode()
    candidate = Path(text).expanduser()
    try:
        return candidate.read_bytes() if candidate.is_file() else text.encode()
    except OSError:
        return text.encode()


def signing_private_key() -> Ed25519PrivateKey | None:
    """读取导出签名私钥；未配置返回 None（包保持无签名）。"""
    material = _key_material(os.getenv(PRIVATE_KEY_ENV, ""))
    if not material:
        try:
            material = (settings.home / PRIVATE_KEY_FILE).read_bytes()
        except OSError:
            return None
    try:
        key = serialization.load_pem_private_key(material, password=None)
    except (TypeError, ValueError):
        return None
    return key if isinstance(key, Ed25519PrivateKey) else None


def _public_key_from_text(value: str) -> Ed25519PublicKey | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        if "PUBLIC KEY" in text:
            key = serialization.load_pem_public_key(text.encode())
        else:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(text, validate=True))
        return key if isinstance(key, Ed25519PublicKey) else None
    except (ValueError, TypeError, InvalidSignature):
        return None


def trusted_public_keys() -> list[Ed25519PublicKey]:
    """读取导入端信任的公钥列表。"""
    values: list[str] = [
        item.strip()
        for item in os.getenv(TRUSTED_KEYS_ENV, "").split(",")
        if item.strip()
    ]
    try:
        payload = json.loads((settings.home / TRUSTED_KEYS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = []
    if isinstance(payload, list):
        values.extend(str(item) for item in payload if str(item).strip())
    keys = [key for item in values if (key := _public_key_from_text(item)) is not None]
    return keys


def _canonical_bytes(manifest: dict[str, Any]) -> bytes:
    """签名载荷：去掉 signature 后的规范化 JSON（与缩进/键顺序无关）。"""
    payload = {key: value for key, value in manifest.items() if key != SIGNATURE_KEY}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """配置签名私钥时给 manifest 加签名；未配置则原样返回（向后兼容）。"""
    key = signing_private_key()
    if key is None:
        return manifest
    signature = key.sign(_canonical_bytes(manifest))
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {
        **manifest,
        SIGNATURE_KEY: {
            "alg": SIGNATURE_ALG,
            "key_id": hashlib.sha256(public).hexdigest()[:12],
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_manifest_signature(
    manifest: dict[str, Any], *, require: bool | None = None
) -> str:
    """校验 manifest 上的 Ed25519 签名，返回 ``signed`` / ``unsigned``。

    签名存在但格式错误、无信任公钥或验签失败一律抛
    :class:`PluginPackSignatureError`；无签名且 ``require=True`` 同样拒绝。
    """
    signature = manifest.get(SIGNATURE_KEY)
    if signature is None:
        if require is None:
            require = require_signature()
        if require:
            raise PluginPackSignatureError("插件包缺少签名，部署要求必须验签")
        return "unsigned"
    if not isinstance(signature, dict) or signature.get("alg") != SIGNATURE_ALG:
        raise PluginPackSignatureError("插件包签名格式不受支持")
    try:
        raw = base64.b64decode(str(signature.get("value") or ""), validate=True)
    except (ValueError, TypeError):
        raise PluginPackSignatureError("插件包签名值非法") from None
    keys = trusted_public_keys()
    if not keys:
        raise PluginPackSignatureError("插件包带签名，但未配置信任公钥，无法验签")
    data = _canonical_bytes(manifest)
    for key in keys:
        try:
            key.verify(raw, data)
            return "signed"
        except InvalidSignature:
            continue
    raise PluginPackSignatureError("插件包签名验证失败（manifest 可能被篡改）")


def build_manifest(row: Any) -> dict[str, Any]:
    """把插件行序列化成可移植 manifest（含导出时间）。"""
    ref = plugins.snapshot(row)
    return {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "name": ref.name,
        "transport": ref.transport,
        "command": ref.command,
        "args": list(ref.args),
        "env": dict(ref.env),
        "url": ref.url,
        "enabled": bool(ref.enabled),
        "tool_allowlist": list(ref.tool_allowlist),
        "mem_limit_mb": ref.mem_limit_mb,
        "cpu_seconds": ref.cpu_seconds,
        "scope": ref.scope,
        "owner_id": ref.owner_id,
        "allow_private_net": ref.allow_private_net,
        "exported_at": datetime.now(UTC).isoformat(),
    }


def export_package(row: Any) -> bytes:
    """导出 zip 字节流（内含 ``manifest.json``，配置私钥时自动签名）。"""
    manifest = sign_manifest(build_manifest(row))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            MANIFEST_PATH,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    return buffer.getvalue()


def parse_package(
    data: bytes, *, require_signature: bool | None = None
) -> dict[str, Any]:
    """校验包体并取出 manifest；任何非法都抛 ``ValueError``（中文消息）。"""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("插件包必须是二进制内容")  # noqa: TRY004 - 契约统一 ValueError→400
    raw = bytes(data)
    if not raw:
        raise ValueError("插件包为空")
    if len(raw) > MAX_PACKAGE_BYTES:
        raise ValueError("插件包过大（超过 5MB）")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            if MANIFEST_PATH not in bundle.namelist():
                raise ValueError(f"插件包缺少 {MANIFEST_PATH}")
            content = bundle.read(MANIFEST_PATH)
    except zipfile.BadZipFile:
        raise ValueError("插件包不是有效的 zip 文件") from None
    if len(content) > MAX_PACKAGE_BYTES:
        raise ValueError("manifest.json 过大")
    try:
        manifest = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("manifest.json 不是有效的 JSON") from None
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json 必须是 JSON 对象")  # noqa: TRY004 - 同上
    if manifest.get("format") != FORMAT:
        raise ValueError(f"manifest format 必须是 {FORMAT}")
    version = manifest.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("manifest version 必须是整数")  # noqa: TRY004 - 同上
    if not 1 <= version <= FORMAT_VERSION:
        raise ValueError(f"manifest version 不受支持（当前支持 1..{FORMAT_VERSION}）")
    verify_manifest_signature(manifest, require=require_signature)
    return manifest


def validate_manifest(m: Any) -> dict[str, Any]:
    """规范化 manifest 并返回可直接落库的字段；非法抛 ``ValueError``。

    ``note``/``exported_at`` 等未知键忽略；沙箱限额用 ``plugins.clamp_*``
    收敛（越界回退默认值，而不是 400）。
    """
    if not isinstance(m, dict):
        raise ValueError("manifest 必须是 JSON 对象")  # noqa: TRY004 - 同上
    name = plugins.plugin_name(m.get("name"))
    transport = plugins.plugin_transport(m.get("transport"))
    scope = plugins.plugin_scope(m.get("scope"))
    owner_id = plugins.plugin_owner_id(m.get("owner_id")) if scope == "owner" else ""
    allow_private_net = bool(m.get("allow_private_net"))
    command = str(m.get("command") or "").strip()
    url = str(m.get("url") or "").strip()
    if len(command) > MAX_TEXT_CHARS:
        raise ValueError(f"command 不能超过 {MAX_TEXT_CHARS} 个字符")
    if len(url) > MAX_TEXT_CHARS:
        raise ValueError(f"url 不能超过 {MAX_TEXT_CHARS} 个字符")
    plugins.plugin_validate(transport, command, url, allow_private_net=allow_private_net)

    args = plugins.plugin_items(
        m.get("args"), field="args", limits=MAX_ARGS + 1, item_max=None
    )
    if len(args) > MAX_ARGS:
        raise ValueError(f"args 不能超过 {MAX_ARGS} 项")
    if any(len(item) > MAX_ARG_CHARS for item in args):
        raise ValueError(f"args 每项不能超过 {MAX_ARG_CHARS} 个字符")

    env = plugins.plugin_env(m.get("env"))
    if len(env) > MAX_ENV_KEYS:
        raise ValueError(f"env 不能超过 {MAX_ENV_KEYS} 个键")
    if any(len(key) > MAX_ENV_KEY_CHARS for key in env):
        raise ValueError(f"env 键不能超过 {MAX_ENV_KEY_CHARS} 个字符")
    if any(len(value) > MAX_ENV_VALUE_CHARS for value in env.values()):
        raise ValueError(f"env 值不能超过 {MAX_ENV_VALUE_CHARS} 个字符")

    allowlist = plugins.plugin_items(
        m.get("tool_allowlist"),
        field="tool_allowlist",
        limits=MAX_ALLOWLIST + 1,
        item_max=None,
    )
    if len(allowlist) > MAX_ALLOWLIST:
        raise ValueError(f"tool_allowlist 不能超过 {MAX_ALLOWLIST} 项")
    if any(len(item) > MAX_ALLOWLIST_CHARS for item in allowlist):
        raise ValueError(f"tool_allowlist 每项不能超过 {MAX_ALLOWLIST_CHARS} 个字符")

    enabled = m.get("enabled", True)
    return {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "name": name,
        "transport": transport,
        "command": command,
        "args": args,
        "env": env,
        "url": url,
        "enabled": True if enabled is None else bool(enabled),
        "tool_allowlist": allowlist,
        "mem_limit_mb": plugins.clamp_mem_limit(m.get("mem_limit_mb")),
        "cpu_seconds": plugins.clamp_cpu_seconds(m.get("cpu_seconds")),
        "scope": scope,
        "owner_id": owner_id,
        "allow_private_net": allow_private_net,
        "exported_at": m.get("exported_at"),
    }


def apply_manifest(
    db: Any, manifest: Any, *, on_conflict: str = "replace"
) -> tuple[Plugin, bool]:
    """按 manifest 新建或升级同名插件，返回 ``(row, created)``。

    ``on_conflict="replace"`` 覆盖全部可导入字段；``"skip"`` 遇同名抛
    :class:`PluginPackConflict`。调用方负责提交事务与写审计。
    """
    if on_conflict not in {"replace", "skip"}:
        raise ValueError("on_conflict 只能是 replace/skip")
    fields = validate_manifest(manifest)
    if fields["scope"] == "owner":
        if not fields["owner_id"]:
            raise ValueError("个人插件包必须指定 owner_id")
        if db.get(User, fields["owner_id"]) is None:
            raise ValueError("插件包 owner_id 对应的用户不存在")
    row = db.scalar(select(Plugin).where(Plugin.name == fields["name"]))
    created = row is None
    if created:
        row = Plugin(name=fields["name"])
        db.add(row)
    elif on_conflict == "skip":
        raise PluginPackConflict(f"插件名称已存在：{fields['name']}")
    row.transport = fields["transport"]
    row.command = fields["command"]
    row.args = fields["args"]
    row.env = fields["env"]
    row.url = fields["url"]
    row.enabled = fields["enabled"]
    row.tool_allowlist = fields["tool_allowlist"]
    row.mem_limit_mb = fields["mem_limit_mb"]
    row.cpu_seconds = fields["cpu_seconds"]
    row.scope = fields["scope"]
    row.owner_id = fields["owner_id"] or None
    row.allow_private_net = fields["allow_private_net"]
    if row.enabled:
        # 升级/启用后清掉熔断状态，与 PATCH enabled=True 行为一致。
        row.fail_streak = 0
        row.auto_disabled = False
    db.flush()
    return row, created
