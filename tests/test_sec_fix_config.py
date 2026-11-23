"""配置与密钥修复：强口令、健康信息开关、多实例主密钥与日志级别。"""

from __future__ import annotations

from bidcraft.config import MIN_ADMIN_PASSWORD_LENGTH, Settings
from bidcraft.security import decrypt_secret, encrypt_secret, sign_state, verify_state


def _prod(monkeypatch, password: str = "prod-strong-pass-123", **extra) -> Settings:
    monkeypatch.setenv("BIDCRAFT_ENV", "prod")
    monkeypatch.setenv("BIDCRAFT_OFFLINE", "1")
    monkeypatch.setenv("BIDCRAFT_ADMIN_PASSWORD", password)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_prod_rejects_short_and_placeholder_admin_password(monkeypatch):
    short = _prod(monkeypatch, "short")
    errors, _warnings = short.validate()
    assert any("BIDCRAFT_ADMIN_PASSWORD" in message for message in errors)

    for weak in ("change-me", "admin123", "password", "123456789012", "aaaaaaaaaaaa"):
        monkeypatch.setenv("BIDCRAFT_ADMIN_PASSWORD", weak)
        errors, _warnings = Settings().validate()
        assert errors, weak
        assert any("BIDCRAFT_ADMIN_PASSWORD" in message for message in errors)

    strong = _prod(monkeypatch, "x9!Coral-River-42")
    assert strong.validate()[0] == []
    assert MIN_ADMIN_PASSWORD_LENGTH == 12


def test_multiworker_sqlite_and_pg_secret_warnings(monkeypatch):
    config = _prod(monkeypatch)
    errors, warnings = config.validate()
    assert errors == []
    assert any("SQLite" in message for message in warnings)

    monkeypatch.setenv("BIDCRAFT_DB_URL", "postgresql://u:p@127.0.0.1/db")
    config = Settings()
    errors, warnings = config.validate()
    assert errors == []
    assert any("secret.key" in message for message in warnings)


def test_health_public_env_values(monkeypatch):
    for value in ("minimal", "public", "MINIMAL"):
        monkeypatch.setenv("BIDCRAFT_HEALTH_PUBLIC", value)
        assert Settings().health_public is True
    for value in ("private", "full", "0", "false"):
        monkeypatch.setenv("BIDCRAFT_HEALTH_PUBLIC", value)
        assert Settings().health_public is False


def test_secret_key_env_shared_across_hosts(db, monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    from bidcraft import config as config_module

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config_module.settings, "secret_key", key)
    ciphertext = encrypt_secret("sk-shared-key-123")
    state = sign_state({"user": "alice"})

    # 模拟另一台机器：BIDCRAFT_HOME 不同、没有共享 secret.key 文件
    other_home = tmp_path / "other-host"
    monkeypatch.setattr(config_module.settings, "home", other_home)
    assert decrypt_secret(ciphertext) == "sk-shared-key-123"
    payload = verify_state(state)
    assert payload is not None and payload["user"] == "alice"
    assert not (other_home / "secret.key").exists()

    monkeypatch.setattr(config_module.settings, "secret_key", "not-a-fernet-key")
    try:
        encrypt_secret("x")
    except ValueError as exc:
        assert "BIDCRAFT_SECRET_KEY" in str(exc)
    else:
        raise AssertionError("invalid secret key must be rejected")


def test_logging_env_is_accepted(monkeypatch):
    monkeypatch.setenv("BIDCRAFT_LOG_LEVEL", "debug")
    monkeypatch.setenv("BIDCRAFT_LOG_FORMAT", "json")
    config = Settings()
    assert config.log_level == "DEBUG"
    assert config.log_format == "json"
