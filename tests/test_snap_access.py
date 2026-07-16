"""Classic-snap device access: confinement detection, guidance, and the
one-click pkexec udev-rule installer.

This is the path the shipped snap actually takes. A snap cannot install a udev
rule itself, so the classic build bundles the rule plus a helper and elevates
via polkit; without that rule /dev/hidraw is unreadable and the deck is inert.
"""
from __future__ import annotations

import subprocess
import types

import pytest

from fifine_deck import actions


@pytest.fixture
def classic_snap(tmp_path, monkeypatch):
    """Simulate running inside the classic snap, with rule + helper staged."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "fifine-install-udev-rule").write_text("#!/bin/sh\n")
    (tmp_path / "udev").mkdir()
    (tmp_path / "udev" / "70-fifine-deck.rules").write_text("# rule\n")
    monkeypatch.setenv("SNAP", str(tmp_path))
    monkeypatch.setenv("SNAP_NAME", "fifine-control-deck")
    monkeypatch.setattr(actions, "IN_SNAP", True)
    monkeypatch.setattr(actions, "IN_SNAP_CLASSIC", True)
    return tmp_path


@pytest.fixture
def fake_pkexec(monkeypatch):
    """Capture the pkexec invocation instead of raising a real auth prompt."""
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        result = run.result
        if isinstance(result, Exception):
            raise result
        return result

    run.result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(actions.subprocess, "run", run)
    run.calls = calls
    return run


# -- confinement detection --------------------------------------------------

@pytest.mark.parametrize("line, expected", [
    ("confinement: classic", True),
    ("confinement: strict", False),
    ("confinement: devmode", False),
])
def test_snap_is_classic_reads_meta_snap_yaml(tmp_path, monkeypatch, line, expected):
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "snap.yaml").write_text(f"name: fifine-control-deck\n{line}\n")
    monkeypatch.setenv("SNAP", str(tmp_path))
    assert actions._snap_is_classic() is expected


def test_snap_is_classic_false_without_snap_env(monkeypatch):
    monkeypatch.delenv("SNAP", raising=False)
    assert actions._snap_is_classic() is False


def test_snap_is_classic_false_when_meta_unreadable(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAP", str(tmp_path))          # no meta/snap.yaml at all
    assert actions._snap_is_classic() is False


# -- guidance ---------------------------------------------------------------

def test_classic_hint_points_at_the_bundled_rule(classic_snap):
    """The classic snap CAN drive the deck once the host has the rule, so its
    guidance must name the bundled rule — not tell the user to give up."""
    hint = actions.snap_usb_hint()
    assert hint is not None
    assert str(classic_snap / "udev" / "70-fifine-deck.rules") in hint
    assert "udevadm" in hint
    assert "raw-usb" not in hint                       # strict-only advice


def test_strict_hint_sends_the_user_to_the_ppa(monkeypatch):
    """Strict confinement cannot reach /dev/hidraw at all; the hint must say so
    rather than offer a fix that cannot work."""
    monkeypatch.setattr(actions, "IN_SNAP", True)
    monkeypatch.setattr(actions, "IN_SNAP_CLASSIC", False)
    hint = actions.snap_usb_hint()
    assert hint is not None
    assert "cannot control the deck" in hint
    assert "ppa:zoutmax/fifine" in hint


# -- availability of the one-click button -----------------------------------

def test_button_available_in_classic_snap(classic_snap):
    assert actions.can_install_udev_rule() is True


def test_button_hidden_outside_a_snap(monkeypatch):
    monkeypatch.setattr(actions, "IN_SNAP_CLASSIC", False)
    assert actions.can_install_udev_rule() is False


def test_button_hidden_when_helper_missing(classic_snap):
    (classic_snap / "bin" / "fifine-install-udev-rule").unlink()
    assert actions.can_install_udev_rule() is False


def test_installer_refuses_outside_classic_snap(monkeypatch):
    monkeypatch.setattr(actions, "IN_SNAP_CLASSIC", False)
    ok, msg = actions.install_udev_rule_pkexec()
    assert ok is False
    assert "classic snap" in msg


# -- the pkexec call itself -------------------------------------------------

def test_installer_elevates_the_bundled_helper(classic_snap, fake_pkexec):
    ok, msg = actions.install_udev_rule_pkexec()
    assert ok is True
    argv, kw = fake_pkexec.calls[0]
    assert argv[0].endswith("pkexec")
    assert argv[1] == str(classic_snap / "bin" / "fifine-install-udev-rule")
    assert kw["timeout"] == 120                       # must not hang the GUI forever
    assert kw["capture_output"] is True


@pytest.mark.parametrize("rc", [126, 127])
def test_cancelled_auth_is_reported_gently(classic_snap, fake_pkexec, rc):
    """pkexec exits 126/127 when the user dismisses the polkit dialog. That is
    a normal choice, not an error to shout about."""
    fake_pkexec.result = types.SimpleNamespace(returncode=rc, stdout="", stderr="")
    ok, msg = actions.install_udev_rule_pkexec()
    assert ok is False
    assert msg == "Authentication was cancelled."


def test_real_failure_surfaces_stderr(classic_snap, fake_pkexec):
    fake_pkexec.result = types.SimpleNamespace(
        returncode=1, stdout="", stderr="  cp: read-only file system\n")
    ok, msg = actions.install_udev_rule_pkexec()
    assert ok is False
    assert msg == "cp: read-only file system"


def test_failure_falls_back_to_stdout_then_a_default(classic_snap, fake_pkexec):
    fake_pkexec.result = types.SimpleNamespace(returncode=1, stdout="boom\n", stderr="")
    assert actions.install_udev_rule_pkexec() == (False, "boom")
    fake_pkexec.result = types.SimpleNamespace(returncode=1, stdout="", stderr="")
    ok, msg = actions.install_udev_rule_pkexec()
    assert ok is False and msg                        # never a blank dialog


def test_missing_pkexec_is_reported(classic_snap, fake_pkexec):
    fake_pkexec.result = FileNotFoundError()
    assert actions.install_udev_rule_pkexec() == (False, "pkexec is not available on this system.")


def test_timeout_is_reported(classic_snap, fake_pkexec):
    fake_pkexec.result = subprocess.TimeoutExpired("pkexec", 120)
    ok, msg = actions.install_udev_rule_pkexec()
    assert ok is False
    assert "Timed out" in msg
