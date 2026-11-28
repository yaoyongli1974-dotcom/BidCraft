"""WeChat Pay API v3 Native orders and notification verification."""

from __future__ import annotations

import base64
import json
import secrets
import time
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings
from .models import RechargeOrder

API = "https://api.mch.weixin.qq.com"
TIMEOUT = 20.0

#: 回调签名头时间戳与本机时间的最大偏差（秒）。超出即视为重放/时钟错误。
SIGNATURE_TIMESTAMP_TOLERANCE = 300
#: 微信支付 APIv3 唯一支持的回调签名类型；头存在时必须是这个值。
SIGNATURE_TYPE = "WECHATPAY2-SHA256-RSA2048"
#: 回调资源加密算法。
RESOURCE_ALGORITHM = "AEAD_AES_256_GCM"
#: 可接受的交易状态。只有 SUCCESS 才入账，其余状态只确认收到。
KNOWN_TRADE_STATES = frozenset(
    {
        "SUCCESS",
        "REFUND",
        "NOTPAY",
        "CLOSED",
        "REVOKED",
        "USERPAYING",
        "ACCEPTPAY",
        "PAYERROR",
        "FAIL",
    }
)


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
        and settings.wechat_pay_platform_cert
        and settings.wechat_pay_platform_serial_no
        and Path(settings.wechat_pay_private_key).is_file()
        and Path(settings.wechat_pay_platform_cert).is_file()
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
    """校验微信支付回调的签名头、新鲜度与平台证书序列号。

    HTTP 头名称大小写不敏感，因此统一转小写后读取。签名仍使用原始 body 字节，
    避免任何 JSON 重序列化造成的字节差异。
    """
    normalized = {str(key).lower(): value for key, value in headers.items()}
    cert_path = Path(settings.wechat_pay_platform_cert)
    if not cert_path.is_file():
        raise PaymentError("缺少微信支付平台证书，无法验证回调")
    timestamp = normalized.get("wechatpay-timestamp", "")
    nonce = normalized.get("wechatpay-nonce", "")
    signature = normalized.get("wechatpay-signature", "")
    if not (timestamp and nonce and signature):
        raise PaymentError("回调缺少签名头")

    if not isinstance(timestamp, str) or not timestamp.isdigit():
        raise PaymentError("回调时间戳无效")
    timestamp_value = int(timestamp)
    if abs(int(time.time()) - timestamp_value) > SIGNATURE_TIMESTAMP_TOLERANCE:
        raise PaymentError("回调时间戳超出允许范围")

    expected_serial = settings.wechat_pay_platform_serial_no
    if expected_serial and normalized.get("wechatpay-serial") != expected_serial:
        raise PaymentError("回调平台证书序列号不匹配")

    signature_type = normalized.get("wechatpay-signature-type")
    if signature_type is not None and signature_type != SIGNATURE_TYPE:
        raise PaymentError("回调签名类型不支持")

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
    except (ValueError, TypeError) as exc:
        raise PaymentError("回调解密失败") from exc
    if not isinstance(payload, dict):
        raise PaymentError("回调事件类型无效")
    event_type = payload.get("event_type")
    if not isinstance(event_type, str) or not event_type.startswith("TRANSACTION."):
        raise PaymentError("回调事件类型无效")

    resource = payload.get("resource")
    if not isinstance(resource, dict) or resource.get("algorithm") != RESOURCE_ALGORITHM:
        raise PaymentError("回调加密算法不支持")
    try:
        plaintext = AESGCM(settings.wechat_pay_apiv3_key.encode()).decrypt(
            resource["nonce"].encode(),
            base64.b64decode(resource["ciphertext"]),
            (resource.get("associated_data") or "").encode(),
        )
        data = json.loads(plaintext)
    except InvalidTag as exc:
        raise PaymentError("回调解密失败") from exc
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise PaymentError("回调解密失败") from exc
    if not isinstance(data, dict):
        raise PaymentError("回调解密失败")

    if data.get("mchid") != settings.wechat_pay_mch_id:
        raise PaymentError("回调商户号不匹配")
    if data.get("appid") != settings.wechat_app_id:
        raise PaymentError("回调应用号不匹配")
    out_trade_no = data.get("out_trade_no")
    if not isinstance(out_trade_no, str) or not out_trade_no.strip():
        raise PaymentError("回调订单号无效")
    transaction_id = data.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise PaymentError("回调交易号无效")
    trade_state = data.get("trade_state")
    if not isinstance(trade_state, str) or trade_state not in KNOWN_TRADE_STATES:
        raise PaymentError("回调交易状态无效")
    amount = data.get("amount")
    if not isinstance(amount, dict):
        raise PaymentError("回调金额无效")
    currency = amount.get("currency")
    if currency != "CNY":
        raise PaymentError("回调币种不支持")
    total = amount.get("total")
    # bool 是 int 的子类，必须显式排除；浮点/字符串金额一律拒绝。
    if type(total) is not int or total <= 0:
        raise PaymentError("回调金额无效")

    return {
        "out_trade_no": out_trade_no,
        "transaction_id": transaction_id,
        "trade_state": trade_state,
        "amount": total,
        "currency": currency,
        "mchid": data["mchid"],
        "appid": data["appid"],
        "event_type": event_type,
        "raw": data,
    }
