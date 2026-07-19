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
    missing (headless session, some flatpak/snap setups)."""

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


# ---------------------------------------------------------------------------
# 0.9.0: Secret-portal backend chained before the keyring (Flatpak)
# ---------------------------------------------------------------------------
class _FakePortal:
    def __init__(self):
        self.d = {}

    def store(self, sid, pw):
        self.d[sid] = pw
        return True

    def get(self, sid):
        return self.d.get(sid)

    def delete(self, sid):
        self.d.pop(sid, None)


def test_portal_backend_preferred_when_active(monkeypatch):
    portal = _FakePortal()
    kr = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_portal", lambda: portal)
    monkeypatch.setattr(secret_store, "_keyring", lambda: kr)
    assert secret_store.available() is True
    assert secret_store.store("pw-a", "s3cret!") is True
    assert portal.d == {"pw-a": "s3cret!"}     # went to the portal store
    assert kr.d == {}                          # keyring untouched
    assert secret_store.get("pw-a") == "s3cret!"
    secret_store.delete("pw-a")
    assert portal.d == {}


def test_portal_available_even_without_keyring(monkeypatch):
    monkeypatch.setattr(secret_store, "_portal", lambda: _FakePortal())
    monkeypatch.setattr(secret_store, "_keyring", lambda: None)
    assert secret_store.available() is True
    assert secret_store.store("pw-a", "x") is True


def test_get_falls_back_to_keyring_for_pre_upgrade_secrets(monkeypatch):
    """A secret stored via SecretService before the portal backend existed
    must keep resolving after the upgrade: gets consult BOTH backends."""
    portal = _FakePortal()
    kr = _FakeKeyring()
    kr.set_password(secret_store.SERVICE, "pw-old", "legacy!")
    monkeypatch.setattr(secret_store, "_portal", lambda: portal)
    monkeypatch.setattr(secret_store, "_keyring", lambda: kr)
    assert secret_store.get("pw-old") == "legacy!"


def test_delete_clears_both_backends(monkeypatch):
    portal = _FakePortal()
    portal.d["pw-x"] = "a"
    kr = _FakeKeyring()
    kr.set_password(secret_store.SERVICE, "pw-x", "a")
    monkeypatch.setattr(secret_store, "_portal", lambda: portal)
    monkeypatch.setattr(secret_store, "_keyring", lambda: kr)
    secret_store.delete("pw-x")
    assert portal.d == {} and kr.d == {}


def test_portal_never_activates_outside_flatpak(monkeypatch):
    """On deb/PPA installs the portal path must be inert: IN_FLATPAK is the
    gate, before any import or D-Bus work happens."""
    from fifine_deck import actions
    monkeypatch.setattr(actions, "IN_FLATPAK", False)
    assert secret_store._portal() is None
