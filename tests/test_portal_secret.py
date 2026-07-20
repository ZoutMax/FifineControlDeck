"""Secret-portal store: encryption roundtrip, durability discipline, and the
availability gates. The D-Bus retrieval itself mirrors the proven Background
portal pattern; here the master secret is faked so no portal is needed."""
import json
import os

import pytest

from fifine_deck import portal_secret

pytest.importorskip("cryptography")

MASTER = b"a-32-byte-master-secret-from-portal"


@pytest.fixture
def portal(monkeypatch):
    monkeypatch.setattr(portal_secret, "_master", lambda: MASTER)
    return portal_secret


def test_available_requires_master_and_crypto(monkeypatch):
    monkeypatch.setattr(portal_secret, "_master", lambda: None)
    assert portal_secret.available() is False
    monkeypatch.setattr(portal_secret, "_master", lambda: MASTER)
    assert portal_secret.available() is True
    monkeypatch.setattr(portal_secret, "_crypto", lambda: None)
    assert portal_secret.available() is False


def test_store_get_delete_roundtrip(portal):
    assert portal.store("pw-abc", "s3cret!") is True
    assert portal.get("pw-abc") == "s3cret!"
    assert portal.get("pw-never") is None
    portal.delete("pw-abc")
    assert portal.get("pw-abc") is None


def test_values_are_encrypted_on_disk_and_file_is_private(portal):
    portal.store("pw-abc", "hunter2")
    path = portal._secrets_path()
    assert os.stat(path).st_mode & 0o777 == 0o600
    raw = open(path).read()
    assert "hunter2" not in raw            # never plaintext on disk
    data = json.loads(raw)
    assert set(data) == {"pw-abc"}


def test_wrong_master_cannot_decrypt(portal, monkeypatch):
    portal.store("pw-abc", "s3cret!")
    monkeypatch.setattr(portal_secret, "_master", lambda: b"some-other-master")
    assert portal_secret.get("pw-abc") is None     # graceful, no exception


def test_save_fsyncs_before_replace(portal, monkeypatch):
    """These are the user's passwords: the same fsync-before-rename
    durability as the config (a power cut must never eat the store)."""
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync",
                        lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace",
                        lambda a, b: (order.append("replace"), real_replace(a, b))[1])
    portal.store("pw-abc", "x")
    assert "fsync" in order and order.index("fsync") < order.index("replace")


def test_corrupt_store_file_degrades_gracefully(portal):
    os.makedirs(os.path.dirname(portal._secrets_path()), exist_ok=True)
    with open(portal._secrets_path(), "w") as f:
        f.write("{ not json")
    assert portal.get("pw-abc") is None            # no crash
    assert portal.store("pw-new", "v") is True     # store recovers the file
    assert portal.get("pw-new") == "v"


# ---------------------------------------------------------------------------
# Threading: the portal retrieval must never run on the action worker thread
# ---------------------------------------------------------------------------
def test_master_is_never_retrieved_off_the_main_thread(monkeypatch):
    """Password keys dispatch on the controller's action worker thread. The
    portal retrieval builds Qt/D-Bus objects and runs a nested event loop, so
    doing it there would block the SERIAL action queue for the whole portal
    timeout: every later key press stuck behind one password key. Off the
    main thread an unprimed store must decline, not retrieve."""
    import threading

    monkeypatch.setattr(portal_secret, "_MASTER", None)
    monkeypatch.setattr(portal_secret, "_MASTER_TRIED", False)
    monkeypatch.setattr(portal_secret, "_retrieve_master_secret",
                        lambda *a, **k: pytest.fail(
                            "portal retrieval attempted off the main thread"))
    result = {}

    def worker():
        result["master"] = portal_secret._master()

    t = threading.Thread(target=worker)
    t.start()
    t.join(5)
    assert result["master"] is None
    assert portal_secret._MASTER_TRIED is False   # still primeable later


def test_primed_master_is_usable_from_a_worker_thread(monkeypatch):
    """After prime() on the main thread, worker-thread reads are pure
    decryption: no Qt, no D-Bus, no blocking."""
    import threading

    monkeypatch.setattr(portal_secret, "_MASTER", None)
    monkeypatch.setattr(portal_secret, "_MASTER_TRIED", False)
    monkeypatch.setattr(portal_secret, "_retrieve_master_secret",
                        lambda *a, **k: MASTER)
    assert portal_secret.prime() is True
    monkeypatch.setattr(portal_secret, "_retrieve_master_secret",
                        lambda *a, **k: pytest.fail("must use the primed value"))
    assert portal_secret.store("pw-t", "threaded") is True
    got = {}

    def worker():
        got["v"] = portal_secret.get("pw-t")

    t = threading.Thread(target=worker)
    t.start()
    t.join(5)
    assert got["v"] == "threaded"
    portal_secret.delete("pw-t")
