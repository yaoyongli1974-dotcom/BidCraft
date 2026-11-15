from __future__ import annotations

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bidcraft import wechatpay

APIV3_KEY = "0123456789abcdef0123456789abcdef"


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
    monkeypatch.setattr(settings, "wechat_pay_mch_id", "1900000001")
    monkeypatch.setattr(settings, "wechat_pay_serial_no", "SERIAL-0001")
    monkeypatch.setattr(settings, "wechat_pay_private_key", str(private_path))
    monkeypatch.setattr(settings, "wechat_pay_platform_cert", str(public_path))
    monkeypatch.setattr(settings, "wechat_pay_apiv3_key", APIV3_KEY)
    monkeypatch.setattr(settings, "wechat_pay_notify_url", "https://example.com/pay/notify")
    monkeypatch.setattr(settings, "wechat_app_id", "wx-test-appid")
    monkeypatch.setattr(settings, "wechat_pay_mock", False)
    return private, private_path, public_path


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
    assert body["appid"] == "wx-test-appid"


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


def _signed_headers(private, body: bytes) -> dict[str, str]:
    timestamp = "1700000000"
    nonce = "nonce-abc"
    message = f"{timestamp}\n{nonce}\n{body.decode('utf-8')}\n".encode()
    signature = private.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return {
        "wechatpay-timestamp": timestamp,
        "wechatpay-nonce": nonce,
        "wechatpay-signature": base64.b64encode(signature).decode(),
    }


def test_verify_signature_accepts_valid_callback(pay_env):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-1"}'
    wechatpay._verify_signature(_signed_headers(private, body), body)


def test_verify_signature_rejects_tampered_body(pay_env):
    private, _private_path, _public_path = pay_env
    body = b'{"id":"notify-1"}'
    headers = _signed_headers(private, body)
    with pytest.raises(wechatpay.PaymentError, match="签名无效"):
        wechatpay._verify_signature(headers, body + b" ")


def test_verify_signature_rejects_missing_headers(pay_env):
    with pytest.raises(wechatpay.PaymentError, match="缺少签名头"):
        wechatpay._verify_signature({}, b"{}")


def test_verify_signature_requires_platform_cert(pay_env, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "wechat_pay_platform_cert", "/nope/missing.pem")
    with pytest.raises(wechatpay.PaymentError, match="缺少微信支付平台证书"):
        wechatpay._verify_signature({}, b"{}")


def _encrypted_notification(payload: dict) -> bytes:
    nonce = "abcdefghijkl"
    associated_data = "transaction"
    ciphertext = AESGCM(APIV3_KEY.encode()).encrypt(
        nonce.encode(),
        json.dumps(payload, ensure_ascii=False).encode(),
        associated_data.encode(),
    )
    return json.dumps(
        {
            "id": "notify-1",
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "nonce": nonce,
                "associated_data": associated_data,
            },
        }
    ).encode()


def test_parse_notification_success(pay_env):
    private, _private_path, _public_path = pay_env
    data = {
        "out_trade_no": "BC20261116000001",
        "transaction_id": "4200000000001",
        "trade_state": "SUCCESS",
        "amount": {"total": 9900, "currency": "CNY"},
    }
    body = _encrypted_notification(data)

    parsed = wechatpay.parse_notification(_signed_headers(private, body), body)

    assert parsed["out_trade_no"] == "BC20261116000001"
    assert parsed["transaction_id"] == "4200000000001"
    assert parsed["trade_state"] == "SUCCESS"
    assert parsed["amount"] == 9900
    assert parsed["raw"] == data


def test_parse_notification_rejects_invalid_json(pay_env):
    private, _private_path, _public_path = pay_env
    body = b"{not-json"
    with pytest.raises(wechatpay.PaymentError, match="回调解密失败"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


def test_parse_notification_rejects_bad_ciphertext(pay_env):
    private, _private_path, _public_path = pay_env
    body = json.dumps(
        {"resource": {"nonce": "abcdefghijkl", "ciphertext": "not-base64", "associated_data": ""}}
    ).encode()
    with pytest.raises(wechatpay.PaymentError, match="回调解密失败"):
        wechatpay.parse_notification(_signed_headers(private, body), body)


def test_parse_notification_requires_apiv3_key(pay_env, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "wechat_pay_apiv3_key", "")
    with pytest.raises(wechatpay.PaymentError, match="未配置 APIv3 密钥"):
        wechatpay.parse_notification({}, b"{}")
