from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bidcraft import wechatpay

APIV3_KEY = "0123456789abcdef0123456789abcdef"
MCH_ID = "1900000001"
APP_ID = "wx-test-appid"
PLATFORM_SERIAL = "PLATFORM-SERIAL-0001"
SIGNATURE_TYPE = "WECHATPAY2-SHA256-RSA2048"


def _keypair(tmp_path):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path = tmp_path / "merchant-key.pem"
    public_path = tmp_path / "platform-cert.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private, private_path, public_path


@pytest.fixture()
def pay_env(tmp_path, monkeypatch):
    from bidcraft.config import settings

    private, private_path, public_path = _keypair(tmp_path)
    monkeypatch.setattr(settings, "wechat_pay_mch_id", MCH_ID)
    monkeypatch.setattr(settings, "wechat_pay_serial_no", "SERIAL-0001")
    monkeypatch.setattr(settings, "wechat_pay_private_key", str(private_path))
    monkeypatch.setattr(settings, "wechat_pay_platform_cert", str(public_path))
    monkeypatch.setattr(settings, "wechat_pay_platform_serial_no", PLATFORM_SERIAL)
    monkeypatch.setattr(settings, "wechat_pay_apiv3_key", APIV3_KEY)
    monkeypatch.setattr(settings, "wechat_pay_notify_url", "https://example.com/pay/notify")
    monkeypatch.setattr(settings, "wechat_app_id", APP_ID)
    monkeypatch.setattr(settings, "wechat_pay_mock", False)
    return private, private_path, public_path


