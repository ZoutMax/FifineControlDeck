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


def test_delete_without_keyring_is_a_noop(monkeypatch):
    monkeypatch.setattr(secret_store, "_keyring", lambda: None)
    secret_store.delete("pw-x")                       # must not raise


def test_get_unknown_id_returns_none(monkeypatch):
    monkeypatch.setattr(secret_store, "_keyring", lambda: _FakeKeyring())
    assert secret_store.get("pw-never-stored") is None


class _LockedKeyring:
    """A backend that errors on every call — a locked SecretService, or D-Bus
    missing (e.g. a headless session)."""

    def set_password(self, *a):
        raise RuntimeError("SecretService is locked")

    def get_password(self, *a):
        raise RuntimeError("SecretService is locked")

    def delete_password(self, *a):
        raise RuntimeError("SecretService is locked")


def test_locked_keyring_degrades_instead_of_raising(monkeypatch):
    """A locked keyring is a normal state on a fresh login. store() must report
    failure so the caller can fall back, and get()/delete() must stay quiet —
    an exception here would surface while the user edits a key."""
    monkeypatch.setattr(secret_store, "_keyring", lambda: _LockedKeyring())
    assert secret_store.store("pw-x", "s3cret") is False
    assert secret_store.get("pw-x") is None
    secret_store.delete("pw-x")                       # must not raise


def test_keyring_import_failure_is_contained(monkeypatch):
    """The keyring package is optional; an absent or broken one must disable
    the feature rather than break the app."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "keyring":
            raise ImportError("no keyring installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert secret_store._keyring() is None
    assert secret_store.available() is False
