"""The password action's editor round-trip, against a keyring that misbehaves.

A locked SecretService is an ordinary state — it's the default on a fresh login
until something unlocks it — and the editor has to survive it without throwing
the user's saved password away.
"""
import pytest

pytest.importorskip("PyQt6")

from fifine_deck import secret_store                       # noqa: E402
from fifine_deck.gui import widgets                        # noqa: E402
from fifine_deck.gui.widgets import ActionParamsWidget     # noqa: E402
from fifine_deck.model import Action                       # noqa: E402


@pytest.fixture
def keyring(monkeypatch):
    """A fake keyring whose behaviour each test dictates."""
    class Fake:
        def __init__(self):
            self.store_ok = True
            self.values = {}
            self.readable = True

        def get(self, sid):
            return self.values.get(sid) if self.readable else None

        def store(self, sid, pw):
            if not self.store_ok:
                return False
            self.values[sid] = pw
            return True

    fake = Fake()
    monkeypatch.setattr(secret_store, "get", fake.get)
    monkeypatch.setattr(secret_store, "store", fake.store)
    return fake


@pytest.fixture
def warned(monkeypatch):
    seen = []
    monkeypatch.setattr(widgets.QMessageBox, "warning",
                        staticmethod(lambda parent, title, text, *a, **k: seen.append(text)))
    return seen


def _editor(action):
    w = ActionParamsWidget()
    w.set_action(action)
    return w


# -- the data-loss bug --------------------------------------------------------

def test_locked_keyring_does_not_destroy_the_saved_binding(qapp, keyring):
    """The bug: get() returns None for a locked keyring, so the field renders
    empty. _collect_password read that empty field as "no password" and dropped
    secret_id — so editing the LABEL next to it permanently unbound a working
    password and orphaned the keyring entry."""
    keyring.values["pw-abc"] = "s3cret"
    keyring.readable = False                       # locked: cannot read it back

    w = _editor(Action("password", {"secret_id": "pw-abc"}))
    assert w._params["password"].text() == ""      # nothing to display

    got = w.get_action()                           # any other edit triggers this
    assert got.params.get("secret_id") == "pw-abc", "the binding was destroyed"
    assert "password" not in got.params            # and not leaked to cleartext


def test_a_readable_password_survives_an_unrelated_edit(qapp, keyring):
    keyring.values["pw-abc"] = "s3cret"
    w = _editor(Action("password", {"secret_id": "pw-abc"}))
    assert w._params["password"].text() == "s3cret"
    assert w.get_action().params.get("secret_id") == "pw-abc"


def test_clearing_a_readable_password_still_removes_it(qapp, keyring):
    """The other side of the fix: when we COULD show the password, an empty
    field really is the user clearing it, and must not be second-guessed."""
    keyring.values["pw-abc"] = "s3cret"
    w = _editor(Action("password", {"secret_id": "pw-abc"}))
    w._params["password"].setText("")

    got = w.get_action()
    assert "secret_id" not in got.params
    assert not got.params.get("password")


def test_typing_a_new_password_stores_it_in_the_keyring(qapp, keyring):
    w = _editor(Action("password", {}))
    w._params["password"].setText("hunter2")

    got = w.get_action()
    sid = got.params.get("secret_id")
    assert sid and sid.startswith("pw-")
    assert keyring.values[sid] == "hunter2"
    assert "password" not in got.params            # never the value itself


def test_replacing_a_password_under_a_locked_keyring_reuses_the_id(qapp, keyring):
    """Typing a new value while locked must overwrite the existing entry, not
    orphan it and mint a second one."""
    keyring.values["pw-abc"] = "old"
    keyring.readable = False
    w = _editor(Action("password", {"secret_id": "pw-abc"}))
    w._params["password"].setText("new")

    got = w.get_action()
    assert got.params.get("secret_id") == "pw-abc"
    assert keyring.values["pw-abc"] == "new"


# -- the cleartext fallback ---------------------------------------------------

def test_no_keyring_falls_back_to_cleartext_but_warns(qapp, keyring, warned):
    """Storing it in config.json in the clear is the documented fallback, but
    the user picked a password action expecting it to be secured — doing
    otherwise silently is the problem."""
    keyring.store_ok = False
    w = _editor(Action("password", {}))
    w._params["password"].setText("hunter2")

    got = w.get_action()
    assert got.params.get("password") == "hunter2"
    assert "secret_id" not in got.params
    assert warned and "cleartext" in warned[0]


def test_the_cleartext_warning_is_shown_only_once(qapp, keyring, warned):
    """It fires from _collect, which runs on every keystroke."""
    keyring.store_ok = False
    w = _editor(Action("password", {}))
    w._params["password"].setText("hunter2")
    for _ in range(5):
        w.get_action()
    assert len(warned) == 1


def test_a_working_keyring_never_warns(qapp, keyring, warned):
    w = _editor(Action("password", {}))
    w._params["password"].setText("hunter2")
    w.get_action()
    assert warned == []