@pytest.fixture()
def frozen_time(monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(wechatpay.time, "time", lambda: float(now))
    return now


def _order():
    from bidcraft.models import RechargeOrder

    return RechargeOrder(
        out_trade_no="BC20261116000001",
        amount_cents=9900,
        credits=100,
    )


def _mock_post(monkeypatch, *, response=None, error=None):
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["content"] = kwargs.get("content")
        captured["headers"] = kwargs.get("headers") or {}
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(wechatpay.httpx, "post", fake_post)
    return captured


def test_create_native_success(pay_env, monkeypatch):
    captured = _mock_post(
        monkeypatch,
        response=httpx.Response(200, json={"code_url": "weixin://wxpay/bizpayurl?pr=abc"}),
    )

    code_url = wechatpay.create_native(_order())

    assert code_url == "weixin://wxpay/bizpayurl?pr=abc"
    assert captured["url"] == f"{wechatpay.API}/v3/pay/transactions/native"
    assert captured["headers"]["Authorization"].startswith("WECHATPAY2-SHA256-RSA2048 ")
    body = json.loads(captured["content"])
    assert body["out_trade_no"] == "BC20261116000001"
    assert body["amount"] == {"total": 9900, "currency": "CNY"}
    assert body["appid"] == APP_ID


def test_create_native_api_error_message(pay_env, monkeypatch):
    _mock_post(
        monkeypatch,
        response=httpx.Response(400, json={"code": "SIGN_ERROR", "message": "签名错误"}),
    )
    with pytest.raises(wechatpay.PaymentError, match="签名错误"):
        wechatpay.create_native(_order())


def test_create_native_missing_code_url(pay_env, monkeypatch):
    _mock_post(monkeypatch, response=httpx.Response(200, json={}))
    with pytest.raises(wechatpay.PaymentError, match="未返回二维码链接"):
        wechatpay.create_native(_order())


def test_create_native_network_error(pay_env, monkeypatch):
    _mock_post(monkeypatch, error=httpx.ConnectError("connection refused"))
    with pytest.raises(wechatpay.PaymentError, match="连接失败"):
        wechatpay.create_native(_order())


def test_create_native_unconfigured(monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "wechat_pay_private_key", "")
    with pytest.raises(wechatpay.PaymentError, match="尚未配置"):
        wechatpay.create_native(_order())


def _signed_headers(
    private,
    body: bytes,
    *,
    timestamp: str | None = None,
    nonce: str = "nonce-abc",
    serial: str | None = PLATFORM_SERIAL,
    signature_type: str | None = SIGNATURE_TYPE,
) -> dict[str, str]:
    stamp = timestamp if timestamp is not None else str(int(time.time()))
    message = f"{stamp}\n{nonce}\n{body.decode('utf-8')}\n".encode()
    signature = private.sign(message, padding.PKCS1v15(), hashes.SHA256())
    headers = {
        "wechatpay-timestamp": stamp,
        "wechatpay-nonce": nonce,
        "wechatpay-signature": base64.b64encode(signature).decode(),
    }
    if serial is not None:
        headers["wechatpay-serial"] = serial
    if signature_type is not None:
        headers["wechatpay-signature-type"] = signature_type
    return headers


def test_verify_signature_accepts_valid_callback(pay_env):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-1"}'
    wechatpay._verify_signature(_signed_headers(private, body), body)


def test_verify_signature_accepts_case_insensitive_headers(pay_env):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-case"}'
    headers = {key.upper(): value for key, value in _signed_headers(private, body).items()}
    wechatpay._verify_signature(headers, body)


def test_verify_signature_accepts_missing_signature_type(pay_env):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-no-type"}'
    wechatpay._verify_signature(_signed_headers(private, body, signature_type=None), body)


def test_verify_signature_rejects_tampered_body(pay_env):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-1"}'
    headers = _signed_headers(private, body)
    with pytest.raises(wechatpay.PaymentError, match="签名无效"):
        wechatpay._verify_signature(headers, body + b" ")


def test_verify_signature_rejects_missing_headers(pay_env):
    with pytest.raises(wechatpay.PaymentError, match="缺少签名头"):
        wechatpay._verify_signature({}, b"{}")


@pytest.mark.parametrize("offset", [-301, 301])
def test_verify_signature_rejects_timestamp_outside_window(pay_env, frozen_time, offset):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-freshness"}'
    headers = _signed_headers(private, body, timestamp=str(frozen_time + offset))
    with pytest.raises(wechatpay.PaymentError, match="时间戳"):
        wechatpay._verify_signature(headers, body)


def test_verify_signature_rejects_non_numeric_timestamp(pay_env, frozen_time):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-bad-time"}'
    headers = _signed_headers(private, body, timestamp="not-a-timestamp")
    with pytest.raises(wechatpay.PaymentError, match="时间戳"):
        wechatpay._verify_signature(headers, body)


@pytest.mark.parametrize("serial", ["WRONG-SERIAL", None])
def test_verify_signature_rejects_wrong_platform_serial(pay_env, serial):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-serial"}'
    headers = _signed_headers(private, body, serial=serial)
    with pytest.raises(wechatpay.PaymentError, match="序列号"):
        wechatpay._verify_signature(headers, body)


def test_verify_signature_rejects_wrong_signature_type(pay_env):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-type"}'
    headers = _signed_headers(private, body, signature_type="WECHATPAY3-SHA256-RSA2048")
    with pytest.raises(wechatpay.PaymentError, match="签名类型"):
        wechatpay._verify_signature(headers, body)


def test_verify_signature_requires_platform_cert(pay_env, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "wechat_pay_platform_cert", "/nope/missing.pem")
    with pytest.raises(wechatpay.PaymentError, match="缺少微信支付平台证书"):
        wechatpay._verify_signature({}, b"{}")


def _encrypted_notification(
    payload: dict,
    *,
    event_type: str = "TRANSACTION.SUCCESS",
    algorithm: str = "AEAD_AES_256_GCM",
    nonce: str = "abcdefghijkl",
    associated_data: str = "transaction",
) -> bytes:
    ciphertext = AESGCM(APIV3_KEY.encode()).encrypt(
        nonce.encode(),
        json.dumps(payload, ensure_ascii=False).encode(),
        associated_data.encode(),
    )
    return json.dumps(
        {
            "id": "notify-1",
            "event_type": event_type,
            "resource": {
                "algorithm": algorithm,
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "nonce": nonce,
                "associated_data": associated_data,
            },
        }
    ).encode()


def _payment(
    *,
    out_trade_no: str = "BC20261116000001",
    transaction_id: str = "4200000000001",
    trade_state: str = "SUCCESS",
    total: object = 9900,
    currency: str = "CNY",
    mchid: object = MCH_ID,
    appid: object = APP_ID,
) -> dict:
    return {
        "out_trade_no": out_trade_no,
        "transaction_id": transaction_id,
        "trade_state": trade_state,
        "mchid": mchid,
        "appid": appid,
        "amount": {"total": total, "currency": currency},
    }


def test_parse_notification_success(pay_env):
    private, _private_path, _public_path = pay_env
    data = _payment()
    body = _encrypted_notification(data)

    parsed = wechatpay.parse_notification(_signed_headers(private, body), body)

    assert parsed["out_trade_no"] == "BC20261116000001"
    assert parsed["transaction_id"] == "4200000000001"
    assert parsed["trade_state"] == "SUCCESS"
    assert parsed["amount"] == 9900
    assert parsed["currency"] == "CNY"
    assert parsed["mchid"] == MCH_ID
    assert parsed["appid"] == APP_ID
    assert parsed["event_type"] == "TRANSACTION.SUCCESS"
    assert parsed["raw"] == data


@pytest.mark.parametrize("state", ["CLOSED", "FAIL", "PAYERROR", "NOTPAY", "REFUND"])
def test_parse_notification_accepts_known_non_success_states(pay_env, state):
    private, _private_path, _public_path = pay_env
    body = _encrypted_notification(_payment(trade_state=state))
    parsed = wechatpay.parse_notification(_signed_headers(private, body), body)
    assert parsed["trade_state"] == state
    assert parsed["amount"] == 9900


def test_parse_notification_rejects_unknown_trade_state(pay_env):
    private, _private_path, _public_path = pay_env
    body = _encrypted_notification(_payment(trade_state="SUCCESSFUL"))
    with pytest.raises(wechatpay.PaymentError, match="交易状态"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


@pytest.mark.parametrize("event_type", ["", "PAY.SUCCESS", None, 123])
def test_parse_notification_rejects_bad_event_type(pay_env, event_type):
    private, _private_path, _public_path = pay_env
    body = _encrypted_notification(_payment(), event_type=event_type)
    with pytest.raises(wechatpay.PaymentError, match="事件类型"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


def test_parse_notification_rejects_wrong_algorithm(pay_env):
    private, _private_path, _public_path = pay_env
    body = _encrypted_notification(_payment(), algorithm="AEAD_AES_128_GCM")
    with pytest.raises(wechatpay.PaymentError, match="加密算法"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mchid", "1999999999", "商户号"),
        ("mchid", "", "商户号"),
        ("appid", "wx-other", "应用号"),
        ("out_trade_no", "", "订单号"),
        ("out_trade_no", 123, "订单号"),
        ("transaction_id", "", "交易号"),
        ("transaction_id", None, "交易号"),
    ],
)
def test_parse_notification_rejects_mismatched_identity(pay_env, field, value, message):
    private, _private_path, _public_path = pay_env
    body = _encrypted_notification(_payment(**{field: value}))
    with pytest.raises(wechatpay.PaymentError, match=message):
        wechatpay.parse_notification(_signed_headers(private, body), body)


@pytest.mark.parametrize(
    "total", [True, False, 9900.0, "9900", 0, -1, None, "12.5"]
)
def test_parse_notification_rejects_malformed_amount(pay_env, total):
    private, _private_path, _public_path = pay_env
    body = _encrypted_notification(_payment(total=total))
    with pytest.raises(wechatpay.PaymentError, match="金额"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


@pytest.mark.parametrize("currency", ["USD", "", None, 156])
def test_parse_notification_rejects_wrong_currency(pay_env, currency):
    private, _private_path, _public_path = pay_env
    body = _encrypted_notification(_payment(currency=currency))
    with pytest.raises(wechatpay.PaymentError, match="币种"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


def test_parse_notification_rejects_invalid_json(pay_env):
    private, _private_path, _public_path = pay_env
    body = b"{not-json"
    with pytest.raises(wechatpay.PaymentError, match="回调解密失败"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


def test_parse_notification_rejects_bad_ciphertext(pay_env):
    private, _private_path, _public_path = pay_env
    body = json.dumps(
        {
            "id": "notify-1",
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "nonce": "abcdefghijkl",
                "ciphertext": "not-base64",
                "associated_data": "",
            },
        }
    ).encode()
    with pytest.raises(wechatpay.PaymentError, match="回调解密失败"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


def test_parse_notification_rejects_invalid_tag(pay_env):
    private, _private_path, _public_path = pay_env
    body = json.dumps(
        {
            "id": "notify-1",
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "nonce": "abcdefghijkl",
                # 32 字节合法 base64，但 tag 不正确 → AESGCM 抛 InvalidTag。
                "ciphertext": base64.b64encode(b"x" * 32).decode(),
                "associated_data": "transaction",
            },
        }
    ).encode()
    with pytest.raises(wechatpay.PaymentError, match="回调解密失败"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


def test_parse_notification_requires_apiv3_key(pay_env, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "wechat_pay_apiv3_key", "")
    with pytest.raises(wechatpay.PaymentError, match="未配置 APIv3 密钥"):
        wechatpay.parse_notification({}, b"{}")
