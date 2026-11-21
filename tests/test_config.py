"""配置管理：启动校验、环境区分、密钥脱敏。

原则：dev/test 不阻断启动（现有 767 例在 dev 下跑），prod 关键项缺失直接报错；
校验只查齐全性、不发网络请求；密钥永不返回明文。
"""

from __future__ import annotations

import pytest

from bidcraft.config import (
    ENVIRONMENTS,
    Settings,
    redact_secret,
    validate_startup,
)


def _prod(monkeypatch, **overrides) -> Settings:
    monkeypatch.setenv("BIDCRAFT_ENV", "prod")
    values = {
        "BIDCRAFT_ADMIN_PASSWORD": "prod-pass-123",
        "BIDCRAFT_MODEL_BASE_URL": "https://llm.example/v1",
        "BIDCRAFT_MODEL_NAME": "qwen",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_environment_defaults_and_normalization(monkeypatch):
    monkeypatch.delenv("BIDCRAFT_ENV", raising=False)
    assert Settings().env == "dev"
    monkeypatch.setenv("BIDCRAFT_ENV", "PROD")
    assert Settings().env == "prod"
    monkeypatch.setenv("BIDCRAFT_ENV", "garbage")
    assert Settings().env == "dev"
    assert set(ENVIRONMENTS) == {"dev", "test", "prod"}


def test_dev_missing_params_are_warnings_only(monkeypatch):
    monkeypatch.setenv("BIDCRAFT_ENV", "dev")
    monkeypatch.setenv("BIDCRAFT_OFFLINE", "1")
    monkeypatch.delenv("BIDCRAFT_ADMIN_PASSWORD", raising=False)
    config = Settings()
    errors, warnings = config.validate()
    assert errors == []
    assert any("BIDCRAFT_ADMIN_PASSWORD" in message for message in warnings)
    # 不抛异常
    validate_startup(config)


def test_prod_requires_admin_password(monkeypatch):
    config = _prod(monkeypatch)
    monkeypatch.setenv("BIDCRAFT_ADMIN_PASSWORD", "")
    config = Settings()
    errors, _warnings = config.validate()
    assert any("BIDCRAFT_ADMIN_PASSWORD" in message for message in errors)
    with pytest.raises(RuntimeError, match="启动配置校验失败"):
        validate_startup(config)


def test_prod_online_requires_model_params(monkeypatch):
    config = _prod(monkeypatch)
    monkeypatch.setenv("BIDCRAFT_MODEL_NAME", "")
    config = Settings()
    errors, _warnings = config.validate()
    assert any("模型" in message for message in errors)


def test_prod_partial_pay_config_is_error(monkeypatch):
    config = _prod(monkeypatch)
    monkeypatch.setenv("BIDCRAFT_WECHAT_PAY_MCH_ID", "1900000001")
    config = Settings()
    errors, _warnings = config.validate()
    assert any("微信支付参数不全" in message for message in errors)


def test_pay_mock_skips_payment_requirement(monkeypatch):
    config = _prod(monkeypatch)
    monkeypatch.setenv("BIDCRAFT_WECHAT_PAY_MOCK", "1")
    config = Settings()
    errors, _warnings = config.validate()
    assert errors == []


def test_offline_prod_skips_model_requirement(monkeypatch):
    config = _prod(monkeypatch)
    monkeypatch.setenv("BIDCRAFT_OFFLINE", "1")
    monkeypatch.setenv("BIDCRAFT_MODEL_NAME", "")
    config = Settings()
    errors, _warnings = config.validate()
    assert errors == []


def test_redact_secret():
    assert redact_secret("") == ""
    assert redact_secret("abcd") == "****"
    assert redact_secret("abcdefghij", keep=3) == "abc****hij"
    assert "*" in redact_secret("sk-1234567890")


def test_safe_dict_never_leaks_plaintext(monkeypatch):
    monkeypatch.setenv("BIDCRAFT_MODEL_API_KEY", "sk-supersecret-123456")
    monkeypatch.setenv("BIDCRAFT_WECHAT_APP_SECRET", "appsecret-abcdefgh")
    config = Settings()
    safe = config.safe_dict()
    assert "sk-supersecret-123456" not in str(safe)
    assert "appsecret-abcdefgh" not in str(safe)
    assert config.model_api_key.startswith("sk-")  # 原值未被改写
    assert safe["env"] == config.env
