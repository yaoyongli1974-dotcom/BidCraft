"""WeChat Pay API v3 Native orders and notification verification."""

from __future__ import annotations

import base64
import json
import secrets
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings
from .models import RechargeOrder

API = "https://api.mch.weixin.qq.com"
TIMEOUT = 20.0


class PaymentError(RuntimeError):
    pass


def configured() -> bool:
    return bool(
        settings.wechat_pay_mch_id
        and settings.wechat_pay_serial_no
        and settings.wechat_pay_private_key
        and settings.wechat_pay_apiv3_key
        and settings.wechat_pay_notify_url
        and settings.wechat_app_id
        and Path(settings.wechat_pay_private_key).is_file()
    )


def available() -> bool:
    return configured() or settings.wechat_pay_mock


def _private_key():
    path = Path(settings.wechat_pay_private_key)
    if not path.is_file():
        raise PaymentError("商户私钥文件不存在")
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def _authorization(method: str, path: str, body: str) -> str:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n".encode()
    signature = _private_key().sign(message, padding.PKCS1v15(), hashes.SHA256())
    token = base64.b64encode(signature).decode("ascii")
    return (
        'WECHATPAY2-SHA256-RSA2048 '
        f'mchid="{settings.wechat_pay_mch_id}",nonce_str="{nonce}",'
        f'signature="{token}",timestamp="{timestamp}",'
        f'serial_no="{settings.wechat_pay_serial_no}"'
    )


def create_native(order: RechargeOrder) -> str:
    if not configured():
        raise PaymentError("微信支付尚未配置")
    path = "/v3/pay/transactions/native"
    payload = {
        "appid": settings.wechat_app_id,
        "mchid": settings.wechat_pay_mch_id,
        "description": f"BidCraft 点数充值 {order.credits} 点",
        "out_trade_no": order.out_trade_no,
        "notify_url": settings.wechat_pay_notify_url,
        "amount": {"total": order.amount_cents, "currency": "CNY"},
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    headers = {
        "Authorization": _authorization("POST", path, body),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "BidCraft/0.1",
    }
    try:
        response = httpx.post(f"{API}{path}", content=body.encode(), headers=headers, timeout=TIMEOUT)
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            raise PaymentError(data.get("message") or f"微信支付下单失败：HTTP {response.status_code}")
        code_url = data.get("code_url")
        if not code_url:
            raise PaymentError("微信支付未返回二维码链接")
        return str(code_url)
    except httpx.HTTPError as exc:
        raise PaymentError(f"微信支付接口连接失败：{exc}") from exc
    except ValueError as exc:
        raise PaymentError("微信支付返回了无法解析的数据") from exc


def _verify_signature(headers: dict[str, str], body: bytes) -> None:
    cert_path = Path(settings.wechat_pay_platform_cert)
    if not cert_path.is_file():
        raise PaymentError("缺少微信支付平台证书，无法验证回调")
    timestamp = headers.get("wechatpay-timestamp", "")
    nonce = headers.get("wechatpay-nonce", "")
    signature = headers.get("wechatpay-signature", "")
    if not (timestamp and nonce and signature):
        raise PaymentError("回调缺少签名头")
    message = f"{timestamp}\n{nonce}\n{body.decode('utf-8')}\n".encode()
    try:
        cert = serialization.load_pem_public_key(cert_path.read_bytes())
        cert.verify(
            base64.b64decode(signature),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise PaymentError("微信支付回调签名无效") from exc


def parse_notification(headers: dict[str, str], body: bytes) -> dict:
    if not settings.wechat_pay_apiv3_key:
        raise PaymentError("未配置 APIv3 密钥")
    _verify_signature({key.lower(): value for key, value in headers.items()}, body)
    try:
        payload = json.loads(body)
        resource = payload["resource"]
        plaintext = AESGCM(settings.wechat_pay_apiv3_key.encode()).decrypt(
            resource["nonce"].encode(),
            base64.b64decode(resource["ciphertext"]),
            (resource.get("associated_data") or "").encode(),
        )
        data = json.loads(plaintext)
    except (KeyError, ValueError, TypeError) as exc:
        raise PaymentError("微信支付回调解密失败") from exc
    return {
        "out_trade_no": data.get("out_trade_no", ""),
        "transaction_id": data.get("transaction_id", ""),
        "trade_state": data.get("trade_state", ""),
        "amount": int((data.get("amount") or {}).get("total") or 0),
        "raw": data,
    }
