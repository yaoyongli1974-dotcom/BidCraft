from __future__ import annotations

from bidcraft import security


def test_encrypt_decrypt_roundtrip(db):
    secret = "sk-live-测试-0123456789abcdef"
    token = security.encrypt_secret(secret)
    assert token
    assert token != secret
    assert security.decrypt_secret(token) == secret


def test_roundtrip_is_stable_across_calls(db):
    token = security.encrypt_secret("payload")
    assert security.decrypt_secret(token) == "payload"
    assert security.decrypt_secret(token) == "payload"


def test_empty_values_short_circuit(db):
    assert security.encrypt_secret("") == ""
    assert security.decrypt_secret("") == ""


def test_tampered_ciphertext_is_rejected(db):
    token = security.encrypt_secret("do-not-leak")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    # Fail closed: a corrupted token never yields the plaintext.
    assert security.decrypt_secret(tampered) == ""
    assert security.decrypt_secret(tampered) != "do-not-leak"
    # The untouched token still decrypts.
    assert security.decrypt_secret(token) == "do-not-leak"


def test_malformed_ciphertext_is_rejected(db):
    assert security.decrypt_secret("not-a-fernet-token") == ""
    assert security.decrypt_secret("中文不是合法 token") == ""
