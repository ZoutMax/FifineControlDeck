"""Keyring-backed secret store: roundtrip with a fake backend, graceful fallback."""
from fifine_deck import secret_store


class _FakeKeyring:
    def __init__(self):
        self.d = {}

    def set_password(self, service, sid, pw):
        self.d[(service, sid)] = pw

    def get_password(self, service, sid):
        return self.d.get((service, sid))

    def delete_password(self, service, sid):
        self.d.pop((service, sid), None)


def test_new_id_unique_and_prefixed():
    a, b = secret_store.new_id(), secret_store.new_id()
    assert a != b and a.startswith("pw-")


def test_store_get_delete_roundtrip(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_keyring", lambda: fake)
    assert secret_store.available() is True
    sid = secret_store.new_id()
    assert secret_store.store(sid, "s3cret!") is True
    assert secret_store.get(sid) == "s3cret!"
    secret_store.delete(sid)
    assert secret_store.get(sid) is None


def test_graceful_fallback_without_keyring(monkeypatch):
    monkeypatch.setattr(secret_store, "_keyring", lambda: None)
    assert secret_store.available() is False
    assert secret_store.store("pw-x", "s") is False   # caller falls back to plaintext
    assert secret_store.get("pw-x") is None
