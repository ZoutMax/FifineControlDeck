"""Start-on-login: the XDG autostart .desktop entry, its path handling, and
the enable/disable behaviour."""
import os

import pytest

from fifine_deck import app
from fifine_deck.model import DeckConfig


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_autostart_file_honors_xdg_config_home(xdg):
    assert app.autostart_file() == str(
        xdg / "autostart" / "fifine-control-deck.desktop")


def test_autostart_file_falls_back_to_home_config(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert app.autostart_file() == os.path.expanduser(
        "~/.config/autostart/fifine-control-deck.desktop")


def test_set_autostart_writes_and_removes_the_entry(xdg, monkeypatch):
    assert app.set_autostart(True) == 0
    path = app.autostart_file()
    entry = open(path).read()
    assert "Exec=fifine-control-deck --hidden" in entry
    assert "Type=Application" in entry
    assert app.set_autostart(False) == 0
    assert not os.path.exists(path)
    # disabling twice stays a friendly no-op
    assert app.set_autostart(False) == 0


def test_quit_flag_waits_until_the_instance_is_gone(tmp_path, monkeypatch, capsys):
    """--quit must be synchronous: returning while the old instance is still
    dying makes quit-and-relaunch a race, and the relaunch defers to the
    zombie. The wait must poll the socket FILE, never connect — older
    versions treat any connection as "show", interrupting their shutdown."""
    sock = tmp_path / "ipc.sock"
    sock.write_text("")
    calls = []

    def fake_signal(cmd):
        calls.append(cmd)
        return True
    monkeypatch.setattr(app, "_signal_existing", fake_signal)
    monkeypatch.setattr(app, "_liveness_paths", lambda: {str(sock)})
    checks = {"n": 0}
    real_exists = app.os.path.exists

    def counting_exists(p):
        if p == str(sock):
            checks["n"] += 1
            if checks["n"] >= 3:
                sock.unlink(missing_ok=True)     # instance exits on 3rd poll
        return real_exists(p)
    monkeypatch.setattr(app.os.path, "exists", counting_exists)
    rc = app.run_gui(quit_flag=True)
    assert rc == 0
    assert calls == ["quit"]                     # ONE signal, zero pings
    assert "stopped" in capsys.readouterr().out


def test_quit_flag_reports_a_stuck_instance(tmp_path, monkeypatch):
    sock = tmp_path / "ipc.sock"
    sock.write_text("")                          # never removed: stuck
    monkeypatch.setattr(app, "_signal_existing", lambda cmd: True)
    monkeypatch.setattr(app, "_liveness_paths", lambda: {str(sock)})
    import time as _time
    t = {"now": 0.0}
    monkeypatch.setattr(_time, "monotonic", lambda: t.__setitem__("now", t["now"] + 3) or t["now"])
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    assert app.run_gui(quit_flag=True) == 1
