import hashlib
import hmac

from famulus import config
from famulus.main import _signature_ok

SECRET = "test-secret"
BODY = b'{"entry": []}'


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_accepted(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", SECRET)
    monkeypatch.setattr(config, "WA_ALLOW_UNSIGNED", False)
    assert _signature_ok(BODY, _sig(SECRET, BODY))


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", SECRET)
    monkeypatch.setattr(config, "WA_ALLOW_UNSIGNED", False)
    assert not _signature_ok(BODY, _sig("other-secret", BODY))


def test_tampered_body_rejected(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", SECRET)
    monkeypatch.setattr(config, "WA_ALLOW_UNSIGNED", False)
    assert not _signature_ok(b'{"entry": [1]}', _sig(SECRET, BODY))


def test_missing_header_rejected(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", SECRET)
    monkeypatch.setattr(config, "WA_ALLOW_UNSIGNED", False)
    assert not _signature_ok(BODY, None)


def test_no_secret_rejects_by_default(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "")
    monkeypatch.setattr(config, "WA_ALLOW_UNSIGNED", False)
    assert not _signature_ok(BODY, _sig(SECRET, BODY))


def test_unsigned_optin_accepts(monkeypatch):
    monkeypatch.setattr(config, "WA_ALLOW_UNSIGNED", True)
    assert _signature_ok(BODY, None)
